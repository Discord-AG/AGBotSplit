import os, json, random, asyncio, aiosqlite, discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, UTC
from typing import Optional
import io 

import common
from common import (
    get_db, db_lock, setup_database, log_event, _log_embed, command_enabled,
    is_allowed_to_giveaway, _is_allowed_ctx, is_system_enabled, set_system_flag,
    get_balance, add_balance, get_exp, add_exp, get_level,
    inventory_add, inventory_remove, inventory_get,
    get_tickets, add_tickets,
    add_stat, ensure_stats, _do_reset,
    bump_msg_count, msg_count_flush_loop,
    FakeInteraction, _MC,
    GAMBLE_TOKEN, VIP_CHEST_KEY, BOT_OWNER_ID, COUNTING_BOT_ID, _COUNTING_FAIL_EMOJI,
    _SYSTEM_LABELS, _SYSTEM_CHOICES,
    disabled_commands, global_disabled_commands, load_disabled_commands,
    prefix_channel_rules, _prefix_channel_allowed, load_prefix_restrictions, set_prefix,
    register_bot_instance, parse_amount, EmbedPaginator, paginate_lines,
    record_host_event, get_host_leaderboard, get_host_role_config,
    get_host_bonus_entries, sync_host_roles, get_exchange_config,
)

TOKEN = os.getenv("TOKEN_ADMIN")
_GUILD_ID = int(os.getenv("GUILD_ID", "0"))
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix=common.get_prefix, intents=intents, help_command=None)
register_bot_instance(bot)

# ═══════════════════════════════════════════════════════
# REDEEM CODES
# ═══════════════════════════════════════════════════════

def _parse_prize_json(prize_str: str) -> dict | None:
    """Parse prize JSON or shorthand like 'balance:1000' or 'exp:500'."""
    try:
        data = json.loads(prize_str)
        if isinstance(data, dict): return data
    except (json.JSONDecodeError, ValueError):
        pass
    if ":" in prize_str:
        parts = prize_str.strip().split(":")
        if len(parts) == 2:
            key, val = parts[0].strip().lower(), parts[1].strip()
            try: return {key: int(val)}
            except ValueError: return {"label": val}
    return None


async def _award_code_prize(gid: int, uid: int, prize: dict, guild: discord.Guild = None):
    if "balance" in prize and prize["balance"]: await add_balance(gid, uid, int(prize["balance"]), bot=bot)
    if "exp" in prize and prize["exp"]: await add_exp(gid, uid, int(prize["exp"]))
    if "tickets" in prize and prize["tickets"]: await add_tickets(gid, uid, int(prize["tickets"]))
    if "gamble_tokens" in prize and prize["gamble_tokens"]:
        await inventory_add(gid, uid, GAMBLE_TOKEN, int(prize["gamble_tokens"]))
    if "vip_keys" in prize and prize["vip_keys"]:
        await inventory_add(gid, uid, VIP_CHEST_KEY, int(prize["vip_keys"]))
    if "item" in prize and prize["item"]:
        qty = int(prize.get("item_qty", 1))
        await inventory_add(gid, uid, prize["item"], qty)
    if "role_id" in prize and prize["role_id"] and guild:
        role = guild.get_role(int(prize["role_id"]))
        member = guild.get_member(uid)
        if role and member:
            try: await member.add_roles(role)
            except Exception: pass


def _prize_summary(prize: dict, guild: discord.Guild = None) -> str:
    parts = []
    if prize.get("balance"):       parts.append(f"💰 {int(prize['balance']):,} coins")
    if prize.get("exp"):           parts.append(f"⭐ {int(prize['exp']):,} EXP")
    if prize.get("tickets"):       parts.append(f"🎟 {prize['tickets']} ticket(s)")
    if prize.get("gamble_tokens"): parts.append(f"🎲 {prize['gamble_tokens']} gamble token(s)")
    if prize.get("vip_keys"):      parts.append(f"🔑 {prize['vip_keys']} VIP key(s)")
    if prize.get("item"):          parts.append(f"🎒 {prize.get('item_qty',1)}x {prize['item']}")
    if prize.get("role_id") and guild:
        r = guild.get_role(int(prize["role_id"]))
        if r: parts.append(f"👑 {r.mention}")
    if prize.get("label"):         parts.append(str(prize["label"]))
    return " + ".join(parts) if parts else "Unknown reward"


@bot.tree.command(name="createcode", description="Create a redeem code")
@app_commands.describe(
    code="The code users type to redeem",
    prize_json='Prize as JSON {"balance":1000,"exp":500} or shorthand "balance:1000"',
    uses="Max uses (-1 = unlimited, default 1)",
    min_level="Minimum Activity Rank required (default 0 = any)",
    min_balance="Minimum balance required (default 0 = any)",
    required_role="Role required to redeem this code")
@command_enabled()
async def createcode(interaction: discord.Interaction, code: str, prize_json: str,
                     uses: int = 1, min_level: int = 0, min_balance: int = 0,
                     required_role: discord.Role = None):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    code = code.strip().upper()
    if not code:
        await interaction.response.send_message("❌ Code cannot be empty.", ephemeral=True); return
    prize = _parse_prize_json(prize_json)
    if not prize:
        await interaction.response.send_message(
            '❌ Invalid prize format. Use JSON like `{"balance":1000}` or shorthand `balance:1000`.',
            ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            try:
                await db.execute(
                    "INSERT INTO redeem_codes(guild_id,code,prize_json,uses_left,min_level,min_balance,required_role_id) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (interaction.guild.id, code, json.dumps(prize), uses,
                     min_level, min_balance, required_role.id if required_role else 0))
                await db.commit()
            except aiosqlite.IntegrityError:
                await interaction.response.send_message(f"❌ Code **{code}** already exists.", ephemeral=True); return
    uses_str = "unlimited" if uses == -1 else str(uses)
    reqs = []
    if min_level: reqs.append(f"Level {min_level}+")
    if min_balance: reqs.append(f"{min_balance:,}+ balance")
    if required_role: reqs.append(required_role.mention)
    req_str = " | Requirements: " + ", ".join(reqs) if reqs else ""
    await interaction.response.send_message(
        f"✅ Code **{code}** created!\n"
        f"Prize: {_prize_summary(prize, interaction.guild)} | Uses: {uses_str}{req_str}", ephemeral=True)
    await log_event(interaction.guild.id, "admin", _log_embed("🎟 Code Created", discord.Color.green(),
        Admin=interaction.user.mention, Code=code, Uses=uses_str))

@bot.command(name="createcode")
async def pfx_createcode(ctx, code: str, prize_json: str, uses: int = 1,
                          min_level: int = 0, min_balance: int = 0):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await createcode._callback(FakeInteraction(ctx), code, prize_json, uses, min_level, min_balance, None)


@bot.command(name="deletecode")
async def cmd_deletecode(ctx, code: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    code = code.strip().upper()
    async with db_lock:
        async with get_db() as db:
            async with db.execute("SELECT code FROM redeem_codes WHERE guild_id=? AND code=?",
                                  (ctx.guild.id, code)) as cur:
                if not await cur.fetchone(): await ctx.send(f"❌ Code **{code}** not found."); return
            await db.execute("DELETE FROM redeem_codes WHERE guild_id=? AND code=?", (ctx.guild.id, code))
            await db.execute("DELETE FROM code_uses WHERE guild_id=? AND code=?", (ctx.guild.id, code))
            await db.commit()
    await ctx.send(f"🗑 Code **{code}** deleted.")


@bot.command(name="listcodes")
async def cmd_listcodes(ctx):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    async with get_db() as db:
        async with db.execute(
            "SELECT code,prize_json,uses_left,min_level,min_balance FROM redeem_codes WHERE guild_id=? ORDER BY code",
            (ctx.guild.id,)) as cur:
            codes = await cur.fetchall()
    if not codes: await ctx.send("❌ No codes configured."); return
    lines = []
    for code, pj, uses, ml, mb in codes:
        try: prize = json.loads(pj); ps = _prize_summary(prize)
        except Exception: ps = str(pj)
        uses_str = "∞" if uses == -1 else str(uses)
        reqs = []
        if ml: reqs.append(f"Lvl{ml}+")
        if mb: reqs.append(f"{mb:,}+ bal")
        req_str = f" [{', '.join(reqs)}]" if reqs else ""
        lines.append(f"**{code}** ({uses_str} use{'s' if uses!=1 else ''}){req_str} — {ps[:60]}")
    embed = discord.Embed(title="🎟 Redeem Codes", description="\n".join(lines[:20]), color=discord.Color.green())
    if len(codes) > 20: embed.set_footer(text=f"+{len(codes)-20} more codes not shown")
    await ctx.send(embed=embed)


@bot.tree.command(name="redeem", description="Redeem a code for a reward")
@app_commands.describe(code="The code to redeem")
@command_enabled()
async def redeem(interaction: discord.Interaction, code: str):
    code = code.strip().upper()
    gid, uid = interaction.guild.id, interaction.user.id
    await interaction.response.defer(ephemeral=True)

    async with get_db() as db:
        async with db.execute(
            "SELECT prize_json,uses_left,min_level,min_balance,required_role_id FROM redeem_codes "
            "WHERE guild_id=? AND code=?", (gid, code)) as cur:
            guild_code = await cur.fetchone()
        async with db.execute("SELECT prize_json,uses_left,min_level,min_balance FROM global_redeem_codes WHERE code=?",
                              (code,)) as cur:
            global_code = await cur.fetchone()

    if not guild_code and not global_code:
        await interaction.followup.send("❌ Invalid code.", ephemeral=True); return

    if guild_code:
        pj, uses_left, min_level, min_balance, req_role_id = guild_code
        async with get_db() as db:
            async with db.execute("SELECT user_id FROM code_uses WHERE guild_id=? AND code=? AND user_id=?",
                                  (gid, code, uid)) as cur:
                if await cur.fetchone():
                    await interaction.followup.send("❌ You've already redeemed this code.", ephemeral=True); return
        if uses_left == 0:
            await interaction.followup.send("❌ This code has no uses remaining.", ephemeral=True); return
        if min_level:
            lvl = await get_level(gid, uid)
            if lvl < min_level:
                await interaction.followup.send(f"❌ You need Activity Rank **{min_level}+** (you are {lvl}).", ephemeral=True); return
        if min_balance:
            bal = await get_balance(gid, uid)
            if bal < min_balance:
                await interaction.followup.send(f"❌ You need **{min_balance:,}+** coins (you have {bal:,}).", ephemeral=True); return
        if req_role_id:
            member = interaction.guild.get_member(uid)
            if not member or req_role_id not in {r.id for r in member.roles}:
                role = interaction.guild.get_role(req_role_id)
                await interaction.followup.send(
                    f"❌ You need the {role.mention if role else 'required'} role to redeem this code.", ephemeral=True); return
        try: prize = json.loads(pj)
        except Exception: await interaction.followup.send("❌ Corrupt prize data.", ephemeral=True); return
        async with db_lock:
            async with get_db() as db:
                await db.execute("INSERT INTO code_uses VALUES(?,?,?)", (gid, code, uid))
                if uses_left != -1:
                    await db.execute("UPDATE redeem_codes SET uses_left=uses_left-1 WHERE guild_id=? AND code=?",
                                     (gid, code))
                await db.commit()
        await _award_code_prize(gid, uid, prize, interaction.guild)
        await interaction.followup.send(
            f"✅ Code **{code}** redeemed!\nReward: {_prize_summary(prize, interaction.guild)}", ephemeral=True)
        await log_event(gid, "admin", _log_embed("🎟 Code Redeemed", discord.Color.green(),
            User=interaction.user.mention, Code=code, Reward=_prize_summary(prize)[:100]))
        return

    pj, uses_left, min_level, min_balance = global_code
    async with get_db() as db:
        async with db.execute("SELECT user_id FROM global_code_uses WHERE code=? AND user_id=?", (code, uid)) as cur:
            if await cur.fetchone():
                await interaction.followup.send("❌ You've already redeemed this global code.", ephemeral=True); return
    if uses_left == 0:
        await interaction.followup.send("❌ This code has no uses remaining.", ephemeral=True); return
    if min_level:
        lvl = await get_level(gid, uid)
        if lvl < min_level:
            await interaction.followup.send(f"❌ You need Activity Rank **{min_level}+**.", ephemeral=True); return
    if min_balance:
        bal = await get_balance(gid, uid)
        if bal < min_balance:
            await interaction.followup.send(f"❌ You need **{min_balance:,}+** coins.", ephemeral=True); return
    try: prize = json.loads(pj)
    except Exception: await interaction.followup.send("❌ Corrupt prize data.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT INTO global_code_uses VALUES(?,?)", (code, uid))
            if uses_left != -1:
                await db.execute("UPDATE global_redeem_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
            await db.commit()
    await _award_code_prize(gid, uid, prize, interaction.guild)
    await interaction.followup.send(
        f"✅ Global code **{code}** redeemed!\nReward: {_prize_summary(prize, interaction.guild)}", ephemeral=True)

@bot.command(name="redeem")
async def pfx_redeem(ctx, code: str):
    await redeem._callback(FakeInteraction(ctx), code)


# ═══════════════════════════════════════════════════════
# ADMIN PANEL
# ═══════════════════════════════════════════════════════

class _APModal(discord.ui.Modal):
    user_input    = discord.ui.TextInput(label="User ID or @mention", max_length=30)
    amount_input  = discord.ui.TextInput(label="Amount (leave blank where not needed)", required=False, max_length=20)

    def __init__(self, action: str):
        super().__init__(title=f"Admin Panel — {action.replace('_',' ').title()}")
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        uid_raw = self.user_input.value.strip().lstrip("<@!").rstrip(">")
        try: uid = int(uid_raw)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True); return

        member = interaction.guild.get_member(uid)
        if not member:
            await interaction.response.send_message("❌ Member not found in this server.", ephemeral=True); return

        amount = 0
        if self.amount_input.value.strip():
            try: amount = int(self.amount_input.value.strip())
            except ValueError:
                await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return

        gid = interaction.guild.id
        action = self.action
        if action == "add_balance":
            if amount <= 0: await interaction.response.send_message("❌ Amount must be > 0.", ephemeral=True); return
            await add_balance(gid, uid, amount, bot=bot)
            msg = f"✅ Added {amount:,} coins to {member.mention}."
        elif action == "remove_balance":
            if amount <= 0: await interaction.response.send_message("❌ Amount must be > 0.", ephemeral=True); return
            await add_balance(gid, uid, -amount, bot=bot)
            msg = f"❌ Removed {amount:,} coins from {member.mention}."
        elif action == "add_exp":
            if amount <= 0: await interaction.response.send_message("❌ Amount must be > 0.", ephemeral=True); return
            await add_exp(gid, uid, amount, is_bonus=True)
            msg = f"⭐ Added {amount:,} EXP to {member.mention}."
        elif action == "reset_balance":
            await _do_reset(gid, uid, "balance")
            msg = f"🔄 Reset balance for {member.mention}."
        elif action == "reset_exp":
            await _do_reset(gid, uid, "exp")
            msg = f"🔄 Reset EXP for {member.mention}."
        elif action == "reset_inventory":
            await _do_reset(gid, uid, "inventory")
            msg = f"🔄 Reset inventory for {member.mention}."
        elif action == "reset_stats":
            await _do_reset(gid, uid, "stats")
            msg = f"🔄 Reset stats for {member.mention}."
        elif action == "reset_all":
            await _do_reset(gid, uid, "all")
            msg = f"🔄 Reset everything for {member.mention}."
        else:
            msg = "❌ Unknown action."

        await interaction.response.send_message(msg, ephemeral=True)
        await log_event(gid, "admin", _log_embed(f"⚙️ Admin Panel: {action}", discord.Color.orange(),
            Admin=interaction.user.mention, Target=member.mention,
            Amount=str(amount) if amount else "N/A"))


class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check(self, interaction: discord.Interaction) -> bool:
        if not await is_allowed_to_giveaway(interaction):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💰 Add Balance", style=discord.ButtonStyle.success, custom_id="ap:add_balance", row=0)
    async def add_bal(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("add_balance"))

    @discord.ui.button(label="💸 Remove Balance", style=discord.ButtonStyle.danger, custom_id="ap:remove_balance", row=0)
    async def rem_bal(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("remove_balance"))

    @discord.ui.button(label="⭐ Add EXP", style=discord.ButtonStyle.success, custom_id="ap:add_exp", row=0)
    async def add_exp_btn(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("add_exp"))

    @discord.ui.button(label="🔄 Reset Balance", style=discord.ButtonStyle.secondary, custom_id="ap:reset_balance", row=1)
    async def rst_bal(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("reset_balance"))

    @discord.ui.button(label="🔄 Reset EXP", style=discord.ButtonStyle.secondary, custom_id="ap:reset_exp", row=1)
    async def rst_exp(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("reset_exp"))

    @discord.ui.button(label="🔄 Reset Inventory", style=discord.ButtonStyle.secondary, custom_id="ap:reset_inventory", row=1)
    async def rst_inv(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("reset_inventory"))

    @discord.ui.button(label="🔄 Reset Stats", style=discord.ButtonStyle.secondary, custom_id="ap:reset_stats", row=2)
    async def rst_stats(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("reset_stats"))

    @discord.ui.button(label="💥 Reset Everything", style=discord.ButtonStyle.danger, custom_id="ap:reset_all", row=2)
    async def rst_all(self, i, b):
        if await self._check(i): await i.response.send_modal(_APModal("reset_all"))


@bot.tree.command(name="setadminpanel", description="Post the admin panel in a channel")
@app_commands.describe(channel="Channel to post the admin panel in")
@command_enabled()
async def setadminpanel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await interaction.response.defer()
    gid = interaction.guild.id
    async with get_db() as db:
        async with db.execute("SELECT channel_id,message_id FROM admin_panel_config WHERE guild_id=?", (gid,)) as cur:
            old = await cur.fetchone()
    if old and old[0] and old[1]:
        old_ch = bot.get_channel(old[0])
        if old_ch:
            try: await (await old_ch.fetch_message(old[1])).delete()
            except Exception: pass
    embed = discord.Embed(title="⚙️ Admin Panel",
        description="Use the buttons below to manage player balances, EXP, and data.",
        color=discord.Color.blurple())
    embed.set_footer(text="All actions are logged. Only authorised roles can use these buttons.")
    msg = await channel.send(embed=embed, view=AdminPanelView())
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO admin_panel_config(guild_id,channel_id,message_id) VALUES(?,?,?) "
                "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,message_id=excluded.message_id",
                (gid, channel.id, msg.id))
            await db.commit()
    await interaction.followup.send(f"✅ Admin panel posted in {channel.mention}.")

