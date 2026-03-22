import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

DATA_FILE = "moderation_data.json"

# Change this if you want a dedicated mod log channel name
MOD_LOG_CHANNEL_NAME = "mod-logs"

# Very lenient anti-spam settings
SPAM_MESSAGE_COUNT = 7          # messages
SPAM_WINDOW_SECONDS = 8         # within this many seconds
DUPLICATE_MESSAGE_COUNT = 5     # repeated same/similar messages
DUPLICATE_WINDOW_SECONDS = 12
MASS_MENTION_THRESHOLD = 6

# Auto mute lengths
SPAM_TIMEOUT_MINUTES = 5
SEVERE_WORD_TIMEOUT_MINUTES = 30

# Extremely bad words / phrases only
BANNED_PATTERNS = [
    r"\bnigger\b",
    r"\bnigga\b",
    r"\bkys\b",
    r"\bkill\s*yourself\b",
]

# Messages older than this are ignored by the duplicate cache cleanup
CACHE_PRUNE_SECONDS = 1800


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"cases": [], "case_counter": 0}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"cases": [], "case_counter": 0}

    if "cases" not in data:
        data["cases"] = []
    if "case_counter" not in data:
        data["case_counter"] = 0

    return data


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def format_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True

    return (
        member.guild_permissions.manage_messages
        or member.guild_permissions.moderate_members
        or member.guild_permissions.kick_members
        or member.guild_permissions.ban_members
    )


