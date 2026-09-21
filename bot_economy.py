import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, UTC
 
import common
from common import (
    get_db, db_lock, setup_database, log_event, _log_embed, command_enabled,
    is_allowed_to_giveaway, _is_allowed_ctx,
    get_balance, add_balance, get_xp, add_xp, get_level, get_level_xp,
    ensure_stats, add_stat,
    inventory_add, inventory_remove, inventory_get, get_item, get_all_items, add_item, remove_item,
    get_tickets, add_tickets, get_gamble_tokens,
    _update_balance_rank,
    FakeInteraction, _MC,
    VIP_CHEST_KEY, GAMBLE_TOKEN,
    disabled_commands, global_disabled_commands, load_disabled_commands,
    prefix_channel_rules, _prefix_channel_allowed, load_prefix_restrictions,
    register_bot_instance, parse_amount,
    is_blacklisted,
)
 
TOKEN = os.getenv("TOKEN_ECONOMY")
_GUILD_ID = int(os.getenv("GUILD_ID", "0"))
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
 
bot = commands.Bot(command_prefix=common.get_prefix, intents=intents, help_command=None)
register_bot_instance(bot)
 
# ═══════════════════════════════════════════════════════
# BALANCE
# ═══════════════════════════════════════════════════════
 
@bot.tree.command(name="balance", description="Check a balance")
@command_enabled()
async def balance(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    bal = await get_balance(interaction.guild.id, user.id)
    embed = discord.Embed(title=f"💰 {user.display_name}'s Balance",
                          description=f"{bal:,} coins", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)
 

@bot.command(name="addbalance")
async def cmd_addbalance(ctx, user: discord.Member, amount: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0: await ctx.send("❌ Invalid amount."); return
    await add_balance(ctx.guild.id, user.id, parsed, bot=bot)
    await ctx.send(f"✅ Added {parsed:,} coins to {user.mention}.")
    await log_event(ctx.guild.id, "balance", _log_embed("💰 Balance Added", discord.Color.green(),
        Admin=ctx.author.mention, User=user.mention, Amount=f"+{parsed:,}"))
    await log_event(ctx.guild.id, "admin", _log_embed("⚙️ addbalance", discord.Color.orange(),
        By=ctx.author.mention, User=user.mention, Amount=f"+{parsed:,}"))

@bot.command(name="removebalance")
async def cmd_removebalance(ctx, user: discord.Member, amount: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0: await ctx.send("❌ Invalid amount."); return
    await add_balance(ctx.guild.id, user.id, -parsed, bot=bot)
    await ctx.send(f"❌ Removed {parsed:,} coins from {user.mention}.")
    await log_event(ctx.guild.id, "balance", _log_embed("💸 Balance Removed", discord.Color.red(),
        Admin=ctx.author.mention, User=user.mention, Amount=f"-{parsed:,}"))
 
# ═══════════════════════════════════════════════════════
# xp / ACTIVITY RANK
# ═══════════════════════════════════════════════════════
 
@bot.tree.command(name="activityrank", description="Check a user's Activity Rank and xp")
@command_enabled()
async def level(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    gid = interaction.guild.id
    xp    = await get_level_xp(gid, user.id)
    usable = await get_xp(gid, user.id)
    lvl    = await get_level(gid, user.id)
    embed = discord.Embed(title=f"⭐ {user.display_name}'s Activity Rank", color=discord.Color.gold())
    embed.add_field(name="Activity Rank", value=str(lvl), inline=False)
    embed.add_field(name="Total xp (7d)", value=f"{xp:,}", inline=False)
    embed.add_field(name="Usable xp", value=f"{usable:,}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)
 
@bot.command(name="addxp")
async def cmd_addxp(ctx, user: discord.Member, amount: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0: await ctx.send("❌ Invalid amount."); return
    await add_xp(ctx.guild.id, user.id, parsed, is_bonus=True)
    await ctx.send(f"✅ Added **{parsed:,}** usable xp to {user.mention}.")

@bot.command(name="removxp")
async def cmd_removxp(ctx, user: discord.Member, amount: str):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0: await ctx.send("❌ Invalid amount."); return
    await add_xp(ctx.guild.id, user.id, -parsed)
    await ctx.send(f"❌ Removed {parsed:,} xp from {user.mention}.")
 
@bot.command(name="addtotalxp")
async def cmd_addtotalxp(ctx, user: discord.Member, amount: int):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if amount <= 0: await ctx.send("❌ Amount must be > 0."); return
    now = int(datetime.now(UTC).timestamp())
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT INTO xp_history(guild_id,user_id,amount,timestamp,is_bonus) VALUES(?,?,?,?,?)",
                             (ctx.guild.id, user.id, amount, now, 0))
            await db.execute("INSERT INTO xp_history(guild_id,user_id,amount,timestamp,is_bonus) VALUES(?,?,?,?,?)",
                             (ctx.guild.id, user.id, -amount, now, 0))
            await db.commit()
    await ctx.send(f"✅ Added **{amount:,}** to {user.mention}'s Total xp (7d) / Activity Rank. Usable xp unchanged.")
 
@bot.command(name="removetotalxp")
async def cmd_removetotalxp(ctx, user: discord.Member, amount: int):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if amount <= 0: await ctx.send("❌ Amount must be > 0."); return
    week_ago = int((datetime.now(UTC) - timedelta(days=7)).timestamp())
    remaining = amount; actually_removed = 0
    async with db_lock:
        async with get_db() as db:
            async with db.execute(
                "SELECT rowid, amount FROM xp_history "
                "WHERE guild_id=? AND user_id=? AND timestamp>=? AND amount>0 AND is_bonus=0 "
                "ORDER BY timestamp ASC", (ctx.guild.id, user.id, week_ago)) as cur:
                entries = await cur.fetchall()
            for rowid, entry_amount in entries:
                if remaining <= 0: break
                if entry_amount <= remaining:
                    await db.execute("DELETE FROM xp_history WHERE rowid=?", (rowid,))
                    remaining -= entry_amount
                else:
                    await db.execute("UPDATE xp_history SET amount=? WHERE rowid=?",
                                     (entry_amount - remaining, rowid)); remaining = 0
            actually_removed = amount - remaining
            if actually_removed > 0:
                await db.execute(
                    "INSERT INTO xp_history(guild_id,user_id,amount,timestamp,is_bonus) VALUES(?,?,?,?,?)",
                    (ctx.guild.id, user.id, actually_removed, int(datetime.now(UTC).timestamp()), 1))
            await db.commit()
    if actually_removed == 0:
        await ctx.send(f"❌ {user.mention} has no Total xp (7d) to remove.")
    else:
        await ctx.send(f"✅ Removed **{actually_removed:,}** from {user.mention}'s Total xp (7d). Usable xp unchanged.")
    await log_event(ctx.guild.id, "xp", _log_embed("📉 Total xp Removed", discord.Color.orange(),
        Admin=ctx.author.mention, User=user.mention,
        Removed=f"-{actually_removed:,}", Requested=f"-{amount:,}"))
 
# ═══════════════════════════════════════════════════════
# xp BOOSTS
# ═══════════════════════════════════════════════════════
 
@bot.tree.command(name="xpboost",
                  description="Set an xp boost for a role — optionally limit it to a channel or category")
@app_commands.describe(
    role="Role to boost", boost="e.g. 1.5 = +1.5%, -25 = penalty. All matching boosts are summed.",
    channel="Only apply in this channel", category="Only apply in this category")
@command_enabled()
async def xpboost(interaction: discord.Interaction, role: discord.Role, boost: float,
                   channel: discord.TextChannel = None, category: discord.CategoryChannel = None):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if boost == 0:
        await interaction.response.send_message("❌ Boost cannot be 0%.", ephemeral=True); return
    if channel and category:
        await interaction.response.send_message("❌ Specify a channel OR a category, not both.", ephemeral=True); return
    channel_id  = channel.id  if channel  else 0
    category_id = category.id if category else 0
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT OR REPLACE INTO xp_boosts VALUES(?,?,?,?,?)",
                             (interaction.guild.id, role.id, boost, channel_id, category_id))
            await db.commit()
    sign  = "+" if boost > 0 else ""
    scope = "globally"
    if channel:   scope = f"in {channel.mention} only"
    elif category: scope = f"in the **{category.name}** category only"
    await interaction.response.send_message(
        f"✅ {role.mention} now earns **{sign}{boost}% xp** per message {scope}.")
 
@bot.command(name="xpboost")
async def pfx_xpboost(ctx, role: discord.Role, boost: float, channel: discord.TextChannel = None):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await xpboost._callback(FakeInteraction(ctx), role, boost, channel, None)
 
 
@bot.tree.command(name="removxpboost",
                  description="Remove an xp boost — specify the same scope used when it was set")
@app_commands.describe(role="Role to remove boost from", channel="Channel-specific boost", category="Category-specific boost")
@command_enabled()
async def removxpboost(interaction: discord.Interaction, role: discord.Role,
                          channel: discord.TextChannel = None, category: discord.CategoryChannel = None):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if channel and category:
        await interaction.response.send_message("❌ Specify a channel OR a category, not both.", ephemeral=True); return
    channel_id  = channel.id  if channel  else 0
    category_id = category.id if category else 0
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM xp_boosts WHERE guild_id=? AND role_id=? AND channel_id=? AND category_id=?",
                (interaction.guild.id, role.id, channel_id, category_id))
            await db.commit()
    scope = "global"
    if channel:   scope = f"channel {channel.mention}"
    elif category: scope = f"category **{category.name}**"
    await interaction.response.send_message(f"🗑 Removed {scope} xp boost from {role.mention}.")
 
@bot.command(name="removxpboost")
async def pfx_removxpboost(ctx, role: discord.Role, channel: discord.TextChannel = None):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await removxpboost._callback(FakeInteraction(ctx), role, channel, None)
 
 
_xp_BOOSTS_PER_EMBED = 20
 
def _build_xp_boost_embeds(guild, rows: list) -> list:
    lines = []
    for role_id, boost, channel_id, category_id in rows:
        role = guild.get_role(role_id)
        name = role.mention if role else f"<deleted role {role_id}>"
        sign = "+" if boost > 0 else ""
        if channel_id:
            ch = guild.get_channel(channel_id)
            scope = ch.mention if ch else "🗑 deleted channel"
        elif category_id:
            cat = guild.get_channel(category_id)
            scope = f"📁 {cat.name}" if cat else "🗑 deleted category"
        else:
            scope = "🌐 Global"
        lines.append(f"• {name} — **{sign}{boost}%** | {scope}")
    chunks = [lines[i:i+_xp_BOOSTS_PER_EMBED] for i in range(0, len(lines), _xp_BOOSTS_PER_EMBED)]
    total_pages = len(chunks)
    embeds = []
    for i, chunk in enumerate(chunks):
        title = "⚡ Active xp Boosts" + (f"  ({i+1}/{total_pages})" if total_pages > 1 else "")
        embed = discord.Embed(title=title, description="\n".join(chunk), color=discord.Color.blurple())
        if i == total_pages - 1:
            embed.set_footer(text=f"{len(rows)} boost(s) total")
        embeds.append(embed)
    return embeds
 
@bot.tree.command(name="listxpboosts", description="List all active xp boosts for this server")
@command_enabled()
async def slash_listxpboosts(interaction: discord.Interaction):
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT role_id, boost_percent, channel_id, category_id FROM xp_boosts "
                "WHERE guild_id=? ORDER BY boost_percent DESC", (interaction.guild.id,)) as cur:
                rows = await cur.fetchall()
        if not rows:
            await interaction.response.send_message("❌ No xp boosts configured.", ephemeral=True); return
        embeds = _build_xp_boost_embeds(interaction.guild, rows)
        await interaction.response.send_message(embeds=embeds[:10])
        for extra in embeds[10:]:
            await interaction.followup.send(embed=extra)
    except Exception as e:
        try: await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
        except Exception: await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
 
@bot.command(name="listxpboosts")
async def cmd_listxpboosts(ctx):
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT role_id, boost_percent, channel_id, category_id FROM xp_boosts "
                "WHERE guild_id=? ORDER BY boost_percent DESC", (ctx.guild.id,)) as cur:
                rows = await cur.fetchall()
        if not rows:
            await ctx.send("❌ No xp boosts configured."); return
        embeds = _build_xp_boost_embeds(ctx.guild, rows)
        await ctx.send(embeds=embeds[:10])
        for extra in embeds[10:]:
            await ctx.send(embed=extra)
    except Exception as e:
        await ctx.send(f"❌ Error fetching xp boosts: {e}")
 
# ═══════════════════════════════════════════════════════
# LEADERBOARD
# ═══════════════════════════════════════════════════════
 
_LB_PER_PAGE = 10
 
class LeaderboardView(discord.ui.View):
    def __init__(self, all_data, guild, caller_id, caller_rank, caller_amt, title, current_page, total_pages):
        super().__init__(timeout=120)
        self.all_data, self.guild, self.caller_id = all_data, guild, caller_id
        self.caller_rank, self.caller_amt = caller_rank, caller_amt
        self.title, self.current, self.total = title, current_page, total_pages
        self._sync()
 
    def _sync(self):
        for btn in self.children:
            if isinstance(btn, discord.ui.Button):
                if btn.label == "◀": btn.disabled = self.current <= 1
                elif btn.label == "▶": btn.disabled = self.current >= self.total
 
    def build_embed(self, page: int) -> discord.Embed:
        medals = ["🥇", "🥈", "🥉"]
        start  = (page - 1) * _LB_PER_PAGE
        chunk  = self.all_data[start:start + _LB_PER_PAGE]
        embed  = discord.Embed(title=self.title, color=discord.Color.gold())
        lines  = []
        for i, (uid, amt) in enumerate(chunk):
            rank = start + i + 1
            m = self.guild.get_member(uid)
            name = m.display_name if m else "*[Left Server]*"
            star = " ★" if uid == self.caller_id else ""
            prefix = medals[rank-1] if rank <= 3 else f"**#{rank}**"
            lines.append(f"{prefix} {name}{star} — {amt:,}")
        embed.description = "\n".join(lines) if lines else "*No entries on this page.*"
        page_info = f"Page {page}/{self.total} · {len(self.all_data)} entries"
        if self.caller_rank is not None:
            embed.set_footer(text=f"{page_info} · Your rank: #{self.caller_rank} ({self.caller_amt:,})")
        else:
            embed.set_footer(text=f"{page_info} · You have no entry yet")
        return embed
 
    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_page(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.caller_id:
            await interaction.response.send_message("❌ Not your leaderboard.", ephemeral=True); return
        self.current -= 1; self._sync()
        await interaction.response.edit_message(embed=self.build_embed(self.current), view=self)
 
    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.caller_id:
            await interaction.response.send_message("❌ Not your leaderboard.", ephemeral=True); return
        self.current += 1; self._sync()
        await interaction.response.edit_message(embed=self.build_embed(self.current), view=self)
 
    async def on_timeout(self):
        for item in self.children: item.disabled = True
 
 
@bot.tree.command(name="leaderboard", description="View leaderboards")
@app_commands.choices(category=[
    app_commands.Choice(name="Total xp", value="total_xp"),
    app_commands.Choice(name="Usable xp", value="current_xp"),
    app_commands.Choice(name="Balance", value="balance"),
    app_commands.Choice(name="Chests Opened", value="chests_opened"),
    app_commands.Choice(name="Gifted Balance", value="gifted_balance"),
    app_commands.Choice(name="Hosted Balance (given away via /host)", value="hosted_balance"),
])
@app_commands.describe(category="Which leaderboard to view", page="Jump directly to this page number (default: 1)")
@command_enabled()
async def leaderboard(interaction: discord.Interaction, category: app_commands.Choice[str], page: int = 1):
    value = category.value
    gid   = interaction.guild.id
    week_ago = int((datetime.now(UTC) - timedelta(days=7)).timestamp())
    await interaction.response.defer()
 
    all_data = []
    async with get_db() as db:
        if value == "current_xp":
            async with db.execute(
                "SELECT user_id, SUM(amount) FROM xp_history "
                "WHERE guild_id=? AND timestamp>=? GROUP BY user_id "
                "HAVING SUM(amount)>0 ORDER BY SUM(amount) DESC", (gid, week_ago)) as cur:
                all_data = [(uid, int(amt)) for uid, amt in await cur.fetchall()]
        elif value == "balance":
            async with db.execute(
                "SELECT user_id, balance FROM balances WHERE guild_id=? AND balance>0 ORDER BY balance DESC",
                (gid,)) as cur:
                all_data = list(await cur.fetchall())
        else:
            async with db.execute(
                f"SELECT user_id, {value} FROM user_stats WHERE guild_id=? AND {value}>0 ORDER BY {value} DESC",
                (gid,)) as cur:
                all_data = list(await cur.fetchall())
 
    if not all_data:
        await interaction.followup.send("❌ No data found.", ephemeral=True); return
 
    caller_rank = caller_amt = None
    for rank, (uid, amt) in enumerate(all_data, 1):
        if uid == interaction.user.id:
            caller_rank, caller_amt = rank, amt
            break
 
    title_map = {
        "total_xp": "🏆 Total xp", "current_xp": "⭐ Usable xp", "balance": "💰 Balance",
        "chests_opened": "📦 Chests Opened", "gifted_balance": "💸 Gifted Balance",
        "hosted_balance": "🎁 Hosted Balance Given Away",
    }
    title = title_map[value] + " Leaderboard"
    total_pages = max(1, (len(all_data) + _LB_PER_PAGE - 1) // _LB_PER_PAGE)
    page = max(1, min(page, total_pages))
    view = LeaderboardView(all_data, interaction.guild, interaction.user.id, caller_rank, caller_amt, title, page, total_pages)
    await interaction.followup.send(embed=view.build_embed(page), view=view if total_pages > 1 else None, ephemeral=True)

_VALID_STATS = {"total_xp", "gifted_balance", "chests_opened", "hosted_balance"}
 
@bot.command(name="addleaderboardstat")
async def cmd_addleaderboardstat(ctx, user: discord.Member, stat: str, amount: int):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if stat not in _VALID_STATS: await ctx.send(f"❌ Valid stats: {', '.join(_VALID_STATS)}"); return
    if amount <= 0: await ctx.send("❌ Amount must be > 0."); return
    await ensure_stats(ctx.guild.id, user.id)
    async with db_lock:
        async with get_db() as db:
            await db.execute(f"UPDATE user_stats SET {stat}={stat}+? WHERE guild_id=? AND user_id=?",
                             (amount, ctx.guild.id, user.id))
            await db.commit()
    await ctx.send(f"✅ Added **{amount:,}** to {user.mention}'s **{stat}**.")
 
@bot.command(name="removeleaderboardstat")
async def cmd_removeleaderboardstat(ctx, user: discord.Member, stat: str, amount: int):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    if stat not in _VALID_STATS: await ctx.send(f"❌ Valid stats: {', '.join(_VALID_STATS)}"); return
    if amount <= 0: await ctx.send("❌ Amount must be > 0."); return
    await ensure_stats(ctx.guild.id, user.id)
    async with db_lock:
        async with get_db() as db:
            await db.execute(f"UPDATE user_stats SET {stat}=MAX(0,{stat}-?) WHERE guild_id=? AND user_id=?",
                             (amount, ctx.guild.id, user.id))
            await db.commit()
    await ctx.send(f"❌ Removed **{amount:,}** from {user.mention}'s **{stat}**.")
 
# ═══════════════════════════════════════════════════════
# BALANCE RANKS
# ═══════════════════════════════════════════════════════
 
@bot.tree.command(name="addbalancerank", description="Add or update a balance rank role")
@app_commands.describe(threshold="Balance required to receive this role", role="Role granted at this balance")
@command_enabled()
async def addbalancerank(interaction: discord.Interaction, threshold: int, role: discord.Role):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if threshold < 0:
        await interaction.response.send_message("❌ Threshold must be ≥ 0.", ephemeral=True); return
 
    async with get_db() as db:
        async with db.execute(
            "SELECT role_id FROM balance_ranks WHERE guild_id=? AND threshold=? AND role_id!=?",
            (interaction.guild.id, threshold, role.id)) as cur:
            dup = await cur.fetchone()
 
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO balance_ranks(guild_id,role_id,threshold) VALUES(?,?,?) "
                "ON CONFLICT(guild_id,role_id) DO UPDATE SET threshold=excluded.threshold",
                (interaction.guild.id, role.id, threshold))
            await db.commit()
 
    warnings = []
    if dup:
        other = interaction.guild.get_role(dup[0])
        warnings.append(f"⚠️ {other.mention if other else dup[0]} already uses threshold {threshold:,} — ties broken arbitrarily.")
    bot_member = interaction.guild.me
    if not bot_member.guild_permissions.manage_roles:
        warnings.append("⚠️ I don't have the **Manage Roles** permission — I won't be able to assign this rank.")
    elif bot_member.top_role <= role:
        warnings.append(
            f"⚠️ My highest role ({bot_member.top_role.mention}) is below or equal to {role.mention} — "
            f"move my role ABOVE it in Server Settings → Roles, or I can't assign it.")
 
    msg = (f"✅ {role.mention} is now the balance rank for **{threshold:,}+** coins.\n"
           f"ℹ️ Run `/refreshbalanceranks` to apply this to members with existing balances.")
    if warnings:
        msg += "\n" + "\n".join(warnings)
    await interaction.response.send_message(msg)
 
@bot.command(name="addbalancerank")
async def pfx_addbalancerank(ctx, threshold: int, role: discord.Role):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await addbalancerank._callback(FakeInteraction(ctx), threshold, role)
 
 
@bot.tree.command(name="removebalancerank", description="Remove a balance rank role")
@command_enabled()
async def removebalancerank(interaction: discord.Interaction, role: discord.Role):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    async with db_lock:
        async with get_db() as db:
            await db.execute("DELETE FROM balance_ranks WHERE guild_id=? AND role_id=?",
                             (interaction.guild.id, role.id))
            await db.commit()
    await interaction.response.send_message(
        f"🗑 {role.mention} removed from balance ranks. Members who already have it keep it until manually removed.")
 
@bot.command(name="removebalancerank")
async def pfx_removebalancerank(ctx, role: discord.Role):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await removebalancerank._callback(FakeInteraction(ctx), role)
 
 
@bot.tree.command(name="refreshbalanceranks",
                  description="Re-evaluate balance ranks for every member with a balance")
@command_enabled()
async def refreshbalanceranks(interaction: discord.Interaction):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await interaction.response.defer()
    gid = interaction.guild.id
    async with get_db() as db:
        async with db.execute("SELECT DISTINCT user_id FROM balances WHERE guild_id=?", (gid,)) as cur:
            user_ids = [r[0] for r in await cur.fetchall()]
    updated = errors = 0
    for uid in user_ids:
        try:
            member = interaction.guild.get_member(uid)
            if not member: continue
            before_roles = {r.id for r in member.roles}
            await _update_balance_rank(bot, gid, uid)
            member2 = interaction.guild.get_member(uid)
            if member2 and {r.id for r in member2.roles} != before_roles:
                updated += 1
        except Exception as e:
            errors += 1
            print(f"[RefreshBalanceRanks] {uid}: {e}")
    msg = f"✅ Refreshed balance ranks for **{len(user_ids)}** member(s) — **{updated}** role change(s) made."
    if errors:
        msg += f"\n⚠️ **{errors}** error(s) occurred — check your admin log channel."
    await interaction.followup.send(msg)
 
@bot.command(name="refreshbalanceranks")
async def pfx_refreshbalanceranks(ctx):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await refreshbalanceranks._callback(FakeInteraction(ctx))
 
# ═══════════════════════════════════════════════════════
# STATS PANEL
# ═══════════════════════════════════════════════════════
 
async def _build_stats_embed(guild: discord.Guild) -> discord.Embed:
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(tickets),0) FROM mega_tickets WHERE guild_id=? AND tickets>0",
            (guild.id,)) as cur:
            members, pool = await cur.fetchone()
    embed = discord.Embed(
        title="📊 Stats",
        description="Click a button below to check your personal stats.",
        color=discord.Color.blurple())
    embed.set_footer(text="Results are only visible to you")
    return embed
 