@bot.command(name="setadminpanel")
async def pfx_setadminpanel(ctx, channel: discord.TextChannel):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await setadminpanel._callback(FakeInteraction(ctx), channel)

# ═══════════════════════════════════════════════════════
# DISABLED COMMANDS
# ═══════════════════════════════════════════════════════

@bot.command(name="disablecmd")
async def cmd_disablecmd(ctx, *, cmd_name: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    cmd_name = cmd_name.strip().lower()
    gid = ctx.guild.id
    disabled_commands.setdefault(gid, set()).add(cmd_name)
    async with db_lock:
        async with get_db() as db:
            try:
                await db.execute("INSERT INTO disabled_commands_persist VALUES(?,?)", (gid, cmd_name))
                await db.commit()
            except aiosqlite.IntegrityError:
                pass
    await ctx.send(f"🔒 Command **{cmd_name}** disabled in this server.")

@bot.command(name="enablecmd")
async def cmd_enablecmd(ctx, *, cmd_name: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    cmd_name = cmd_name.strip().lower()
    gid = ctx.guild.id
    disabled_commands.get(gid, set()).discard(cmd_name)
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM disabled_commands_persist WHERE guild_id=? AND command_name=?",
                             (gid, cmd_name))
            await db.commit()
    await ctx.send(f"✅ Command **{cmd_name}** re-enabled.")

@bot.tree.command(name="listcmds",
                  description="List all disabled commands in this server and globally")
@command_enabled()
async def slash_listcmds(interaction: discord.Interaction):
    gid   = interaction.guild.id
    gcmds = sorted(global_disabled_commands)
    lcmds = sorted(disabled_commands.get(gid, set()))
    lines = []
    if gcmds:
        lines.append("**── Globally Disabled ──**")
        lines += [f"• {c}" for c in gcmds]
    if lcmds:
        lines.append("\n**── Disabled in this Server ──**")
        lines += [f"• {c}" for c in lcmds]
    if not lines:
        await interaction.response.send_message("✅ No commands are currently disabled.", ephemeral=True); return
    pages = paginate_lines(lines, "🔒 Disabled Commands", discord.Color.red())
    view  = EmbedPaginator(pages, interaction.user.id) if len(pages) > 1 else None
    await interaction.response.send_message(embed=pages[0], view=view)

@bot.command(name="listcmds")
async def cmd_listcmds(ctx):
    gid   = ctx.guild.id
    gcmds = sorted(global_disabled_commands)
    lcmds = sorted(disabled_commands.get(gid, set()))
    lines = []
    if gcmds:
        lines.append("**── Globally Disabled ──**")
        lines += [f"• {c}" for c in gcmds]
    if lcmds:
        lines.append("\n**── Disabled in this Server ──**")
        lines += [f"• {c}" for c in lcmds]
    if not lines:
        await ctx.send("✅ No commands are currently disabled."); return
    pages = paginate_lines(lines, "🔒 Disabled Commands", discord.Color.red())
    for embed in pages:
        await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════
# SYSTEM TOGGLES
# ═══════════════════════════════════════════════════════

@bot.tree.command(name="enablesystem", description="Enable a system in this server")
@app_commands.choices(system=_SYSTEM_CHOICES)
@command_enabled()
async def enablesystem(interaction: discord.Interaction, system: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await set_system_flag(interaction.guild.id, system, True)
    label = _SYSTEM_LABELS.get(system, system)
    await interaction.response.send_message(f"✅ {label} **enabled** in this server.")

@bot.command(name="enablesystem")
async def pfx_enablesystem(ctx, system: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if system not in _SYSTEM_LABELS: await ctx.send(f"❌ Valid systems: {', '.join(_SYSTEM_LABELS)}"); return
    await enablesystem._callback(FakeInteraction(ctx), system)


@bot.tree.command(name="disablesystem", description="Disable a system in this server")
@app_commands.choices(system=_SYSTEM_CHOICES)
@command_enabled()
async def disablesystem(interaction: discord.Interaction, system: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await set_system_flag(interaction.guild.id, system, False)
    label = _SYSTEM_LABELS.get(system, system)
    await interaction.response.send_message(f"🔒 {label} **disabled** in this server.")

@bot.command(name="disablesystem")
async def pfx_disablesystem(ctx, system: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if system not in _SYSTEM_LABELS: await ctx.send(f"❌ Valid systems: {', '.join(_SYSTEM_LABELS)}"); return
    await disablesystem._callback(FakeInteraction(ctx), system)


@bot.command(name="systemstatus")
async def cmd_systemstatus(ctx):
    gid = ctx.guild.id
    embed = discord.Embed(title="⚙️ System Status", color=discord.Color.blurple())
    for key, label in _SYSTEM_LABELS.items():
        enabled = await is_system_enabled(gid, key)
        embed.add_field(name=label, value="✅ Enabled" if enabled else "🔒 Disabled", inline=True)
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════
# LOG CHANNELS
# ═══════════════════════════════════════════════════════

_LOG_TYPES = ["balance","exp","giveaway","mega","chest","box","item","trade","admin","command","error"]
_LOG_CHOICES = [app_commands.Choice(name=t.title(), value=t) for t in _LOG_TYPES]

@bot.tree.command(name="setlogchannel", description="Set a log channel for a specific event type")
@app_commands.describe(log_type="Type of events to log", channel="Channel to log to")
@app_commands.choices(log_type=_LOG_CHOICES)
@command_enabled()
async def setlogchannel(interaction: discord.Interaction, log_type: str, channel: discord.TextChannel):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO log_channels VALUES(?,?,?)",
                             (interaction.guild.id, log_type, channel.id))
            await db.commit()
    await interaction.response.send_message(f"✅ **{log_type.title()}** events → {channel.mention}")

@bot.command(name="setlogchannel")
async def pfx_setlogchannel(ctx, log_type: str, channel: discord.TextChannel):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if log_type not in _LOG_TYPES: await ctx.send(f"❌ Valid types: {', '.join(_LOG_TYPES)}"); return
    await setlogchannel._callback(FakeInteraction(ctx), log_type, channel)


@bot.command(name="removelogchannel")
async def cmd_removelogchannel(ctx, log_type: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if log_type not in _LOG_TYPES: await ctx.send(f"❌ Valid types: {', '.join(_LOG_TYPES)}"); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM log_channels WHERE guild_id=? AND log_type=?", (ctx.guild.id, log_type))
            await db.commit()
    await ctx.send(f"🗑 Removed log channel for **{log_type}**.")


@bot.command(name="listlogchannels")
async def cmd_listlogchannels(ctx):
    async with get_db() as db:
        async with db.execute("SELECT log_type,channel_id FROM log_channels WHERE guild_id=? ORDER BY log_type",
                              (ctx.guild.id,)) as cur:
            rows = await cur.fetchall()
    if not rows: await ctx.send("❌ No log channels configured."); return
    lines = []
    for lt, cid in rows:
        ch = ctx.guild.get_channel(cid)
        lines.append(f"• **{lt.title()}** → {ch.mention if ch else f'<deleted {cid}>'}")
    await ctx.send(embed=discord.Embed(title="📋 Log Channels", description="\n".join(lines),
                                       color=discord.Color.blurple()))

# ═══════════════════════════════════════════════════════
# PREFIX CHANNEL RESTRICTIONS
# ═══════════════════════════════════════════════════════

@bot.command(name="disableprefixchannel")
async def cmd_disableprefixchannel(ctx, channel: discord.TextChannel, role: discord.Role = None):
    """Disable prefix commands in a channel globally, or for a specific role."""
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    rid = role.id if role else 0
    key = (ctx.guild.id, channel.id)
    prefix_channel_rules.setdefault(key, {})[rid] = False
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO prefix_restrictions VALUES(?,?,?,?)",
                             (ctx.guild.id, channel.id, rid, 0))
            await db.commit()
    scope = f" for {role.mention}" if role else " for everyone"
    await ctx.send(f"🔒 Prefix commands disabled in {channel.mention}{scope}.")

@bot.command(name="enableprefixchannel")
async def cmd_enableprefixchannel(ctx, channel: discord.TextChannel, role: discord.Role = None):
    """Allow a specific role to use prefix commands in a channel where they're otherwise disabled."""
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    rid = role.id if role else 0
    key = (ctx.guild.id, channel.id)
    prefix_channel_rules.setdefault(key, {})[rid] = True
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO prefix_restrictions VALUES(?,?,?,?)",
                             (ctx.guild.id, channel.id, rid, 1))
            await db.commit()
    scope = f" for {role.mention}" if role else " for everyone"
    await ctx.send(f"✅ Prefix commands enabled in {channel.mention}{scope}.")

@bot.command(name="resetprefixchannel")
async def cmd_resetprefixchannel(ctx, channel: discord.TextChannel):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    key = (ctx.guild.id, channel.id)
    prefix_channel_rules.pop(key, None)
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM prefix_restrictions WHERE guild_id=? AND channel_id=?",
                             (ctx.guild.id, channel.id))
            await db.commit()
    await ctx.send(f"✅ Prefix restrictions cleared for {channel.mention}.")

@bot.command(name="listprefixchannels")
async def cmd_listprefixchannels(ctx):
    async with get_db() as db:
        async with db.execute("SELECT channel_id,role_id,allowed FROM prefix_restrictions WHERE guild_id=?",
                              (ctx.guild.id,)) as cur:
            rows = await cur.fetchall()
    if not rows: await ctx.send("❌ No prefix restrictions set."); return
    lines = []
    for cid, rid, allowed in rows:
        ch = ctx.guild.get_channel(cid)
        ch_str = ch.mention if ch else f"<#{cid}>"
        role_str = f"@{ctx.guild.get_role(rid).name}" if rid and ctx.guild.get_role(rid) else "everyone"
        status = "✅ Allowed" if allowed else "🔒 Blocked"
        lines.append(f"• {ch_str} | {role_str} → {status}")
    await ctx.send(embed=discord.Embed(title="🔒 Prefix Restrictions", description="\n".join(lines),
                                       color=discord.Color.orange()))


# ═══════════════════════════════════════════════════════
# AUTO-RESET ON LEAVE
# ═══════════════════════════════════════════════════════

@bot.command(name="enableautoreset")
async def cmd_enableautoreset(ctx):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO auto_reset_config VALUES(?,1)", (ctx.guild.id,))
            await db.commit()
    await ctx.send("✅ Auto-reset on leave enabled.")

@bot.command(name="disableautoreset")
async def cmd_disableautoreset(ctx):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("UPDATE auto_reset_config SET enabled=0 WHERE guild_id=?", (ctx.guild.id,))
            await db.commit()
    await ctx.send("🔒 Auto-reset on leave disabled.")


@bot.command(name="setautoresetrule")
async def cmd_setautoresetrule(ctx, reset_type: str, delay_seconds: int = 0):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if reset_type not in ("balance","exp","inventory","tickets","stats","all"):
        await ctx.send("❌ Valid types: balance, exp, inventory, tickets, stats, all"); return
    if delay_seconds < 0: await ctx.send("❌ Delay must be ≥ 0."); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO auto_reset_rules VALUES(?,?,?)",
                             (ctx.guild.id, reset_type, delay_seconds))
            await db.commit()
    delay_str = f"after {delay_seconds}s" if delay_seconds else "immediately"
    await ctx.send(f"✅ Auto-reset rule: **{reset_type}** will be reset {delay_str} when a member leaves.")


@bot.command(name="removeautoresetrule")
async def cmd_removeautoresetrule(ctx, reset_type: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM auto_reset_rules WHERE guild_id=? AND reset_type=?",
                             (ctx.guild.id, reset_type))
            await db.commit()
    await ctx.send(f"🗑 Removed auto-reset rule for **{reset_type}**.")


@bot.command(name="listautoresetrules")
async def cmd_listautoresetrules(ctx):
    async with get_db() as db:
        async with db.execute("SELECT enabled FROM auto_reset_config WHERE guild_id=?", (ctx.guild.id,)) as cur:
            cfg = await cur.fetchone()
        async with db.execute("SELECT reset_type,delay_seconds FROM auto_reset_rules WHERE guild_id=? ORDER BY reset_type",
                              (ctx.guild.id,)) as cur:
            rules = await cur.fetchall()
    status = "✅ Enabled" if (cfg and cfg[0]) else "🔒 Disabled"
    embed = discord.Embed(title="🔄 Auto-Reset on Leave", color=discord.Color.orange())
    embed.add_field(name="Status", value=status, inline=False)
    if rules:
        lines = [f"• **{rt}** — {'immediately' if ds==0 else f'after {ds}s'}" for rt, ds in rules]
        embed.add_field(name="Rules", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Rules", value="No rules set.", inline=False)
    await ctx.send(embed=embed)


async def auto_reset_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = int(datetime.now(UTC).timestamp())
        async with get_db() as db:
            async with db.execute(
                "SELECT guild_id,user_id,reset_type FROM auto_reset_pending WHERE reset_after<=?", (now,)) as cur:
                pending = await cur.fetchall()
        for gid, uid, reset_type in pending:
            try:
                await _do_reset(gid, uid, reset_type)
                await log_event(gid, "admin", _log_embed("🔄 Auto-Reset Executed", discord.Color.orange(),
                    User=f"<@{uid}>", Type=reset_type))
            except Exception as e:
                print(f"[AutoReset] execute error gid={gid} uid={uid} type={reset_type}: {e}")
                await log_event(gid, "error", _log_embed("❌ AutoReset Error", discord.Color.red(),
                    User=f"<@{uid}>", Type=reset_type, Error=str(e)[:200]))
        if pending:
            async with db_lock:
                async with get_db() as db:
                    await db.execute("DELETE FROM auto_reset_pending WHERE reset_after<=?", (now,))
                    await db.commit()
        await asyncio.sleep(30)

# ═══════════════════════════════════════════════════════
# EXP FROM CHAT
# ═══════════════════════════════════════════════════════

async def _calc_exp_gain(member: discord.Member, channel: discord.TextChannel) -> int:
    base = random.randint(30, 50)
    gid = member.guild.id
    async with get_db() as db:
        async with db.execute(
            "SELECT role_id,boost_percent,channel_id,category_id FROM exp_boosts WHERE guild_id=?",
            (gid,)) as cur:
            boosts = await cur.fetchall()
    role_ids    = {r.id for r in member.roles}
    cat_id      = getattr(channel, "category_id", None)
    total_boost = 0.0
    for role_id, boost_pct, ch_id, cat_id_rule in boosts:
        if role_id not in role_ids: continue
        if ch_id and ch_id != channel.id: continue
        if cat_id_rule and cat_id_rule != cat_id: continue
        total_boost += boost_pct
    result = int(base * (1 + min(total_boost, 100) / 100)) 
    return max(1, min(result, 100)) 


# ═══════════════════════════════════════════════════════
# RESET COMMANDS
# ═══════════════════════════════════════════════════════

_RESET_TYPES = ("balance","exp","inventory","tickets","stats","all")

@bot.command(name="resetuser")
async def cmd_resetuser(ctx, user: discord.Member, reset_type: str = "all"):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if reset_type not in _RESET_TYPES:
        await ctx.send(f"❌ Valid types: {', '.join(_RESET_TYPES)}"); return
    await _do_reset(ctx.guild.id, user.id, reset_type)
    await ctx.send(f"🔄 Reset **{reset_type}** for {user.mention}.")
    await log_event(ctx.guild.id, "admin", _log_embed("🔄 User Reset", discord.Color.orange(),
        Admin=ctx.author.mention, User=user.mention, Type=reset_type))

@bot.command(name="resetrole")
async def cmd_resetrole(ctx, role: discord.Role, reset_type: str = "all"):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if reset_type not in _RESET_TYPES:
        await ctx.send(f"❌ Valid types: {', '.join(_RESET_TYPES)}"); return
    members = [m for m in ctx.guild.members if role in m.roles and not m.bot]
    if not members: await ctx.send(f"❌ No non-bot members with {role.mention}."); return
    async with ctx.typing():
        for m in members:
            await _do_reset(ctx.guild.id, m.id, reset_type)
    await ctx.send(f"🔄 Reset **{reset_type}** for **{len(members)}** member(s) with {role.mention}.")
    await log_event(ctx.guild.id, "admin", _log_embed("🔄 Role Reset", discord.Color.orange(),
        Admin=ctx.author.mention, Role=role.name, Type=reset_type, Count=str(len(members))))

# ═══════════════════════════════════════════════════════
# CLEANUP TRANSFER
# ═══════════════════════════════════════════════════════

@bot.command(name="cleanuptransfer")
async def cmd_cleanuptransfer(ctx):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    gid = ctx.guild.id
    _CLEANUP_TABLES = [
        "balances","exp_history","user_stats","inventory",
        "mega_tickets","mega_bought","giveaway_roles","log_channels",
        "disabled_commands_persist","system_flags","prefix_restrictions",
        "auto_reset_config","auto_reset_rules","auto_reset_pending",
        "verification_config","welcome_config","counting_config","counting_state",
        "counting_bans","counting_prizes","counting_special_prizes",
        "auto_giveaway_config","auto_giveaway_pool","giveaway_game_notify_config",
        "auto_entry_roles","auto_entry_users","auto_entry_threshold",
        "mega_announce_config","mega_info_config","mega_payout_config",
        "chest_channel_config","chest_prizes","rare_chest_config",
        "rare_drop_config","abuse_boxes","abuse_box_prizes","rare_box_config",
        "game_config","stats_channel_config","admin_panel_config",
        "balance_ranks","item_store","daily_key_log",
        "daily_gamble_log","redeem_codes","code_uses",
    ]
    async with ctx.typing():
        async with db_lock:
            async with get_db() as db:
                for tbl in _CLEANUP_TABLES:
                    try:
                        await db.execute(f"DELETE FROM {tbl} WHERE guild_id=?", (gid,))
                    except Exception as e:
                        print(f"[Cleanup] {tbl}: {e}")
                for tbl in ("power_giveaway_config","power_giveaway_role_entries","power_giveaway_channel_rates",
                            "power_giveaway_role_boosts","power_giveaway_user_entries",
                            "games","game_config","game_answers","game_hints"):
                    try:
                        await db.execute(f"DELETE FROM {tbl} WHERE guild_id=?", (gid,))
                    except Exception as e:
                        print(f"[Cleanup] {tbl}: {e}")
                await db.commit()
    await ctx.send(f"🗑 Cleaned up all data for **{ctx.guild.name}** (`{gid}`).")

# ═══════════════════════════════════════════════════════
# TRANSFER (owner-only)
# ═══════════════════════════════════════════════════════

@bot.command(name="transfer")
async def cmd_transfer(ctx, guild_id_from: int, guild_id_to: int):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    guild_from = bot.get_guild(guild_id_from)
    guild_to   = bot.get_guild(guild_id_to)
    if not guild_from: await ctx.send(f"❌ Source guild {guild_id_from} not found."); return
    if not guild_to:   await ctx.send(f"❌ Target guild {guild_id_to} not found."); return

    async with ctx.typing():
        _SIMPLE = [
            "balances","exp_history","user_stats","inventory",
            "mega_tickets","mega_bought","giveaway_roles","log_channels",
            "disabled_commands_persist","system_flags","prefix_restrictions",
            "auto_reset_config","auto_reset_rules","auto_reset_pending",
            "verification_config","welcome_config","counting_config","counting_state",
            "counting_bans","mega_announce_config","mega_info_config","mega_payout_config",
            "chest_channel_config","rare_chest_config","rare_drop_config",
            "game_config","stats_channel_config","admin_panel_config","balance_ranks",
            "item_store","daily_key_log","daily_gamble_log","redeem_codes","code_uses",
            "giveaway_game_notify_config","auto_entry_roles","auto_entry_users",
            "auto_entry_threshold","mega_payout_config","power_giveaway_config",
            "power_giveaway_role_entries","power_giveaway_channel_rates",
            "power_giveaway_role_boosts","power_giveaway_user_entries",
        ]

        # Tables with auto-increment IDs that are referenced by other tables
        _AUTO_INC = {
            "auto_giveaway_pool":   ("auto_giveaway_pool",   None, None),
            "chest_prizes":         ("chest_prizes",          None, None),
            "counting_prizes":      ("counting_prizes",       None, None),
            "counting_special_prizes": ("counting_special_prizes", None, None),
            "abuse_boxes":          None,
            "games":                ("games", None, None),
        }

        async with db_lock:
            async with get_db() as db:
                await db.execute("DELETE FROM auto_giveaway_config WHERE guild_id=?", (guild_id_to,))
                async with db.execute("SELECT channel_id,interval_seconds,duration_seconds,running FROM auto_giveaway_config WHERE guild_id=?",
                                       (guild_id_from,)) as cur:
                    row = await cur.fetchone()
                if row:
                    await db.execute("INSERT OR REPLACE INTO auto_giveaway_config VALUES(?,?,?,?,?)",
                                     (guild_id_to, *row))

                for tbl in _SIMPLE:
                    try:
                        async with db.execute(f"PRAGMA table_info({tbl})") as cur:
                            cols = [r[1] for r in await cur.fetchall()]
                        if "guild_id" not in cols: continue
                        await db.execute(f"DELETE FROM {tbl} WHERE guild_id=?", (guild_id_to,))
                        non_gid = [c for c in cols if c != "guild_id"]
                        col_str = "guild_id," + ",".join(non_gid)
                        sel_str = str(guild_id_to) + "," + ",".join(non_gid)
                        await db.execute(
                            f"INSERT OR IGNORE INTO {tbl}({col_str}) "
                            f"SELECT {sel_str} FROM {tbl} WHERE guild_id=?", (guild_id_from,))
                    except Exception as e:
                        print(f"[Transfer] {tbl}: {e}")

                await db.execute("DELETE FROM games WHERE guild_id=?", (guild_id_to,))
                await db.execute("DELETE FROM game_answers WHERE guild_id=?", (guild_id_to,))
                await db.execute("DELETE FROM game_hints WHERE guild_id=?", (guild_id_to,))
                async with db.execute("SELECT game_name,enabled,reward_balance,reward_exp,reward_tickets,"
                                       "reward_gamble_tokens,reward_vip_keys,reward_item,reward_item_qty,"
                                       "reward_role_id,chance,answer_time FROM games WHERE guild_id=?",
                                       (guild_id_from,)) as cur:
                    games_rows = await cur.fetchall()
                for row in games_rows:
                    await db.execute("INSERT OR IGNORE INTO games(guild_id,game_name,enabled,reward_balance,"
                                     "reward_exp,reward_tickets,reward_gamble_tokens,reward_vip_keys,"
                                     "reward_item,reward_item_qty,reward_role_id,chance,answer_time) "
                                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (guild_id_to, *row))
                    gname = row[0]
                    async with db.execute("SELECT id,answer FROM game_answers WHERE guild_id=? AND game_name=?",
                                           (guild_id_from, gname)) as cur:
                        ans_rows = await cur.fetchall()
                    for old_aid, answer in ans_rows:
                        cur2 = await db.execute("INSERT INTO game_answers(guild_id,game_name,answer) VALUES(?,?,?)",
                                                (guild_id_to, gname, answer))
                        new_aid = cur2.lastrowid
                        async with db.execute("SELECT hint_text,hint_order FROM game_hints "
                                               "WHERE guild_id=? AND game_name=? AND answer_id=?",
                                               (guild_id_from, gname, old_aid)) as cur:
                            hint_rows = await cur.fetchall()
                        for ht, ho in hint_rows:
                            await db.execute("INSERT INTO game_hints(guild_id,game_name,answer_id,hint_text,hint_order) "
                                             "VALUES(?,?,?,?,?)", (guild_id_to, gname, new_aid, ht, ho))

                await db.execute("DELETE FROM abuse_boxes WHERE guild_id=?", (guild_id_to,))
                await db.execute("DELETE FROM abuse_box_prizes WHERE guild_id=?", (guild_id_to,))
                await db.execute("DELETE FROM rare_box_config WHERE guild_id=?", (guild_id_to,))
                async with db.execute("SELECT box_name FROM abuse_boxes WHERE guild_id=?", (guild_id_from,)) as cur:
                    box_rows = await cur.fetchall()
                for (box_name,) in box_rows:
                    await db.execute("INSERT OR IGNORE INTO abuse_boxes VALUES(?,?)", (guild_id_to, box_name))
                    async with db.execute("SELECT id,prize_type,prize_value,prize_amount,chance FROM abuse_box_prizes "
                                           "WHERE guild_id=? AND box_name=?", (guild_id_from, box_name)) as cur:
                        prize_rows = await cur.fetchall()
                    for old_pid, pt, pv, pa, pc in prize_rows:
                        cur2 = await db.execute("INSERT INTO abuse_box_prizes(guild_id,box_name,prize_type,prize_value,prize_amount,chance) "
                                                "VALUES(?,?,?,?,?,?)", (guild_id_to, box_name, pt, pv, pa, pc))
                        new_pid = cur2.lastrowid
                        async with db.execute("SELECT 1 FROM rare_box_config WHERE guild_id=? AND box_name=? AND prize_id=?",
                                               (guild_id_from, box_name, old_pid)) as cur:
                            if await cur.fetchone():
                                await db.execute("INSERT OR IGNORE INTO rare_box_config VALUES(?,?,?)",
                                                 (guild_id_to, box_name, new_pid))

                await db.commit()

    await ctx.send(f"✅ Transferred data from **{guild_from.name}** → **{guild_to.name}**.")

# ═══════════════════════════════════════════════════════
# HELP COMMAND
# ═══════════════════════════════════════════════════════

_HELP_CATS = {
    "economy": ("💰","Economy",[
        ("balance","Check your or another user's balance","/balance [@user]"),
        ("gift","Give your own coins to another user","!gift @user <amount>"),
        ("addbalance / removebalance","Admin: add/remove coins","!addbalance @user <amount>"),
        ("activityrank","Check Activity Rank and EXP","/activityrank [@user]"),
        ("addexp / removeexp","Admin: add/remove usable EXP","!addexp @user <amount>"),
        ("addtotalexp / removetotalexp","Admin: add/remove Total EXP (affects leaderboard rank)","!addtotalexp @user <amount>"),
        ("expboost","Admin: set EXP boost for a role","/expboost <role> <boost%> [channel]"),
        ("removeexpboost","Admin: remove an EXP boost","/removeexpboost <role> [channel]"),
        ("listexpboosts","List all active EXP boosts","/listexpboosts"),
        ("leaderboard","View leaderboards (balance, EXP, tickets, hosted balance…)","/leaderboard <category>"),
        ("addbalancerank","Admin: add a role granted at a balance threshold","/addbalancerank <threshold> @role"),
        ("removebalancerank / listbalanceranks / refreshbalanceranks / checkbalancerank","Manage balance ranks","!listbalanceranks"),
        ("setstatchannel","Admin: post the stats panel","/setstatchannel #channel"),
        ("trade","Open a trade offer with another user","/trade @user"),
        ("item store/buy/use/inv/info/give/take/add/remove","Item store and inventory","!item store"),
    ]),
    "giveaways": ("🎉","Giveaways",[
        ("giveaway","Admin: create a timed giveaway","/giveaway <prize> <seconds> <winners> [rewards…]"),
        ("host","Any user: host a giveaway from your own balance","/host <amount> [winners] [seconds] [prize]"),
        ("reroll","Admin: reroll a giveaway winner","!reroll <message_id>"),
        ("mywinnings","View your giveaway win history","/mywinnings [@user]"),
        ("addgiveawayrole / removegiveawayrole / giveawayroles","Manage who can run giveaways","!addgiveawayrole @role"),
        ("addautogiveaway","Add to the auto giveaway pool","/addautogiveaway <prize> [winners] [chance] [rewards…]"),
        ("removeautogiveaway / listautogiveaways","Manage the auto pool","!listautogiveaways"),
        ("startgiveaways","Start automatic giveaways","/startgiveaways <interval> <duration> [#channel]"),
        ("stopgiveaways","Stop automatic giveaways","!stopgiveaways"),
        ("addautoentryrole","Admin: allow a role to use auto-entry","/addautoentryrole @role [message_req]"),
        ("autoentry","Toggle automatic entry into all giveaways","/autoentry"),
        ("setautoentrythreshold","Admin: set min prize for auto-entry","/setautoentrythreshold <min_bal> <recent_msgs>"),
        ("setnotifychannel","Admin: set notification channel for giveaway/game starts","/setnotifychannel #channel"),
    ]),
    "drops": ("📦","Drops & Raffle",[
        ("chest","Open EXP chest(s)","/chest [amount]  or  !chest [amount]"),
        ("vipchest","Open VIP Chest(s) — needs VIP Key","/vipchest [amount]"),
        ("setchestchannel","Admin: post the chest panel","/setchestchannel #channel"),
        ("addchestprize / removechestprize / listchestprizes","Admin: configure chest prizes","!listchestprizes [chest|vipchest]"),
        ("setraredropchannel","Admin: set rare drop announcement channel","!setraredropchannel #channel"),
        ("buytickets","Buy mega raffle tickets (capped; chat 3+ words to earn unlimited)","/buytickets <amount>"),
        ("megachance","Check your mega raffle win chance","/megachance [@user]"),
        ("setmegachannel / setmegainfochannel","Admin: configure mega raffle channels","!setmegachannel #channel"),
        ("setmegapayout","Admin: configure payout formula (total vs bought)","/setmegapayout <mode> <multiplier> [winners]"),
        ("checkmegahistory","View recent mega raffle draw history","!checkmegahistory"),
        ("openbox","Open an abuse box from your inventory","/openbox <box> [amount]"),
        ("addbox / removebox / addboxprize / removeboxprize / listboxes / givebox","Admin: manage abuse boxes","!listboxes"),
        ("addrarechestdrop / removerarechestdrop","Admin: mark a prize as rare","!addrarechestdrop chest <name>"),
    ]),
    "powergiveaway": ("🔥","Power Giveaways",[
        ("powergiveaway setup","Create/update a named recurring giveaway (run multiple at once)","/powergiveaway setup <name> <prize> <winners> <interval> <embed_ch> <winners_ch>"),
        ("powergiveaway setrole/removerole","Set fixed entries for a role per named giveaway","/powergiveaway setrole <name> @role <entries>"),
        ("powergiveaway setchannel/removechannel","Set chat entries per message per named giveaway","/powergiveaway setchannel <name> #channel <entries>"),
        ("powergiveaway setboost/removeboost","Set a chat-entry multiplier for a role","/powergiveaway setboost <name> @role <multiplier>"),
        ("powergiveaway start/stop","Start or stop a named giveaway","/powergiveaway start <name>"),
        ("powergiveaway status/list/delete","View or manage named giveaways","/powergiveaway list"),
    ]),
    "games": ("🎮","Random Games",[
        ("addgame","Add a game/question to the pool","/addgame <name> [rewards…] [chance] [answer_time]"),
        ("removegame / enablegame / disablegame","Manage games","!removegame <name>"),
        ("addgameanswer / removegameanswer","Add/remove answers for a game","!addgameanswer <game> <answer>"),
        ("addgamepreset","Bulk-load a preset (colors, food, countries…)","/addgamepreset <game> <preset>"),
        ("listgames","List all games or answers for a specific game","!listgames [name]"),
        ("addhint / removehint / listhints","Manage answer hints","!addhint <game> <answer_id> <order 1-5> <hint>"),
        ("setgamechannel","Set the channel and interval for games","/setgamechannel #channel [interval] [hint1] [hint2] [hint3]"),
        ("startgames / stopgames","Start/stop the random games loop","!startgames"),
    ]),
    "codes": ("🎟","Redeem Codes",[
        ("createcode","Admin: create a redeem code","/createcode <code> <prize> [uses] [min_level] [min_balance] [required_role]"),
        ("deletecode / listcodes","Admin: delete or list codes","!listcodes"),
        ("redeem","Redeem a code for a reward","/redeem <code>"),
    ]),
    "admin": ("⚙️","Admin & Systems",[
        ("setadminpanel","Post the admin panel (balance/EXP/reset buttons)","/setadminpanel #channel"),
        ("disablecmd / enablecmd / listcmds","Disable or re-enable commands in this server","!disablecmd <name>"),
        ("enablesystem / disablesystem / systemstatus","Toggle systems (mega, vipkey, gamble)","/enablesystem <system>"),
        ("setlogchannel / removelogchannel / listlogchannels","Configure log channels","/setlogchannel <type> #channel"),
        ("disableprefixchannel / enableprefixchannel / resetprefixchannel / listprefixchannels","Control prefix usage per channel","!disableprefixchannel #channel [@role]"),
        ("enableautoreset / disableautoreset","Toggle auto-reset when a member leaves","!enableautoreset"),
        ("setautoresetrule / removeautoresetrule / listautoresetrules","Configure what resets and when","!setautoresetrule balance 0"),
        ("resetuser / resetrole","Manually reset a user's or role's data","!resetuser @user [type]"),
        ("cleanuptransfer","Owner: wipe all data for this server","!cleanuptransfer"),
        ("transfer","Owner: migrate all server data to another server","!transfer <from_id> <to_id>"),
        ("setverification","Post a verification button panel","/setverification #channel [message] [@verified_role] [@unverified_role]"),
        ("setwelcome / disablewelcome","Set/disable DM welcome message","!setwelcome <message>"),
        ("setwelcomechannel / disablewelcomechannel","Set/disable channel welcome message","!setwelcomechannel #channel <message>"),
    ]),
}

@bot.tree.command(name="help", description="Browse all available commands")
@app_commands.describe(category="Filter by category")
@app_commands.choices(category=[
    app_commands.Choice(name=f"{v[0]} {v[1]}", value=k) for k, v in _HELP_CATS.items()
])
async def help_cmd(interaction: discord.Interaction, category: str = None):
    p = common._BOT_PREFIX
    if category and category in _HELP_CATS:
        emoji, title, cmds = _HELP_CATS[category]
        embed = discord.Embed(title=f"{emoji} {title} Commands", color=discord.Color.blurple())
        for name, desc, usage in cmds:
            embed.add_field(name=f"`{name}`", value=f"{desc}\n`{usage}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(title="📋 Help — All Categories",
            description=f"Use `/help <category>` or `{p}help <category>` to view commands.\nPrefix: `{p}`",
            color=discord.Color.blurple())
        for key, (emoji, title, cmds) in _HELP_CATS.items():
            embed.add_field(name=f"{emoji} **{title}**", value=f"`{p}help {key}` — {len(cmds)} command(s)", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name="help")
async def pfx_help(ctx, category: str = None):
    p = common._BOT_PREFIX
    if category and category.lower() in _HELP_CATS:
        emoji, title, cmds = _HELP_CATS[category.lower()]
        embed = discord.Embed(title=f"{emoji} {title} Commands", color=discord.Color.blurple())
        for name, desc, usage in cmds:
            embed.add_field(name=f"`{name}`", value=f"{desc}\n`{usage}`", inline=False)
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="📋 Help — All Categories",
            description=f"Use `{p}help <category>` to view specific commands.\nPrefix: `{p}`",
            color=discord.Color.blurple())
        for key, (emoji, title, cmds) in _HELP_CATS.items():
            embed.add_field(name=f"{emoji} **{title}**", value=f"`{p}help {key}` — {len(cmds)} command(s)", inline=True)
        await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════
# OWNER COMMANDS
# ═══════════════════════════════════════════════════════

@bot.command(name="setprefix")
async def cmd_setprefix(ctx, new_prefix: str):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    await set_prefix(new_prefix)
    await ctx.send(f"✅ Prefix updated to `{new_prefix}`.")

@bot.command(name="gstatus")
async def cmd_gstatus(ctx):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    lines = [f"• **{g.name}** (`{g.id}`) — {g.member_count} members" for g in bot.guilds]
    embed = discord.Embed(title=f"🌐 Guilds ({len(bot.guilds)})",
        description="\n".join(lines) or "None", color=discord.Color.blurple())
    await ctx.send(embed=embed)

@bot.command(name="gdisablecmd")
async def cmd_gdisablecmd(ctx, *, cmd_name: str):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    cmd_name = cmd_name.strip().lower()
    global_disabled_commands.add(cmd_name)
    async with db_lock:
        async with get_db() as db:
            try:
                await db.execute("INSERT INTO global_disabled_commands VALUES(?)", (cmd_name,))
                await db.commit()
            except aiosqlite.IntegrityError:
                pass
    await ctx.send(f"🔒 **{cmd_name}** disabled globally.")

@bot.command(name="genablecmd")
async def cmd_genablecmd(ctx, *, cmd_name: str):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    cmd_name = cmd_name.strip().lower()
    global_disabled_commands.discard(cmd_name)
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM global_disabled_commands WHERE command_name=?", (cmd_name,))
            await db.commit()
    await ctx.send(f"✅ **{cmd_name}** re-enabled globally.")

@bot.command(name="gdisablesystem")
async def cmd_gdisablesystem(ctx, system: str):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO global_system_flags VALUES(?,0)", (system,))
            await db.commit()
    await ctx.send(f"🔒 **{system}** disabled globally.")

@bot.command(name="genablesystem")
async def cmd_genablesystem(ctx, system: str):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO global_system_flags VALUES(?,1)", (system,))
            await db.commit()
    await ctx.send(f"✅ **{system}** enabled globally.")

@bot.command(name="gcreate")
async def cmd_gcreate(ctx, code: str, prize_json: str, uses: int = -1, min_level: int = 0):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    code = code.strip().upper()
    prize = _parse_prize_json(prize_json)
    if not prize: await ctx.send("❌ Invalid prize format."); return
    async with db_lock:
        async with get_db() as db:
            try:
                await db.execute("INSERT INTO global_redeem_codes VALUES(?,?,?,?,0)",
                                 (code, json.dumps(prize), uses, min_level))
                await db.commit()
            except aiosqlite.IntegrityError:
                await ctx.send(f"❌ Global code **{code}** already exists."); return
    uses_str = "unlimited" if uses == -1 else str(uses)
    await ctx.send(f"✅ Global code **{code}** created ({uses_str} uses). Prize: {_prize_summary(prize)}")

@bot.command(name="gdelete")
async def cmd_gdelete(ctx, code: str):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    code = code.strip().upper()
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM global_redeem_codes WHERE code=?", (code,))
            await db.execute("DELETE FROM global_code_uses WHERE code=?", (code,))
            await db.commit()
    await ctx.send(f"🗑 Global code **{code}** deleted.")

@bot.command(name="gcodes")
async def cmd_gcodes(ctx):
    if ctx.author.id != BOT_OWNER_ID: await ctx.send("❌ Owner only."); return
    async with get_db() as db:
        async with db.execute("SELECT code,prize_json,uses_left,min_level FROM global_redeem_codes ORDER BY code") as cur:
            codes = await cur.fetchall()
    if not codes: await ctx.send("❌ No global codes."); return
    lines = []
    for code, pj, uses, ml in codes:
        try: prize = json.loads(pj); ps = _prize_summary(prize)
        except Exception: ps = str(pj)
        uses_str = "∞" if uses == -1 else str(uses)
        req = f" [Lvl{ml}+]" if ml else ""
        lines.append(f"**{code}** ({uses_str}){req} — {ps[:60]}")
    embed = discord.Embed(title="🎟 Global Codes", description="\n".join(lines), color=discord.Color.green())
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════
# CORE EVENTS
# ═══════════════════════════════════════════════════════

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if not message.guild: return

    if isinstance(message.author, discord.Member) and not message.author.bot:
        gid, uid = message.guild.id, message.author.id
        try:
            exp_gain = await _calc_exp_gain(message.author, message.channel)
            await add_exp(gid, uid, exp_gain)
        except Exception as e:
            print(f"[EXP] {e}")
        bump_msg_count(gid, uid)

    if message.content.startswith(common._BOT_PREFIX) and not _prefix_channel_allowed(message):
        return
    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not payload.guild_id: return
    guild = bot.get_guild(payload.guild_id)
    if not guild: return

    if payload.user_id == COUNTING_BOT_ID and str(payload.emoji) in _COUNTING_FAIL_EMOJI:
        async with get_db() as db:
            async with db.execute("SELECT channel_id FROM counting_config WHERE guild_id=? AND enabled=1",
                                  (payload.guild_id,)) as cur:
                cfg = await cur.fetchone()
        if cfg and cfg[0] == payload.channel_id:
            async with db_lock:
                async with get_db() as db:
                    await db.execute("UPDATE counting_state SET current_count=0,last_user_id=0 WHERE guild_id=?",
                                     (payload.guild_id,))
                    await db.commit()
            ch = guild.get_channel(payload.channel_id)
            if ch:
                try: await ch.send("🔄 Counting bot marked that as wrong — count reset to **0**.")
                except Exception: pass


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot: return
    gid = member.guild.id

    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM auto_reset_pending WHERE guild_id=? AND user_id=?",
                             (gid, member.id))
            await db.commit()

    async with get_db() as db:
        async with db.execute("SELECT unverified_role_id FROM verification_config WHERE guild_id=?", (gid,)) as cur:
            vcfg = await cur.fetchone()
    if vcfg and vcfg[0]:
        role = member.guild.get_role(vcfg[0])
        if role:
            try: await member.add_roles(role)
            except Exception: pass

    async with get_db() as db:
        async with db.execute("SELECT enabled,message,channel_id,channel_enabled,channel_message FROM welcome_config WHERE guild_id=?",
                              (gid,)) as cur:
            wcfg = await cur.fetchone()
    if wcfg:
        enabled, dm_msg, ch_id, ch_enabled, ch_msg = wcfg
        if enabled and dm_msg:
            text = dm_msg.replace("{user}", member.mention).replace("{server}", member.guild.name)
            try: await member.send(text)
            except Exception: pass
        if ch_enabled and ch_id and ch_msg:
            ch = member.guild.get_channel(ch_id)
            if ch:
                text = ch_msg.replace("{user}", member.mention).replace("{server}", member.guild.name)
                try: await ch.send(text)
                except Exception: pass


