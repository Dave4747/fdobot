import asyncio
import io
import json
import os
import re
from datetime import datetime
from typing import Dict, Optional

import discord
from discord.ext import commands

DATA_FILE = "applications.json"

APPLICATION_CATEGORY_NAME = "Applications"
APPLY_PANEL_CHANNEL_ID = 1482938874937217025

DONUT_ROLE_NAME = "Donut Games"
SUPPORT_ROLE_KEYWORD = "support"
DONUT_BUTTON_EMOJI = "🍩"


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {
            "active_applications": {},
            "user_status": {}
        }

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "active_applications" not in data:
        data["active_applications"] = {}
    if "user_status" not in data:
        data["user_status"] = {}

    return data


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def normalize_data(data: dict) -> dict:
    active = data.get("active_applications", {})
    normalized_active = {}

    for user_id_str, value in active.items():
        if isinstance(value, int):
            normalized_active[user_id_str] = {
                "Donut Games": {
                    "channel_id": value,
                    "app_type": "Donut Games"
                }
            }
            continue

        if isinstance(value, dict) and "channel_id" in value:
            app_type = value.get("app_type", "Donut Games")
            if app_type == "Donut Games":
                normalized_active[user_id_str] = {
                    "Donut Games": {
                        "channel_id": value["channel_id"],
                        "app_type": "Donut Games"
                    }
                }
            continue

        if isinstance(value, dict):
            user_apps = {}
            for app_type, app_data in value.items():
                if (
                    app_type == "Donut Games"
                    and isinstance(app_data, dict)
                    and "channel_id" in app_data
                ):
                    user_apps["Donut Games"] = {
                        "channel_id": app_data["channel_id"],
                        "app_type": "Donut Games"
                    }

            if user_apps:
                normalized_active[user_id_str] = user_apps

    data["active_applications"] = normalized_active

    user_status = data.get("user_status", {})
    normalized_status = {}

    for user_id_str, value in user_status.items():
        if isinstance(value, str):
            normalized_status[user_id_str] = {"Donut Games": value}
        elif isinstance(value, dict):
            if "Donut Games" in value:
                normalized_status[user_id_str] = {"Donut Games": value["Donut Games"]}

    data["user_status"] = normalized_status
    return data


def clean_username_for_channel(name: str) -> str:
    cleaned = name.lower().strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9._-]", "", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:40] if cleaned else "user"


def build_application_channel_name(username: str) -> str:
    safe_user = clean_username_for_channel(username)
    name = f"🍩┃{safe_user}"
    return name[:90]


def get_support_roles(guild: discord.Guild):
    return [role for role in guild.roles if SUPPORT_ROLE_KEYWORD in role.name.lower()]


def is_support_staff(member: discord.Member) -> bool:
    if (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or member.guild_permissions.manage_channels
    ):
        return True

    return any(SUPPORT_ROLE_KEYWORD in role.name.lower() for role in member.roles)


def get_application_status(data: dict, user_id: int, app_type: str) -> Optional[str]:
    value = data.get("user_status", {}).get(str(user_id))

    if isinstance(value, str):
        return value if app_type == "Donut Games" else None

    if isinstance(value, dict):
        return value.get(app_type)

    return None


async def set_application_status(user_id: int, app_type: str, status: str) -> None:
    data = normalize_data(load_data())
    user_id_str = str(user_id)

    if user_id_str not in data["user_status"]:
        data["user_status"][user_id_str] = {}

    if isinstance(data["user_status"][user_id_str], str):
        old_status = data["user_status"][user_id_str]
        data["user_status"][user_id_str] = {"Donut Games": old_status}

    data["user_status"][user_id_str][app_type] = status
    save_data(data)


def remove_active_application(user_id: int, app_type: str) -> None:
    data = normalize_data(load_data())
    user_id_str = str(user_id)

    user_apps = data["active_applications"].get(user_id_str, {})
    if isinstance(user_apps, dict):
        user_apps.pop(app_type, None)
        if not user_apps:
            data["active_applications"].pop(user_id_str, None)

    save_data(data)


async def assign_donut_role(guild: discord.Guild, member: discord.Member):
    role = discord.utils.get(guild.roles, name=DONUT_ROLE_NAME)
    if role is None:
        return False, DONUT_ROLE_NAME

    try:
        await member.add_roles(role, reason="Application accepted for Donut Games")
        return True, DONUT_ROLE_NAME
    except discord.Forbidden:
        return False, DONUT_ROLE_NAME


