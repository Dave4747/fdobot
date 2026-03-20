"""Tournament and scoreboard management.

This cog implements a simple tournament scoreboard that can be
created in a specific channel and updated live. Scores are kept
in memory and sorted in descending order whenever they are updated.

Administrators can create a scoreboard message with the
`!tournament_create` command, update individual player scores
with `!score`, and display the current standings with
`!show_scores`. When scores change, the existing embed message
is edited in place so spectators always see the current rankings.
"""

from typing import Dict, Optional
import discord
from discord.ext import commands


class Tournament(commands.Cog):
    """A cog for managing tournament scoreboards."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Mapping of player name to their points
        self.scores: Dict[str, int] = {}
        # ID of the message holding the scoreboard embed
        self.message_id: Optional[int] = None
        # ID of the channel containing the scoreboard message
        self.channel_id: Optional[int] = None

    def build_embed(self) -> discord.Embed:
        """Construct the scoreboard embed based on current scores."""
        embed = discord.Embed(
            title="🏆 Tournament Scoreboard",
            color=discord.Color.orange(),
            description="Current standings for the ongoing tournament."
        )
        if not self.scores:
            embed.add_field(name="No players", value="The scoreboard is empty. Add scores to populate it.", inline=False)
        else:
            # Sort by points descending
            sorted_scores = sorted(self.scores.items(), key=lambda item: item[1], reverse=True)
            position = 1
            for player, points in sorted_scores:
                embed.add_field(
                    name=f"{position}. {player}",
                    value=f"{points} point{'s' if points != 1 else ''}",
                    inline=False,
                )
                position += 1
        return embed

    async def update_message(self) -> None:
        """Edit the existing scoreboard message to reflect current scores.

        Does nothing if no scoreboard message has been created.
        """
        if self.channel_id is None or self.message_id is None:
            return
        channel: Optional[discord.TextChannel] = self.bot.get_channel(self.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(self.message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        embed = self.build_embed()
        await message.edit(embed=embed)

    @commands.command(name="tournament_create")
    @commands.has_permissions(administrator=True)
    async def create_scoreboard(self, ctx: commands.Context, channel_id: int) -> None:
        """Create a new scoreboard in the specified channel.

        Usage:
            !tournament_create <channel_id>

        The command sends an embed to the target channel and stores
        the message and channel IDs for later updates.
        """
        channel: Optional[discord.TextChannel] = self.bot.get_channel(channel_id)
        if channel is None:
            await ctx.send(f"❌ Channel with ID {channel_id} not found.")
            return
        embed = self.build_embed()
        message = await channel.send(embed=embed)
        self.message_id = message.id
        self.channel_id = channel.id
        await ctx.send(f"✅ Tournament scoreboard created in {channel.mention}")

    @commands.command(name="score")
    @commands.has_permissions(administrator=True)
    async def update_score(self, ctx: commands.Context, player: str, points: int) -> None:
        """Add or update a player's score and refresh the scoreboard embed.

        Usage:
            !score <player> <points>

        The player name is case-sensitive. If the player does not
        already exist on the scoreboard, they will be added.
        """
        self.scores[player] = points
        # Update the embed message in place, if it exists
        await self.update_message()
        await ctx.send(f"✅ Updated {player}'s score to {points} point{'s' if points != 1 else ''}.")

    @commands.command(name="show_scores")
    async def show_scores(self, ctx: commands.Context) -> None:
        """Send the current scoreboard embed to the invoking channel."""
        embed = self.build_embed()
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Register the Tournament cog with the bot."""
    await bot.add_cog(Tournament(bot))