@bot.event
async def on_member_remove(member: discord.Member):
    if member.bot: return
    gid = member.guild.id
    async with get_db() as db:
        async with db.execute("SELECT enabled FROM auto_reset_config WHERE guild_id=?", (gid,)) as cur:
            cfg = await cur.fetchone()
    if not cfg or not cfg[0]: return
    async with get_db() as db:
        async with db.execute("SELECT reset_type,delay_seconds FROM auto_reset_rules WHERE guild_id=?", (gid,)) as cur:
            rules = await cur.fetchall()
    if not rules: return
    now = int(datetime.now(UTC).timestamp())
    async with db_lock:
        async with get_db() as db:
            for reset_type, delay_seconds in rules:
                reset_after = now + delay_seconds
                await db.execute(
                    "INSERT OR REPLACE INTO auto_reset_pending(guild_id,user_id,reset_type,reset_after) VALUES(?,?,?,?)",
                    (gid, member.id, reset_type, reset_after))
            await db.commit()


@bot.event
async def on_ready():
    await setup_database()
    await common._load_prefix()
    await load_disabled_commands()
    await load_prefix_restrictions()
    bot.add_view(AdminPanelView())
    bot.add_view(TicketPanelView())
    bot.add_view(CloseTicketView())
    bot.add_view(TradeInitialView())
    bot.add_view(TradeActiveView())

    _guild = discord.Object(id=_GUILD_ID)
    bot.tree.copy_global_to(guild=_guild)
    try:
        synced = await bot.tree.sync(guild=_guild)
        print(f"[Admin Bot] Synced {len(synced)} commands to guild. Logged in as {bot.user}")
    except Exception as e:
        print(f"[Admin Bot] Guild sync failed: {e}")
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    for task_fn in [auto_reset_loop, host_role_loop,
                    lambda: msg_count_flush_loop(bot)]:
        bot.loop.create_task(task_fn())