async def build_transcript(channel: discord.TextChannel) -> discord.File:
    lines = [
        f"Transcript for #{channel.name}",
        f"Generated: {datetime.utcnow().isoformat()} UTC",
        "-" * 60
    ]

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


async def dm_transcript(
    member: discord.Member,
    channel: discord.TextChannel,
    title: str,
    description: str
) -> bool:
    transcript = await build_transcript(channel)

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple()
    )

    try:
        await member.send(embed=embed, file=transcript)
        return True
    except discord.Forbidden:
        return False


class ActionCountdownView(discord.ui.View):
    def __init__(
        self,
        applicant_id: int,
        app_channel: discord.TextChannel,
        app_type: str,
        action: str,
        acted_by: discord.Member,
        reason: Optional[str] = None
    ):
        super().__init__(timeout=15)
        self.applicant_id = applicant_id
        self.app_channel = app_channel
        self.app_type = app_type
        self.action = action
        self.acted_by = acted_by
        self.reason = reason
        self.cancelled = False
        self.message: Optional[discord.Message] = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def begin(self, message: discord.Message):
        self.message = message

        for seconds_left in range(10, 0, -1):
            if self.cancelled:
                return

            if self.action == "accept":
                title = "⏳ Acceptance Scheduled"
                description = (
                    f"This application will be **accepted and closed in {seconds_left} seconds**.\n\n"
                    "Press **Cancel** below to stop it."
                )
                color = discord.Color.green()
            elif self.action == "deny":
                title = "⏳ Denial Scheduled"
                description = (
                    f"This application will be **denied and closed in {seconds_left} seconds**.\n\n"
                    f"**Reason:** {self.reason or 'No reason provided.'}\n\n"
                    "Press **Cancel** below to stop it."
                )
                color = discord.Color.orange()
            else:
                title = "⏳ Close Scheduled"
                description = (
                    f"This application will be **closed in {seconds_left} seconds**.\n\n"
                    f"**Reason:** {self.reason or 'Closed by staff.'}\n\n"
                    "Press **Cancel** below to stop it."
                )
                color = discord.Color.orange()

            embed = discord.Embed(title=title, description=description, color=color)

            try:
                await message.edit(embed=embed, view=self)
            except discord.HTTPException:
                return

            await asyncio.sleep(1)

        if not self.cancelled:
            await self.finish_action()

    async def finish_action(self):
        guild = self.app_channel.guild
        applicant = guild.get_member(self.applicant_id)

        if self.action == "accept":
            await set_application_status(self.applicant_id, self.app_type, "accepted")

            role_text = ""
            dm_sent = False

            if applicant:
                role_success, role_name = await assign_donut_role(guild, applicant)
                if role_success:
                    role_text = f"You have been given the **{role_name}** role."
                else:
                    role_text = f"We could not assign the **{role_name}** role automatically."

                dm_sent = await dm_transcript(
                    applicant,
                    self.app_channel,
                    "✅ Application Accepted",
                    (
                        f"Congratulations — your **{self.app_type}** application has been accepted.\n\n"
                        f"{role_text}\n\n"
                        "Your application channel has now been closed."
                    )
                )

            await self.app_channel.send(
                embed=discord.Embed(
                    title="✅ Application Accepted",
                    description=(
                        f"Accepted by {self.acted_by.mention}.\n"
                        + ("The applicant has been notified." if dm_sent else "Could not DM the applicant.")
                    ),
                    color=discord.Color.green()
                )
            )

        elif self.action == "deny":
            await set_application_status(self.applicant_id, self.app_type, "denied")

            dm_sent = False
            if applicant:
                dm_sent = await dm_transcript(
                    applicant,
                    self.app_channel,
                    "❌ Application Denied",
                    (
                        f"Thank you for applying for **{self.app_type}**.\n\n"
                        f"**Reason:** {self.reason or 'No reason provided.'}\n\n"
                        "You can retry with a stronger application."
                    )
                )

            await self.app_channel.send(
                embed=discord.Embed(
                    title="❌ Application Denied",
                    description=(
                        f"Denied by {self.acted_by.mention}.\n"
                        + ("The applicant has been notified." if dm_sent else "Could not DM the applicant.")
                    ),
                    color=discord.Color.red()
                )
            )

        else:
            dm_sent = False
            if applicant:
                dm_sent = await dm_transcript(
                    applicant,
                    self.app_channel,
                    "🔒 Application Closed",
                    (
                        f"Your **{self.app_type}** application channel was closed.\n\n"
                        f"**Reason:** {self.reason or 'Closed by staff.'}"
                    )
                )

            await self.app_channel.send(
                embed=discord.Embed(
                    title="🔒 Application Closed",
                    description=(
                        f"Closed by {self.acted_by.mention}.\n"
                        + ("The applicant has been notified." if dm_sent else "Could not DM the applicant.")
                    ),
                    color=discord.Color.blurple()
                )
            )

        remove_active_application(self.applicant_id, self.app_type)

        try:
            await self.app_channel.delete()
        except discord.Forbidden:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_support_staff(interaction.user):
            await interaction.response.send_message("❌ Only support staff can cancel this action.", ephemeral=True)
            return

        self.cancelled = True
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Cancelled",
                description="This action has been cancelled. The application will stay open.",
                color=discord.Color.green()
            ),
            view=self
        )


