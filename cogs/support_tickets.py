import asyncio
import io
import os
import re
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TICKET_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", "0"))
TICKET_PANEL_CHANNEL_ID = 1483368134131060958

SUPPORT_CATEGORY_NAME = "Support Tickets"
REWARD_CATEGORY_NAME = "Reward Claims"


def safe_channel_name(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:70] if text else "ticket"


def get_support_roles(guild: discord.Guild):
    return [role for role in guild.roles if "support" in role.name.lower()]


def is_staff(member: discord.Member):
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_channels
        or any("support" in role.name.lower() for role in member.roles)
    )


async def build_transcript(channel: discord.TextChannel):
    lines = []
    lines.append(f"Transcript for #{channel.name}")
    lines.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    lines.append("-" * 60)

    messages = [msg async for msg in channel.history(limit=None, oldest_first=True)]

    for msg in messages:
        created = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content if msg.content else "[no text content]"

        lines.append(f"[{created}] {author}")
        lines.append(content)

        if msg.embeds:
            for idx, embed in enumerate(msg.embeds, start=1):
                lines.append(f"[Embed {idx}] {embed.title or 'No title'}")
                lines.append(embed.description or "No description")
                for field in embed.fields:
                    lines.append(f"[Field] {field.name}")
                    lines.append(field.value)

        if msg.attachments:
            for attachment in msg.attachments:
                lines.append(f"[Attachment] {attachment.filename} - {attachment.url}")

        lines.append("-" * 60)

    transcript_text = "\n".join(lines)
    transcript_bytes = io.BytesIO(transcript_text.encode("utf-8"))
    return discord.File(transcript_bytes, filename=f"{channel.name}-transcript.txt")


class RenameModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Rename Ticket")

        self.new_name = discord.ui.TextInput(
            label="New channel name",
            placeholder="example-new-name",
            required=True,
            max_length=90
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ This can only be used in a ticket channel.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only staff can rename tickets.", ephemeral=True)
            return

        name = safe_channel_name(self.new_name.value)
        await interaction.channel.edit(name=name)
        await interaction.response.send_message(f"✅ Ticket renamed to `{name}`.", ephemeral=True)


class CloseCountdownView(discord.ui.View):
    def __init__(self, cog: "SupportTickets", channel: discord.TextChannel, closed_by: discord.Member, reason: str):
        super().__init__(timeout=15)
        self.cog = cog
        self.channel = channel
        self.closed_by = closed_by
        self.reason = reason
        self.cancelled = False
        self.message = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def start(self, msg: discord.Message):
        self.message = msg

        for i in range(10, 0, -1):
            if self.cancelled:
                return

            embed = discord.Embed(
                title="⏳ Closing Ticket",
                description=(
                    f"This ticket will close in **{i} seconds**.\n\n"
                    f"**Reason:** {self.reason}"
                ),
                color=discord.Color.orange()
            )

            try:
                await msg.edit(embed=embed, view=self)
            except discord.HTTPException:
                return

            await asyncio.sleep(1)

        if not self.cancelled:
            await self.cog.final_close(self.channel, self.closed_by, self.reason)

    @discord.ui.button(label="Cancel Close", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def cancel(self, interaction: discord.Interaction, button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only", ephemeral=True)
            return

        self.cancelled = True
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Cancelled",
                description="Ticket will remain open.",
                color=discord.Color.green()
            ),
            view=self
        )


class CloseModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Optional reason",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="Leave blank if no reason is needed."
    )

    def __init__(self, cog: "SupportTickets"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only", ephemeral=True)
            return

        close_reason = self.reason.value.strip() if self.reason.value else ""
        if not close_reason:
            close_reason = "Closed by staff."

        view = CloseCountdownView(self.cog, interaction.channel, interaction.user, close_reason)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Closing Scheduled",
                description=(
                    f"This ticket will close in **10 seconds**.\n\n"
                    f"**Reason:** {close_reason}\n\n"
                    "Press **Cancel Close** below to stop it."
                ),
                color=discord.Color.orange()
            ),
            view=view
        )

        msg = await interaction.original_response()
        asyncio.create_task(view.start(msg))


