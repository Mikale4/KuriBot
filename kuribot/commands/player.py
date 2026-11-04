"""Player commands: daily, quests, settings, profile, rank, leaderboard, inbox, guild."""

from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord.ext import commands

from kuribot.config import DAILY_ARENA_TICKETS, PACKS_PER_DAILY, QUEST_TICKETS
from kuribot.timeutil import human_timedelta, reset_countdown_text
from kuribot.settings import (
    BATTLE_SPEED_DESCRIPTIONS, battle_speed_label, notification_status_label,
)
from kuribot.core import bot
from kuribot.database import db
from kuribot.portraits import get_user_profile_avatar_bytes
from kuribot.quests import describe_quest_row, ensure_player_quest
from kuribot.render.profile import make_rank_badge_file
from kuribot.ui.settings import BotSettingsView
from kuribot.ui.inbox import InboxView
from kuribot.ui.profile import ProfileView
from kuribot.ui.guild import build_guild_hub_summary_text, GuildHubView
from kuribot.ui.dueling_grounds import RankedLeaderboardView


@bot.command(name="daily")
async def daily_cmd(ctx: commands.Context):
    claimed, remaining = db.try_claim_daily(ctx.author.id)
    if claimed:
        user = db.get_user(ctx.author.id)
        pack_count = db.pack_quantity(ctx.author.id, "random_themed")
        await ctx.send(f"🎁 {ctx.author.mention}, you claimed **{PACKS_PER_DAILY} Random Packs** and **{DAILY_ARENA_TICKETS}** arena tickets. You now have **{pack_count}** Random Pack(s) and **{user['arena_tickets']}** arena tickets. Next refresh: **{reset_countdown_text()}** (**12:00 AM / 12:00 PM ET**).")
    else:
        await ctx.send(f"⏳ {ctx.author.mention}, your next `;daily` is ready in **{human_timedelta(remaining)}** (**12:00 AM / 12:00 PM ET** reset).")


@bot.command(name="quest")
async def quest_cmd(ctx: commands.Context):
    quest_row = ensure_player_quest(ctx.author.id)
    completed = bool(quest_row["completed_at"])
    status_text = "Completed" if completed else "In Progress"
    reward_text = f"**Reward:** {QUEST_TICKETS} Random Pack"
    refresh_text = f"**Refresh:** {reset_countdown_text()} (**12:00 AM / 12:00 PM ET**)"
    note_text = "Lobby duelist wins count as Normal mode for quest progress."
    await ctx.send(
        "\n".join([
            f"🗺️ **Quest** — {status_text}",
            describe_quest_row(quest_row),
            reward_text,
            refresh_text,
            note_text,
        ])
    )


@bot.command(name="settings")
async def settings_cmd(ctx: commands.Context):
    current_speed = db.get_battle_speed(ctx.author.id)
    notifications_enabled = db.notifications_enabled(ctx.author.id)
    await ctx.send(
        "**Settings**\n"
        f"Battle Speed: **{battle_speed_label(current_speed)}** — {BATTLE_SPEED_DESCRIPTIONS[current_speed]}\n"
        f"Notifications: **{notification_status_label(notifications_enabled)}** — {'Kuribot can DM you when `;ping` affects you' if notifications_enabled else 'Kuribot will not DM you when `;ping` affects you'}",
        view=BotSettingsView(ctx.author.id),
    )


@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard_cmd(ctx: commands.Context):
    rows = db.ranked_leaderboard()[:RankedLeaderboardView.MAX_ENTRIES]
    if not rows:
        await ctx.send("No ranked players are on the leaderboard yet.")
        return

    view = RankedLeaderboardView(ctx.author.id, rows)
    await ctx.send(await view.summary_text(), view=view)


@bot.command(name="rank")
async def rank_cmd(ctx: commands.Context, target: Optional[discord.Member] = None):
    target_user = target or ctx.author
    if target is not None and not db.is_registered_user(target_user.id):
        await ctx.send(f"{target_user.display_name} has not started KuriBot yet.")
        return
    user_row = db.get_user(target_user.id)
    rank_file = make_rank_badge_file(target_user.display_name, user_row, db.get_avatar_path(target_user.id))
    await ctx.send(file=rank_file)


@bot.command(name="profile", aliases=["profil"])
async def profile_cmd(ctx: commands.Context, target: Optional[discord.Member] = None):
    db.ensure_user(ctx.author.id)
    target_user = target or ctx.author
    if target is not None and not db.is_registered_user(target_user.id):
        await ctx.send(f"{target_user.display_name} has not started KuriBot yet.")
        return
    avatar_bytes = await get_user_profile_avatar_bytes(target_user, size=256)
    editable = target_user.id == ctx.author.id
    view = ProfileView(target_user.id, target_user.display_name, avatar_bytes, viewer_id=ctx.author.id, editable=editable)
    view.message = await ctx.send(file=await asyncio.to_thread(view.make_file), view=view)


@bot.command(name="guild")
async def guild_cmd(ctx: commands.Context):
    view = GuildHubView(ctx.author.id, ctx.author.display_name)
    message = await ctx.send(await build_guild_hub_summary_text(ctx.author.id, view.page, view.guild_order, ctx.guild), view=view)
    view.message = message


@bot.command(name="inbox")
async def inbox_cmd(ctx: commands.Context):
    db.ensure_user(ctx.author.id)
    avatar_bytes = await get_user_profile_avatar_bytes(ctx.author, size=256)
    view = InboxView(ctx.author.id, ctx.author.display_name, avatar_bytes, 1)
    view.message = await ctx.send(content=view.summary_text(), file=await asyncio.to_thread(view.make_file), view=view)