async def _refresh_stats_channel(guild: discord.Guild):
    async with get_db() as db:
        async with db.execute(
            "SELECT channel_id, message_id FROM stats_channel_config WHERE guild_id=?", (guild.id,)) as cur:
            row = await cur.fetchone()
    if not row or not row[0]: return
    ch = bot.get_channel(row[0])
    if not ch: return
    embed = await _build_stats_embed(guild)
    view = StatsChannelView()
    if row[1]:
        try:
            msg = await ch.fetch_message(row[1])
            await msg.edit(embed=embed, view=view)
            return
        except discord.NotFound:
            pass
    new_msg = await ch.send(embed=embed, view=view)
    async with db_lock:
        async with get_db() as db:
            await db.execute("UPDATE stats_channel_config SET message_id=? WHERE guild_id=?", (new_msg.id, guild.id))
            await db.commit()
 
 
class StatsChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="💰 Balance", style=discord.ButtonStyle.secondary, custom_id="stats_panel:balance", row=0)
    async def check_balance(self, interaction: discord.Interaction, btn):
        bal = await get_balance(interaction.guild.id, interaction.user.id)
        embed = discord.Embed(title=f"💰 {interaction.user.display_name}'s Balance",
                              description=f"**{bal:,}** coins", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
 
    @discord.ui.button(label="⭐ Activity Rank", style=discord.ButtonStyle.secondary, custom_id="stats_panel:rank", row=0)
    async def check_rank(self, interaction: discord.Interaction, btn):
        gid, uid = interaction.guild.id, interaction.user.id
        xp = await get_level_xp(gid, uid); usable = await get_xp(gid, uid); lvl = await get_level(gid, uid)
        embed = discord.Embed(title=f"⭐ {interaction.user.display_name}'s Activity Rank", color=discord.Color.gold())
        embed.add_field(name="Activity Rank", value=str(lvl), inline=True)
        embed.add_field(name="Total xp (7d)", value=f"{xp:,}", inline=True)
        embed.add_field(name="Usable xp", value=f"{usable:,}", inline=True)
        embed.add_field(name="Chests Available", value=f"{usable // common.CHEST_COST}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
 


@bot.tree.command(name="setstatchannel", description="Post the stats panel embed in a channel")
@app_commands.describe(channel="Channel to post the panel in")
@command_enabled()
async def setstatchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await interaction.response.defer()
    gid = interaction.guild.id
    async with get_db() as db:
        async with db.execute("SELECT channel_id, message_id FROM stats_channel_config WHERE guild_id=?", (gid,)) as cur:
            old = await cur.fetchone()
    if old and old[0] and old[0] != channel.id and old[1]:
        old_ch = bot.get_channel(old[0])
        if old_ch:
            try: await (await old_ch.fetch_message(old[1])).delete()
            except Exception: pass
    async with db_lock:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO stats_channel_config(guild_id, channel_id, message_id) VALUES(?,?,0) "
                "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, message_id=0",
                (gid, channel.id))
            await db.commit()
    await _refresh_stats_channel(interaction.guild)
    await interaction.followup.send(f"✅ Stats panel posted in {channel.mention}.")
 
@bot.command(name="setstatchannel")
async def pfx_setstatchannel(ctx, channel: discord.TextChannel):
    if not await _is_allowed_ctx(ctx): await ctx.send("❌ No permission."); return
    await setstatchannel._callback(FakeInteraction(ctx), channel)
 

# ═══════════════════════════════════════════════════════
# CORE EVENTS
# ═══════════════════════════════════════════════════════
 
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith(common._BOT_PREFIX) and not _prefix_channel_allowed(message):
        return
    await bot.process_commands(message)
 
@bot.event
async def on_ready():
    await setup_database()
    await common._load_prefix()
    await load_disabled_commands()
    await load_prefix_restrictions()
    bot.add_view(StatsChannelView())

    _guild = discord.Object(id=_GUILD_ID)
    bot.tree.copy_global_to(guild=_guild)
    try:
        synced = await bot.tree.sync(guild=_guild)
        print(f"[Economy Bot] Synced {len(synced)} commands to guild. Logged in as {bot.user}")
    except Exception as e:
        print(f"[Economy Bot] Guild sync failed: {e}")
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    for g in bot.guilds:
        try:
            await _refresh_stats_channel(g)
        except Exception as e:
            print(f"[StatsPanel restore] {g.name}: {e}")
 
@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    except discord.HTTPException as e:
        print(f"[Economy Sync] Failed on join: {e}")
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return
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
    if not ctx.guild:
        return
    embed = discord.Embed(
        description=f"{ctx.author.mention} used **`{common._BOT_PREFIX}{ctx.command.qualified_name}`**",
        color=discord.Color.light_grey(), timestamp=datetime.now(UTC))
    embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"#{getattr(ctx.channel, 'name', 'DM')} | UID: {ctx.author.id}")
    await log_event(ctx.guild.id, "command", embed)

