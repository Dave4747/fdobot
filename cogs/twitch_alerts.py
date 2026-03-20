import os
import json
import time
import asyncio
import logging
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_CHANNEL_LOGIN = os.getenv("TWITCH_CHANNEL_LOGIN", "frodinator").strip().lower()
TWITCH_ALERT_CHANNEL_ID = int(os.getenv("TWITCH_ALERT_CHANNEL_ID", "0"))

STATE_FILE = Path("twitch_alert_state.json")
ALERT_COOLDOWN_SECONDS = 5 * 60 * 60  # 5 hours


def load_state():
    if not STATE_FILE.exists():
        return {
            "last_alert_at": 0,
            "last_live_stream_id": None,
            "was_live": False,
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "last_alert_at": data.get("last_alert_at", 0),
                "last_live_stream_id": data.get("last_live_stream_id"),
                "was_live": data.get("was_live", False),
            }
    except Exception:
        logger.exception("Failed to load Twitch alert state; using defaults.")
        return {
            "last_alert_at": 0,
            "last_live_stream_id": None,
            "was_live": False,
        }


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        logger.exception("Failed to save Twitch alert state.")


class TwitchAlerts(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.app_access_token: str | None = None
        self.token_expires_at: float = 0
        self.state = load_state()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        self.check_twitch_live.start()

    async def cog_unload(self):
        self.check_twitch_live.cancel()
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_app_access_token(self) -> str:
        now = time.time()

        if self.app_access_token and now < self.token_expires_at - 60:
            return self.app_access_token

        if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
            raise RuntimeError("Missing TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET in .env")

        if self.session is None:
            raise RuntimeError("HTTP session is not initialized")

        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        }

        async with self.session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Failed to get Twitch token: {resp.status} {text}")

            data = await resp.json()
            self.app_access_token = data["access_token"]
            expires_in = int(data.get("expires_in", 0))
            self.token_expires_at = now + expires_in
            logger.info("Fetched new Twitch app access token.")
            return self.app_access_token

    async def fetch_live_stream(self):
        token = await self.get_app_access_token()

        if self.session is None:
            raise RuntimeError("HTTP session is not initialized")

        url = "https://api.twitch.tv/helix/streams"
        headers = {
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        }
        params = {
            "user_login": TWITCH_CHANNEL_LOGIN,
        }

        async with self.session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Failed to fetch stream status: {resp.status} {text}")

            payload = await resp.json()
            streams = payload.get("data", [])
            if not streams:
                return None

            return streams[0]

    async def send_live_alert(self, stream_data: dict):
        if TWITCH_ALERT_CHANNEL_ID == 0:
            raise RuntimeError("TWITCH_ALERT_CHANNEL_ID is missing or invalid in .env")

        channel = self.bot.get_channel(TWITCH_ALERT_CHANNEL_ID)
        if channel is None:
            try:
                fetched = await self.bot.fetch_channel(TWITCH_ALERT_CHANNEL_ID)
                if isinstance(fetched, discord.TextChannel):
                    channel = fetched
            except Exception as exc:
                raise RuntimeError(f"Could not fetch alert channel: {exc}") from exc

        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("TWITCH alert channel is not a text channel")

        title = stream_data.get("title", "Frodinator is live!")
        game_name = stream_data.get("game_name") or "Live now"
        viewer_count = stream_data.get("viewer_count")
        stream_id = stream_data.get("id")
        thumbnail_url = stream_data.get("thumbnail_url", "")
        thumbnail_url = thumbnail_url.replace("{width}", "1280").replace("{height}", "720")

        embed = discord.Embed(
            title="🔴 Frodinator is LIVE on Twitch",
            description=f"**{title}**\n\nJump in and watch now: https://www.twitch.tv/{TWITCH_CHANNEL_LOGIN}",
            color=discord.Color.purple(),
            url=f"https://www.twitch.tv/{TWITCH_CHANNEL_LOGIN}",
        )
        embed.add_field(name="Channel", value=TWITCH_CHANNEL_LOGIN, inline=True)
        embed.add_field(name="Category", value=game_name, inline=True)

        if viewer_count is not None:
            embed.add_field(name="Viewers", value=str(viewer_count), inline=True)

        if thumbnail_url:
            # cache-bust Discord image caching
            embed.set_image(url=f"{thumbnail_url}?t={int(time.time())}")

        embed.set_footer(text="Live alert limited to once every 5 hours.")

        content = "@everyone Frodinator just went live on Twitch!"

        await channel.send(content=content, embed=embed)

        self.state["last_alert_at"] = int(time.time())
        self.state["last_live_stream_id"] = stream_id
        self.state["was_live"] = True
        save_state(self.state)

        logger.info("Posted Twitch live alert for stream %s", stream_id)

    @tasks.loop(minutes=1)
    async def check_twitch_live(self):
        try:
            stream = await self.fetch_live_stream()
            now = int(time.time())

            if stream is None:
                if self.state.get("was_live"):
                    logger.info("Twitch channel is now offline.")
                self.state["was_live"] = False
                self.state["last_live_stream_id"] = None
                save_state(self.state)
                return

            stream_id = stream.get("id")
            was_live = self.state.get("was_live", False)
            last_alert_at = int(self.state.get("last_alert_at", 0))
            last_stream_id = self.state.get("last_live_stream_id")

            should_alert = False

            # Fresh live session
            if not was_live:
                should_alert = True

            # Stream changed / restarted
            elif stream_id and last_stream_id and stream_id != last_stream_id:
                should_alert = True

            # Safety cooldown in case state got weird
            elif now - last_alert_at >= ALERT_COOLDOWN_SECONDS:
                should_alert = True

            if should_alert:
                await self.send_live_alert(stream)
            else:
                self.state["was_live"] = True
                self.state["last_live_stream_id"] = stream_id
                save_state(self.state)

        except Exception:
            logger.exception("Error while checking Twitch live status.")

    @check_twitch_live.before_loop
    async def before_check_twitch_live(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)


async def setup(bot: commands.Bot):
    await bot.add_cog(TwitchAlerts(bot))