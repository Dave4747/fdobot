"""Rich message utilities for FdoBot.

Admin-only commands for sending embeds and reaction messages to channels.
Designed to be safer and more reliable than cache-only channel lookups.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class Messages(commands.Cog):
    """Commands for sending rich content to channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _resolve_text_channel(
        self,
        guild: discord.Guild,
        channel_id: int,
    ) -> Optional[discord.TextChannel]:
        """Get a text channel reliably using cache first, then fetch."""
        channel = guild.get_channel(channel_id)

        if channel is None:
            try:
                fetched = await guild.fetch_channel(channel_id)
                if isinstance(fetched, discord.TextChannel):
                    return fetched
                logger.warning(
                    "Fetched channel %s in guild %s but it is not a TextChannel",
                    channel_id,
                    guild.id,
                )
                return None
            except discord.NotFound:
                logger.warning("Channel %s not found in guild %s", channel_id, guild.id)
                return None
            except discord.Forbidden:
                logger.warning(
                    "Missing permission to fetch channel %s in guild %s",
                    channel_id,
                    guild.id,
                )
                return None
            except discord.HTTPException as exc:
                logger.exception(
                    "HTTP error fetching channel %s in guild %s: %s",
                    channel_id,
                    guild.id,
                    exc,
                )
                return None

        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Channel %s in guild %s is not a TextChannel",
                channel_id,
                guild.id,
            )
            return None

        return channel

    @commands.command(name="send_embed")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def send_embed(
        self,
        ctx: commands.Context,
        channel_id: int,
        *,
        payload: str,
    ) -> None:
        """Send an embed with optional image.

        Format:
            !send_embed <channel_id> Title | Description | https://image.url/pic.png

        Image URL is optional.
        """
        parts: Tuple[str, ...] = tuple(part.strip() for part in payload.split("|"))

        if len(parts) < 2:
            await ctx.send(
                "❌ Format is: `!send_embed <channel_id> Title | Description | optional_image_url`"
            )
            return

        title: str = parts[0]
        description: str = parts[1]
        image_url: Optional[str] = parts[2] if len(parts) > 2 and parts[2] else None

        if not title:
            await ctx.send("❌ The embed title cannot be empty.")
            return

        if not description:
            await ctx.send("❌ The embed description cannot be empty.")
            return

        if len(title) > 256:
            await ctx.send("❌ Embed title is too long. Discord allows up to 256 characters.")
            return

        if len(description) > 4096:
            await ctx.send("❌ Embed description is too long. Discord allows up to 4096 characters.")
            return

        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server.")
            return

        channel = await self._resolve_text_channel(ctx.guild, channel_id)
        if channel is None:
            await ctx.send(f"❌ Text channel with ID `{channel_id}` not found.")
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blue(),
        )

        if image_url:
            if image_url.startswith(("http://", "https://")):
                embed.set_image(url=image_url)
            else:
                await ctx.send("❌ Image URL must start with `http://` or `https://`.")
                return

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(
                "Missing permission to send embed in channel %s (%s)",
                channel.name,
                channel.id,
            )
            await ctx.send("❌ I don't have permission to send embeds to that channel.")
            return
        except discord.HTTPException as exc:
            logger.exception(
                "Failed to send embed to channel %s (%s): %s",
                channel.name,
                channel.id,
                exc,
            )
            await ctx.send("❌ Discord rejected the embed or the request failed.")
            return

        logger.info(
            "Embed sent by %s (%s) to channel %s (%s)",
            ctx.author,
            ctx.author.id,
            channel.name,
            channel.id,
        )
        await ctx.send(f"✅ Embed sent to {channel.mention}")

    @commands.command(name="send_reaction_message")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def send_reaction_message(
        self,
        ctx: commands.Context,
        channel_id: int,
        message: str,
        *emojis: str,
    ) -> None:
        """Send a message and add reactions.

        Example:
            !send_reaction_message 123456789012345678 "Vote now!" 👍 👎
        """
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server.")
            return

        channel = await self._resolve_text_channel(ctx.guild, channel_id)
        if channel is None:
            await ctx.send(f"❌ Text channel with ID `{channel_id}` not found.")
            return

        if not message.strip():
            await ctx.send("❌ The message cannot be empty.")
            return

        try:
            sent = await channel.send(message)
        except discord.Forbidden:
            logger.warning(
                "Missing permission to send message in channel %s (%s)",
                channel.name,
                channel.id,
            )
            await ctx.send("❌ I don't have permission to send messages to that channel.")
            return
        except discord.HTTPException as exc:
            logger.exception(
                "Failed to send reaction message to channel %s (%s): %s",
                channel.name,
                channel.id,
                exc,
            )
            await ctx.send("❌ Failed to send the message to that channel.")
            return

        failed_emojis = []

        for emoji in emojis:
            try:
                await sent.add_reaction(emoji)
            except discord.HTTPException:
                failed_emojis.append(emoji)
            except Exception as exc:
                logger.exception("Unexpected reaction error for emoji %s: %s", emoji, exc)
                failed_emojis.append(emoji)

        logger.info(
            "Reaction message sent by %s (%s) to channel %s (%s)",
            ctx.author,
            ctx.author.id,
            channel.name,
            channel.id,
        )

        if failed_emojis:
            await ctx.send(
                f"✅ Message sent to {channel.mention}, but these reactions failed: {' '.join(failed_emojis)}"
            )
        else:
            await ctx.send(f"✅ Message sent to {channel.mention} with reactions.")

    @send_embed.error
    @send_reaction_message.error
    async def admin_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        """User-friendly command error handling."""
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You need Administrator permission to use that command.")
            return

        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ One of the arguments is invalid. Check the channel ID and command format.")
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Missing required information for that command.")
            return

        logger.exception("Unhandled command error in messages cog: %s", error)
        await ctx.send("❌ Something went wrong while running that command.")


async def setup(bot: commands.Bot) -> None:
    """Register the Messages cog with the bot."""
    await bot.add_cog(Messages(bot))