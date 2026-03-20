"""Announcement commands and utilities.

This cog provides commands for sending messages and attachments
to specific channels. Administrators can post announcements
directly to any text channel by ID and distribute a PDF or
other file. The path to the PDF is read from the environment
variable `PDF_PATH`.
"""

import os
from typing import Optional

import discord
from discord.ext import commands


class Announcement(commands.Cog):
    """A cog for announcements and file distribution."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="announce")
    @commands.has_permissions(administrator=True)
    async def announce(self, ctx: commands.Context, channel_id: int, *, message: str) -> None:
        """Send a plain text announcement to a specific channel.

        Usage:
            !announce <channel_id> <message>

        Only users with administrator permissions can call this command.

        :param ctx: The invocation context.
        :param channel_id: The numerical ID of the channel to send the announcement to.
        :param message: The announcement message to send.
        """
        channel: Optional[discord.TextChannel] = self.bot.get_channel(channel_id)
        if channel is None:
            await ctx.send(f"❌ Channel with ID {channel_id} not found. Make sure the bot can see it.")
            return
        try:
            await channel.send(message)
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to send messages to that channel.")
            return
        await ctx.send(f"✅ Announcement sent to {channel.mention}")

    @commands.command(name="sendpdf")
    @commands.has_permissions(administrator=True)
    async def sendpdf(self, ctx: commands.Context, channel_id: int) -> None:
        """Send a PDF file configured in the environment to a specific channel.

        Usage:
            !sendpdf <channel_id>

        Reads the `PDF_PATH` environment variable to locate the file. If it
        isn't set or the file doesn't exist, the command will notify the user.
        """
        channel: Optional[discord.TextChannel] = self.bot.get_channel(channel_id)
        if channel is None:
            await ctx.send(f"❌ Channel with ID {channel_id} not found.")
            return

        pdf_path = os.getenv("PDF_PATH")
        if not pdf_path:
            await ctx.send("❌ PDF_PATH is not set in the environment. Please specify it in your .env file.")
            return

        if not os.path.isfile(pdf_path):
            await ctx.send(f"❌ PDF file not found at: {pdf_path}")
            return

        try:
            await channel.send(file=discord.File(pdf_path))
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to send files to that channel.")
            return
        await ctx.send(f"✅ PDF sent to {channel.mention}")


async def setup(bot: commands.Bot) -> None:
    """Register the Announcement cog."""
    await bot.add_cog(Announcement(bot))