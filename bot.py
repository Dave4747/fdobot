import os
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is not set. Please create a `.env` file and add your bot token."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("fdobot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.guild_messages = True
intents.guild_reactions = True


class FdoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        extensions = [
            "cogs.announcement",
            "cogs.tournament",
            "cogs.moderation",
            "cogs.messages",
            "cogs.tickets",
            "cogs.welcome",
            "cogs.twitch_alerts",
            "cogs.support_tickets",
        ]

        loaded = set()

        for ext in extensions:
            if ext in loaded:
                logger.warning("Skipped duplicate extension entry: %s", ext)
                continue

            try:
                await self.load_extension(ext)
                loaded.add(ext)
                logger.info("Loaded extension: %s", ext)
            except Exception:
                logger.exception("Failed to load extension: %s", ext)

    async def on_ready(self):
        logger.info("FdoBot is online as %s (ID: %s)", self.user, self.user.id)

    async def on_command_error(self, ctx, error):
        logger.exception("Command error in %s: %s", getattr(ctx.command, "name", "unknown"), error)


bot = FdoBot()
bot.run(TOKEN, log_handler=None)