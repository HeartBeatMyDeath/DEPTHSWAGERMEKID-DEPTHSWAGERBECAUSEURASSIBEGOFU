import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput, Select
from dotenv import load_dotenv
import webserver
import os
from datetime import timedelta, datetime, timezone
import json
import re
import asyncio
import aiohttp
import io

# ----------------------------
# Load token and setup intents[
# ----------------------------
load_dotenv()
token = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# ----------------------------
# Guild, channel and roles
# ----------------------------
GUILD_ID = 1362770221034897639
BLACKLIST_CHANNEL_ID = 1385956899752509532
BLACKLIST_MESSAGE_ID = 1438983885517099112
MOD_LOG_CHANNEL_ID = 1438981968380301403  # channel used for persistent mod logs




PERMISSION_TIERS = {
    1362889706563440900: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete", "blacklist_interface", "panel"], #owner
    1362896066504036402: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete"], #co owner
    1399809075252039824: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete"], # senior
    1391861560967954483: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete"], # mod
    1431713725362212917: ["blacklist_interface"], #blacklister
    1399808293999738961: ["kick", "timeout", "log", "warn", "warnlog", "warndelete"], #junior
    1440250118946164816: ["timeout", "warn", "warnlog", "warndelete", "log"], #trial
}

# ----------------------------
# Bot setup
# ----------------------------
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"✅ Slash commands synced to guild {GUILD_ID}")

bot = MyBot()

# ----------------------------
# Permission helpers
# ----------------------------
async def check_permissions(interaction: discord.Interaction, command_name: str) -> bool:
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        member = await interaction.guild.fetch_member(interaction.user.id)
    user_roles = [r.id for r in member.roles]
    for role_id in user_roles:
        allowed_commands = PERMISSION_TIERS.get(role_id, [])
        if command_name in allowed_commands:
            return True
    return False

async def run_command_with_permission(interaction: discord.Interaction, command_name: str, func, *args, **kwargs):
    if not await check_permissions(interaction, command_name):
        await interaction.response.send_message("❌ You are not allowed to use this command.", ephemeral=True)
        return
    await func(interaction, *args, **kwargs)

# ----------------------------
# DM helper
# ----------------------------
async def safe_dm(user: discord.Member, content: str):
    try:
        await user.send(content)
    except discord.Forbidden:
        print(f"⚠️ Could not DM user {user} (DMs closed).")
    except Exception as e:
        print(f"⚠️ Error DMing user {user}: {e}")

# ----------------------------
# Blacklist (in-memory; unchanged)
# ----------------------------
blacklist_items = []

def get_blacklist_items():
    return blacklist_items.copy()

def add_blacklist_item(item_text: str):
    if item_text in blacklist_items:
        return False
    blacklist_items.append(item_text)
    blacklist_items.sort(key=lambda s: s.lower())  # alphabetical
    return True

def remove_blacklist_item_by_index(index: int):
    if index < 0 or index >= len(blacklist_items):
        return None
    return blacklist_items.pop(index)

# ----------------------------
# Blacklist message helpers (unchanged)
# ----------------------------
async def update_blacklist_message(channel: discord.TextChannel):
    items = get_blacklist_items()
    desc = "*(Currently empty)*" if not items else "\n".join([f"{i+1} - {w}" for i, w in enumerate(items)])
    message = None
    if BLACKLIST_MESSAGE_ID != 0:
        try:
            message = await channel.fetch_message(BLACKLIST_MESSAGE_ID)
        except Exception:
            message = None

    if message is None and BLACKLIST_MESSAGE_ID == 0:
        message = await channel.send(embed=discord.Embed(title="📝 Blacklist", description=desc, color=discord.Color.dark_theme()))
    elif message:
        try:
            await message.edit(embed=discord.Embed(title="📝 Blacklist", description=desc, color=discord.Color.dark_theme()))
        except Exception as e:
            print("⚠️ Failed to update blacklist message:", e)