class DenyReasonModal(discord.ui.Modal, title="Deny Application"):
    deny_reason = discord.ui.TextInput(
        label="Optional reason",
        style=discord.TextStyle.paragraph,
        placeholder="Leave blank to use the default retry message.",
        required=False,
        max_length=1000
    )

    def __init__(self, applicant_id: int, app_channel_id: int, app_type: str):
        super().__init__()
        self.applicant_id = applicant_id
        self.app_channel_id = app_channel_id
        self.app_type = app_type

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_support_staff(interaction.user):
            await interaction.response.send_message("❌ Only support staff can deny applications.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        channel = guild.get_channel(self.app_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ The application channel no longer exists.", ephemeral=True)
            return

        reason = self.deny_reason.value.strip() or "Your application was not accepted this time, but you are welcome to retry with a stronger application."

        countdown_view = ActionCountdownView(
            applicant_id=self.applicant_id,
            app_channel=channel,
            app_type=self.app_type,
            action="deny",
            reason=reason,
            acted_by=interaction.user
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Denial Scheduled",
                description=(
                    f"This application will be **denied and closed in 10 seconds**.\n\n"
                    f"**Reason:** {reason}\n\n"
                    "Press **Cancel** below to stop it."
                ),
                color=discord.Color.orange()
            ),
            view=countdown_view
        )

        countdown_message = await interaction.original_response()
        asyncio.create_task(countdown_view.begin(countdown_message))


class CloseReasonModal(discord.ui.Modal, title="Close Application"):
    close_reason = discord.ui.TextInput(
        label="Optional reason",
        style=discord.TextStyle.paragraph,
        placeholder="Leave blank to use a default close message.",
        required=False,
        max_length=1000
    )

    def __init__(self, applicant_id: int, app_channel_id: int, app_type: str):
        super().__init__()
        self.applicant_id = applicant_id
        self.app_channel_id = app_channel_id
        self.app_type = app_type

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_support_staff(interaction.user):
            await interaction.response.send_message("❌ Only support staff can close applications.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        channel = guild.get_channel(self.app_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ The application channel no longer exists.", ephemeral=True)
            return

        reason = self.close_reason.value.strip() or "Closed by staff."

        countdown_view = ActionCountdownView(
            applicant_id=self.applicant_id,
            app_channel=channel,
            app_type=self.app_type,
            action="close",
            reason=reason,
            acted_by=interaction.user
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Close Scheduled",
                description=(
                    f"This application will be **closed in 10 seconds**.\n\n"
                    f"**Reason:** {reason}\n\n"
                    "Press **Cancel** below to stop it."
                ),
                color=discord.Color.orange()
            ),
            view=countdown_view
        )

        countdown_message = await interaction.original_response()
        asyncio.create_task(countdown_view.begin(countdown_message))


