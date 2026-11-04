"""The Discord bot object, the logger, admin checks and the ;debug channel lock."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands

from kuribot.config import (
    ADMIN_COMMAND_USER_IDS, BOT_CREATOR_IDS, DEBUG_ALLOWED_CHANNEL_ID, PREFIX,
    PROFILE_TAGS_APPROVED_DIR, PROFILE_TAGS_DIR, PROFILE_TAGS_PENDING_DIR,
)


DEBUG_CHANNEL_LOCK_ENABLED = False
DEBUG_HARD_MODE_UNLOCK_USER_IDS: set[int] = set()


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
for _dir in (PROFILE_TAGS_DIR, PROFILE_TAGS_PENDING_DIR, PROFILE_TAGS_APPROVED_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None, case_insensitive=True)

BINDER_COMMAND_DEBOUNCE_SECONDS = 2.0
_recent_binder_invocations: dict[tuple[int, int], float] = {}

_ORIGINAL_MESSAGEABLE_SEND = discord.abc.Messageable.send


async def _debug_guarded_messageable_send(self, *args, **kwargs):
    if not is_debug_send_allowed(self):
        return None
    return await _ORIGINAL_MESSAGEABLE_SEND(self, *args, **kwargs)


discord.abc.Messageable.send = _debug_guarded_messageable_send


def is_bot_creator(user_or_id: object) -> bool:
    raw_value = getattr(user_or_id, 'id', user_or_id)
    try:
        return int(raw_value) in BOT_CREATOR_IDS
    except Exception:
        return False


def is_admin_command_user(user_or_id: object) -> bool:
    raw_value = getattr(user_or_id, 'id', user_or_id)
    try:
        return int(raw_value) in ADMIN_COMMAND_USER_IDS
    except Exception:
        return False


def get_messageable_channel_id(target: object) -> Optional[int]:
    raw_id = getattr(target, "id", None)
    if raw_id is None:
        channel = getattr(target, "channel", None)
        raw_id = getattr(channel, "id", None)
    try:
        return int(raw_id) if raw_id is not None else None
    except Exception:
        return None


def is_debug_send_allowed(target: object) -> bool:
    if not DEBUG_CHANNEL_LOCK_ENABLED:
        return True
    channel_id = get_messageable_channel_id(target)
    return channel_id == DEBUG_ALLOWED_CHANNEL_ID


def set_debug_channel_lock(enabled: bool) -> bool:
    global DEBUG_CHANNEL_LOCK_ENABLED
    previous = DEBUG_CHANNEL_LOCK_ENABLED
    DEBUG_CHANNEL_LOCK_ENABLED = bool(enabled)
    if not DEBUG_CHANNEL_LOCK_ENABLED:
        DEBUG_HARD_MODE_UNLOCK_USER_IDS.clear()
    return previous


def debug_channel_lock_enabled() -> bool:
    """Whether ;debug mode is on (read through this, since set_debug_channel_lock rebinds the flag)."""
    return bool(DEBUG_CHANNEL_LOCK_ENABLED)


def grant_debug_hard_mode_unlock(user_id: int):
    DEBUG_HARD_MODE_UNLOCK_USER_IDS.add(int(user_id))


def debug_hard_mode_unlock_active(user_id: int) -> bool:
    try:
        normalized_user_id = int(user_id)
    except Exception:
        return False
    return normalized_user_id in DEBUG_HARD_MODE_UNLOCK_USER_IDS


def debug_channel_lock_status_text() -> str:
    state = "ON" if DEBUG_CHANNEL_LOCK_ENABLED else "OFF"
    return f"Debug channel lock is **{state}**. Allowed channel: <#{DEBUG_ALLOWED_CHANNEL_ID}>"


log = logging.getLogger("kuribot")
