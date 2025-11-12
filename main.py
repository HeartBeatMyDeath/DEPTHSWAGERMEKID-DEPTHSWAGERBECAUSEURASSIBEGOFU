import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from dotenv import load_dotenv
import webserver
import os
import sqlite3
from datetime import timedelta, datetime, timezone


# ----------------------------
# Load token and setup intents
# ----------------------------
load_dotenv()
token = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# ----------------------------
# Guild, channel, and roles
# ----------------------------
GUILD_ID = 1362770221034897639
BLACKLIST_CHANNEL_ID = 1385956899752509532
BLACKLIST_MESSAGE_ID = 0

PERMISSION_TIERS = {
    1362889706563440900: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete", "blacklist_interface"],#owner
    1362896066504036402: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete"],# co owner
    1399809075252039824: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete",], #senior
    1391861560967954483: ["kick", "ban", "timeout", "log", "warn", "warnlog", "warndelete"],  #mod
    1431713725362212917: ["blacklist_interface"], #blacklist
    1399808293999738961: ["kick", "timeout", "log", "warn", "warnlog", "warndelete"],#junior

}

DB_PATH = "mod_logs.db"

# ----------------------------
# Database helpers / setup
# ----------------------------
def get_db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_db()
    c = conn.cursor()
    # moderation logs
    c.execute('''CREATE TABLE IF NOT EXISTS mod_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        moderator_id INTEGER,
        action TEXT,
        reason TEXT,
        timestamp INTEGER,
        duration INTEGER
    )''')
    # warns (separate table so warnlog shows only warns)
    c.execute('''CREATE TABLE IF NOT EXISTS warns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        moderator_id INTEGER,
        reason TEXT,
        timestamp INTEGER
    )''')
    # blacklist items
    c.execute('''CREATE TABLE IF NOT EXISTS blacklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT UNIQUE
    )''')
    # optional table to store a message id (not required since config variable exists)
    c.execute('''CREATE TABLE IF NOT EXISTS blacklist_message (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        message_id INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()
print("✅ Database ready:", DB_PATH)

# ----------------------------
# Logging functions
# ----------------------------
def log_action(user: discord.Member, moderator: discord.Member, action: str, reason: str, duration: int = None):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now(timezone.utc)
    unix_timestamp = int(now.timestamp())
    c.execute(
        "INSERT INTO mod_logs (user_id, moderator_id, action, reason, timestamp, duration) VALUES (?, ?, ?, ?, ?, ?)",
        (user.id, moderator.id, action, reason, unix_timestamp, duration)
    )
    conn.commit()
    conn.close()

def get_user_logs(user: discord.Member):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM mod_logs WHERE user_id = ? ORDER BY id ASC", (user.id,))
    rows = c.fetchall()
    conn.close()
    logs = []
    for row in rows:
        logs.append({
            "id": row[0],
            "user": row[1],
            "moderator": row[2],
            "action": row[3],
            "reason": row[4],
            "timestamp": row[5],
            "duration": row[6]
        })
    return logs

# ----------------------------
# Warn functions
# ----------------------------
def warn_user(user: discord.Member, moderator: discord.Member, reason: str):
    conn = get_db()
    c = conn.cursor()
    unix_timestamp = int(datetime.now(timezone.utc).timestamp())
    c.execute(
        "INSERT INTO warns (user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?)",
        (user.id, moderator.id, reason, unix_timestamp)
    )
    conn.commit()
    conn.close()

def delete_warn(warn_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def get_user_warns(user: discord.Member):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM warns WHERE user_id = ? ORDER BY id ASC", (user.id,))
    rows = c.fetchall()
    conn.close()
    warns = []
    for row in rows:
        warns.append({
            "id": row[0],
            "user": row[1],
            "moderator": row[2],
            "reason": row[3],
            "timestamp": row[4]
        })
    return warns

# ----------------------------
# Blacklist DB helpers
# ----------------------------
def get_blacklist_items():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT item FROM blacklist_items ORDER BY item ASC")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def add_blacklist_item(item_text: str):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO blacklist_items (item) VALUES (?)", (item_text,))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    conn.close()
    return success

def remove_blacklist_item_by_index(index: int):
    items = get_blacklist_items()
    if index < 0 or index >= len(items):
        return None
    removed = items[index]
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM blacklist_items WHERE item = ?", (removed,))
    conn.commit()
    conn.close()
    return removed

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
        # user has DMs off or blocked bot; fail silently
        print(f"⚠️ Could not DM user {user} (DMs closed).")

# ----------------------------
# Blacklist message helpers
# ----------------------------
async def fetch_blacklist_message(channel: discord.TextChannel):
    """
    If BLACKLIST_MESSAGE_ID != 0, try to fetch it. If 0, return None.
    """
    if BLACKLIST_MESSAGE_ID == 0:
        return None
    try:
        return await channel.fetch_message(BLACKLIST_MESSAGE_ID)
    except discord.NotFound:
        print(f"⚠️ Blacklist message id {BLACKLIST_MESSAGE_ID} not found in {channel.name}.")
        return None
    except Exception as e:
        print("⚠️ Error fetching blacklist message:", e)
        return None

async def create_blacklist_message(channel: discord.TextChannel):
    """
    Create a new message (only used if BLACKLIST_MESSAGE_ID == 0 or manual creation allowed).
    Note: If you prefer not to auto-create, keep BLACKLIST_MESSAGE_ID set to your ID.
    """
    items = get_blacklist_items()
    if not items:
        desc = "*(Currently empty)*"
    else:
        desc = "\n".join([f"{i+1} - {w}" for i,w in enumerate(items)])
    embed = discord.Embed(title="📝 Blacklist", description=desc, color=discord.Color.dark_theme())
    msg = await channel.send(embed=embed)
    # also store into DB table for convenience (so admins can retrieve it later) - replace id 1 row
    conn = get_db()
    c = conn.cursor()
    c.execute("REPLACE INTO blacklist_message (id, message_id) VALUES (1, ?)", (msg.id,))
    conn.commit()
    conn.close()
    return msg

async def update_blacklist_message(channel: discord.TextChannel):
    """
    Update the chosen blacklist message. If BLACKLIST_MESSAGE_ID provided use that, otherwise
    try DB-stored message id, otherwise create one.
    """
    items = get_blacklist_items()
    if not items:
        desc = "*(Currently empty)*"
    else:
        desc = "\n".join([f"{i+1} - {w}" for i,w in enumerate(items)])

    # priority: explicit config ID -> DB stored -> do nothing / create
    # try explicit config first
    message = None
    if BLACKLIST_MESSAGE_ID != 0:
        message = await fetch_blacklist_message(channel)

    if message is None:
        # try DB-stored message id
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT message_id FROM blacklist_message WHERE id = 1")
        row = c.fetchone()
        conn.close()
        if row:
            try:
                message = await channel.fetch_message(row[0])
            except Exception:
                message = None

    if message is None:
        # if explicit config is 0, create new message and store it, otherwise bail (we respect your config)
        if BLACKLIST_MESSAGE_ID == 0:
            message = await create_blacklist_message(channel)
        else:
            print("⚠️ No valid blacklist message found and BLACKLIST_MESSAGE_ID is set; not creating a new message.")
            return

    embed = discord.Embed(title="📝 Blacklist (To-Do List)", description=desc, color=discord.Color.dark_theme())
    try:
        await message.edit(embed=embed)
    except Exception as e:
        print("⚠️ Failed to update blacklist message:", e)

# ----------------------------
# Modals and View for Blacklist
# ----------------------------
class AddItemModal(Modal, title="Add item to Blacklist"):
    item = TextInput(label="Item", placeholder="Enter item to add (single word/phrase)", required=True, max_length=200)

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
            await interaction.response.send_message("⚠️ That item already exists in the blacklist.", ephemeral=True)

class RemoveItemModal(Modal, title="Remove item from Blacklist"):
    number = TextInput(label="Item number", placeholder="Enter the number (e.g. 1) of the item to remove", required=True, max_length=10)

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            num = int(self.number.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Invalid number.", ephemeral=True)
            return

        items = get_blacklist_items()
        if num < 1 or num > len(items):
            await interaction.response.send_message("❌ That number doesn't exist.", ephemeral=True)
            return

        removed = remove_blacklist_item_by_index(num - 1)
        if removed is None:
            await interaction.response.send_message("❌ Failed to remove item.", ephemeral=True)
        else:
            await update_blacklist_message(self.channel)
            await interaction.response.send_message(f"🗑️ Removed **{removed}** from the blacklist.", ephemeral=True)

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

# ----------------------------
# Slash: blacklist_interface
# ----------------------------
@bot.tree.command(name="blacklist_interface", description="Open the blacklist interface (Add / Remove items)", guild=discord.Object(id=GUILD_ID))
async def blacklist_interface(interaction: discord.Interaction):
    async def inner(interaction: discord.Interaction):
        # permission already checked by wrapper
        channel = interaction.guild.get_channel(BLACKLIST_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Blacklist channel not found.", ephemeral=True)
            return

        items = get_blacklist_items()
        if not items:
            desc = "*(Currently empty)*"
        else:
            desc = "\n".join([f"{i+1} - {word}" for i, word in enumerate(items)])

        embed = discord.Embed(title="📝 Blacklist Manager", description=desc, color=discord.Color.dark_theme())
        view = BlacklistView(channel)
        # update the persistent message as a precaution
        await update_blacklist_message(channel)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    await run_command_with_permission(interaction, "blacklist_interface", inner)

# ----------------------------
# Moderation commands (kick, ban, timeout, warn, warndelete, warnlog, log)
# ----------------------------
# DM helper used in commands
def format_timeout_end(duration_minutes: int):
    until = discord.utils.utcnow() + timedelta(minutes=duration_minutes)
    return int(until.timestamp())

@bot.tree.command(name="kick", description="Kick a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    async def func(interaction, member, reason):
        try:
            await member.kick(reason=reason)
            log_action(member, interaction.user, "kick", reason)
            await safe_dm(member, f"🚨 You were kicked from **{interaction.guild.name}** by {interaction.user}. Reason: {reason}")
            await interaction.response.send_message(f"👢 {member.mention} was kicked. Reason: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I cannot kick this member.", ephemeral=True)
    await run_command_with_permission(interaction, "kick", func, member, reason)

@bot.tree.command(name="ban", description="Ban a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member to ban", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    async def func(interaction, member, reason):
        try:
            await member.ban(reason=reason)
            log_action(member, interaction.user, "ban", reason)
            await safe_dm(member, f"🚨 You were banned from **{interaction.guild.name}** by {interaction.user}. Reason: {reason}")
            await interaction.response.send_message(f"🔨 {member.mention} was banned. Reason: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I cannot ban this member.", ephemeral=True)
    await run_command_with_permission(interaction, "ban", func, member, reason)

@bot.tree.command(name="timeout", description="Timeout a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member", duration="Minutes", reason="Reason")
async def timeout(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str = "No reason provided"):
    async def func(interaction, member, duration, reason):
        try:
            until = discord.utils.utcnow() + timedelta(minutes=duration)
            await member.timeout(until, reason=reason)
            log_action(member, interaction.user, "timeout", reason, duration)
            await safe_dm(member, f"⏱️ You were timed out in **{interaction.guild.name}** by {interaction.user} for {duration} minutes. Reason: {reason}\nTimeout ends at: <t:{int(until.timestamp())}:f>")
            await interaction.response.send_message(f"⏱️ {member.mention} timed out for {duration} minute(s). Ends at: <t:{int(until.timestamp())}:f>")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I cannot timeout this member.", ephemeral=True)
    await run_command_with_permission(interaction, "timeout", func, member, duration, reason)

@bot.tree.command(name="warn", description="Warn a member", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member", reason="Reason")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    async def func(interaction, member, reason):
        warn_user(member, interaction.user, reason)
        await safe_dm(member, f"⚠️ You were warned in **{interaction.guild.name}** by {interaction.user}. Reason: {reason}")
        await interaction.response.send_message(f"⚠️ {member.mention} has been warned. Reason: {reason}")
    await run_command_with_permission(interaction, "warn", func, member, reason)

@bot.tree.command(name="warndelete", description="Delete a specific warning by ID", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(warn_id="ID of the warning to delete")
async def warndelete(interaction: discord.Interaction, warn_id: int):
    async def func(interaction, warn_id):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM warns WHERE id = ?", (warn_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            await interaction.response.send_message(f"❌ Warning ID {warn_id} not found.", ephemeral=True)
            return
        delete_warn(warn_id)
        await interaction.response.send_message(f"✅ Warning ID {warn_id} deleted.", ephemeral=True)
    await run_command_with_permission(interaction, "warndelete", func, warn_id)

@bot.tree.command(name="warnlog", description="Show warnings for a user", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member")
async def warnlog(interaction: discord.Interaction, member: discord.Member):
    async def func(interaction, member):
        warns = get_user_warns(member)
        if not warns:
            await interaction.response.send_message(f"ℹ️ {member.mention} has no warnings.", ephemeral=True)
            return
        # adapt to LogView expected dict keys
        entries = []
        for w in warns:
            entries.append({
                "id": w["id"],
                "moderator": w["moderator"],
                "reason": w["reason"],
                "timestamp": w["timestamp"],
                # no duration / action for warns, but set action value for display
                "action": "warn",
                "duration": None
            })
        view = LogView(entries, member, interaction)
        await view.send_initial()
    await run_command_with_permission(interaction, "warnlog", func, member)

@bot.tree.command(name="log", description="Show moderation logs for a user (excluding warns)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(member="Member")
async def log(interaction: discord.Interaction, member: discord.Member):
    async def func(interaction, member):
        logs = [l for l in get_user_logs(member) if l["action"] != "warn"]
        if not logs:
            await interaction.response.send_message(f"ℹ️ No moderation history found for {member.mention}.", ephemeral=True)
            return
        # ensure each entry has expected keys for LogView
        entries = []
        for l in logs:
            entries.append({
                "id": l["id"],
                "moderator": l["moderator"],
                "action": l["action"],
                "reason": l["reason"],
                "timestamp": l["timestamp"],
                "duration": l["duration"]
            })
        view = LogView(entries, member, interaction)
        await view.send_initial()
    await run_command_with_permission(interaction, "log", func, member)

# ----------------------------
# Run the bot
# ----------------------------
webserver.keep_alive()
bot.run(token)
