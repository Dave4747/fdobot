import io
import json
import os
import re
from datetime import datetime

import discord
from discord.ext import commands

DATA_FILE = "applications.json"
REVIEW_CHANNEL_NAME = "📄application-reviews"  # change if needed
APPLICATION_CATEGORY_NAME = "Applications"

DONUT_ROLE_NAME = "Contestant"
MINIGAMES_ROLE_NAME = "Mini-Games Contestant"


def load_data():
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


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def safe_channel_name(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:90]


def normalize_active_applications(data):
    """
    Upgrades old formats safely into:
    active_applications[user_id][app_type] = {
        "channel_id": int,
        "app_type": str,
        "review_message_id": int | None
    }
    """
    active = data.get("active_applications", {})
    normalized = {}

    for user_id_str, value in active.items():
        # Old format: "123": 4567890123
        if isinstance(value, int):
            normalized[user_id_str] = {
                "Donut Games": {
                    "channel_id": value,
                    "app_type": "Donut Games",
                    "review_message_id": None
                }
            }
            continue

        # Old format: "123": {"channel_id": ..., "app_type": ...}
        if isinstance(value, dict) and "channel_id" in value:
            app_type = value.get("app_type", "Donut Games")
            normalized[user_id_str] = {
                app_type: {
                    "channel_id": value["channel_id"],
                    "app_type": app_type,
                    "review_message_id": value.get("review_message_id")
                }
            }
            continue

        # New format already
        if isinstance(value, dict):
            normalized[user_id_str] = {}
            for app_type, app_data in value.items():
                if isinstance(app_data, dict) and "channel_id" in app_data:
                    normalized[user_id_str][app_type] = {
                        "channel_id": app_data["channel_id"],
                        "app_type": app_data.get("app_type", app_type),
                        "review_message_id": app_data.get("review_message_id")
                    }

    data["active_applications"] = normalized
    return data


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
                embed_title = embed.title or "No title"
                embed_desc = embed.description or "No description"
                lines.append(f"[Embed {idx}] {embed_title}")
                lines.append(embed_desc)

        if msg.attachments:
            for attachment in msg.attachments:
                lines.append(f"[Attachment] {attachment.filename} - {attachment.url}")

        lines.append("-" * 60)

    transcript_text = "\n".join(lines)
    transcript_bytes = io.BytesIO(transcript_text.encode("utf-8"))
    return discord.File(transcript_bytes, filename=f"{channel.name}-transcript.txt")


async def send_transcript_and_message(
    member: discord.Member,
    channel: discord.TextChannel,
    title: str,
    description: str
):
    transcript_file = await build_transcript(channel)

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple()
    )

    try:
        await member.send(embed=embed, file=transcript_file)
        return True
    except discord.Forbidden:
        return False


async def cleanup_application(channel: discord.TextChannel, user_id: int, app_type: str):
    data = normalize_active_applications(load_data())
    user_id_str = str(user_id)

    user_apps = data["active_applications"].get(user_id_str, {})
    if isinstance(user_apps, dict):
        user_apps.pop(app_type, None)
        if not user_apps:
            data["active_applications"].pop(user_id_str, None)

    save_data(data)

    try:
        await channel.delete()
    except discord.Forbidden:
        pass


async def set_application_status(user_id: int, app_type: str, status: str):
    data = normalize_active_applications(load_data())
    user_id_str = str(user_id)

    if user_id_str not in data["user_status"]:
        data["user_status"][user_id_str] = {}

    if isinstance(data["user_status"][user_id_str], str):
        # Upgrade old format
        old_status = data["user_status"][user_id_str]
        data["user_status"][user_id_str] = {"Donut Games": old_status}

    data["user_status"][user_id_str][app_type] = status
    save_data(data)


def get_application_status(data, user_id: int, app_type: str):
    value = data.get("user_status", {}).get(str(user_id))

    if isinstance(value, str):
        return value if app_type == "Donut Games" else None

    if isinstance(value, dict):
        return value.get(app_type)

    return None


async def assign_role_by_app_type(guild: discord.Guild, member: discord.Member, app_type: str):
    if app_type == "Donut Games":
        role_name = DONUT_ROLE_NAME
    else:
        role_name = MINIGAMES_ROLE_NAME

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        return False, role_name

    try:
        await member.add_roles(role, reason=f"Application accepted for {app_type}")
        return True, role_name
    except discord.Forbidden:
        return False, role_name