# ── gift ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="gift", description="Give your own coins to another user")
@app_commands.describe(user="Who to gift to",
                       amount="Amount — supports 1k, 1m, 1b, 1t, 1q, 1qn")
@command_enabled()
async def slash_gift(interaction: discord.Interaction, user: discord.Member, amount: str):
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return
    if user.id == interaction.user.id:
        await interaction.response.send_message("❌ You cannot gift yourself.", ephemeral=True); return
    gid = interaction.guild.id
    bal = await get_balance(gid, interaction.user.id)
    if bal < parsed:
        await interaction.response.send_message("❌ Not enough balance.", ephemeral=True); return
    if await is_blacklisted(gid, interaction.user.id):
        await interaction.response.send_message(
            "🚫 You're blacklisted from the economy.", ephemeral=True); return
    if await is_blacklisted(gid, user.id):
        await interaction.response.send_message(
            f"🚫 {user.mention} is blacklisted and can't receive coins.", ephemeral=True); return
    await add_balance(gid, interaction.user.id, -parsed, bot=bot)
    await add_balance(gid, user.id, parsed, bot=bot)
    await add_stat(gid, interaction.user.id, "gifted_balance", parsed)
    await interaction.response.send_message(f"💸 You gifted **{parsed:,}** coins to {user.mention}!")
    await log_event(gid, "balance", _log_embed("🎁 Gift Sent", discord.Color.green(),
        From=interaction.user.mention, To=user.mention, Amount=f"{parsed:,}"))