@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    except discord.HTTPException as e:
        print(f"[Admin Sync] Failed on join: {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure): return
    raise error


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument: {error}")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        raise error


@bot.before_invoke
async def _log_prefix_command(ctx: commands.Context):
    if not ctx.guild: return
    embed = discord.Embed(
        description=f"{ctx.author.mention} used **`{common._BOT_PREFIX}{ctx.command.qualified_name}`**",
        color=discord.Color.light_grey(), timestamp=datetime.now(UTC))
    embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"#{getattr(ctx.channel,'name','DM')} | UID: {ctx.author.id}")
    await log_event(ctx.guild.id, "command", embed)

# ── System status ─────────────────────────────────────────────────────────────

@bot.tree.command(name="systemstatus",
                  description="Show which systems are enabled or disabled in this server")
@command_enabled()
async def slash_systemstatus(interaction: discord.Interaction):
    gid = interaction.guild.id
    embed = discord.Embed(title="⚙️ System Status", color=discord.Color.blurple())
    for key, label in _SYSTEM_LABELS.items():
        enabled = await is_system_enabled(gid, key)
        embed.add_field(name=label,
                        value="✅ Enabled" if enabled else "🔒 Disabled",
                        inline=True)
    await interaction.response.send_message(embed=embed)


# ── Prefix channel restrictions ───────────────────────────────────────────────