class DenyReasonModal(discord.ui.Modal, title="Deny Application"):
    deny_reason = discord.ui.TextInput(
        label="Reason for denial",
        style=discord.TextStyle.paragraph,
        placeholder="Write the reason the application is being denied...",
        required=False,
        max_length=1000
    )

    def __init__(self, applicant_id: int, app_channel_id: int, app_type: str):
        super().__init__()
        self.applicant_id = applicant_id
        self.app_channel_id = app_channel_id
        self.app_type = app_type

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ This action can only be used in a server.",
                ephemeral=True
            )
            return

        if not (
            interaction.user.guild_permissions.administrator
            or interaction.user.guild_permissions.manage_guild
        ):
            await interaction.response.send_message(
                "❌ You do not have permission to deny applications.",
                ephemeral=True
            )
            return

        member = guild.get_member(self.applicant_id)
        app_channel = guild.get_channel(self.app_channel_id)

        custom_reason = self.deny_reason.value.strip()
        if not custom_reason:
            custom_reason = (
                "Your application was not accepted at this time. "
                "This may be due to fit, effort, selection limits, or overall review decisions."
            )

        await set_application_status(self.applicant_id, self.app_type, "denied")

        dm_sent = False
        if member and app_channel:
            dm_sent = await send_transcript_and_message(
                member,
                app_channel,
                "❌ Application Denied",
                (
                    f"Thanks for taking the time to apply for **{self.app_type}**.\n\n"
                    f"**Reason:** {custom_reason}\n\n"
                    "Sorry, but your application was not successful this time."
                )
            )

        if app_channel:
            await cleanup_application(app_channel, self.applicant_id, self.app_type)

        await interaction.response.send_message(
            (
                f"✅ Denied <@{self.applicant_id}>'s application."
                + (" Transcript + message sent." if dm_sent else " Could not DM the player.")
            ),
            ephemeral=True
        )