# ----------------------------
# Blacklist Modals & View (unchanged)
# ----------------------------
class AddItemModal(Modal, title="Add item to Blacklist"):
    item = TextInput(label="Item", placeholder="Enter item to add", required=True, max_length=200)
    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel
    async def on_submit(self, interaction: discord.Interaction):
        item_text = self.item.value.strip()
        if not item_text:
            await interaction.response.send_message("❌ Item cannot be empty.", ephemeral=True)
            return
        success = add_blacklist_item(item_text)
        if success:
            await update_blacklist_message(self.channel)
            await interaction.response.send_message(f"✅ Added **{item_text}** to blacklist.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ That item already exists.", ephemeral=True)

class RemoveItemModal(Modal, title="Remove item from Blacklist"):
    number = TextInput(label="Item number", placeholder="Enter the number of the item", required=True, max_length=10)
    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel
    async def on_submit(self, interaction: discord.Interaction):
        try:
            num = int(self.number.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Invalid number.", ephemeral=True)
            return
        removed = remove_blacklist_item_by_index(num - 1)
        if removed is None:
            await interaction.response.send_message("❌ That number doesn't exist.", ephemeral=True)
        else:
            await update_blacklist_message(self.channel)
            await interaction.response.send_message(f"🗑️ Removed **{removed}** from blacklist.", ephemeral=True)

class BlacklistView(View):
    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.channel = channel
    @discord.ui.button(label="➕ Add", style=discord.ButtonStyle.green)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddItemModal(self.channel))
    @discord.ui.button(label="➖ Remove", style=discord.ButtonStyle.red)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveItemModal(self.channel))