@bot.tree.command(name="disableprefixchannel",
                  description="Block prefix commands in a channel (optionally for a specific role only)")
@app_commands.describe(channel="Channel to restrict",
                       role="Only block this role (leave blank to block everyone)")
@command_enabled()
async def slash_disableprefixchannel(interaction: discord.Interaction,
                                     channel: discord.TextChannel,
                                     role: discord.Role = None):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    rid = role.id if role else 0
    key = (interaction.guild.id, channel.id)
    prefix_channel_rules.setdefault(key, {})[rid] = False
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO prefix_restrictions VALUES(?,?,?,?)",
                             (interaction.guild.id, channel.id, rid, 0))
            await db.commit()
    scope = f" for {role.mention}" if role else " for everyone"
    await interaction.response.send_message(
        f"🔒 Prefix commands disabled in {channel.mention}{scope}.")


@bot.tree.command(name="enableprefixchannel",
                  description="Allow a role to use prefix commands in a restricted channel")
@app_commands.describe(channel="Channel to update",
                       role="Role to allow (leave blank to allow everyone)")
@command_enabled()
async def slash_enableprefixchannel(interaction: discord.Interaction,
                                    channel: discord.TextChannel,
                                    role: discord.Role = None):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    rid = role.id if role else 0
    key = (interaction.guild.id, channel.id)
    prefix_channel_rules.setdefault(key, {})[rid] = True
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO prefix_restrictions VALUES(?,?,?,?)",
                             (interaction.guild.id, channel.id, rid, 1))
            await db.commit()
    scope = f" for {role.mention}" if role else " for everyone"
    await interaction.response.send_message(
        f"✅ Prefix commands allowed in {channel.mention}{scope}.")


@bot.tree.command(name="resetprefixchannel",
                  description="Clear all prefix restrictions for a channel")
@app_commands.describe(channel="Channel to clear restrictions for")
@command_enabled()
async def slash_resetprefixchannel(interaction: discord.Interaction,
                                   channel: discord.TextChannel):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    key = (interaction.guild.id, channel.id)
    prefix_channel_rules.pop(key, None)
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM prefix_restrictions WHERE guild_id=? AND channel_id=?",
                (interaction.guild.id, channel.id))
            await db.commit()
    await interaction.response.send_message(
        f"✅ Prefix restrictions cleared for {channel.mention}.")


@bot.tree.command(name="listprefixchannels",
                  description="List all channels with prefix command restrictions")
@command_enabled()
async def slash_listprefixchannels(interaction: discord.Interaction):
    async with get_db() as db:
        async with db.execute(
            "SELECT channel_id,role_id,allowed FROM prefix_restrictions WHERE guild_id=?",
            (interaction.guild.id,)) as cur:
            rows = await cur.fetchall()
    if not rows:
        await interaction.response.send_message("❌ No prefix restrictions set.", ephemeral=True); return
    lines = []
    for cid, rid, allowed in rows:
        ch      = interaction.guild.get_channel(cid)
        ch_str  = ch.mention if ch else f"<#{cid}>"
        r       = interaction.guild.get_role(rid) if rid else None
        role_str = f"{r.mention}" if r else "everyone"
        status   = "✅ Allowed" if allowed else "🔒 Blocked"
        lines.append(f"• {ch_str} | {role_str} → {status}")
    pages = paginate_lines(lines, "🔒 Prefix Restrictions", discord.Color.orange())
    view  = EmbedPaginator(pages, interaction.user.id) if len(pages) > 1 else None
    await interaction.response.send_message(embed=pages[0], view=view)


# ── setprefix ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="setprefix",
                  description="Owner only: change the bot command prefix")
@app_commands.describe(new_prefix="The new prefix character(s)")
@command_enabled()
async def slash_setprefix(interaction: discord.Interaction, new_prefix: str):
    if interaction.user.id != BOT_OWNER_ID:
        await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    await set_prefix(new_prefix)
    await interaction.response.send_message(f"✅ Prefix updated to `{new_prefix}`.", ephemeral=True)


# ── Auto-reset commands ───────────────────────────────────────────────────────

@bot.tree.command(name="enableautoreset",
                  description="Enable automatic data-reset when a member leaves")
@command_enabled()
async def slash_enableautoreset(interaction: discord.Interaction):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO auto_reset_config VALUES(?,1)",
                             (interaction.guild.id,))
            await db.commit()
    await interaction.response.send_message("✅ Auto-reset on leave enabled.")


@bot.tree.command(name="disableautoreset",
                  description="Disable automatic data-reset when a member leaves")
@command_enabled()
async def slash_disableautoreset(interaction: discord.Interaction):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "UPDATE auto_reset_config SET enabled=0 WHERE guild_id=?",
                (interaction.guild.id,))
            await db.commit()
    await interaction.response.send_message("🔒 Auto-reset on leave disabled.")


_RESET_TYPE_CHOICES = [
    app_commands.Choice(name=t.title(), value=t)
    for t in ("balance","exp","inventory","tickets","stats","all")
]

@bot.tree.command(name="setautoresetrule",
                  description="Set what gets reset (and when) after a member leaves")
@app_commands.describe(
    reset_type="What to reset",
    delay_seconds="Seconds after leaving before the reset fires (0 = immediately)")
@app_commands.choices(reset_type=_RESET_TYPE_CHOICES)
@command_enabled()
async def slash_setautoresetrule(interaction: discord.Interaction,
                                 reset_type: str, delay_seconds: int = 0):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if delay_seconds < 0:
        await interaction.response.send_message("❌ Delay must be ≥ 0.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO auto_reset_rules VALUES(?,?,?)",
                             (interaction.guild.id, reset_type, delay_seconds))
            await db.commit()
    delay_str = f"after {delay_seconds}s" if delay_seconds else "immediately"
    await interaction.response.send_message(
        f"✅ **{reset_type}** will be reset {delay_str} when a member leaves.")


@bot.tree.command(name="removeautoresetrule",
                  description="Remove an auto-reset rule")
@app_commands.describe(reset_type="Which rule to remove")
@app_commands.choices(reset_type=_RESET_TYPE_CHOICES)
@command_enabled()
async def slash_removeautoresetrule(interaction: discord.Interaction, reset_type: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM auto_reset_rules WHERE guild_id=? AND reset_type=?",
                (interaction.guild.id, reset_type))
            await db.commit()
    await interaction.response.send_message(f"🗑 Removed auto-reset rule for **{reset_type}**.")


