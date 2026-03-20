"""Moderation commands for FdoBot.

This cog provides basic administrative utilities to help keep the
server safe and orderly. Commands include kick, ban, warn and mute.
All moderation commands require the caller to have the appropriate
guild permissions. The mute command uses Discord's native timeouts
to temporarily restrict a user's ability to send messages.
"""

import datetime
from typing import Optional

import discord
from discord.ext import commands


class Moderation(commands.Cog):
    """A cog that implements simple moderation tools."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick_member(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """Kick a member from the guild.

        Usage:
            !kick @member [reason]

        Requires the caller to have the `Kick Members` permission.
        """
        try:
            await member.kick(reason=reason)
            await ctx.send(f"✅ {member.display_name} has been kicked.{' Reason: ' + reason if reason else ''}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to kick that user.")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_member(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """Ban a member from the guild.

        Usage:
            !ban @member [reason]

        Requires the caller to have the `Ban Members` permission.
        """
        try:
            await member.ban(reason=reason)
            await ctx.send(f"✅ {member.display_name} has been banned.{' Reason: ' + reason if reason else ''}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to ban that user.")

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn_member(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """Privately warn a member via direct message.

        Usage:
            !warn @member [reason]

        The user receives a DM with the warning reason. If DMs are closed,
        the warning will silently fail for the DM but still notify the invoker.
        """
        try:
            if reason:
                message = f"You have been warned in {ctx.guild.name}: {reason}"
            else:
                message = f"You have been warned in {ctx.guild.name}."
            await member.send(message)
        except discord.Forbidden:
            # The user has DMs closed or blocked the bot
            pass
        await ctx.send(f"⚠️ {member.display_name} has been warned.{ ' Reason: ' + reason if reason else ''}")

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def mute_member(self, ctx: commands.Context, member: discord.Member, minutes: int, *, reason: Optional[str] = None) -> None:
        """Mute a member by applying a timeout for a specified number of minutes.

        Usage:
            !mute @member <minutes> [reason]

        Discord timeouts prevent the user from sending messages or joining
        voice channels for the duration of the timeout. Requires the caller
        to have the `Moderate Members` permission.
        """
        if minutes <= 0:
            await ctx.send("❌ Duration must be a positive number of minutes.")
            return
        duration = datetime.timedelta(minutes=minutes)
        try:
            await member.timeout(duration, reason=reason)
            await ctx.send(
                f"🔇 {member.display_name} has been muted for {minutes} minute{'s' if minutes != 1 else ''}."
                f"{' Reason: ' + reason if reason else ''}"
            )
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to mute that user.")


async def setup(bot: commands.Bot) -> None:
    """Register the Moderation cog with the bot."""
    await bot.add_cog(Moderation(bot))