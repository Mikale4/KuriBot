"""Discord events (on_ready, on_message) and the daily reset loop."""

from __future__ import annotations

import asyncio
import time
from typing import Dict

import discord
from discord.ext import tasks

from kuribot.config import (
    CARDS_DIR, LEVELING_FALLBACK_CHANNEL_ID, LEVELING_MESSAGE_COOLDOWN_SECONDS,
    LEVELING_MESSAGE_XP_MAX, LEVELING_MESSAGE_XP_MIN, LEVELING_REWARD_GOLD, LEVELING_REWARD_PACKS,
    RNG,
)
from kuribot.core import bot, log
from kuribot.database import db
from kuribot.monsters import (
    backfill_special_monster_compensation_mail_usernames, scan_cards_directory,
    sync_boss_monster_base_stats,
)
from kuribot.cards.support_cards import init_support_cards
from kuribot.arena import maybe_run_daily_arena_reset, maybe_run_guild_boss_attempt_reset
from kuribot.profile import send_profile_tag_moderation_request
from kuribot.ui.start import send_unregistered_prompt
from kuribot.maintenance import backup_database_daily, restore_moderation_views


async def _level_up_fallback_channel(guild: discord.Guild):
    """The level-up fallback channel, but only if it belongs to this server."""
    channel = guild.get_channel(LEVELING_FALLBACK_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(LEVELING_FALLBACK_CHANNEL_ID)
        except Exception:
            return None
    if channel is None or not hasattr(channel, "send") or getattr(getattr(channel, "guild", None), "id", None) != guild.id:
        return None
    return channel


async def send_level_up_announcement(message: discord.Message, level_result: dict):
    """Announce a level-up in the channel the player wrote in, or in the fallback channel if
    the bot can't post there (no permission, or the send fails)."""
    if message.guild is None or not hasattr(message.channel, "send"):
        return
    guild = message.guild
    me = guild.me or guild.get_member(bot.user.id if bot.user else 0)
    can_send_here = False
    if me is not None:
        try:
            perms = message.channel.permissions_for(me)
            # Threads need "send messages in threads"; normal channels need "send messages".
            can_send = perms.send_messages_in_threads if isinstance(message.channel, discord.Thread) else perms.send_messages
            can_send_here = bool(perms.view_channel and can_send)
        except Exception:
            can_send_here = False

    levels_gained = int(level_result.get("levels_gained", 1) or 1)
    pack_count = LEVELING_REWARD_PACKS * levels_gained
    pack_label = "Pack" if pack_count == 1 else "Packs"
    rewards = f"+{pack_count} Random {pack_label} • +{LEVELING_REWARD_GOLD * levels_gained:,} Gold"
    source_channel_id = getattr(message.channel, "id", 0)

    def announcement_for(channel) -> str:
        location_note = "" if getattr(channel, "id", 0) == source_channel_id else f" (posted here because I couldn't send in <#{source_channel_id}>)"
        return (
            f"🎉 {message.author.mention} leveled up to **Level {int(level_result.get('new_level', 1) or 1)}**!\n"
            f"Rewards: {rewards}{location_note}"
        )

    if can_send_here:
        try:
            await message.channel.send(announcement_for(message.channel))
            return
        except Exception:
            pass  # e.g. permissions changed or slow mode: try the fallback channel
    fallback_channel = await _level_up_fallback_channel(guild)
    if fallback_channel is None or getattr(fallback_channel, "id", 0) == source_channel_id:
        return
    try:
        await fallback_channel.send(announcement_for(fallback_channel))
    except Exception:
        return


_LAST_CHAT_XP_ATTEMPT: Dict[int, float] = {}


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Skip the database entirely while a player is inside the chat-XP cooldown; previously
    # every message in the server ran several queries and a commit.
    now_mono = time.monotonic()
    if (
        message.guild is not None
        and now_mono - _LAST_CHAT_XP_ATTEMPT.get(message.author.id, -1e9) >= LEVELING_MESSAGE_COOLDOWN_SECONDS
        and db.is_registered_user(message.author.id)
    ):
        _LAST_CHAT_XP_ATTEMPT[message.author.id] = now_mono
        xp_roll = RNG.randint(LEVELING_MESSAGE_XP_MIN, LEVELING_MESSAGE_XP_MAX)
        level_result = db.add_message_xp(message.author.id, xp_roll)
        if level_result and level_result.get('leveled_up'):
            await send_level_up_announcement(message, level_result)

    ctx = await bot.get_context(message)
    if ctx.command is not None:
        command_name = str(getattr(ctx.command, "qualified_name", "")).split()[0].lower()
        is_registered = db.is_registered_user(message.author.id)
        if not is_registered:
            if command_name != "start":
                await send_unregistered_prompt(ctx)
                return
        # The beginner tutorial is optional. Registered players can use all commands immediately,
        # while `;tutorial` remains available for free tutorial rewards.

    await bot.process_commands(message)


_STARTUP_TASKS_DONE = False


@bot.event
async def on_ready():
    # Discord fires on_ready again after every reconnect. The startup work below only
    # needs to run once per process; repeating it froze the bot on each reconnect.
    global _STARTUP_TASKS_DONE
    if _STARTUP_TASKS_DONE:
        print(f"Reconnected as {bot.user}.")
        return
    _STARTUP_TASKS_DONE = True
    init_support_cards()
    maybe_run_daily_arena_reset()
    maybe_run_guild_boss_attempt_reset()
    if not arena_daily_reset_loop.is_running():
        arena_daily_reset_loop.start()
    count = scan_cards_directory(CARDS_DIR)
    refreshed_boss_stats = sync_boss_monster_base_stats()
    db.cleanup_special_monsters_and_compensate()
    granted_special_compensation = 0
    updated_special_compensation = await backfill_special_monster_compensation_mail_usernames()
    try:
        restore_moderation_views()
    except Exception as exc:
        log.error("Restoring moderation buttons failed", exc_info=exc)
    pending_requests = db.list_pending_profile_tag_uploads()
    restored = 0
    for row in pending_requests:
        try:
            restored += await send_profile_tag_moderation_request(str(row["request_id"]))
        except Exception:
            continue
    print(
        f"Logged in as {bot.user} | synced {count} monster definitions from {CARDS_DIR} | "
        f"refreshed {refreshed_boss_stats} Extra Deck monster stat row(s) | "
        f"granted {granted_special_compensation} special compensation mail(s) | "
        f"updated {updated_special_compensation} existing special compensation mail(s) | "
        f"resent {restored} profile tag moderation DM(s)"
    )


@tasks.loop(minutes=1)
async def arena_daily_reset_loop():
    maybe_run_daily_arena_reset()
    maybe_run_guild_boss_attempt_reset()
    try:
        # One restorable copy per day even when the bot runs for weeks without a restart.
        await asyncio.to_thread(backup_database_daily)
    except Exception as exc:
        log.error("Daily database backup failed", exc_info=exc)


@arena_daily_reset_loop.before_loop
async def before_arena_daily_reset_loop():
    await bot.wait_until_ready()