class TicketControls(discord.ui.View):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="👤", custom_id="ticket_claim")
    async def claim(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only", ephemeral=True)
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Ticket Claimed",
                description=f"{interaction.user.mention} claimed this ticket.",
                color=discord.Color.blurple()
            )
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close")
    async def close(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only", ephemeral=True)
            return

        await interaction.response.send_modal(CloseModal(self.cog))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="ticket_rename")
    async def rename(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only", ephemeral=True)
            return

        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Priority", style=discord.ButtonStyle.success, emoji="⚡", custom_id="ticket_priority")
    async def priority(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ Invalid channel.", ephemeral=True)
            return

        channel = interaction.channel

        if channel.name.startswith("priority-"):
            new_name = channel.name[len("priority-"):]
            await channel.edit(name=new_name)
            await interaction.response.send_message("✅ Priority removed.", ephemeral=True)
        else:
            await channel.edit(name=f"priority-{channel.name}")
            await interaction.response.send_message("✅ Priority added.", ephemeral=True)


class SupportModal(discord.ui.Modal, title="Support"):
    message = discord.ui.TextInput(
        label="What do you need support with?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    def __init__(self, cog: "SupportTickets"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction):
        await self.cog.create_ticket(
            interaction=interaction,
            prefix="support",
            category_name=SUPPORT_CATEGORY_NAME,
            answers=[("What do you need support with?", self.message.value)],
            intro="Welcome to support. Please explain your issue clearly so the team can help."
        )


class RewardModal(discord.ui.Modal, title="Claim Rewards"):
    item = discord.ui.TextInput(
        label="What are you claiming?",
        required=True,
        max_length=300
    )
    amount = discord.ui.TextInput(
        label="How much are you claiming?",
        required=True,
        max_length=100
    )
    ign = discord.ui.TextInput(
        label="Minecraft Username",
        required=True,
        max_length=100
    )

    def __init__(self, cog: "SupportTickets"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction):
        await self.cog.create_ticket(
            interaction=interaction,
            prefix="reward",
            category_name=REWARD_CATEGORY_NAME,
            answers=[
                ("What are you claiming?", self.item.value),
                ("How much are you claiming?", self.amount.value),
                ("Minecraft Username", self.ign.value)
            ],
            intro=(
                "Please read carefully before staff review this claim.\n\n"
                "False claims or breaking the rules, including griefing events, may result in you not being paid out.\n\n"
                "We always do our best to ensure fair competition, but please record your own gameplay just in case."
            )
        )


class TicketPanel(discord.ui.View):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Support", style=discord.ButtonStyle.primary, emoji="🆘", custom_id="support_ticket_button")
    async def support(self, interaction, button):
        await interaction.response.send_modal(SupportModal(self.cog))

    @discord.ui.button(label="Claim Rewards", style=discord.ButtonStyle.success, emoji="🎁", custom_id="reward_claim_button")
    async def reward(self, interaction, button):
        await interaction.response.send_modal(RewardModal(self.cog))


class SupportTickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketPanel(self))
        self.bot.add_view(TicketControls(self))

    async def create_ticket(self, interaction, prefix, category_name, answers, intro):
        guild = interaction.guild
        user = interaction.user

        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)

        clean_name = safe_channel_name(user.name)

        if prefix == "support":
            base_name = f"🆘┃{clean_name}"
        else:
            base_name = f"🎁┃{clean_name}"

        channel_name = base_name
        count = 1

        while discord.utils.get(guild.channels, name=channel_name):
            count += 1
            channel_name = f"{base_name}-{count}"

        channel = await guild.create_text_channel(name=channel_name, category=category)

        await channel.set_permissions(guild.default_role, view_channel=False)
        await channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)

        for role in get_support_roles(guild):
            await channel.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

        for role in guild.roles:
            if role.permissions.administrator or role.permissions.manage_channels:
                await channel.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

        embed = discord.Embed(
            title="New Ticket",
            description=intro,
            color=discord.Color.blurple()
        )
        embed.add_field(name="User", value=user.mention, inline=False)

        for k, v in answers:
            embed.add_field(name=k, value=v, inline=False)

        mentions = " ".join([user.mention] + [r.mention for r in get_support_roles(guild)])

        await channel.send(content=mentions, embed=embed, view=TicketControls(self))
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)

    async def final_close(self, channel, user, reason):
        transcript = await build_transcript(channel)

        opener = None
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and overwrite.view_channel is True and not is_staff(target):
                opener = target
                break

        if TICKET_LOG_CHANNEL_ID:
            log = channel.guild.get_channel(TICKET_LOG_CHANNEL_ID)
            if log and isinstance(log, discord.TextChannel):
                embed = discord.Embed(
                    title="📁 Ticket Closed",
                    description=(
                        f"**Channel:** {channel.name}\n"
                        f"**Closed by:** {user.mention}\n"
                        f"**Reason:** {reason}"
                    ),
                    color=discord.Color.red()
                )
                await log.send(embed=embed, file=transcript)

        if opener:
            try:
                dm_embed = discord.Embed(
                    title="Your ticket was closed",
                    description=(
                        f"**Server:** {channel.guild.name}\n"
                        f"**Ticket:** {channel.name}\n"
                        f"**Reason:** {reason}"
                    ),
                    color=discord.Color.blurple()
                )
                await opener.send(embed=dm_embed, file=transcript)
            except discord.Forbidden:
                pass

        await channel.delete()

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setup_ticket_panel_here(self, ctx):
        if ctx.channel.id != TICKET_PANEL_CHANNEL_ID:
            await ctx.send(f"⚠️ This command is intended for <#{TICKET_PANEL_CHANNEL_ID}>.")

        embed = discord.Embed(
            title="🎫 Support Center",
            description=(
                "Open a ticket using the buttons below.\n\n"
                "**Support**\n"
                "Get help from the team.\n\n"
                "**Claim Rewards**\n"
                "Open a ticket for prize or reward claims."
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Tickets are reviewed by staff.")

        await ctx.send(embed=embed, view=TicketPanel(self))

    @commands.command(name="ticketclose")
    @commands.has_permissions(manage_channels=True)
    async def ticketclose(self, ctx):
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("❌ Invalid channel.")
            return

        if not channel.category or channel.category.name not in {SUPPORT_CATEGORY_NAME, REWARD_CATEGORY_NAME}:
            await ctx.send("❌ This command can only be used inside a ticket channel.")
            return

        countdown_view = CloseCountdownView(
            cog=self,
            channel=channel,
            closed_by=ctx.author,
            reason="Closed by staff."
        )

        countdown_message = await ctx.send(
            embed=discord.Embed(
                title="⏳ Closing Scheduled",
                description=(
                    "This ticket will close in **10 seconds**.\n\n"
                    "**Reason:** Closed by staff.\n\n"
                    "Press **Cancel Close** below to stop it."
                ),
                color=discord.Color.orange()
            ),
            view=countdown_view
        )

        asyncio.create_task(countdown_view.start(countdown_message))


async def setup(bot):
    await bot.add_cog(SupportTickets(bot))