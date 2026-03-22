import logging
import time
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

WELCOME_CHANNEL_ID = 1482938350552875029
UPCOMING_GAMES_CHANNEL_ID = 1482938554026823782
MEMBER_ROLE_NAME = "Member"

DEDUP_SECONDS = 60


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent_joins: dict[tuple[int, int], float] = {}

    def _is_duplicate_join(self, guild_id: int, member_id: int) -> bool:
        now = time.time()
        key = (guild_id, member_id)

        expired = [k for k, ts in self._recent_joins.items() if now - ts > DEDUP_SECONDS]
        for k in expired:
            del self._recent_joins[k]

        if key in self._recent_joins:
            return True

        self._recent_joins[key] = now
        return False

    async def _resolve_text_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched = await guild.fetch_channel(channel_id)
            if isinstance(fetched, discord.TextChannel):
                return fetched
        except discord.NotFound:
            logger.warning("Channel %s not found in guild %s", channel_id, guild.id)
        except discord.Forbidden:
            logger.warning("No permission to fetch channel %s in guild %s", channel_id, guild.id)
        except discord.HTTPException as exc:
            logger.exception("Failed fetching channel %s in guild %s: %s", channel_id, guild.id, exc)

        return None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild

        if self._is_duplicate_join(guild.id, member.id):
            logger.info("Skipped duplicate join event for %s (%s)", member, member.id)
            return

        logger.info("Handling join for %s (%s) in guild %s", member, member.id, guild.id)

        role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)

        if role is None:
            logger.warning("Role '%s' not found in guild '%s' (%s)", MEMBER_ROLE_NAME, guild.name, guild.id)
        else:
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason="Auto role on join")
                    logger.info("Assigned role '%s' to %s (%s)", role.name, member, member.id)
            except discord.Forbidden:
                logger.warning(
                    "Could not assign role '%s' to %s (%s). Check Manage Roles permission and role order.",
                    role.name,
                    member,
                    member.id,
                )
            except discord.HTTPException as exc:
                logger.exception("HTTP error assigning role '%s' to %s (%s): %s", role.name, member, member.id, exc)

        welcome_channel = await self._resolve_text_channel(guild, WELCOME_CHANNEL_ID)

        if welcome_channel is None:
            logger.warning("Welcome channel %s unavailable in guild %s", WELCOME_CHANNEL_ID, guild.id)
            return

        message = (
            f"Welcome {member.mention} to the server 🍩\n"
            f"Please check <#{UPCOMING_GAMES_CHANNEL_ID}> for upcoming games."
        )

        try:
            await welcome_channel.send(message)
            logger.info("Sent welcome message for %s (%s)", member, member.id)
        except discord.Forbidden:
            logger.warning("No permission to send welcome message in channel %s", WELCOME_CHANNEL_ID)
        except discord.HTTPException as exc:
            logger.exception("Failed sending welcome message for %s (%s): %s", member, member.id, exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))