@bot.tree.command(name="blacklist_interface", description="Open the blacklist interface (Add / Remove items)", guild=discord.Object(id=GUILD_ID))
async def blacklist_interface(interaction: discord.Interaction):
    async def inner(interaction: discord.Interaction):
        channel = interaction.guild.get_channel(BLACKLIST_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Blacklist channel not found.", ephemeral=True)
            return
        items = get_blacklist_items()
        desc = "*(Currently empty)*" if not items else "\n".join([f"{i+1} - {word}" for i, word in enumerate(items)])
        embed = discord.Embed(title="📝 Blacklist Manager", description=desc, color=discord.Color.dark_theme())
        view = BlacklistView(channel)
        await update_blacklist_message(channel)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await run_command_with_permission(interaction, "blacklist_interface", inner)

# ----------------------------
# Message-based moderation logging
# ----------------------------
MODLOG_PREFIX = "__modlog__:"          # marker prefix
SPoILER_WRAP = ("||", "||")            # invisible spoiler wrappers

def _wrap_spoiler(s: str) -> str:
    return f"{SPoILER_WRAP[0]}{s}{SPoILER_WRAP[1]}"

def _make_modlog_content(data: dict) -> str:
    # returns a content string where JSON metadata is wrapped in spoilers and prefixed.
    return _wrap_spoiler(MODLOG_PREFIX + json.dumps(data, separators=(",", ":"), ensure_ascii=False))

def _extract_modlog_from_content(content: str):
    """
    Given a message content, return metadata dict if present, otherwise None.
    Accepts content where JSON is inside spoilers, possibly other text in message.
    """
    if not content:
        return None
    # Find the MODLOG_PREFIX anywhere in the content (inside spoilers or not)
    m = re.search(re.escape(MODLOG_PREFIX) + r'(\{.*\})', content)
    if not m:
        # maybe content is wrapped in spoilers: remove outer ||...|| then try again
        stripped = content
        if stripped.startswith("||") and stripped.endswith("||"):
            stripped = stripped[2:-2]
            m = re.search(re.escape(MODLOG_PREFIX) + r'(\{.*\})', stripped)
            if not m:
                return None
        else:
            return None
    try:
        json_part = m.group(1)
        return json.loads(json_part)
    except Exception:
        return None

async def log_action_msg(user: discord.Member, moderator: discord.Member, action: str, reason: str, duration: int = None):
    """
    Posts a modlog message in MOD_LOG_CHANNEL_ID.
    Metadata is stored in an invisible spoiler at the start of the message:
      ||__modlog__:{...}||
    The visible part is a clean embed. After sending, we edit the message to include msg_id inside metadata.
    """
    channel = user.guild.get_channel(MOD_LOG_CHANNEL_ID)
    if not channel:
        print("⚠️ Mod log channel not found.")
        return None

    now = datetime.now(timezone.utc)
    metadata = {
        "user": user.id,
        "moderator": moderator.id,
        "action": action,
        "reason": reason,
        "timestamp": int(now.timestamp()),
        "duration": duration
    }

    # Build visible embed:
    title = f"{action.title()} | {user.display_name}"
    embed = discord.Embed(title=title, description=reason, color=discord.Color.red() if action in ("ban","kick","timeout") else discord.Color.orange(), timestamp=now)
    embed.add_field(name="Moderator", value=f"{moderator} (ID: {moderator.id})", inline=True)
    embed.add_field(name="User ID", value=str(user.id), inline=True)
    if duration:
        embed.add_field(name="Duration (minutes)", value=str(duration), inline=False)

    # Send message with invisible metadata in content
    content = _make_modlog_content(metadata)
    try:
        msg = await channel.send(content=content, embed=embed)
    except Exception as e:
        print("⚠️ Failed to send modlog message:", e)
        return None

    # Update metadata with msg_id and edit content (still hidden)
    try:
        metadata["msg_id"] = msg.id
        new_content = _make_modlog_content(metadata)
        # edit embed to include message ID in footer for convenience (visible)
        embed.set_footer(text=f"Message ID: {msg.id}")
        await msg.edit(content=new_content, embed=embed)
    except Exception as e:
        print("⚠️ Failed to update modlog message:", e)
    return msg.id

# ----------------------------
# Fetch logs from mod channel (no DB)
# ----------------------------
async def fetch_mod_logs(user: discord.Member, only_warns=False, lookback_limit=1000):
    channel = user.guild.get_channel(MOD_LOG_CHANNEL_ID)
    if not channel:
        return []
    logs = []
    # iterate history (newest first) — accumulate up to lookback_limit messages checked
    checked = 0
    async for msg in channel.history(limit=None):
        checked += 1
        if checked > lookback_limit:
            break
        meta = _extract_modlog_from_content(msg.content)
        if not meta:
            continue
        # filter by user
        if meta.get("user") != user.id:
            continue
        # filter warns / non-warns
        if only_warns and meta.get("action") != "warn":
            continue
        if (not only_warns) and meta.get("action") == "warn":
            continue
        # ensure msg_id present
        meta["msg_id"] = meta.get("msg_id", msg.id)
        logs.append(meta)
    # sort newest -> oldest by timestamp (some messages could be out of order)
    logs.sort(key=lambda d: d.get("timestamp", 0), reverse=True)
    return logs

# ----------------------------
# LogView (pagination) - 5 per page, newest -> oldest
# ----------------------------
class LogView(View):
    def __init__(self, entries, member, interaction):
        super().__init__(timeout=180)
        self.entries = entries
        self.member = member
        self.interaction = interaction
        self.index = 0
        self.per_page = 5
        self.max_index = max(0, (len(entries)-1)//self.per_page)

        # create buttons and assign callbacks
        self.first_button = Button(label="⏮️ First", style=discord.ButtonStyle.gray)
        self.prev_button = Button(label="◀️ Prev", style=discord.ButtonStyle.gray)
        self.next_button = Button(label="▶️ Next", style=discord.ButtonStyle.gray)
        self.last_button = Button(label="⏭️ Last", style=discord.ButtonStyle.gray)

        for btn in (self.first_button, self.prev_button, self.next_button, self.last_button):
            self.add_item(btn)

        self.first_button.callback = self.first_page
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page
        self.last_button.callback = self.last_page

    def get_page_embed(self):
        start = self.index * self.per_page
        end = start + self.per_page
        page_entries = self.entries[start:end]
        if not page_entries:
            desc = "No entries on this page."
        else:
            parts = []
            for e in page_entries:
                ts = f"<t:{e['timestamp']}:f>" if e.get("timestamp") else "Unknown time"
                duration = f" ({e['duration']} min)" if e.get("duration") else ""
                parts.append(
                    f"**{e['action'].title()}**{duration} — Moderator: <@{e['moderator']}> — Time: {ts}\n"
                    f"Reason: {e.get('reason','No reason')}\nMessage ID: `{e.get('msg_id')}`"
                )
            desc = "\n\n".join(parts)
        embed = discord.Embed(title=f"Logs for {self.member.display_name}", description=desc, color=discord.Color.dark_theme())
        embed.set_footer(text=f"Page {self.index+1}/{self.max_index+1} — {len(self.entries)} entries total")
        return embed

    async def send_initial(self):
        await self.interaction.response.send_message(embed=self.get_page_embed(), view=self, ephemeral=True)

    async def update_message(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

    async def first_page(self, interaction: discord.Interaction):
        self.index = 0
        await self.update_message(interaction)

    async def prev_page(self, interaction: discord.Interaction):
        self.index = max(0, self.index - 1)
        await self.update_message(interaction)

    async def next_page(self, interaction: discord.Interaction):
        self.index = min(self.max_index, self.index + 1)
        await self.update_message(interaction)

    async def last_page(self, interaction: discord.Interaction):
        self.index = self.max_index
        await self.update_message(interaction)

# ----------------------------
# Moderation commands (log via mod channel messages)
# ----------------------------
@bot.tree.command(name="kick", description="Kick a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str="No reason provided"):
    async def func(interaction, member, reason):
        try:
            await member.kick(reason=reason)
            msg_id = await log_action_msg(member, interaction.user, "kick", reason)
            await safe_dm(member, f"🚨 You were kicked from **{interaction.guild.name}** by {interaction.user}. Reason: {reason}\nLog ID: `{msg_id}`")
            await interaction.response.send_message(f"👢 {member.mention} was kicked. Log ID: `{msg_id}`")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Cannot kick this member.", ephemeral=True)
    await run_command_with_permission(interaction, "kick", func, member, reason)

@bot.tree.command(name="ban", description="Ban a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str="No reason provided"):
    async def func(interaction, member, reason):
        try:
            await member.ban(reason=reason)
            msg_id = await log_action_msg(member, interaction.user, "ban", reason)
            await safe_dm(member, f"🚨 You were banned from **{interaction.guild.name}** by {interaction.user}. Reason: {reason}\nLog ID: `{msg_id}`")
            await interaction.response.send_message(f"🔨 {member.mention} was banned. Log ID: `{msg_id}`")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Cannot ban this member.", ephemeral=True)
    await run_command_with_permission(interaction, "ban", func, member, reason)

@bot.tree.command(name="timeout", description="Timeout a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member", duration="In minutes", reason="Reason")
async def timeout(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str="No reason provided"):
    async def func(interaction, member, duration, reason):
        try:
            until = discord.utils.utcnow() + timedelta(minutes=duration)
            await member.timeout(until, reason=reason)
            msg_id = await log_action_msg(member, interaction.user, "timeout", reason, duration)
            await safe_dm(member, f"⏱️ You were timed out for {duration} minutes in **{interaction.guild.name}**. Reason: {reason}\nLog ID: `{msg_id}`\nEnds: <t:{int(until.timestamp())}:f>")
            await interaction.response.send_message(f"⏱️ {member.mention} timed out for {duration} minute(s). Log ID: `{msg_id}`", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Cannot timeout this member.", ephemeral=True)
    await run_command_with_permission(interaction, "timeout", func, member, duration, reason)

@bot.tree.command(name="warn", description="Warn a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member", reason="Reason")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    async def func(interaction, member, reason):
        msg_id = await log_action_msg(member, interaction.user, "warn", reason)
        await safe_dm(member, f"⚠️ You were warned in **{interaction.guild.name}** by {interaction.user}. Reason: {reason}\nWarn ID: `{msg_id}`")
        await interaction.response.send_message(f"⚠️ {member.mention} warned. Warn ID: `{msg_id}`", ephemeral=True)
    await run_command_with_permission(interaction, "warn", func, member, reason)

@bot.tree.command(name="warndelete", description="Delete a warning by Message ID", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="Message ID of the warning")
async def warndelete(interaction: discord.Interaction, message_id: int):
    async def func(interaction, message_id):
        channel = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Mod log channel not found.", ephemeral=True)
            return
        try:
            msg = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.response.send_message(f"❌ Message ID {message_id} not found in mod log channel.", ephemeral=True)
            return
        meta = _extract_modlog_from_content(msg.content)
        if not meta or meta.get("action") != "warn":
            await interaction.response.send_message("❌ That message is not a warn log.", ephemeral=True)
            return
        try:
            await msg.delete()
            await interaction.response.send_message(f"✅ Warning message {message_id} deleted.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to delete message: {e}", ephemeral=True)
    await run_command_with_permission(interaction, "warndelete", func, message_id)

@bot.tree.command(name="warnlog", description="Show warnings for a user", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member")
async def warnlog(interaction: discord.Interaction, member: discord.Member):
    async def func(interaction, member):
        logs = await fetch_mod_logs(member, only_warns=True)
        if not logs:
            await interaction.response.send_message(f"ℹ️ {member.mention} has no warnings.", ephemeral=True)
            return
        view = LogView(logs, member, interaction)
        await view.send_initial()
    await run_command_with_permission(interaction, "warnlog", func, member)

@bot.tree.command(name="log", description="Show moderation logs for a user (excluding warns)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member")
async def log(interaction: discord.Interaction, member: discord.Member):
    async def func(interaction, member):
        logs = await fetch_mod_logs(member, only_warns=False)
        if not logs:
            await interaction.response.send_message(f"ℹ️ No logs found for {member.mention}.", ephemeral=True)
            return
        view = LogView(logs, member, interaction)
        await view.send_initial()
    await run_command_with_permission(interaction, "log", func, member)

# ----------------------------
# ----------------------------
#  CONTROL PANEL (Owner-only)
# ----------------------------
# ----------------------------
class ChannelSelect(Select):
    def __init__(self):
        super().__init__(placeholder="Select a text channel...", min_values=1, max_values=1, options=[])

    async def callback(self, interaction: discord.Interaction):
        # value is channel id as str
        channel_id = int(self.values[0])
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            await interaction.response.send_message("❌ Invalid channel selected.", ephemeral=True)
            return

        view = ChannelActions(channel, interaction.user.id)
        await interaction.response.send_message(f"🛠 Control panel for <#{channel_id}>", view=view, ephemeral=True)

class ControlPanelView(View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(ChannelSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

class MessageModal(Modal, title="Send Message as Bot"):
    message = TextInput(label="Message", style=discord.TextStyle.paragraph, required=True, max_length=2000)
    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.channel.send(self.message.value)
            await interaction.response.send_message("✅ Message sent!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to send messages in that channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

class EmbedModal(Modal, title="Create Embed"):
    title_field = TextInput(label="Title", required=False, max_length=256)
    desc_field = TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000)
    color_field = TextInput(label="Color (hex, e.g. ff8800) or empty", required=False, max_length=6, placeholder="ff8800")
    thumbnail_field = TextInput(label="Thumbnail URL (optional)", required=False)
    image_field = TextInput(label="Image URL (optional)", required=False)
    footer_field = TextInput(label="Footer Text (optional)", required=False)
    timestamp_field = TextInput(label="Add timestamp? (yes/no)", required=False, placeholder="yes")

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        color = None
        if self.color_field.value:
            try:
                color = discord.Color(int(self.color_field.value.strip(), 16))
            except Exception:
                color = None
        embed = discord.Embed(
            title=self.title_field.value or None,
            description=self.desc_field.value or None,
            color=color or discord.Color.default()
        )
        if self.thumbnail_field.value:
            embed.set_thumbnail(url=self.thumbnail_field.value.strip())
        if self.image_field.value:
            embed.set_image(url=self.image_field.value.strip())
        if self.footer_field.value:
            embed.set_footer(text=self.footer_field.value.strip())
        if self.timestamp_field.value and self.timestamp_field.value.lower().startswith("y"):
            embed.timestamp = datetime.now(timezone.utc)
        try:
            await self.channel.send(embed=embed)
            await interaction.response.send_message("✅ Embed sent!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to send embeds in that channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

class AttachmentModal(Modal, title="Send Attachment (via file URL)"):
    file_url = TextInput(label="File URL (http/https)", required=True)
    filename = TextInput(label="Filename to save as (optional)", required=False, placeholder="example.png")

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        url = self.file_url.value.strip()
        fname = (self.filename.value.strip() or None) if self.filename.value else None
        if not url.lower().startswith(("http://", "https://")):
            await interaction.response.send_message("❌ URL must start with http:// or https://", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(f"❌ Failed to download file: HTTP {resp.status}", ephemeral=True)
                        return
                    data = await resp.read()
            fp = io.BytesIO(data)
            # choose filename
            if not fname:
                # try to infer from URL
                fname = url.split("/")[-1].split("?")[0] or "file"
            fp.seek(0)
            discord_file = discord.File(fp, filename=fname)
            await self.channel.send(file=discord_file)
            await interaction.followup.send("✅ Attachment sent!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Bot lacks permission to send attachments in that channel.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error sending attachment: {e}", ephemeral=True)

# ----------------------------
# ChannelActions (fixed, no duplicate buttons)
# ----------------------------
class ChannelActions(View):
    def __init__(self, channel: discord.TextChannel, user_id: int):
        super().__init__(timeout=300)
        self.channel = channel
        self.user_id = user_id

        # Buttons with callbacks
        self.msg_button = Button(label="Send Message", style=discord.ButtonStyle.primary)
        self.msg_button.callback = self.send_message
        self.add_item(self.msg_button)



        self.attach_button = Button(label="Send Attachment (URL)", style=discord.ButtonStyle.secondary)
        self.attach_button.callback = self.send_attachment
        self.add_item(self.attach_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ensure only the user who opened the panel can interact
        return interaction.user.id == self.user_id

    # Button callbacks
    async def send_message(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MessageModal(self.channel))

   

    async def send_attachment(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AttachmentModal(self.channel))


# ----------------------------
# /panel command (unchanged)
# ----------------------------
@bot.tree.command(name="panel", description="Open the bot control panel", guild=discord.Object(id=GUILD_ID))
async def panel(interaction: discord.Interaction):
    view = ControlPanelView(interaction.user.id)

    # populate channel select with all text channels
    select: ChannelSelect = view.children[0]  # type: ignore
    for ch in interaction.guild.text_channels:
        select.options.append(discord.SelectOption(label=f"#{ch.name}", value=str(ch.id)))

    await interaction.response.send_message(
        "🛠 Bot Control Panel — choose a channel",
        view=view,
        ephemeral=True
    )


# ----------------------------
# Run the bot
# ----------------------------
webserver.keep_alive()
bot.run(token)