class ReviewView(discord.ui.View):
    def __init__(self, bot, applicant_id: int, app_channel_id: int, app_type: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.applicant_id = applicant_id
        self.app_channel_id = app_channel_id
        self.app_type = app_type

    async def _staff_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.user.guild_permissions.administrator
            or interaction.user.guild_permissions.manage_guild
        ):
            return True

        await interaction.response.send_message(
            "❌ You do not have permission to use these buttons.",
            ephemeral=True
        )
        return False

    @discord.ui.button(
        label="Under Review",
        style=discord.ButtonStyle.secondary,
        emoji="🟡",
        custom_id="review_under_review"
    )
    async def under_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not await self._staff_check(interaction):
                return

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return

            await set_application_status(self.applicant_id, self.app_type, "under_review")

            member = guild.get_member(self.applicant_id)
            app_channel = guild.get_channel(self.app_channel_id)

            if app_channel:
                embed = discord.Embed(
                    title="🟡 Application Update",
                    description=(
                        f"Your **{self.app_type}** application is now **under review**.\n\n"
                        "Staff are currently looking through it and will update you once a decision has been made."
                    ),
                    color=discord.Color.yellow()
                )
                await app_channel.send(embed=embed)

            if member:
                try:
                    await member.send(
                        embed=discord.Embed(
                            title="🟡 Application Under Review",
                            description=f"Your **{self.app_type}** application is now under review.",
                            color=discord.Color.yellow()
                        )
                    )
                except discord.Forbidden:
                    pass

            await interaction.response.send_message(
                f"✅ Marked <@{self.applicant_id}>'s application as under review.",
                ephemeral=True
            )

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="review_accept"
    )
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not await self._staff_check(interaction):
                return

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return

            member = guild.get_member(self.applicant_id)
            app_channel = guild.get_channel(self.app_channel_id)

            if app_channel is None:
                await interaction.response.send_message(
                    "❌ The application channel no longer exists.",
                    ephemeral=True
                )
                return

            await set_application_status(self.applicant_id, self.app_type, "accepted")

            role_result = None
            if member:
                role_result = await assign_role_by_app_type(guild, member, self.app_type)

            dm_sent = False
            if member and app_channel:
                dm_sent = await send_transcript_and_message(
                    member,
                    app_channel,
                    "✅ Application Accepted",
                    (
                        f"Congratulations — your **{self.app_type}** application has been accepted.\n\n"
                        "Your application channel has now been closed and your next steps will be handled through the server."
                    )
                )

            if app_channel:
                await cleanup_application(app_channel, self.applicant_id, self.app_type)

            role_text = ""
            if role_result is not None:
                success, role_name = role_result
                if success:
                    role_text = f" Role **{role_name}** assigned."
                else:
                    role_text = f" Could not assign role **{role_name}**."

            await interaction.response.send_message(
                (
                    f"✅ Accepted <@{self.applicant_id}>'s application."
                    + role_text
                    + (" Transcript + message sent." if dm_sent else " Could not DM the player.")
                ),
                ephemeral=True
            )

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="review_deny"
    )
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not await self._staff_check(interaction):
                return

            await interaction.response.send_modal(
                DenyReasonModal(
                    applicant_id=self.applicant_id,
                    app_channel_id=self.app_channel_id,
                    app_type=self.app_type
                )
            )

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class ApplicationView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.select(
        custom_id="application_select",
        placeholder="Choose what you want to apply for...",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="Donut Games Application",
                description="Apply for the main Donut Games competition.",
                emoji="🍩"
            ),
            discord.SelectOption(
                label="Mini-Games Application",
                description="Apply for live stream mini-games and fun events.",
                emoji="🎮"
            ),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            guild = interaction.guild
            user = interaction.user
            choice = select.values[0]

            if guild is None:
                await interaction.response.send_message(
                    "❌ This can only be used inside a server.",
                    ephemeral=True
                )
                return

            data = normalize_active_applications(load_data())
            user_id_str = str(user.id)

            if user_id_str not in data["active_applications"]:
                data["active_applications"][user_id_str] = {}

            if choice == "Donut Games Application":
                app_type = "Donut Games"
            else:
                app_type = "Mini-Games"

            # Admins can bypass open-app check
            if not interaction.user.guild_permissions.administrator:
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
                        data["active_applications"][user_id_str].pop(app_type, None)
                        if not data["active_applications"][user_id_str]:
                            data["active_applications"].pop(user_id_str, None)
                        save_data(data)

                status = get_application_status(data, user.id, app_type)
                if status == "denied":
                    await interaction.response.send_message(
                        f"❌ You cannot open a new **{app_type}** application because a previous one was denied.",
                        ephemeral=True
                    )
                    return

            category = discord.utils.get(guild.categories, name=APPLICATION_CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(APPLICATION_CATEGORY_NAME)

            review_channel = discord.utils.get(guild.text_channels, name=REVIEW_CHANNEL_NAME)
            if review_channel is None:
                await interaction.response.send_message(
                    f"❌ Staff review channel `#{REVIEW_CHANNEL_NAME}` was not found.",
                    ephemeral=True
                )
                return

            base_name = safe_channel_name(f"app-{user.name}-{app_type}")
            channel_name = base_name
            count = 1

            while discord.utils.get(guild.channels, name=channel_name):
                count += 1
                channel_name = f"{base_name}-{count}"

            channel = await guild.create_text_channel(channel_name, category=category)

            await channel.set_permissions(user, read_messages=True, send_messages=True)
            await channel.set_permissions(guild.default_role, read_messages=False)

            for role in guild.roles:
                if role.permissions.administrator or role.permissions.manage_guild:
                    await channel.set_permissions(role, read_messages=True, send_messages=True)

            data["active_applications"].setdefault(user_id_str, {})
            data["active_applications"][user_id_str][app_type] = {
                "channel_id": channel.id,
                "app_type": app_type,
                "review_message_id": None
            }
            data["user_status"].setdefault(user_id_str, {})
            if isinstance(data["user_status"][user_id_str], str):
                old = data["user_status"][user_id_str]
                data["user_status"][user_id_str] = {"Donut Games": old}
            data["user_status"][user_id_str][app_type] = "open"
            save_data(data)

            if app_type == "Donut Games":
                intro_embed = discord.Embed(
                    title="🍩 Donut Games Application",
                    description=(
                        "Welcome to the **Donut Games** application.\n\n"
                        "This is for the **main competition** and is aimed at more serious, committed players and viewers.\n\n"
                        "**Important:** If you are selected, you are expected to show up and take part.\n"
                        "If you do not show up, we may replace you with a reserve contestant.\n\n"
                        "Please answer everything clearly and honestly."
                    ),
                    color=discord.Color.orange()
                )

                guidance_embed = discord.Embed(
                    title="📌 Guidance",
                    description=(
                        "• Answer honestly and in your own words\n"
                        "• Put effort into your answers\n"
                        "• Joke or low-effort applications are unlikely to be accepted\n"
                        "• **Using AI to write your application may result in your application being denied**"
                    ),
                    color=discord.Color.red()
                )

                questions = (
                    "**Please answer these questions:**\n\n"
                    "**1.** Discord username:\n"
                    "**2.** Age:\n"
                    "**3.** Minecraft in-game name:\n"
                    "**4.** Twitch username:\n"
                    "**5.** How much playtime do you have on DonutSMP?\n"
                    "**6.** Do you have voice chat enabled and working?\n"
                    "**7.** Why do you want to be part of Donut Games?\n"
                    "**8.** What makes you a strong contestant?\n"
                    "**9.** Have you taken part in events, competitions, or community activities before?\n"
                    "**10.** Why should we pick you over someone else?\n"
                    "**11.** Anything else you want staff to know?\n"
                )
            else:
                intro_embed = discord.Embed(
                    title="🎮 Mini-Games Application",
                    description=(
                        "Welcome to the **Mini-Games** application.\n\n"
                        "This is for **live stream events, lighter competitions, and fun community participation**.\n\n"
                        "Mini-Games are more casual than Donut Games, but we still want players who are genuine, fun, and reliable."
                    ),
                    color=discord.Color.blurple()
                )

                guidance_embed = discord.Embed(
                    title="📌 Guidance",
                    description=(
                        "• Answer honestly and in your own words\n"
                        "• Keep your answers clear and readable\n"
                        "• Low-effort or joke applications are unlikely to be accepted\n"
                        "• **Using AI to write your application may result in your application being denied**"
                    ),
                    color=discord.Color.red()
                )

                questions = (
                    "**Please answer these questions:**\n\n"
                    "**1.** Discord username:\n"
                    "**2.** Age:\n"
                    "**3.** Minecraft in-game name:\n"
                    "**4.** Twitch username:\n"
                    "**5.** How much playtime do you have on DonutSMP?\n"
                    "**6.** Do you have voice chat enabled and working?\n"
                    "**7.** What kind of mini-games or stream events would you like to join?\n"
                    "**8.** What makes you fun to have in live events?\n"
                    "**9.** Have you joined community or stream events before?\n"
                    "**10.** Anything else you want us to know?\n"
                )

            await channel.send(f"{user.mention}")
            await channel.send(embed=intro_embed)
            await channel.send(embed=guidance_embed)
            await channel.send(questions)

            review_embed = discord.Embed(
                title=f"📋 New {app_type} Application",
                description=(
                    f"**Applicant:** {user.mention}\n"
                    f"**Channel:** {channel.mention}\n"
                    f"**Type:** {app_type}\n\n"
                    "Use the buttons below to manage this application."
                ),
                color=discord.Color.gold()
            )

            review_message = await review_channel.send(
                embed=review_embed,
                view=ReviewView(self.bot, user.id, channel.id, app_type)
            )

            data = normalize_active_applications(load_data())
            data["active_applications"].setdefault(user_id_str, {})
            data["active_applications"][user_id_str][app_type] = {
                "channel_id": channel.id,
                "app_type": app_type,
                "review_message_id": review_message.id
            }
            save_data(data)

            await interaction.response.send_message(
                f"✅ Your application channel has been created: {channel.mention}",
                ephemeral=True
            )

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ApplicationView(self.bot))
        await self._restore_review_views()

    async def _restore_review_views(self):
        data = normalize_active_applications(load_data())
        save_data(data)

        for user_id_str, apps in data.get("active_applications", {}).items():
            if not isinstance(apps, dict):
                continue

            for app_type, app_data in apps.items():
                if not isinstance(app_data, dict):
                    continue

                channel_id = app_data.get("channel_id")
                if not channel_id:
                    continue

                status = get_application_status(data, int(user_id_str), app_type)
                if status not in {"open", "under_review"}:
                    continue

                self.bot.add_view(
                    ReviewView(
                        self.bot,
                        applicant_id=int(user_id_str),
                        app_channel_id=channel_id,
                        app_type=app_type
                    )
                )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setup_apply_panel(self, ctx):
        embed = discord.Embed(
            title="🍩 Apply for Donut SMP Events",
            description=(
                "Want to get involved in the action?\n\n"
                "Choose an option below to open your **private application channel**.\n\n"
                "**🍩 Donut Games**\n"
                "Our main competition for more committed viewers and players.\n\n"
                "**🎮 Mini-Games**\n"
                "Fun live stream events, lighter competitions, and casual community participation.\n\n"
                "Pick the option that fits you best and we’ll get you started."
            ),
            color=discord.Color.gold()
        )

        embed.set_footer(
            text="Applications are reviewed by staff. Make sure your answers are honest and complete."
        )

        await ctx.send(embed=embed, view=ApplicationView(self.bot))

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def close(self, ctx):
        channel = ctx.channel
        if isinstance(channel, discord.TextChannel) and channel.category and channel.category.name == APPLICATION_CATEGORY_NAME:
            data = normalize_active_applications(load_data())

            user_id_to_remove = None
            app_type_to_remove = None

            for user_id, apps in data["active_applications"].items():
                if not isinstance(apps, dict):
                    continue

                for app_type, app_data in apps.items():
                    if isinstance(app_data, dict) and app_data.get("channel_id") == channel.id:
                        user_id_to_remove = user_id
                        app_type_to_remove = app_type
                        break

                if user_id_to_remove:
                    break

            if user_id_to_remove and app_type_to_remove:
                data["active_applications"][user_id_to_remove].pop(app_type_to_remove, None)
                if not data["active_applications"][user_id_to_remove]:
                    data["active_applications"].pop(user_id_to_remove, None)
                save_data(data)

            await ctx.send("⛔ Closing this application channel...")
            await channel.delete()
        else:
            await ctx.send("❌ This command can only be used inside an application channel.")


async def setup(bot):
    await bot.add_cog(Tickets(bot))