class ApplicationDecisionView(discord.ui.View):
    def __init__(self, bot, applicant_id: int, app_channel_id: int, app_type: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.applicant_id = applicant_id
        self.app_channel_id = app_channel_id
        self.app_type = app_type

    async def _staff_check(self, interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_support_staff(interaction.user):
            return True

        await interaction.response.send_message("❌ Only support staff can use these buttons.", ephemeral=True)
        return False

    @discord.ui.button(label="Under Review", style=discord.ButtonStyle.secondary, emoji="🟡", custom_id="donut_app_under_review")
    async def under_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._staff_check(interaction):
            return

        await set_application_status(self.applicant_id, self.app_type, "under_review")

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🟡 Application Marked Under Review",
                description=f"{interaction.user.mention} marked this application as under review.",
                color=discord.Color.yellow()
            )
        )

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="donut_app_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._staff_check(interaction):
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        channel = guild.get_channel(self.app_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Application channel not found.", ephemeral=True)
            return

        countdown_view = ActionCountdownView(
            applicant_id=self.applicant_id,
            app_channel=channel,
            app_type=self.app_type,
            action="accept",
            acted_by=interaction.user
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Acceptance Scheduled",
                description=(
                    "This application will be **accepted and closed in 10 seconds**.\n\n"
                    "Press **Cancel** below to stop it."
                ),
                color=discord.Color.green()
            ),
            view=countdown_view
        )

        countdown_message = await interaction.original_response()
        asyncio.create_task(countdown_view.begin(countdown_message))

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="donut_app_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._staff_check(interaction):
            return

        await interaction.response.send_modal(
            DenyReasonModal(
                applicant_id=self.applicant_id,
                app_channel_id=self.app_channel_id,
                app_type=self.app_type
            )
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="donut_app_close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._staff_check(interaction):
            return

        await interaction.response.send_modal(
            CloseReasonModal(
                applicant_id=self.applicant_id,
                app_channel_id=self.app_channel_id,
                app_type=self.app_type
            )
        )


class ApplicationPartTwoModal(discord.ui.Modal, title="Donut Games Application • Part 2"):
    def __init__(self, cog: "Tickets", part_one_answers: Dict[str, str]):
        super().__init__()
        self.cog = cog
        self.part_one_answers = part_one_answers

        self.where_found_us = discord.ui.TextInput(
            label="Where did you find us?",
            required=True,
            max_length=200,
            placeholder="YouTube, TikTok, Discord, friend, etc."
        )
        self.have_competed_before = discord.ui.TextInput(
            label="Have you competed in the games before?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            placeholder="If so, tell us more."
        )
        self.follow_rules = discord.ui.TextInput(
            label="Will you follow the rules and not grief?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.anything_else = discord.ui.TextInput(
            label="Anything else you would like to add?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000
        )

        self.add_item(self.where_found_us)
        self.add_item(self.have_competed_before)
        self.add_item(self.follow_rules)
        self.add_item(self.anything_else)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            user = interaction.user
            app_type = "Donut Games"

            if guild is None or not isinstance(user, discord.Member):
                await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
                return

            data = normalize_data(load_data())
            user_id_str = str(user.id)

            existing_app = data["active_applications"].get(user_id_str, {}).get(app_type)
            if existing_app:
                existing_channel = guild.get_channel(existing_app["channel_id"])
                if existing_channel:
                    await interaction.response.send_message(
                        f"❌ You already have an open **{app_type}** application: {existing_channel.mention}",
                        ephemeral=True
                    )
                    return
                else:
                    data["active_applications"].get(user_id_str, {}).pop(app_type, None)
                    if user_id_str in data["active_applications"] and not data["active_applications"][user_id_str]:
                        data["active_applications"].pop(user_id_str, None)
                    save_data(data)

            category = discord.utils.get(guild.categories, name=APPLICATION_CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(APPLICATION_CATEGORY_NAME)

            base_name = build_application_channel_name(user.name)
            channel_name = base_name
            count = 1

            while discord.utils.get(guild.channels, name=channel_name):
                count += 1
                channel_name = f"{base_name[:80]}-{count}"

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True
                )
            }

            support_roles = get_support_roles(guild)
            for role in support_roles:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True
                )

            for role in guild.roles:
                if role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_channels:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        manage_messages=True
                    )

            channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

            data["active_applications"].setdefault(user_id_str, {})
            data["active_applications"][user_id_str][app_type] = {
                "channel_id": channel.id,
                "app_type": app_type
            }

            data["user_status"].setdefault(user_id_str, {})
            if isinstance(data["user_status"][user_id_str], str):
                old = data["user_status"][user_id_str]
                data["user_status"][user_id_str] = {"Donut Games": old}

            data["user_status"][user_id_str][app_type] = "open"
            save_data(data)

            support_mentions = " ".join(role.mention for role in support_roles)
            ping_line = f"{support_mentions} {user.mention}".strip() or user.mention

            submitted_embed = discord.Embed(
                title="🍩 Donut Games Application Submitted",
                description=(
                    f"Thank you for applying, {user.mention}.\n\n"
                    "Your application has been created and is now ready for staff review."
                ),
                color=discord.Color.orange()
            )
            submitted_embed.set_footer(text="Please wait patiently while staff review your application.")

            answers_embed = discord.Embed(
                title="Application Answers",
                description=f"**Applicant:** {user.mention}",
                color=discord.Color.gold()
            )
            answers_embed.add_field(name="Minecraft Username", value=self.part_one_answers["minecraft_username"], inline=False)
            answers_embed.add_field(name="Where did you find us?", value=self.where_found_us.value, inline=False)
            answers_embed.add_field(name="Timezone", value=self.part_one_answers["timezone"], inline=True)
            answers_embed.add_field(name="Java or Bedrock", value=self.part_one_answers["java_or_bedrock"], inline=True)
            answers_embed.add_field(name="In-Game Mic", value=self.part_one_answers["in_game_mic"], inline=True)
            answers_embed.add_field(name="Age", value=self.part_one_answers["age"], inline=True)
            answers_embed.add_field(
                name="Have you competed in the games before? If so tell us more",
                value=self.have_competed_before.value,
                inline=False
            )
            answers_embed.add_field(
                name="Are you going to follow the rules and not grief",
                value=self.follow_rules.value,
                inline=False
            )
            answers_embed.add_field(
                name="Anything else you would like to add?",
                value=self.anything_else.value.strip() if self.anything_else.value.strip() else "N/A",
                inline=False
            )

            staff_embed = discord.Embed(
                title="Staff Actions",
                description="Support staff can manage this application using the buttons below.",
                color=discord.Color.blurple()
            )

            await channel.send(ping_line)
            await channel.send(embed=submitted_embed)
            await channel.send(embed=answers_embed)
            await channel.send(
                embed=staff_embed,
                view=ApplicationDecisionView(self.cog.bot, user.id, channel.id, app_type)
            )

            self.cog.pending_part_one.pop(user.id, None)

            await interaction.response.send_message(
                f"✅ Your **Donut Games** application has been submitted: {channel.mention}",
                ephemeral=True
            )

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class ContinueApplicationView(discord.ui.View):
    def __init__(self, cog: "Tickets", user_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id

    @discord.ui.button(label="Continue Application", style=discord.ButtonStyle.primary, emoji="➡️")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This button is not for you.", ephemeral=True)
            return

        part_one_answers = self.cog.pending_part_one.get(self.user_id)
        if not part_one_answers:
            await interaction.response.send_message("❌ Your first form expired. Please start again.", ephemeral=True)
            return

        await interaction.response.send_modal(ApplicationPartTwoModal(self.cog, part_one_answers))


class ApplicationPartOneModal(discord.ui.Modal, title="Donut Games Application • Part 1"):
    def __init__(self, cog: "Tickets"):
        super().__init__()
        self.cog = cog

        self.minecraft_username = discord.ui.TextInput(label="Minecraft Username", required=True, max_length=100)
        self.timezone = discord.ui.TextInput(
            label="Timezone",
            required=True,
            max_length=100,
            placeholder="Example: GMT, EST, UTC+1"
        )
        self.java_or_bedrock = discord.ui.TextInput(
            label="Java or Bedrock",
            required=True,
            max_length=50,
            placeholder="Java, Bedrock, or both"
        )
        self.in_game_mic = discord.ui.TextInput(
            label="In-Game Mic",
            required=True,
            max_length=100,
            placeholder="Yes / No / Sometimes"
        )
        self.age = discord.ui.TextInput(label="Age", required=True, max_length=20)

        self.add_item(self.minecraft_username)
        self.add_item(self.timezone)
        self.add_item(self.java_or_bedrock)
        self.add_item(self.in_game_mic)
        self.add_item(self.age)

    async def on_submit(self, interaction: discord.Interaction):
        self.cog.pending_part_one[interaction.user.id] = {
            "minecraft_username": self.minecraft_username.value.strip(),
            "timezone": self.timezone.value.strip(),
            "java_or_bedrock": self.java_or_bedrock.value.strip(),
            "in_game_mic": self.in_game_mic.value.strip(),
            "age": self.age.value.strip(),
        }

        await interaction.response.send_message(
            "✅ Part 1 saved. Click below to continue to Part 2.",
            ephemeral=True,
            view=ContinueApplicationView(self.cog, interaction.user.id)
        )


class ApplyPanelView(discord.ui.View):
    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Apply for Donut Games",
        style=discord.ButtonStyle.primary,
        emoji=DONUT_BUTTON_EMOJI,
        custom_id="donut_games_apply_button"
    )
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("❌ This can only be used inside a server.", ephemeral=True)
                return

            data = normalize_data(load_data())
            user_id_str = str(interaction.user.id)
            app_type = "Donut Games"

            existing_app = data["active_applications"].get(user_id_str, {}).get(app_type)
            if existing_app:
                existing_channel = guild.get_channel(existing_app["channel_id"])
                if existing_channel:
                    await interaction.response.send_message(
                        f"❌ You already have an open **Donut Games** application: {existing_channel.mention}",
                        ephemeral=True
                    )
                    return

            await interaction.response.send_modal(ApplicationPartOneModal(self.cog))

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending_part_one: Dict[int, Dict[str, str]] = {}

    async def cog_load(self):
        self.bot.add_view(ApplyPanelView(self))
        await self._restore_application_views()

    async def _restore_application_views(self):
        data = normalize_data(load_data())
        save_data(data)

        for user_id_str, apps in data.get("active_applications", {}).items():
            if not isinstance(apps, dict):
                continue

            donut_app = apps.get("Donut Games")
            if not isinstance(donut_app, dict):
                continue

            channel_id = donut_app.get("channel_id")
            if not channel_id:
                continue

            status = get_application_status(data, int(user_id_str), "Donut Games")
            if status not in {"open", "under_review"}:
                continue

            self.bot.add_view(
                ApplicationDecisionView(
                    self.bot,
                    applicant_id=int(user_id_str),
                    app_channel_id=channel_id,
                    app_type="Donut Games"
                )
            )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setup_apply_panel_here(self, ctx):
        if ctx.channel.id != APPLY_PANEL_CHANNEL_ID:
            await ctx.send(f"⚠️ This command is intended for <#{APPLY_PANEL_CHANNEL_ID}>.")

        embed = discord.Embed(
            title="🍩 Donut Games Applications",
            description=(
                "**Want to apply for Donut Games?**\n\n"
                "Press the button below to begin your application.\n\n"
                "Once completed, a **private application channel** will be created for staff review."
            ),
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Before You Apply",
            value=(
                "• Answer honestly\n"
                "• Do not use AI"
            ),
            inline=False
        )
        embed.set_footer(text="Applications are reviewed by support staff.")

        await ctx.send(embed=embed, view=ApplyPanelView(self))

    @commands.command(name="appclose")
    @commands.has_permissions(manage_channels=True)
    async def appclose(self, ctx):
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("❌ Invalid channel.")
            return

        if not channel.category or channel.category.name != APPLICATION_CATEGORY_NAME:
            await ctx.send("❌ This command can only be used inside an application channel.")
            return

        data = normalize_data(load_data())
        applicant_id = None
        app_type = None

        for user_id_str, apps in data["active_applications"].items():
            if not isinstance(apps, dict):
                continue

            donut_app = apps.get("Donut Games")
            if isinstance(donut_app, dict) and donut_app.get("channel_id") == channel.id:
                applicant_id = int(user_id_str)
                app_type = "Donut Games"
                break

        if applicant_id is None or app_type is None:
            await ctx.send("❌ Could not find linked application data for this channel.")
            return

        countdown_view = ActionCountdownView(
            applicant_id=applicant_id,
            app_channel=channel,
            app_type=app_type,
            action="close",
            acted_by=ctx.author,
            reason="Closed by staff command."
        )

        countdown_message = await ctx.send(
            embed=discord.Embed(
                title="⏳ Close Scheduled",
                description=(
                    "This application will be **closed in 10 seconds**.\n\n"
                    "Press **Cancel** below to stop it."
                ),
                color=discord.Color.orange()
            ),
            view=countdown_view
        )

        asyncio.create_task(countdown_view.begin(countdown_message))


async def setup(bot):
    await bot.add_cog(Tickets(bot))
