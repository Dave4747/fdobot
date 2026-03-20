import io
import os
import re
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

SUPPORT_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", "0"))

SUPPORT_CATEGORY_NAME = "Support Tickets"
REPORT_CATEGORY_NAME = "User Reports"
BUG_CATEGORY_NAME = "Bug Reports"
PAYMENT_CATEGORY_NAME = "Purchase Support"
BUSINESS_CATEGORY_NAME = "Business Inquiries"
SUGGESTION_CATEGORY_NAME = "Suggestions"
APPEAL_CATEGORY_NAME = "Ban Appeals"
PRIZE_CATEGORY_NAME = "Prize Claims"


def safe_channel_name(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:90] if text else "ticket"


def find_role_by_partial(guild: discord.Guild, partial_name: str):
    partial_name = partial_name.lower()
    for role in guild.roles:
        if partial_name in role.name.lower():
            return role
    return None


def is_staff_member(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    return any(("support" in role.name.lower() or "overseer" in role.name.lower()) for role in member.roles)


async def build_transcript(channel: discord.TextChannel) -> discord.File:
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

        if msg.attachments:
            for attachment in msg.attachments:
                lines.append(f"[Attachment] {attachment.filename} - {attachment.url}")

        lines.append("-" * 60)

    transcript_text = "\n".join(lines)
    transcript_bytes = io.BytesIO(transcript_text.encode("utf-8"))
    return discord.File(transcript_bytes, filename=f"{channel.name}-transcript.txt")


class CloseReasonModal(discord.ui.Modal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(title="Close Ticket")
        self.cog = cog

        self.reason = discord.ui.TextInput(
            label="Reason for closing",
            style=discord.TextStyle.paragraph,
            placeholder="Enter the reason for closing this ticket...",
            required=True,
            max_length=1000
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ This can only be used in a ticket channel.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can close tickets.", ephemeral=True)
            return

        channel = interaction.channel
        reason = self.reason.value.strip()

        await interaction.response.defer(ephemeral=True, thinking=True)

        transcript = await build_transcript(channel)
        log_channel = interaction.guild.get_channel(SUPPORT_LOG_CHANNEL_ID) if SUPPORT_LOG_CHANNEL_ID else None

        opener = None
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and overwrite.read_messages is True:
                opener = target
                break

        if log_channel and isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="📁 Ticket Closed",
                description=(
                    f"**Channel:** {channel.name}\n"
                    f"**Closed by:** {interaction.user.mention}\n"
                    f"**Reason:** {reason}"
                ),
                color=discord.Color.red()
            )
            await log_channel.send(embed=embed, file=transcript)

        if opener:
            try:
                dm_embed = discord.Embed(
                    title="Your ticket was closed",
                    description=(
                        f"**Server:** {interaction.guild.name}\n"
                        f"**Ticket:** {channel.name}\n"
                        f"**Reason:** {reason}"
                    ),
                    color=discord.Color.blurple()
                )
                await opener.send(embed=dm_embed, file=transcript)
            except discord.Forbidden:
                pass

        await channel.delete()


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

        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can rename tickets.", ephemeral=True)
            return

        name = safe_channel_name(self.new_name.value)
        await interaction.channel.edit(name=name)
        await interaction.response.send_message(f"✅ Ticket renamed to `{name}`.", ephemeral=True)


class TicketControlView(discord.ui.View):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="👤", custom_id="support_claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Ticket Claimed",
            description=f"{interaction.user.mention} has claimed this ticket.",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="support_close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can close tickets.", ephemeral=True)
            return

        await interaction.response.send_modal(CloseReasonModal(self.cog))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="support_rename")
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can rename tickets.", ephemeral=True)
            return

        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label="Priority", style=discord.ButtonStyle.success, emoji="⚡", custom_id="support_priority")
    async def priority_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff_member(interaction.user):
            await interaction.response.send_message("❌ Only staff can change priority.", ephemeral=True)
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


