"""Looking up Discord user names, DMs and message-length limits."""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Sequence, Tuple

import discord

from kuribot.settings import PING_DM_SETTINGS_FOOTER
from kuribot.core import bot
from kuribot.database import db


DISCORD_MESSAGE_LIMIT = 2000


def fit_discord_message_content(content: Optional[str], *, reserve: int = 0) -> Optional[str]:
    if content is None:
        return None
    text = str(content)
    limit = max(1, DISCORD_MESSAGE_LIMIT - max(0, reserve))
    if len(text) <= limit:
        return text
    ellipsis = "\n..."
    trim_limit = max(1, limit - len(ellipsis))
    trimmed = text[:trim_limit]
    split_at = max(trimmed.rfind("\n"), trimmed.rfind(" "))
    if split_at >= max(1, trim_limit // 2):
        trimmed = trimmed[:split_at]
    return trimmed.rstrip() + ellipsis


async def resolve_discord_user_label(user_id: int, guild: Optional[discord.Guild] = None) -> str:
    user_id = int(user_id)
    if guild is not None:
        member = guild.get_member(user_id)
        if member is not None:
            value = str(getattr(member, "display_name", "") or getattr(member, "global_name", "") or getattr(member, "name", "")).strip()
            if value:
                return value
    cached_user = bot.get_user(user_id)
    if cached_user is not None:
        value = str(getattr(cached_user, "global_name", "") or getattr(cached_user, "display_name", "") or getattr(cached_user, "name", "")).strip()
        if value:
            return value
    try:
        fetched_user = await bot.fetch_user(user_id)
    except Exception:
        fetched_user = None
    if fetched_user is not None:
        value = str(getattr(fetched_user, "global_name", "") or getattr(fetched_user, "display_name", "") or getattr(fetched_user, "name", "")).strip()
        if value:
            return value
    return f"User {user_id}"


async def resolve_guild_member_name_map(user_ids: Sequence[int], guild: Optional[discord.Guild] = None) -> Dict[int, str]:
    name_map: Dict[int, str] = {}
    for raw_user_id in user_ids:
        user_id = int(raw_user_id)
        name_map[user_id] = await resolve_discord_user_label(user_id, guild)
    return name_map


# Names looked up from Discord are remembered for a while, so leaderboards and arena lists
# don't make one Discord request per player every time (players who left the server are
# never in discord.py's own cache). Failed lookups are retried sooner.
_USER_NAME_CACHE: Dict[int, Tuple[float, str]] = {}
_USER_NAME_TTL_SECONDS = 6 * 60 * 60
_USER_NAME_RETRY_SECONDS = 15 * 60


async def resolve_global_discord_user_name(user_id: int) -> str:
    user_id = int(user_id)
    now = time.monotonic()
    cached_user = bot.get_user(user_id)
    if cached_user is not None:
        value = str(getattr(cached_user, 'global_name', '') or getattr(cached_user, 'name', '') or '').strip()
        if value:
            _USER_NAME_CACHE[user_id] = (now + _USER_NAME_TTL_SECONDS, value)
            return value
    remembered = _USER_NAME_CACHE.get(user_id)
    if remembered is not None and remembered[0] > now:
        return remembered[1]
    try:
        fetched_user = await bot.fetch_user(user_id)
    except Exception:
        fetched_user = None
    if fetched_user is not None:
        value = str(getattr(fetched_user, 'global_name', '') or getattr(fetched_user, 'name', '') or '').strip()
        if value:
            _USER_NAME_CACHE[user_id] = (now + _USER_NAME_TTL_SECONDS, value)
            return value
    fallback = f'User {user_id}'
    _USER_NAME_CACHE[user_id] = (now + _USER_NAME_RETRY_SECONDS, fallback)
    return fallback


async def resolve_global_discord_user_names(user_ids: Sequence[int]) -> Dict[int, str]:
    """Names for several players at once (looked up in parallel)."""
    unique_ids = list(dict.fromkeys(int(uid) for uid in user_ids))
    names = await asyncio.gather(*(resolve_global_discord_user_name(uid) for uid in unique_ids))
    return dict(zip(unique_ids, names))


async def send_ping_notification_dm(user_id: int, message_body: str, *, sender_name: str, reward_text: Optional[str] = None) -> bool:
    if not db.is_registered_user(user_id) or not db.notifications_enabled(user_id):
        return False
    user = bot.get_user(int(user_id))
    if user is None:
        try:
            user = await bot.fetch_user(int(user_id))
        except Exception:
            user = None
    if user is None:
        return False
    body_lines = [
        "Hi! Kuribot here.",
        "",
        "You were included in an inbox ping from a Kuribot Admin.",
        "",
        "Message:",
        str(message_body).strip(),
    ]
    if reward_text:
        body_lines.extend([
            "",
            "Please check your inbox for your attached rewards and claim them when you're ready.",
        ])
    body_lines.extend(["", PING_DM_SETTINGS_FOOTER])
    try:
        await user.send("\n".join(body_lines))
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def resolve_mail_username(user_id: int, guild: Optional[discord.Guild] = None) -> str:
    user_id = int(user_id)
    if guild is not None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None
        if member is not None:
            for attr in ('name', 'global_name', 'display_name'):
                value = str(getattr(member, attr, '') or '').strip()
                if value:
                    return value
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            user = None
    if user is not None:
        for attr in ('name', 'global_name', 'display_name'):
            value = str(getattr(user, attr, '') or '').strip()
            if value:
                return value
    return f'User {user_id}'