def shorten(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = load_data()

        self.message_history: dict[int, deque] = defaultdict(deque)
        self.duplicate_history: dict[int, deque] = defaultdict(deque)
        self.last_auto_action: dict[tuple[int, str], float] = {}

        self.cache_cleanup.start()

    def cog_unload(self):
        self.cache_cleanup.cancel()

    async def cog_load(self):
        self.data = load_data()

    @tasks.loop(minutes=10)
    async def cache_cleanup(self):
        now = time.time()

        for user_id in list(self.message_history.keys()):
            while self.message_history[user_id] and now - self.message_history[user_id][0][0] > CACHE_PRUNE_SECONDS:
                self.message_history[user_id].popleft()
            if not self.message_history[user_id]:
                del self.message_history[user_id]

        for user_id in list(self.duplicate_history.keys()):
            while self.duplicate_history[user_id] and now - self.duplicate_history[user_id][0][0] > CACHE_PRUNE_SECONDS:
                self.duplicate_history[user_id].popleft()
            if not self.duplicate_history[user_id]:
                del self.duplicate_history[user_id]

    async def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL_NAME)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def log_case(
        self,
        guild: discord.Guild,
        action: str,
        user: discord.abc.User,
        moderator: Optional[discord.abc.User],
        reason: str,
        duration: Optional[str] = None,
        evidence: Optional[str] = None,
    ) -> int:
        self.data["case_counter"] += 1
        case_id = self.data["case_counter"]

        record = {
            "case_id": case_id,
            "guild_id": guild.id,
            "user_id": user.id,
            "user_tag": str(user),
            "moderator_id": moderator.id if moderator else None,
            "moderator_tag": str(moderator) if moderator else "AutoMod",
            "action": action,
            "reason": reason,
            "duration": duration,
            "evidence": evidence,
            "timestamp": utc_now().isoformat(),
        }
        self.data["cases"].append(record)
        save_data(self.data)

        log_channel = await self.get_log_channel(guild)
        if log_channel:
            embed = discord.Embed(
                title=f"Moderation Log • Case #{case_id}",
                color=discord.Color.orange(),
                timestamp=utc_now(),
            )
            embed.add_field(name="Action", value=action, inline=True)
            embed.add_field(name="User", value=f"{user.mention}\n`{user.id}`", inline=True)
            embed.add_field(
                name="Moderator",
                value=(f"{moderator.mention}\n`{moderator.id}`" if moderator and isinstance(moderator, discord.Member) else "AutoMod"),
                inline=True,
            )
            embed.add_field(name="Reason", value=shorten(reason, 1024), inline=False)

            if duration:
                embed.add_field(name="Duration", value=duration, inline=True)

            if evidence:
                embed.add_field(name="Evidence", value=shorten(evidence, 1024), inline=False)

            await log_channel.send(embed=embed)

        return case_id

    async def apply_timeout(
        self,
        guild: discord.Guild,
        member: discord.Member,
        moderator: Optional[discord.abc.User],
        minutes: int,
        reason: str,
        evidence: Optional[str] = None,
        delete_trigger_message: Optional[discord.Message] = None,
    ) -> bool:
        until = utc_now() + timedelta(minutes=minutes)

        try:
            if delete_trigger_message:
                try:
                    await delete_trigger_message.delete()
                except discord.HTTPException:
                    pass

            await member.timeout(until, reason=reason)
            await self.log_case(
                guild=guild,
                action="Timeout",
                user=member,
                moderator=moderator,
                reason=reason,
                duration=f"{minutes} minute(s)",
                evidence=evidence,
            )
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            return False

    def has_recent_auto_action(self, user_id: int, key: str, cooldown_seconds: int = 60) -> bool:
        now = time.time()
        lookup = (user_id, key)
        last = self.last_auto_action.get(lookup)
        if last and now - last < cooldown_seconds:
            return True
        self.last_auto_action[lookup] = now
        return False

    def contains_banned_phrase(self, content: str) -> Optional[str]:
        lowered = content.lower()
        for pattern in BANNED_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                return pattern
        return None

    def normalize_for_duplicate_check(self, content: str) -> str:
        content = content.lower().strip()
        content = re.sub(r"\s+", " ", content)
        return content[:200]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not isinstance(message.author, discord.Member):
            return
        if is_staff(message.author):
            return

        content = message.content or ""
        lowered = content.lower()

        banned_match = self.contains_banned_phrase(lowered)
        if banned_match:
            if not self.has_recent_auto_action(message.author.id, "banned_phrase", 120):
                success = await self.apply_timeout(
                    guild=message.guild,
                    member=message.author,
                    moderator=None,
                    minutes=SEVERE_WORD_TIMEOUT_MINUTES,
                    reason="Used prohibited language.",
                    evidence=shorten(content, 500),
                    delete_trigger_message=message,
                )
                if success:
                    try:
                        await message.channel.send(
                            f"{message.author.mention} has been timed out for prohibited language.",
                            delete_after=8,
                        )
                    except discord.HTTPException:
                        pass
            return

        now = time.time()

        user_history = self.message_history[message.author.id]
        user_history.append((now, message.id))
        while user_history and now - user_history[0][0] > SPAM_WINDOW_SECONDS:
            user_history.popleft()

        duplicate_history = self.duplicate_history[message.author.id]
        normalized = self.normalize_for_duplicate_check(content)
        duplicate_history.append((now, normalized))
        while duplicate_history and now - duplicate_history[0][0] > DUPLICATE_WINDOW_SECONDS:
            duplicate_history.popleft()

        repeated_count = sum(1 for _, text in duplicate_history if text and text == normalized)

        mention_count = len(message.mentions)

        spam_trigger = len(user_history) >= SPAM_MESSAGE_COUNT
        duplicate_trigger = normalized and repeated_count >= DUPLICATE_MESSAGE_COUNT
        mass_mention_trigger = mention_count >= MASS_MENTION_THRESHOLD

        if spam_trigger or duplicate_trigger or mass_mention_trigger:
            if self.has_recent_auto_action(message.author.id, "spam", 180):
                return

            reason_parts = []
            if spam_trigger:
                reason_parts.append("excessive message spam")
            if duplicate_trigger:
                reason_parts.append("repeated duplicate messages")
            if mass_mention_trigger:
                reason_parts.append("mass mentioning")

            reason = "Auto moderation: " + ", ".join(reason_parts) + "."

            success = await self.apply_timeout(
                guild=message.guild,
                member=message.author,
                moderator=None,
                minutes=SPAM_TIMEOUT_MINUTES,
                reason=reason,
                evidence=shorten(content, 500),
                delete_trigger_message=message,
            )
            if success:
                try:
                    await message.channel.send(
                        f"{message.author.mention} has been timed out for spam.",
                        delete_after=8,
                    )
                except discord.HTTPException:
                    pass

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int):
        if amount < 1 or amount > 200:
            await ctx.send("❌ Choose a number between 1 and 200.", delete_after=8)
            return

        deleted = await ctx.channel.purge(limit=amount + 1)
        await self.log_case(
            guild=ctx.guild,
            action="Purge",
            user=ctx.author,
            moderator=ctx.author,
            reason=f"Purged {len(deleted) - 1} message(s) in #{ctx.channel.name}.",
        )
        confirm = await ctx.send(f"✅ Deleted {len(deleted) - 1} message(s).")
        await asyncio.sleep(4)
        try:
            await confirm.delete()
        except discord.HTTPException:
            pass

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided."):
        if member == ctx.author:
            await ctx.send("❌ You cannot mute yourself.")
            return

        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot mute someone with an equal or higher role.")
            return

        success = await self.apply_timeout(
            guild=ctx.guild,
            member=member,
            moderator=ctx.author,
            minutes=minutes,
            reason=reason,
        )
        if not success:
            await ctx.send("❌ I could not mute that user. Check permissions and role order.")
            return

        await ctx.send(f"✅ Muted {member.mention} for {minutes} minute(s).")

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        try:
            await member.timeout(None, reason=reason)
            await self.log_case(
                guild=ctx.guild,
                action="Unmute",
                user=member,
                moderator=ctx.author,
                reason=reason,
            )
            await ctx.send(f"✅ Unmuted {member.mention}.")
        except discord.Forbidden:
            await ctx.send("❌ I could not unmute that user. Check permissions and role order.")
        except discord.HTTPException:
            await ctx.send("❌ Failed to unmute that user.")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if member == ctx.author:
            await ctx.send("❌ You cannot ban yourself.")
            return

        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot ban someone with an equal or higher role.")
            return

        try:
            await ctx.guild.ban(member, reason=reason, delete_message_days=0)
            await self.log_case(
                guild=ctx.guild,
                action="Ban",
                user=member,
                moderator=ctx.author,
                reason=reason,
            )
            await ctx.send(f"✅ Banned {member}.")
        except discord.Forbidden:
            await ctx.send("❌ I could not ban that user. Check permissions and role order.")
        except discord.HTTPException:
            await ctx.send("❌ Failed to ban that user.")

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason provided."):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
            await self.log_case(
                guild=ctx.guild,
                action="Unban",
                user=user,
                moderator=ctx.author,
                reason=reason,
            )
            await ctx.send(f"✅ Unbanned `{user}`.")
        except discord.NotFound:
            await ctx.send("❌ That user is not banned.")
        except discord.Forbidden:
            await ctx.send("❌ I could not unban that user.")
        except discord.HTTPException:
            await ctx.send("❌ Failed to unban that user.")

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if member == ctx.author:
            await ctx.send("❌ You cannot kick yourself.")
            return

        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot kick someone with an equal or higher role.")
            return

        try:
            await member.kick(reason=reason)
            await self.log_case(
                guild=ctx.guild,
                action="Kick",
                user=member,
                moderator=ctx.author,
                reason=reason,
            )
            await ctx.send(f"✅ Kicked {member}.")
        except discord.Forbidden:
            await ctx.send("❌ I could not kick that user. Check permissions and role order.")
        except discord.HTTPException:
            await ctx.send("❌ Failed to kick that user.")

    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        await self.log_case(
            guild=ctx.guild,
            action="Warn",
            user=member,
            moderator=ctx.author,
            reason=reason,
        )

        try:
            await member.send(f"You were warned in **{ctx.guild.name}**.\nReason: {reason}")
        except discord.Forbidden:
            pass

        await ctx.send(f"✅ Warned {member.mention}.")

    @commands.command(name="modlogs")
    @commands.has_permissions(manage_messages=True)
    async def modlogs(self, ctx: commands.Context, member: discord.Member):
        cases = [
            case for case in self.data.get("cases", [])
            if case.get("guild_id") == ctx.guild.id and case.get("user_id") == member.id
        ]

        if not cases:
            await ctx.send(f"✅ No moderation history found for {member.mention}.")
            return

        recent = cases[-10:]
        embed = discord.Embed(
            title=f"Moderation History • {member}",
            color=discord.Color.blurple(),
        )

        for case in recent:
            timestamp = case.get("timestamp", "Unknown time")
            embed.add_field(
                name=f"Case #{case['case_id']} • {case['action']}",
                value=shorten(
                    f"Reason: {case['reason']}\n"
                    f"Moderator: {case['moderator_tag']}\n"
                    f"When: {timestamp}",
                    1024,
                ),
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="setup_modlogs")
    @commands.has_permissions(administrator=True)
    async def setup_modlogs(self, ctx: commands.Context):
        existing = discord.utils.get(ctx.guild.text_channels, name=MOD_LOG_CHANNEL_NAME)
        if existing:
            await ctx.send(f"✅ Mod log channel already exists: {existing.mention}")
            return

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }

        for role in ctx.guild.roles:
            if (
                role.permissions.administrator
                or role.permissions.manage_messages
                or role.permissions.moderate_members
                or role.permissions.ban_members
                or role.permissions.kick_members
            ):
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        channel = await ctx.guild.create_text_channel(
            MOD_LOG_CHANNEL_NAME,
            overwrites=overwrites,
            reason="Moderation log setup",
        )
        await ctx.send(f"✅ Created moderation log channel: {channel.mention}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))