# ── addbalance / removebalance ───────────────────────────────────────────────
@bot.tree.command(name="addbalance", description="Admin: add coins to a user")
@app_commands.describe(user="Target user", amount="Amount — supports 1k, 1m, 1b, etc.")
@command_enabled()
async def slash_addbalance(interaction: discord.Interaction, user: discord.Member, amount: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return
    await add_balance(interaction.guild.id, user.id, parsed, bot=bot)
    await interaction.response.send_message(f"✅ Added {parsed:,} coins to {user.mention}.")
    await log_event(interaction.guild.id, "balance", _log_embed("💰 Balance Added", discord.Color.green(),
        Admin=interaction.user.mention, User=user.mention, Amount=f"+{parsed:,}"))

@bot.tree.command(name="removebalance", description="Admin: remove coins from a user")
@app_commands.describe(user="Target user", amount="Amount — supports 1k, 1m, 1b, etc.")
@command_enabled()
async def slash_removebalance(interaction: discord.Interaction, user: discord.Member, amount: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return
    await add_balance(interaction.guild.id, user.id, -parsed, bot=bot)
    await interaction.response.send_message(f"❌ Removed {parsed:,} coins from {user.mention}.")
 
# ── xp admin ────────────────────────────────────────────────────────────────
@bot.tree.command(name="addxp", description="Admin: add usable xp to a user")
@app_commands.describe(user="Target user", amount="Amount — supports 1k, 1m, 1b, etc.")
@command_enabled()
async def slash_addxp(interaction: discord.Interaction, user: discord.Member, amount: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return
    await add_xp(interaction.guild.id, user.id, parsed, is_bonus=True)
    await interaction.response.send_message(f"✅ Added **{parsed:,}** usable xp to {user.mention}.")

@bot.tree.command(name="removxp", description="Admin: remove usable xp from a user")
@app_commands.describe(user="Target user", amount="Amount — supports 1k, 1m, 1b, etc.")
@command_enabled()
async def slash_removxp(interaction: discord.Interaction, user: discord.Member, amount: str):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True); return
    await add_xp(interaction.guild.id, user.id, -parsed)
    await interaction.response.send_message(f"❌ Removed {parsed:,} xp from {user.mention}.")

@bot.tree.command(name="addtotalxp", description="Admin: add Total xp (Activity Rank only, usable unchanged)")
@app_commands.describe(user="Target user", amount="xp to add to rank")
@command_enabled()
async def slash_addtotalxp(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be > 0.", ephemeral=True); return
    now = int(datetime.now(UTC).timestamp())
    async with db_lock:
        async with get_db() as db:
            await db.execute("INSERT INTO xp_history(guild_id,user_id,amount,timestamp,is_bonus) VALUES(?,?,?,?,?)",
                             (interaction.guild.id, user.id, amount, now, 0))
            await db.execute("INSERT INTO xp_history(guild_id,user_id,amount,timestamp,is_bonus) VALUES(?,?,?,?,?)",
                             (interaction.guild.id, user.id, -amount, now, 0))
            await db.commit()
    await interaction.response.send_message(
        f"✅ Added **{amount:,}** to {user.mention}'s Total xp (7d). Usable xp unchanged.")

@bot.tree.command(name="removetotalxp", description="Admin: remove Total xp (Activity Rank only)")
@app_commands.describe(user="Target user", amount="xp to remove from rank")
@command_enabled()
async def slash_removetotalxp(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    await interaction.response.defer()
    ctx_like = FakeInteraction(None)
    if amount <= 0:
        await interaction.followup.send("❌ Amount must be > 0."); return
    week_ago = int((datetime.now(UTC) - timedelta(days=7)).timestamp())
    remaining = amount; actually_removed = 0
    async with db_lock:
        async with get_db() as db:
            async with db.execute(
                "SELECT rowid, amount FROM xp_history "
                "WHERE guild_id=? AND user_id=? AND timestamp>=? AND amount>0 AND is_bonus=0 "
                "ORDER BY timestamp ASC", (interaction.guild.id, user.id, week_ago)) as cur:
                entries = await cur.fetchall()
            for rowid, entry_amount in entries:
                if remaining <= 0: break
                if entry_amount <= remaining:
                    await db.execute("DELETE FROM xp_history WHERE rowid=?", (rowid,))
                    remaining -= entry_amount
                else:
                    await db.execute("UPDATE xp_history SET amount=? WHERE rowid=?",
                                     (entry_amount - remaining, rowid)); remaining = 0
            actually_removed = amount - remaining
            if actually_removed > 0:
                await db.execute("INSERT INTO xp_history(guild_id,user_id,amount,timestamp,is_bonus) VALUES(?,?,?,?,?)",
                                 (interaction.guild.id, user.id, actually_removed, int(datetime.now(UTC).timestamp()), 1))
            await db.commit()
    if actually_removed == 0:
        await interaction.followup.send(f"❌ {user.mention} has no Total xp (7d) to remove.")
    else:
        await interaction.followup.send(
            f"✅ Removed **{actually_removed:,}** from {user.mention}'s Total xp (7d). Usable xp unchanged.")

# ── leaderboard stat admin ───────────────────────────────────────────────────
@bot.tree.command(name="addleaderboardstat", description="Admin: manually add to a user's leaderboard stat")
@app_commands.describe(user="Target user", stat="Which stat to add to", amount="Amount to add")
@app_commands.choices(stat=[app_commands.Choice(name=s.replace("_"," ").title(), value=s) for s in
                             ("total_xp","gifted_balance","chests_opened","mega_tickets_bought","hosted_balance")])
@command_enabled()
async def slash_addleaderboardstat(interaction: discord.Interaction, user: discord.Member,
                                   stat: str, amount: int):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be > 0.", ephemeral=True); return
    await ensure_stats(interaction.guild.id, user.id)
    async with db_lock:
        async with get_db() as db:
            await db.execute(f"UPDATE user_stats SET {stat}={stat}+? WHERE guild_id=? AND user_id=?",
                             (amount, interaction.guild.id, user.id))
            await db.commit()
    await interaction.response.send_message(f"✅ Added **{amount:,}** to {user.mention}'s **{stat}**.")

@bot.tree.command(name="removeleaderboardstat", description="Admin: remove from a user's leaderboard stat")
@app_commands.describe(user="Target user", stat="Which stat to remove from", amount="Amount to remove")
@app_commands.choices(stat=[app_commands.Choice(name=s.replace("_"," ").title(), value=s) for s in
                             ("total_xp","gifted_balance","chests_opened","mega_tickets_bought","hosted_balance")])
@command_enabled()
async def slash_removeleaderboardstat(interaction: discord.Interaction, user: discord.Member,
                                      stat: str, amount: int):
    if not await is_allowed_to_giveaway(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True); return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be > 0.", ephemeral=True); return
    await ensure_stats(interaction.guild.id, user.id)
    async with db_lock:
        async with get_db() as db:
            await db.execute(f"UPDATE user_stats SET {stat}=MAX(0,{stat}-?) WHERE guild_id=? AND user_id=?",
                             (amount, interaction.guild.id, user.id))
            await db.commit()
    await interaction.response.send_message(f"❌ Removed **{amount:,}** from {user.mention}'s **{stat}**.")

@bot.tree.command(name="rankspanel", description="Shows the ranks panel")
async def rankspanelembed(interaction: discord.Interaction):
    await interaction.response.send_message("ranks panel is being sent", ephemeral=True)

    embed=discord.Embed(
        title="🌟 RANKS",
        description="""Ranks are updated through your balance 🚀
        
<@&1539341777268375633> 🏵️ - 250M+ 
<@&1539341855941206036> 🔮 - 1.5B+
<@&1539341829017702531> 🐉 - 5B+
<@&1539341884248555531> 👾 - 20B+""",
        color=discord.Color.red()
    )
   
    await interaction.channel.send(embed=embed)


@bot.tree.command(name="guide", description="Shows the NOVA economy guide")
async def guide(interaction: discord.Interaction):
    await interaction.response.send_message("NOVA economy guide is being sent", ephemeral=True)

    embed = discord.Embed(
        title="🪐 NOVA ECONOMY GUIDE",
        description="""Welcome to NOVA. This guide explains how the economy works and how you can get started.

## 💸 EARNING

👉🏻 You can earn NOVA currency through the available economy features.

👉🏻 The more active you are, the more opportunities you have to build your balance.

## 🔁 TRADING

👉🏻 Your NOVA balance can be used to trade with other members.

👉🏻 You can exchange your balance for items, or trade items you no longer need for NOVA currency.

## ❎ CROSS-TRADING

👉🏻 NOVA allows you to trade between different games, **BUT NOT DIRECTLY**.

👉🏻 For example, you can trade an item from one game for NOVA currency and use that currency to get an item from another game.

## 🪙 BALANCE

👉🏻 Your balance is your NOVA currency. Keep track of it and use it to trade, save, or build up your wealth.

## 🔮 GETTING STARTED

1. Start earning NOVA currency.
2. Build up your balance.
3. Find items or trades you're interested in.
4. Trade with other members.
5. Keep building your balance.

**💖 The more you trade, the more opportunities you have.**""",
        color=discord.Color.red()
    )

    await interaction.channel.send(embed=embed)
 
if __name__ == "__main__":
    bot.run(TOKEN)
