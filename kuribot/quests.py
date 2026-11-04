"""Daily quests."""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple

from kuribot.config import CAMPAIGN_AREAS, QUEST_TICKETS, RNG
from kuribot.timeutil import is_current_reset_window, utc_now
from kuribot.database import db
from kuribot.dueling_grounds import get_dueling_grounds_duelist_data
from kuribot.campaign import (
    get_campaign_duelist, get_campaign_duelist_keys, is_campaign_area_unlocked,
)


def build_campaign_quest_pool(user_id: int) -> List[Tuple[str, str, str]]:
    pool: List[Tuple[str, str, str]] = []
    for area in CAMPAIGN_AREAS:
        area_key = str(area.get("key") or "").strip()
        if not area_key or not is_campaign_area_unlocked(user_id, area_key):
            continue
        for duelist_key in get_campaign_duelist_keys(area_key):
            duelist = get_campaign_duelist(area_key, duelist_key) or {"name": duelist_key}
            duelist_name = str(duelist.get("name", duelist_key))
            pool.append(("campaign", f"{area_key}:{duelist_key}", f"Campaign • {area.get('label', area_key)} • {duelist_name}"))
    return pool


def build_normal_quest_pool() -> List[Tuple[str, str, str]]:
    duelists = get_dueling_grounds_duelist_data()
    pool: List[Tuple[str, str, str]] = []
    for duelist_key, duelist in duelists.items():
        duelist_name = str(duelist.get("name", duelist_key))
        pool.append(("normal", str(duelist_key), f"Normal • {duelist_name}"))
    return pool


def generate_random_quest_for_user(user_id: int) -> Tuple[str, str, str]:
    quest_pool = [*build_campaign_quest_pool(user_id), *build_normal_quest_pool()]
    if not quest_pool:
        return ("normal", "", "Normal • Any Duelist")
    return RNG.choice(quest_pool)


def ensure_player_quest(user_id: int, *, force_refresh: bool = False) -> sqlite3.Row:
    now = utc_now()
    quest_row = db.get_current_quest_row(user_id)
    needs_refresh = force_refresh or quest_row is None
    if quest_row is not None:
        generated_at_raw = quest_row["generated_at"]
        needs_refresh = needs_refresh or (not is_current_reset_window(generated_at_raw, now))
    if needs_refresh:
        quest_mode, target_key, target_label = generate_random_quest_for_user(user_id)
        db.set_current_quest(user_id, quest_mode, target_key, target_label, generated_at=now)
        quest_row = db.get_current_quest_row(user_id)
    return quest_row


def describe_quest_row(quest_row: sqlite3.Row) -> str:
    quest_mode = str(quest_row["quest_mode"] or "").strip().lower()
    target_key = str(quest_row["target_key"] or "").strip()
    target_label = str(quest_row["target_label"] or target_key or "Unknown Duelist")
    completed = bool(quest_row["completed_at"])
    if quest_mode == "campaign":
        return f"Defeat **{target_label}** in Campaign." + (" ✅" if completed else "")
    return f"Defeat **{target_label}** in Normal mode." + (" ✅" if completed else "")


def maybe_complete_player_quest(user_id: int, quest_mode: str, target_key: str) -> Optional[str]:
    quest_row = ensure_player_quest(user_id)
    if quest_row is None:
        return None
    current_mode = str(quest_row["quest_mode"] or "").strip().lower()
    current_target_key = str(quest_row["target_key"] or "").strip()
    if quest_row["completed_at"]:
        return None
    if current_mode != str(quest_mode or "").strip().lower() or current_target_key != str(target_key or "").strip():
        return None
    if not db.mark_quest_completed(user_id):
        return None
    db.add_tickets(user_id, QUEST_TICKETS)
    return f" Quest complete! +{QUEST_TICKETS} Random Pack."