@bot.tree.command(name="listautoresetrules",
                  description="Show the current auto-reset configuration")
@command_enabled()
async def slash_listautoresetrules(interaction: discord.Interaction):
    async with get_db() as db:
        async with db.execute(
            "SELECT enabled FROM auto_reset_config WHERE guild_id=?",
            (interaction.guild.id,)) as cur:
            cfg = await cur.fetchone()
        async with db.execute(
            "SELECT reset_type,delay_seconds FROM auto_reset_rules "
            "WHERE guild_id=? ORDER BY reset_type", (interaction.guild.id,)) as cur:
            rules = await cur.fetchall()
    status = "✅ Enabled" if (cfg and cfg[0]) else "🔒 Disabled"
    embed  = discord.Embed(title="🔄 Auto-Reset on Leave", color=discord.Color.orange())
    embed.add_field(name="Status", value=status, inline=False)
    if rules:
        lines = [f"• **{rt}** — {'immediately' if ds == 0 else f'after {ds}s'}"
                 for rt, ds in rules]
        embed.add_field(name="Rules", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Rules", value="No rules configured.", inline=False)
    await interaction.response.send_message(embed=embed)


# ── Reset user / role ─────────────────────────────────────────────────────────

@bot.tree.command(name="resetuser",
                  description="Admin: manually reset a user's data")
@app_commands.describe(user="User to reset",
                       reset_type="What to reset (default: all)")
@app_commands.choices(reset_type=_RESET_TYPE_CHOICES)
@command_enabled()
async def slash_resetuser(interaction: discord.Interaction,
                          user: discord.Member, reset_type: str = "all"):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await _do_reset(interaction.guild.id, user.id, reset_type)
    await interaction.response.send_message(
        f"🔄 Reset **{reset_type}** for {user.mention}.")
    await log_event(interaction.guild.id, "admin", _log_embed(
        "🔄 User Reset", discord.Color.orange(),
        Admin=interaction.user.mention, User=user.mention, Type=reset_type))


@bot.tree.command(name="resetrole",
                  description="Admin: reset data for all members with a role")
@app_commands.describe(role="Role whose members will be reset",
                       reset_type="What to reset (default: all)")
@app_commands.choices(reset_type=_RESET_TYPE_CHOICES)
@command_enabled()
async def slash_resetrole(interaction: discord.Interaction,
                          role: discord.Role, reset_type: str = "all"):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await interaction.response.defer()
    members = [m for m in interaction.guild.members if role in m.roles and not m.bot]
    if not members:
        await interaction.followup.send(f"❌ No non-bot members with {role.mention}."); return
    for m in members:
        await _do_reset(interaction.guild.id, m.id, reset_type)
    await interaction.followup.send(
        f"🔄 Reset **{reset_type}** for **{len(members)}** member(s) with {role.mention}.")
    await log_event(interaction.guild.id, "admin", _log_embed(
        "🔄 Role Reset", discord.Color.orange(),
        Admin=interaction.user.mention, Role=role.name,
        Type=reset_type, Count=str(len(members))))


STAFF_ROLE_ID = 1541906011802050733
TRANSCRIPT_CHANNEL_ID = 1540713749265256599

active_tickets = set()

trade_states = {}


class TradeState:
    def __init__(self, creator_id: int, target_id: int):
        self.creator_id = creator_id
        self.target_id = target_id
        self.depositor_id = None
        self.deposited_amount = 0
        self.cancel_votes = set()
        self.deposit_task = None
        self.release_task = None


async def generate_and_send_transcript(channel: discord.TextChannel, guild: discord.Guild):
    messages = [msg async for msg in channel.history(limit=100, oldest_first=True)]
    transcript_text = "\n".join(
        [f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}" for msg in messages]
    )
    
    transcript_file = discord.File(
        fp=io.BytesIO(transcript_text.encode("utf-8")),
        filename=f"{channel.name}-transcript.txt"
    )

    transcript_channel = guild.get_channel(TRANSCRIPT_CHANNEL_ID)
    if transcript_channel:
        await transcript_channel.send(
            content=f"Transcript for ticket *{channel.name}* (User ID: {channel.topic}):",
            file=transcript_file
        )


async def close_ticket_process(channel: discord.TextChannel, guild: discord.Guild, bot: commands.Bot):
    state = trade_states.get(channel.id)
    if state and state.depositor_id and state.deposited_amount > 0:
        await add_balance(guild.id, state.depositor_id, state.deposited_amount, bot=bot)
        await channel.send(f"Returned *{state.deposited_amount:,}* gems to <@{state.depositor_id}>.")

    if state:
        if state.deposit_task:
            state.deposit_task.cancel()
        if state.release_task:
            state.release_task.cancel()
        trade_states.pop(channel.id, None)

    await generate_and_send_transcript(channel, guild)

    if channel.topic and channel.topic.isdigit():
        active_tickets.discard(int(channel.topic))

    await channel.delete()


async def start_deposit_timer(channel: discord.TextChannel, bot: commands.Bot):
    await asyncio.sleep(300)
    state = trade_states.get(channel.id)
    if state and state.deposited_amount == 0:
        await channel.send("No gems were deposited within 5 minutes. Closing ticket automatically...")
        await close_ticket_process(channel, channel.guild, bot)


async def start_release_timer(channel: discord.TextChannel, bot: commands.Bot):
    await asyncio.sleep(600)
    state = trade_states.get(channel.id)
    if state and state.deposited_amount > 0:
        await channel.send("Gems were not released within 10 minutes. Returning gems to depositor and closing ticket...")
        await close_ticket_process(channel, channel.guild, bot)


async def create_ticket_channel(guild: discord.Guild, user: discord.Member, ticket_type: str, extra_member: discord.Member = None):
    staff_role = guild.get_role(STAFF_ROLE_ID)
    
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    
    if extra_member:
        overwrites[extra_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    return await guild.create_text_channel(
        name=f"{ticket_type}-{user.name}",
        topic=str(user.id),
        overwrites=overwrites
    )


class ReportModal(discord.ui.Modal, title="Report User"):
    reported_user_id = discord.ui.TextInput(
        label="Who are you willing to report (User ID)?",
        placeholder="Enter Discord User ID",
        required=True,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        guild = interaction.guild
        
        try:
            target_id = int(self.reported_user_id.value)
        if target_id == user.id:
            return await interaction.followup.send("you cannot report youself!", ephemeral=True)
        
        except ValueError:
            return await interaction.followup.send("Invalid User ID or user is not in this server.", ephemeral=True)
                
            
       
        channel = await create_ticket_channel(guild, user, "report")
        active_tickets.add(user.id)

        ping_msg = await channel.send(f"<@{user.id}> <@&{STAFF_ROLE_ID}>")
        await ping_msg.delete()

        embed = discord.Embed(
            title="Report Ticket Opened",
            description="Please provide proof and details regarding your report.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed, view=CloseTicketView())
        await channel.send(f"Reported user: <@{self.reported_user_id.value}> ({self.reported_user_id.value})")

        await interaction.followup.send(f"Report ticket created: {channel.mention}", ephemeral=True)


class TradeModal(discord.ui.Modal, title="Trade User"):
    trader_user_id = discord.ui.TextInput(
        label="Who are you willing to trade (User ID)?",
        placeholder="Enter Discord User ID",
        required=True,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        guild = interaction.guild

        try:
            target_id = int(self.trader_user_id.value)
            if target_id == user.id:
                return await interaction.followup.send("you cannot trade youself!", ephemeral=True)
                
            target_member = await guild.fetch_member(target_id)
        except Exception:
            return await interaction.followup.send("Invalid User ID or user is not in this server.", ephemeral=True)

        channel = await create_ticket_channel(guild, user, "trade", extra_member=target_member)
        active_tickets.add(user.id)

        state = TradeState(user.id, target_id)
        trade_states[channel.id] = state

        ping_msg = await channel.send(f"<@{user.id}> <@{target_id}>")
        await ping_msg.delete()

        embed = discord.Embed(
            title="🛡️ Secure Trade Started",
            description=(
                "Gems must be deposited by the buyer. They can be deposited by clicking the button below. "
                "Once a deposit is made, the trade can *continue safely*\n\n"
                "Provides *100% Protection* 💯"
            ),
            color=discord.Color.blue()
        )
        await channel.send(embed=embed, view=TradeInitialView())
        await interaction.followup.send(f"Trade ticket created: {channel.mention}", ephemeral=True)

        state.deposit_task = asyncio.create_task(start_deposit_timer(channel, interaction.client))


class DepositAmountModal(discord.ui.Modal, title="Deposit Gems"):
    amount_input = discord.ui.TextInput(
        label="Amount of Gems to Deposit",
        placeholder="Enter amount",
        required=True,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        channel = interaction.channel
        state = trade_states.get(channel.id)

        if not state:
            return await interaction.followup.send("Trade state error.", ephemeral=True)

        try:
            amount = int(self.amount_input.value)
            if amount <= 0:
                raise ValueError()
        except ValueError:
            return await interaction.followup.send("Please enter a valid positive integer.", ephemeral=True)

        user_balance = await get_balance(guild.id, user.id)
        if user_balance < amount:
            return await interaction.followup.send(f"You don't have enough gems!", ephemeral=True)

        await add_balance(guild.id, user.id, -amount, bot=interaction.client)

        state.depositor_id = user.id
        state.deposited_amount = amount

        if state.deposit_task:
            state.deposit_task.cancel()

        embed = discord.Embed(
            title="🛡️ Trade Deposit Active",
            description=(
                f"*{user.mention}* has deposited *{amount:,} gems*!\n\n"
                "Click *Release Balance* to give the gems to the other user, or *Escalate* if you need staff support."
            ),
            color=discord.Color.purple()
        )
        await channel.send(embed=embed, view=TradeActiveView())
        await interaction.followup.send(f"Successfully deposited {amount:,} gems!", ephemeral=True)

        state.release_task = asyncio.create_task(start_release_timer(channel, interaction.client))


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)

        is_staff = staff_role in interaction.user.roles if staff_role else False
        is_owner = str(interaction.user.id) == channel.topic

        if not is_staff and not is_owner:
            return await interaction.response.send_message("Only staff or the ticket creator can close this ticket.", ephemeral=True)

        await interaction.response.send_message("Closing ticket...")
        await close_ticket_process(channel, interaction.guild, interaction.client)


class TradeInitialView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Deposit", emoji="💎", style=discord.ButtonStyle.primary, custom_id="trade_deposit")
    async def deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        state = trade_states.get(channel.id)

        if not state:
            return await interaction.response.send_message("Trade session lost.", ephemeral=True)

        if interaction.user.id not in [state.creator_id, state.target_id]:
            return await interaction.response.send_message("You are not part of this trade.", ephemeral=True)

        if state.depositor_id is not None:
            return await interaction.response.send_message("Gems have already been deposited!", ephemeral=True)

        await interaction.response.send_modal(DepositAmountModal())


class TradeActiveView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Release Balance", emoji="🔓", style=discord.ButtonStyle.success, custom_id="trade_release")
    async def release(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        state = trade_states.get(channel.id)

        if not state or state.deposited_amount == 0:
            return await interaction.response.send_message("No gems available to release.", ephemeral=True)

        if interaction.user.id != state.depositor_id:
            return await interaction.response.send_message("Only the depositor can release the balance!", ephemeral=True)

        receiver_id = state.target_id if state.depositor_id == state.creator_id else state.creator_id

        await add_balance(interaction.guild.id, receiver_id, state.deposited_amount, bot=interaction.client)

        await interaction.response.send_message(
            f"Released *{state.deposited_amount:,} gems* to <@{receiver_id}>! Trade completed successfully. Closing ticket..."
        )
        
        state.deposited_amount = 0
        await close_ticket_process(channel, interaction.guild, interaction.client)

    @discord.ui.button(label="Escalate", emoji="⚠️", style=discord.ButtonStyle.danger, custom_id="trade_escalate")
    async def escalate(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        state = trade_states.get(channel.id)

        if not state or interaction.user.id not in [state.creator_id, state.target_id]:
            return await interaction.response.send_message("Only trade participants can escalate.", ephemeral=True)

        await channel.send(f"<@&{STAFF_ROLE_ID}> Trade escalated by {interaction.user.mention}! Staff assist needed.")
        await interaction.response.send_message("Staff have been notified.", ephemeral=True)

    @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.danger, custom_id="trade_cancel_active")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        state = trade_states.get(channel.id)

        if not state or interaction.user.id not in [state.creator_id, state.target_id]:
            return await interaction.response.send_message("Only trade participants can cancel.", ephemeral=True)

        state.cancel_votes.add(interaction.user.id)

        if len(state.cancel_votes) >= 2:
            await interaction.response.send_message("Both parties agreed to cancel the trade. Returning gems and closing ticket...")
            await close_ticket_process(channel, interaction.guild, interaction.client)
        else:
            await interaction.response.send_message(
                f"{interaction.user.mention} voted to cancel the trade. Waiting for the second party to click Cancel Trade."
            )


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Help", emoji="📩", style=discord.ButtonStyle.success, custom_id="panel_help")
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in active_tickets:
            return await interaction.response.send_message("You already have an open ticket!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        guild = interaction.guild

        active_tickets.add(user.id)
        channel = await create_ticket_channel(guild, user, "help")

        ping_msg = await channel.send(f"<@{user.id}> <@&{STAFF_ROLE_ID}>")
        await ping_msg.delete()

        embed = discord.Embed(
            title="Help Ticket Created",
            description="Please describe your issue below. Staff will assist you shortly.",
            color=discord.Color.green()
        )
        await channel.send(embed=embed, view=CloseTicketView())
        await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

    @discord.ui.button(label="Trade", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="panel_trade")
    async def trade_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in active_tickets:
            return await interaction.response.send_message("You already have an open ticket!", ephemeral=True)

        await interaction.response.send_modal(TradeModal())

    @discord.ui.button(label="Report", emoji="👮", style=discord.ButtonStyle.danger, custom_id="panel_report")
    async def report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in active_tickets:
            return await interaction.response.send_message("You already have an open ticket!", ephemeral=True)

        await interaction.response.send_modal(ReportModal())



@app_commands.command(name="setup", description="Post the ticket creation panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    embed = discord.Embed(
        description=(
            "**SUPPORT TICKETS**\n\n"
            "Open a support ticket to report, trade or if you need help!\n\n"
            "Staff will respond always!"
        ),
        color=discord.Color.from_rgb(138, 43, 226)
    )
    await interaction.response.send_message("Panel posted successfully!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=TicketPanelView())


@app_commands.command(name="add", description="Add a user to the current ticket channel")
@app_commands.describe(member="The member to add to the ticket")
async def add(interaction: discord.Interaction, member: discord.Member):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    await interaction.response.send_message(f"Added {member.mention} to the ticket.")


@app_commands.command(name="remove", description="Remove a user from the current ticket channel")
@app_commands.describe(member="The member to remove from the ticket")
async def remove(interaction: discord.Interaction, member: discord.Member):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(f"Removed {member.mention} from the ticket.")


@app_commands.command(name="close", description="Close the current ticket")
async def close(interaction: discord.Interaction):
    channel = interaction.channel
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)

    is_staff = staff_role in interaction.user.roles if staff_role else False
    is_owner = str(interaction.user.id) == channel.topic

    if not is_staff and not is_owner:
        return await interaction.response.send_message("Only staff or the ticket creator can close this ticket.", ephemeral=True)

    await interaction.response.send_message("Closing ticket...")
    await close_ticket_process(channel, interaction.guild, interaction.client)

bot.tree.add_command(setup)
bot.tree.add_command(add)
bot.tree.add_command(remove)
bot.tree.add_command(close)


# ═══════════════════════════════════════════════════════
# HOST LEADERBOARD  (weekly + all-time) & HOST ROLE
# ═══════════════════════════════════════════════════════

@bot.tree.command(name="hostleaderboard",
                  description="View the giveaway host leaderboard (weekly or all-time)")
@app_commands.describe(board="Which leaderboard to show")
@app_commands.choices(board=[
    app_commands.Choice(name="Weekly (last 7 days)", value="weekly"),
    app_commands.Choice(name="All-time",             value="alltime"),
])
@command_enabled()
async def hostleaderboard(interaction: discord.Interaction, board: str = "weekly"):
    await interaction.response.defer()
    gid     = interaction.guild.id
    weekly  = (board == "weekly")
    data    = await get_host_leaderboard(gid, weekly=weekly)

    if not data:
        scope = "this week" if weekly else "all time"
        await interaction.followup.send(f"❌ Nobody has hosted a giveaway {scope} yet."); return

    cfg       = await get_host_role_config(gid)
    top_count = cfg[1] if cfg else 0

    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, (uid, amt) in enumerate(data):
        rank   = i + 1
        member = interaction.guild.get_member(uid)
        name   = member.display_name if member else "*[Left Server]*"
        star   = " ★" if uid == interaction.user.id else ""
        prefix = medals[rank - 1] if rank <= 3 else f"**#{rank}**"
        crown  = " 👑" if (top_count and rank <= top_count) else ""
        lines.append(f"{prefix} {name}{star}{crown} — {amt:,} coins")

    title = ("🎁 Weekly Host Leaderboard" if weekly else "🎁 All-Time Host Leaderboard")
    pages = paginate_lines(lines, title, discord.Color.purple(), per_page=10)

    # Footer: caller's own rank + host role info
    caller_rank = next((i + 1 for i, (uid, _) in enumerate(data) if uid == interaction.user.id), None)
    caller_amt  = next((amt for uid, amt in data if uid == interaction.user.id), 0)
    total_pages = len(pages)
    for i, embed in enumerate(pages):
        foot = f"Page {i+1}/{total_pages} · {len(data)} host(s)"
        if caller_rank:
            foot += f" · Your rank: #{caller_rank} ({caller_amt:,})"
        if top_count:
            foot += f" · 👑 = top {top_count} gets the host role"
        embed.set_footer(text=foot)

    view = EmbedPaginator(pages, interaction.user.id) if total_pages > 1 else None
    await interaction.followup.send(embed=pages[0], view=view)


@bot.command(name="hostleaderboard")
async def pfx_hostleaderboard(ctx, board: str = "weekly"):
    if board not in ("weekly", "alltime"):
        await ctx.send("❌ Use `weekly` or `alltime`."); return
    await hostleaderboard._callback(FakeInteraction(ctx), board)


@bot.tree.command(name="sethostrole",
                  description="Set the role given to top hosts, and how many bonus giveaway entries it grants")
@app_commands.describe(
    role="Role to give to the top hosts",
    top_count="How many top hosts get it — applied to BOTH the weekly and all-time boards",
    extra_entries="Bonus giveaway entries this role grants")
@command_enabled()
async def sethostrole(interaction: discord.Interaction, role: discord.Role,
                      top_count: int, extra_entries: int):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if top_count < 1:
        await interaction.response.send_message("❌ top_count must be ≥ 1.", ephemeral=True); return
    if extra_entries < 0:
        await interaction.response.send_message("❌ extra_entries must be ≥ 0.", ephemeral=True); return

    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO host_role_config(guild_id,role_id,top_count,extra_entries) "
                "VALUES(?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET "
                "role_id=excluded.role_id, top_count=excluded.top_count, "
                "extra_entries=excluded.extra_entries",
                (interaction.guild.id, role.id, top_count, extra_entries))
            await db.commit()

    warnings = []
    me = interaction.guild.me
    if not me.guild_permissions.manage_roles:
        warnings.append("⚠️ I'm missing the **Manage Roles** permission — I can't assign this role.")
    elif me.top_role <= role:
        warnings.append(
            f"⚠️ My highest role ({me.top_role.mention}) is below or equal to {role.mention} — "
            f"move my role above it in Server Settings → Roles.")

    await interaction.response.defer()
    added, removed = await sync_host_roles(bot, interaction.guild.id)

    msg = (f"✅ {role.mention} will be given to the **top {top_count}** of the weekly "
           f"leaderboard **and** the top {top_count} of the all-time leaderboard.\n"
           f"It grants **+{extra_entries}** bonus giveaway entries.\n"
           f"🔄 Synced now: **{added}** added, **{removed}** removed.")
    if warnings:
        msg += "\n" + "\n".join(warnings)
    await interaction.followup.send(msg)


@bot.command(name="sethostrole")
async def pfx_sethostrole(ctx, role: discord.Role, top_count: int, extra_entries: int):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await sethostrole._callback(FakeInteraction(ctx), role, top_count, extra_entries)


@bot.tree.command(name="removehostrole",
                  description="Stop giving a host role to top hosts")
@command_enabled()
async def removehostrole(interaction: discord.Interaction):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM host_role_config WHERE guild_id=?",
                             (interaction.guild.id,))
            await db.commit()
    await interaction.response.send_message(
        "🗑 Host role config removed. Members who currently have the role keep it "
        "until you remove it manually.")


@bot.command(name="removehostrole")
async def pfx_removehostrole(ctx):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await removehostrole._callback(FakeInteraction(ctx))


@bot.tree.command(name="hostroleinfo",
                  description="Show the current host role configuration")
@command_enabled()
async def hostroleinfo(interaction: discord.Interaction):
    cfg = await get_host_role_config(interaction.guild.id)
    if not cfg:
        await interaction.response.send_message(
            "❌ No host role configured. Use `/sethostrole`.", ephemeral=True); return
    role_id, top_count, extra_entries = cfg
    role = interaction.guild.get_role(role_id)
    holders = len(role.members) if role else 0
    embed = discord.Embed(title="👑 Host Role Configuration", color=discord.Color.purple())
    embed.add_field(name="Role", value=role.mention if role else f"<deleted {role_id}>", inline=True)
    embed.add_field(name="Top Count", value=f"{top_count} (per board)", inline=True)
    embed.add_field(name="Bonus Entries", value=f"+{extra_entries}", inline=True)
    embed.add_field(name="Current Holders", value=str(holders), inline=True)
    embed.set_footer(text="Given to the top N of the weekly board AND the top N of the all-time board")
    await interaction.response.send_message(embed=embed)


@bot.command(name="hostroleinfo")
async def pfx_hostroleinfo(ctx):
    await hostroleinfo._callback(FakeInteraction(ctx))


@bot.tree.command(name="refreshhostroles",
                  description="Manually re-sync the host role against the leaderboards")
@command_enabled()
async def refreshhostroles(interaction: discord.Interaction):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    cfg = await get_host_role_config(interaction.guild.id)
    if not cfg:
        await interaction.response.send_message(
            "❌ No host role configured. Use `/sethostrole` first.", ephemeral=True); return
    await interaction.response.defer()
    added, removed = await sync_host_roles(bot, interaction.guild.id)
    await interaction.followup.send(
        f"🔄 Host roles synced — **{added}** added, **{removed}** removed.")


@bot.command(name="refreshhostroles")
async def pfx_refreshhostroles(ctx):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await refreshhostroles._callback(FakeInteraction(ctx))


async def host_role_loop():
    """Re-sync host roles every 10 minutes so the weekly board stays accurate."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                cfg = await get_host_role_config(guild.id)
                if not cfg:
                    continue
                added, removed = await sync_host_roles(bot, guild.id)
                if added or removed:
                    await log_event(guild.id, "admin", _log_embed(
                        "👑 Host Roles Synced", discord.Color.purple(),
                        Added=str(added), Removed=str(removed)))
        except Exception as e:
            print(f"[HostRoleLoop] {e}")
        await asyncio.sleep(600)


# ═══════════════════════════════════════════════════════
# EXCHANGE SYSTEM
# ═══════════════════════════════════════════════════════

async def _create_exchange_ticket(guild: discord.Guild, user: discord.Member,
                                  prize_name: str, cost: int) -> discord.TextChannel | None:
    """Open a claim ticket in the configured exchange category."""
    _c2e, _e2c, category_id, _enabled = await get_exchange_config(guild.id)
    category = guild.get_channel(category_id) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None

    staff_role = guild.get_role(STAFF_ROLE_ID)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                          read_message_history=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True)

    try:
        return await guild.create_text_channel(
            name=f"claim-{user.name}",
            topic=str(user.id),
            category=category,
            overwrites=overwrites)
    except Exception as e:
        print(f"[Exchange] ticket creation failed: {e}")
        return None


exchange_group = app_commands.Group(name="exchange",
                                    description="Exchange coins, EXP, and special prizes")
bot.tree.add_command(exchange_group)


@exchange_group.command(name="rates", description="View the current exchange rates and prizes")
async def exchange_rates(interaction: discord.Interaction):
    gid = interaction.guild.id
    c2e, e2c, category_id, enabled = await get_exchange_config(gid)
    if not enabled:
        await interaction.response.send_message("🔒 The exchange system is currently disabled.",
                                                ephemeral=True); return
    async with get_db() as db:
        async with db.execute(
            "SELECT id,name,cost,description,stock FROM exchange_prizes "
            "WHERE guild_id=? ORDER BY cost ASC", (gid,)) as cur:
            prizes = await cur.fetchall()

    embed = discord.Embed(title="💱 Exchange Rates", color=discord.Color.teal())
    embed.add_field(name="💰 → ⭐ Coins to EXP",
                    value=f"1 coin = **{c2e:g}** EXP", inline=True)
    embed.add_field(name="⭐ → 💰 EXP to Coins",
                    value=f"1 EXP = **{e2c:g}** coins", inline=True)
    if prizes:
        lines = []
        for pid, name, cost, desc, stock in prizes:
            stock_str = "" if stock < 0 else (f" · **{stock}** left" if stock > 0 else " · **OUT OF STOCK**")
            lines.append(f"`#{pid}` **{name}** — 💰 {cost:,} coins{stock_str}"
                         + (f"\n    *{desc}*" if desc else ""))
        embed.add_field(name="🎁 Special Prizes", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="🎁 Special Prizes", value="*None configured yet*", inline=False)
    embed.set_footer(text="/exchange coins-to-exp · /exchange exp-to-coins · /exchange prize")
    await interaction.response.send_message(embed=embed)


@exchange_group.command(name="coins-to-exp", description="Exchange your coins for EXP")
@app_commands.describe(amount="Coins to spend — supports 1k, 1m, 1b, etc.")
async def exchange_coins_to_exp(interaction: discord.Interaction, amount: str):
    gid, uid = interaction.guild.id, interaction.user.id
    c2e, _e2c, _cat, enabled = await get_exchange_config(gid)
    if not enabled:
        await interaction.response.send_message("🔒 The exchange system is disabled.",
                                                ephemeral=True); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return

    bal = await get_balance(gid, uid)
    if bal < parsed:
        await interaction.response.send_message(
            f"❌ You need **{parsed:,}** coins but only have **{bal:,}**.", ephemeral=True); return

    exp_gained = int(parsed * c2e)
    if exp_gained < 1:
        await interaction.response.send_message(
            f"❌ That would give **0** EXP at the current rate (1 coin = {c2e:g} EXP). "
            f"Try a larger amount.", ephemeral=True); return

    await add_balance(gid, uid, -parsed, bot=bot)
    await add_exp(gid, uid, exp_gained, is_bonus=True)

    embed = discord.Embed(title="💱 Exchange Complete", color=discord.Color.green(),
        description=f"💰 **-{parsed:,}** coins\n⭐ **+{exp_gained:,}** EXP")
    embed.set_footer(text=f"Rate: 1 coin = {c2e:g} EXP")
    await interaction.response.send_message(embed=embed)
    await log_event(gid, "balance", _log_embed(
        "💱 Coins → EXP", discord.Color.teal(),
        User=interaction.user.mention, Spent=f"{parsed:,} coins", Received=f"{exp_gained:,} EXP"))


@exchange_group.command(name="exp-to-coins", description="Exchange your EXP for coins")
@app_commands.describe(amount="EXP to spend — supports 1k, 1m, 1b, etc.")
async def exchange_exp_to_coins(interaction: discord.Interaction, amount: str):
    gid, uid = interaction.guild.id, interaction.user.id
    _c2e, e2c, _cat, enabled = await get_exchange_config(gid)
    if not enabled:
        await interaction.response.send_message("🔒 The exchange system is disabled.",
                                                ephemeral=True); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return

    exp = await get_exp(gid, uid)
    if exp < parsed:
        await interaction.response.send_message(
            f"❌ You need **{parsed:,}** usable EXP but only have **{exp:,}**.",
            ephemeral=True); return

    coins_gained = int(parsed * e2c)
    if coins_gained < 1:
        await interaction.response.send_message(
            f"❌ That would give **0** coins at the current rate (1 EXP = {e2c:g} coins). "
            f"Try a larger amount.", ephemeral=True); return

    await add_exp(gid, uid, -parsed)
    await add_balance(gid, uid, coins_gained, bot=bot)

    embed = discord.Embed(title="💱 Exchange Complete", color=discord.Color.green(),
        description=f"⭐ **-{parsed:,}** EXP\n💰 **+{coins_gained:,}** coins")
    embed.set_footer(text=f"Rate: 1 EXP = {e2c:g} coins")
    await interaction.response.send_message(embed=embed)
    await log_event(gid, "balance", _log_embed(
        "💱 EXP → Coins", discord.Color.teal(),
        User=interaction.user.mention, Spent=f"{parsed:,} EXP", Received=f"{coins_gained:,} coins"))


@exchange_group.command(name="prize", description="Exchange coins for a special prize (opens a claim ticket)")
@app_commands.describe(prize="Name or ID of the prize — see /exchange rates")
async def exchange_prize(interaction: discord.Interaction, prize: str):
    gid, uid = interaction.guild.id, interaction.user.id
    _c2e, _e2c, category_id, enabled = await get_exchange_config(gid)
    if not enabled:
        await interaction.response.send_message("🔒 The exchange system is disabled.",
                                                ephemeral=True); return

    prize = prize.strip()
    async with get_db() as db:
        if prize.isdigit():
            async with db.execute(
                "SELECT id,name,cost,description,stock FROM exchange_prizes "
                "WHERE guild_id=? AND id=?", (gid, int(prize))) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT id,name,cost,description,stock FROM exchange_prizes "
                "WHERE guild_id=? AND LOWER(name)=LOWER(?)", (gid, prize)) as cur:
                row = await cur.fetchone()
    if not row:
        await interaction.response.send_message(
            f"❌ Prize **{prize}** not found. Use `/exchange rates` to see what's available.",
            ephemeral=True); return

    pid, name, cost, desc, stock = row
    if stock == 0:
        await interaction.response.send_message(
            f"❌ **{name}** is out of stock.", ephemeral=True); return

    bal = await get_balance(gid, uid)
    if bal < cost:
        await interaction.response.send_message(
            f"❌ **{name}** costs **{cost:,}** coins but you only have **{bal:,}**.",
            ephemeral=True); return

    if uid in active_tickets:
        await interaction.response.send_message(
            "❌ You already have an open ticket. Close it before claiming another prize.",
            ephemeral=True); return

    await interaction.response.defer(ephemeral=True)

    channel = await _create_exchange_ticket(interaction.guild, interaction.user, name, cost)
    if channel is None:
        await interaction.followup.send(
            "❌ Couldn't create your claim ticket — ask an admin to check the bot's "
            "permissions and the configured exchange category.", ephemeral=True); return

    # Only charge once the ticket actually exists
    await add_balance(gid, uid, -cost, bot=bot)
    active_tickets.add(uid)
    if stock > 0:
        async with db_lock:
            async with get_db() as db:
                await db.execute(
                    "UPDATE exchange_prizes SET stock=stock-1 WHERE id=?", (pid,))
                await db.commit()

    ping = await channel.send(f"<@{uid}> <@&{STAFF_ROLE_ID}>")
    await ping.delete()

    embed = discord.Embed(title="🎁 Prize Claim", color=discord.Color.gold(),
        description=(f"{interaction.user.mention} exchanged coins for a special prize.\n\n"
                     f"**Prize:** {name}\n"
                     f"**Cost:** 💰 {cost:,} coins *(already deducted)*"))
    if desc:
        embed.add_field(name="Details", value=desc, inline=False)
    embed.set_footer(text="Staff will fulfil this claim shortly.")
    await channel.send(embed=embed, view=CloseTicketView())

    await interaction.followup.send(
        f"✅ Claim ticket created: {channel.mention}\n"
        f"💰 **{cost:,}** coins deducted for **{name}**.", ephemeral=True)
    await log_event(gid, "balance", _log_embed(
        "🎁 Prize Exchanged", discord.Color.gold(),
        User=interaction.user.mention, Prize=name,
        Cost=f"{cost:,} coins", Ticket=channel.mention))


# ── Exchange admin commands ───────────────────────────────────────────────────

@bot.tree.command(name="setexchangerate", description="Admin: set an exchange rate")
@app_commands.describe(
    direction="Which rate to change",
    rate="The multiplier. e.g. 0.5 means 1 unit in = 0.5 units out")
@app_commands.choices(direction=[
    app_commands.Choice(name="Coins → EXP (1 coin = N EXP)",   value="coins_to_exp"),
    app_commands.Choice(name="EXP → Coins (1 EXP = N coins)",  value="exp_to_coins"),
])
@command_enabled()
async def setexchangerate(interaction: discord.Interaction, direction: str, rate: float):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if rate <= 0:
        await interaction.response.send_message("❌ Rate must be greater than 0.", ephemeral=True); return

    column = "coins_to_exp_rate" if direction == "coins_to_exp" else "exp_to_coins_rate"
    await get_exchange_config(interaction.guild.id)   # ensure row exists
    async with db_lock:
        async with get_db() as db:
            await db.execute(f"UPDATE exchange_config SET {column}=? WHERE guild_id=?",
                             (rate, interaction.guild.id))
            await db.commit()

    label = ("1 coin = **{:g}** EXP" if direction == "coins_to_exp"
             else "1 EXP = **{:g}** coins").format(rate)
    await interaction.response.send_message(f"✅ Exchange rate updated: {label}")


@bot.command(name="setexchangerate")
async def pfx_setexchangerate(ctx, direction: str, rate: float):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if direction not in ("coins_to_exp", "exp_to_coins"):
        await ctx.send("❌ Direction must be `coins_to_exp` or `exp_to_coins`."); return
    await setexchangerate._callback(FakeInteraction(ctx), direction, rate)


@bot.tree.command(name="setexchangecategory",
                  description="Admin: set the category where prize claim tickets are created")
@app_commands.describe(category="Category for exchange claim tickets")
@command_enabled()
async def setexchangecategory(interaction: discord.Interaction, category: discord.CategoryChannel):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await get_exchange_config(interaction.guild.id)
    async with db_lock:
        async with get_db() as db:
            await db.execute("UPDATE exchange_config SET ticket_category_id=? WHERE guild_id=?",
                             (category.id, interaction.guild.id))
            await db.commit()
    await interaction.response.send_message(
        f"✅ Prize claim tickets will now be created in **{category.name}**.")


@bot.command(name="setexchangecategory")
async def pfx_setexchangecategory(ctx, category: discord.CategoryChannel):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await setexchangecategory._callback(FakeInteraction(ctx), category)


@bot.tree.command(name="addexchangeprize", description="Admin: add a special prize to the exchange")
@app_commands.describe(
    name="Prize name", cost="Cost in coins — supports 1k, 1m, etc.",
    description="Optional details shown to users",
    stock="How many are available (-1 = unlimited, the default)")
@command_enabled()
async def addexchangeprize(interaction: discord.Interaction, name: str, cost: str,
                           description: str = None, stock: int = -1):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    parsed_cost = parse_amount(cost)
    if parsed_cost is None or parsed_cost <= 0:
        await interaction.response.send_message("❌ Invalid cost.", ephemeral=True); return

    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM exchange_prizes WHERE guild_id=? AND LOWER(name)=LOWER(?)",
            (interaction.guild.id, name)) as cur:
            if await cur.fetchone():
                await interaction.response.send_message(
                    f"❌ A prize named **{name}** already exists.", ephemeral=True); return

    async with db_lock:
        async with get_db() as db:
            cur = await db.execute(
                "INSERT INTO exchange_prizes(guild_id,name,cost,description,stock) VALUES(?,?,?,?,?)",
                (interaction.guild.id, name, parsed_cost, description, stock))
            new_id = cur.lastrowid
            await db.commit()

    stock_str = "unlimited" if stock < 0 else str(stock)
    await interaction.response.send_message(
        f"✅ Added prize `#{new_id}` **{name}** — 💰 {parsed_cost:,} coins | Stock: {stock_str}")


@bot.command(name="addexchangeprize")
async def pfx_addexchangeprize(ctx, name: str, cost: str, *, description: str = None):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await addexchangeprize._callback(FakeInteraction(ctx), name, cost, description, -1)


@bot.tree.command(name="removeexchangeprize", description="Admin: remove a special prize from the exchange")
@app_commands.describe(prize="Prize name or ID")
@command_enabled()
async def removeexchangeprize(interaction: discord.Interaction, prize: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    prize = prize.strip()
    async with db_lock:
        async with get_db() as db:
            if prize.isdigit():
                async with db.execute(
                    "SELECT name FROM exchange_prizes WHERE guild_id=? AND id=?",
                    (interaction.guild.id, int(prize))) as cur:
                    row = await cur.fetchone()
                if not row:
                    await interaction.response.send_message(
                        f"❌ Prize #{prize} not found.", ephemeral=True); return
                await db.execute("DELETE FROM exchange_prizes WHERE guild_id=? AND id=?",
                                 (interaction.guild.id, int(prize)))
            else:
                async with db.execute(
                    "SELECT name FROM exchange_prizes WHERE guild_id=? AND LOWER(name)=LOWER(?)",
                    (interaction.guild.id, prize)) as cur:
                    row = await cur.fetchone()
                if not row:
                    await interaction.response.send_message(
                        f"❌ Prize **{prize}** not found.", ephemeral=True); return
                await db.execute(
                    "DELETE FROM exchange_prizes WHERE guild_id=? AND LOWER(name)=LOWER(?)",
                    (interaction.guild.id, prize))
            await db.commit()
    await interaction.response.send_message(f"🗑 Removed prize **{row[0]}** from the exchange.")


@bot.command(name="removeexchangeprize")
async def pfx_removeexchangeprize(ctx, *, prize: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await removeexchangeprize._callback(FakeInteraction(ctx), prize)


@bot.tree.command(name="setexchangestock", description="Admin: change a prize's remaining stock")
@app_commands.describe(prize="Prize name or ID", stock="New stock (-1 = unlimited)")
@command_enabled()
async def setexchangestock(interaction: discord.Interaction, prize: str, stock: int):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    prize = prize.strip()
    async with db_lock:
        async with get_db() as db:
            if prize.isdigit():
                async with db.execute("SELECT name FROM exchange_prizes WHERE guild_id=? AND id=?",
                                      (interaction.guild.id, int(prize))) as cur:
                    row = await cur.fetchone()
                if not row:
                    await interaction.response.send_message(f"❌ Prize #{prize} not found.",
                                                            ephemeral=True); return
                await db.execute("UPDATE exchange_prizes SET stock=? WHERE guild_id=? AND id=?",
                                 (stock, interaction.guild.id, int(prize)))
            else:
                async with db.execute(
                    "SELECT name FROM exchange_prizes WHERE guild_id=? AND LOWER(name)=LOWER(?)",
                    (interaction.guild.id, prize)) as cur:
                    row = await cur.fetchone()
                if not row:
                    await interaction.response.send_message(f"❌ Prize **{prize}** not found.",
                                                            ephemeral=True); return
                await db.execute(
                    "UPDATE exchange_prizes SET stock=? WHERE guild_id=? AND LOWER(name)=LOWER(?)",
                    (stock, interaction.guild.id, prize))
            await db.commit()
    stock_str = "unlimited" if stock < 0 else str(stock)
    await interaction.response.send_message(f"✅ **{row[0]}** stock set to **{stock_str}**.")


@bot.tree.command(name="toggleexchange", description="Admin: enable or disable the exchange system")
@app_commands.describe(enabled="True to enable, False to disable")
@command_enabled()
async def toggleexchange(interaction: discord.Interaction, enabled: bool):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await get_exchange_config(interaction.guild.id)
    async with db_lock:
        async with get_db() as db:
            await db.execute("UPDATE exchange_config SET enabled=? WHERE guild_id=?",
                             (1 if enabled else 0, interaction.guild.id))
            await db.commit()
    await interaction.response.send_message(
        f"{'✅ Exchange system **enabled**.' if enabled else '🔒 Exchange system **disabled**.'}")


@bot.command(name="toggleexchange")
async def pfx_toggleexchange(ctx, enabled: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    val = enabled.strip().lower() in ("true", "on", "yes", "1", "enable", "enabled")
    await toggleexchange._callback(FakeInteraction(ctx), val)


@bot.command(name="exchange")
async def pfx_exchange(ctx, action: str = None, *, arg: str = None):
    """!exchange rates | !exchange coinstoexp <amt> | !exchange exptocoins <amt> | !exchange prize <name>"""
    p = common._BOT_PREFIX
    if action is None:
        await ctx.send(f"Use `{p}exchange rates`, `{p}exchange coinstoexp <amount>`, "
                       f"`{p}exchange exptocoins <amount>`, or `{p}exchange prize <name>`."); return
    action = action.strip().lower().replace("-", "").replace("_", "")
    fake = FakeInteraction(ctx)
    if action == "rates":
        await exchange_rates._callback(fake)
    elif action in ("coinstoexp", "ctoe"):
        if not arg: await ctx.send("❌ Specify an amount."); return
        await exchange_coins_to_exp._callback(fake, arg)
    elif action in ("exptocoins", "etoc"):
        if not arg: await ctx.send("❌ Specify an amount."); return
        await exchange_exp_to_coins._callback(fake, arg)
    elif action == "prize":
        if not arg: await ctx.send("❌ Specify a prize name or ID."); return
        await exchange_prize._callback(fake, arg)
    else:
        await ctx.send(f"❌ Unknown action. Use `rates`, `coinstoexp`, `exptocoins`, or `prize`.")


if __name__ == "__main__":
    bot.run(TOKEN)