class BaseTicketModal(discord.ui.Modal):
    def __init__(self, cog: "SupportTickets", title: str):
        super().__init__(title=title)
        self.cog = cog

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        ticket_type: str,
        category_name: str,
        channel_prefix: str,
        role_name_partial: str,
        answers,
        intro_text: str
    ):
        guild = interaction.guild
        user = interaction.user

        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            category = await guild.create_category(category_name)

        role = find_role_by_partial(guild, role_name_partial)
        if role is None:
            await interaction.response.send_message(
                f"❌ Could not find a role containing `{role_name_partial}`.",
                ephemeral=True
            )
            return

        base_name = safe_channel_name(f"{channel_prefix}-{user.name}")
        channel_name = base_name
        count = 1

        while discord.utils.get(guild.channels, name=channel_name):
            count += 1
            channel_name = f"{base_name}-{count}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True),
        }

        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="New Ticket",
            description=intro_text,
            color=discord.Color.blurple()
        )
        embed.add_field(name="User", value=user.mention, inline=False)
        embed.add_field(name="Category", value=ticket_type, inline=False)

        for label, value in answers:
            embed.add_field(name=label, value=(value[:1024] if value else "N/A"), inline=False)

        embed.set_footer(text="Please wait for staff. Do not ping staff. False reports may result in punishment.")

        await channel.send(
            content=f"{user.mention} {role.mention}",
            embed=embed,
            view=TicketControlView(self.cog)
        )

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {channel.mention}",
            ephemeral=True
        )


class SupportModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Support Ticket")
        self.what_help = discord.ui.TextInput(
            label="What do you need support with?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.what_help)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Support",
            category_name=SUPPORT_CATEGORY_NAME,
            channel_prefix="support",
            role_name_partial="support",
            answers=[("What do you need support with?", self.what_help.value)],
            intro_text="Welcome. Thank you for reaching out to support."
        )


class ReportUserModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Report a User")

        self.discord_name = discord.ui.TextInput(label="User's Discord name / ID", required=True, max_length=200)
        self.ign = discord.ui.TextInput(label="User's in-game name", required=True, max_length=200)
        self.reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.evidence = discord.ui.TextInput(label="Evidence", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.when = discord.ui.TextInput(label="When did it happen?", required=True, max_length=300)

        self.add_item(self.discord_name)
        self.add_item(self.ign)
        self.add_item(self.reason)
        self.add_item(self.evidence)
        self.add_item(self.when)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Report a User",
            category_name=REPORT_CATEGORY_NAME,
            channel_prefix="report",
            role_name_partial="support",
            answers=[
                ("Discord name / ID", self.discord_name.value),
                ("In-game name", self.ign.value),
                ("Reason", self.reason.value),
                ("Evidence", self.evidence.value),
                ("When", self.when.value),
            ],
            intro_text="Your report has been received and will be reviewed carefully."
        )


class BugModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Bug / Issue")
        self.issue = discord.ui.TextInput(
            label="Describe the bug or issue",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.issue)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Bug / Issue",
            category_name=BUG_CATEGORY_NAME,
            channel_prefix="bug",
            role_name_partial="support",
            answers=[("Issue", self.issue.value)],
            intro_text="Thanks for reporting this issue. Our team will look into it."
        )


class PaymentModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Purchases / Payments")
        self.help_needed = discord.ui.TextInput(
            label="What do you need help with regarding your purchase?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.help_needed)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Purchases / Payments",
            category_name=PAYMENT_CATEGORY_NAME,
            channel_prefix="payment",
            role_name_partial="support",
            answers=[("Purchase help", self.help_needed.value)],
            intro_text="Thank you for contacting purchase support."
        )


class BusinessModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Partnerships / Business")
        self.info = discord.ui.TextInput(
            label="Provide as much information as possible",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.info)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Partnerships / Business",
            category_name=BUSINESS_CATEGORY_NAME,
            channel_prefix="business",
            role_name_partial="overseer",
            answers=[("Information", self.info.value)],
            intro_text="Welcome. Please provide as much relevant information as possible, as these enquiries are handled directly by ownership and replies may take longer."
        )


class SuggestionModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Suggestions")
        self.suggestion = discord.ui.TextInput(
            label="What is your suggestion?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.suggestion)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Suggestions",
            category_name=SUGGESTION_CATEGORY_NAME,
            channel_prefix="suggestion",
            role_name_partial="support",
            answers=[("Suggestion", self.suggestion.value)],
            intro_text="Thank you for taking the time to share a suggestion with us."
        )


class BanAppealModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Ban Appeal")

        self.discord_name = discord.ui.TextInput(label="Your Discord name / ID", required=True, max_length=200)
        self.ign = discord.ui.TextInput(label="Your in-game name", required=True, max_length=200)
        self.why_banned = discord.ui.TextInput(label="Why were you banned?", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.why_unban = discord.ui.TextInput(label="Why should you be unbanned?", style=discord.TextStyle.paragraph, required=True, max_length=1000)

        self.add_item(self.discord_name)
        self.add_item(self.ign)
        self.add_item(self.why_banned)
        self.add_item(self.why_unban)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Ban Appeal",
            category_name=APPEAL_CATEGORY_NAME,
            channel_prefix="appeal",
            role_name_partial="support",
            answers=[
                ("Discord name / ID", self.discord_name.value),
                ("In-game name", self.ign.value),
                ("Why were you banned?", self.why_banned.value),
                ("Why should you be unbanned?", self.why_unban.value),
            ],
            intro_text="Your appeal has been submitted. Joke appeals or appeals that are not taken seriously may result in removal from the server."
        )


class PrizeModal(BaseTicketModal):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(cog, "Claim Prize")
        self.prize = discord.ui.TextInput(
            label="What prize are you claiming?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.prize)

    async def on_submit(self, interaction: discord.Interaction):
        await self.create_ticket(
            interaction=interaction,
            ticket_type="Claim Prize",
            category_name=PRIZE_CATEGORY_NAME,
            channel_prefix="prize",
            role_name_partial="overseer",
            answers=[("Prize being claimed", self.prize.value)],
            intro_text="Congratulations. Please confirm the prize you are claiming and wait for an Overseer to assist you."
        )


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, cog: "SupportTickets"):
        self.cog = cog

        options = [
            discord.SelectOption(label="Support", value="support", emoji="🆘", description="General help and support"),
            discord.SelectOption(label="Report a User", value="report", emoji="🚨", description="Report a user"),
            discord.SelectOption(label="Bug / Issue", value="bug", emoji="⚠️", description="Report a bug or issue"),
            discord.SelectOption(label="Purchases / Payments", value="payment", emoji="💰", description="Purchase help"),
            discord.SelectOption(label="Partnerships / Business", value="business", emoji="🤝", description="Business enquiries"),
            discord.SelectOption(label="Suggestions", value="suggestion", emoji="💡", description="Send a suggestion"),
            discord.SelectOption(label="Ban Appeals", value="appeal", emoji="🔓", description="Appeal a ban"),
            discord.SelectOption(label="Claim Prize", value="prize", emoji="🎁", description="Claim a prize"),
        ]

        super().__init__(
            placeholder="Choose a ticket category...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="support_ticket_select"
        )

    async def callback(self, interaction: discord.Interaction):
        ticket_type = self.values[0]

        if ticket_type == "support":
            await interaction.response.send_modal(SupportModal(self.cog))
        elif ticket_type == "report":
            await interaction.response.send_modal(ReportUserModal(self.cog))
        elif ticket_type == "bug":
            await interaction.response.send_modal(BugModal(self.cog))
        elif ticket_type == "payment":
            await interaction.response.send_modal(PaymentModal(self.cog))
        elif ticket_type == "business":
            await interaction.response.send_modal(BusinessModal(self.cog))
        elif ticket_type == "suggestion":
            await interaction.response.send_modal(SuggestionModal(self.cog))
        elif ticket_type == "appeal":
            await interaction.response.send_modal(BanAppealModal(self.cog))
        elif ticket_type == "prize":
            await interaction.response.send_modal(PrizeModal(self.cog))


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "SupportTickets"):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(cog))


class SupportTickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketPanelView(self))
        self.bot.add_view(TicketControlView(self))

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setup_ticket_panel(self, ctx):
        embed = discord.Embed(
            title="🎫 Support Center",
            description=(
                "Welcome to the support system.\n\n"
                "Please choose the category that best matches your request.\n"
                "Choosing the correct option helps us respond faster.\n\n"
                "⚠️ Misuse of tickets, spam, or false reports may result in moderation action."
            ),
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed, view=TicketPanelView(self))


async def setup(bot):
    await bot.add_cog(SupportTickets(bot))