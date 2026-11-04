"""Campaign progress, rewards and enemy teams."""

from __future__ import annotations

import math
import random
import sqlite3
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord

from kuribot.config import (
    CAMPAIGN_AREAS, CAMPAIGN_BATTLE_HEARTS, CAMPAIGN_DUELIST_ORDER,
    CAMPAIGN_HARD_AREA_CLEAR_EQUIP_SPELLS, CAMPAIGN_HARD_AREA_CLEAR_PREMIUM_TICKETS,
    CAMPAIGN_HARD_AREA_CLEAR_SUPPORT_CARDS, CAMPAIGN_HARD_DUELIST_10_WIN_SUPPORT_CARDS,
    CAMPAIGN_HARD_DUELIST_GOLD_RANGES, CAMPAIGN_HARD_DUELIST_TICKET_DROP,
    CAMPAIGN_NORMAL_AREA_CLEAR_PREMIUM_TICKETS, CAMPAIGN_NORMAL_AREA_CLEAR_TICKETS,
    CAMPAIGN_NORMAL_COMPLETION_GOLD, CAMPAIGN_NORMAL_COMPLETION_PREMIUM_TICKETS,
    CAMPAIGN_NORMAL_COMPLETION_TICKETS, CAMPAIGN_NORMAL_DUELIST_10_WIN_TICKETS,
    CAMPAIGN_NORMAL_DUELIST_GOLD_RANGES, CAMPAIGN_REQUIRED_WINS_PER_DUELIST,
    PACK_TICKET_CONVERSION_RATE, RNG, TEAM_SIZE,
)
from kuribot.models import Fighter
from kuribot.core import DEBUG_HARD_MODE_UNLOCK_USER_IDS
from kuribot.cards.equip_spells import random_equip_spell
from kuribot.database import Database, db
from kuribot.monsters import stable_seed_for_name
from kuribot.cards.support_cards import random_support_card
from kuribot.battle.fighters import make_fighter
from kuribot.battle.stats import scale_fighter_stats
from kuribot.duelists import DUELIST_DATA, slugify_duelist_key
from kuribot.dueling_grounds import (
    _append_duelist_name_prefix_rows, _append_zexal_utopia_rows, _is_zexal_utopia_duelist,
    _sample_enemy_rows_for_pool,
)


def campaign_mode_key(hard_mode: bool = False) -> str:
    return "hard" if bool(hard_mode) else "normal"


def init_campaign_tables(database: Database):
    database.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_progress (
            user_id INTEGER NOT NULL,
            area_key TEXT NOT NULL,
            cleared_stage INTEGER NOT NULL DEFAULT 0,
            area_reward_claimed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, area_key)
        )
        """
    )
    database.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_duelist_progress (
            user_id INTEGER NOT NULL,
            area_key TEXT NOT NULL,
            duelist_key TEXT NOT NULL,
            mode_key TEXT NOT NULL DEFAULT 'normal',
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, area_key, duelist_key, mode_key)
        )
        """
    )
    database.conn.commit()

    progress_columns = {row[1] for row in database.conn.execute("PRAGMA table_info(campaign_duelist_progress)").fetchall()}
    if progress_columns and "mode_key" not in progress_columns:
        database.conn.executescript(
            """
            ALTER TABLE campaign_duelist_progress RENAME TO campaign_duelist_progress_legacy;
            CREATE TABLE campaign_duelist_progress (
                user_id INTEGER NOT NULL,
                area_key TEXT NOT NULL,
                duelist_key TEXT NOT NULL,
                mode_key TEXT NOT NULL DEFAULT 'normal',
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, area_key, duelist_key, mode_key)
            );
            INSERT OR IGNORE INTO campaign_duelist_progress(user_id, area_key, duelist_key, mode_key, wins, losses, draws)
            SELECT user_id, area_key, duelist_key, 'normal', wins, losses, draws
            FROM campaign_duelist_progress_legacy;
            DROP TABLE campaign_duelist_progress_legacy;
            """
        )
        progress_columns = {row[1] for row in database.conn.execute("PRAGMA table_info(campaign_duelist_progress)").fetchall()}
    if progress_columns and "losses" not in progress_columns:
        database.conn.execute("ALTER TABLE campaign_duelist_progress ADD COLUMN losses INTEGER NOT NULL DEFAULT 0")
    if progress_columns and "draws" not in progress_columns:
        database.conn.execute("ALTER TABLE campaign_duelist_progress ADD COLUMN draws INTEGER NOT NULL DEFAULT 0")
    database.conn.commit()


    database.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_reward_claims (
            user_id INTEGER NOT NULL,
            area_key TEXT NOT NULL,
            duelist_key TEXT NOT NULL DEFAULT '',
            mode_key TEXT NOT NULL DEFAULT 'normal',
            reward_key TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, area_key, duelist_key, mode_key, reward_key)
        )
        """
    )
    database.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_completion_rewards (
            user_id INTEGER NOT NULL,
            mode_key TEXT NOT NULL,
            completion_claimed_at TEXT,
            choice_claimed_at TEXT,
            choice_key TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, mode_key)
        )
        """
    )
    database.conn.commit()


def claim_campaign_reward(user_id: int, area_key: str, reward_key: str, *, duelist_key: str = '', hard_mode: bool = False) -> bool:
    mode_key = campaign_mode_key(hard_mode)
    cur = db.conn.execute(
        "INSERT OR IGNORE INTO campaign_reward_claims(user_id, area_key, duelist_key, mode_key, reward_key, claimed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, area_key, duelist_key, mode_key, reward_key, datetime.now(timezone.utc).isoformat()),
    )
    db.conn.commit()
    return cur.rowcount > 0


def get_campaign_completion_reward_row(user_id: int, hard_mode: bool = False) -> sqlite3.Row:
    mode_key = campaign_mode_key(hard_mode)
    db.ensure_user(user_id)
    db.conn.execute(
        "INSERT OR IGNORE INTO campaign_completion_rewards(user_id, mode_key, completion_claimed_at, choice_claimed_at, choice_key) VALUES (?, ?, NULL, NULL, '')",
        (user_id, mode_key),
    )
    db.conn.commit()
    return db.conn.execute(
        "SELECT user_id, mode_key, completion_claimed_at, choice_claimed_at, choice_key FROM campaign_completion_rewards WHERE user_id = ? AND mode_key = ?",
        (user_id, mode_key),
    ).fetchone()


def mark_campaign_completion_claimed(user_id: int, hard_mode: bool = False) -> bool:
    row = get_campaign_completion_reward_row(user_id, hard_mode=hard_mode)
    if row and row["completion_claimed_at"]:
        return False
    db.conn.execute(
        "UPDATE campaign_completion_rewards SET completion_claimed_at = ? WHERE user_id = ? AND mode_key = ?",
        (datetime.now(timezone.utc).isoformat(), user_id, campaign_mode_key(hard_mode)),
    )
    db.conn.commit()
    return True


def campaign_completion_claimed(user_id: int, hard_mode: bool = False) -> bool:
    row = get_campaign_completion_reward_row(user_id, hard_mode=hard_mode)
    return bool(row and row["completion_claimed_at"])


def campaign_hard_completion_choice_claimed(user_id: int) -> bool:
    row = get_campaign_completion_reward_row(user_id, hard_mode=True)
    return bool(row and row["choice_claimed_at"])


def campaign_hard_completion_choice_key(user_id: int) -> str:
    row = get_campaign_completion_reward_row(user_id, hard_mode=True)
    return str(row["choice_key"] or "") if row else ""


def claim_campaign_hard_completion_choice(user_id: int, choice_key: str) -> bool:
    row = get_campaign_completion_reward_row(user_id, hard_mode=True)
    if row and row["choice_claimed_at"]:
        return False
    db.conn.execute(
        "UPDATE campaign_completion_rewards SET choice_claimed_at = ?, choice_key = ? WHERE user_id = ? AND mode_key = 'hard'",
        (datetime.now(timezone.utc).isoformat(), str(choice_key or '').strip().lower(), user_id),
    )
    db.conn.commit()
    return True


def reset_all_campaign_progress() -> dict:
    reward_rows = db.conn.execute("SELECT COUNT(*) AS c FROM campaign_reward_claims").fetchone()
    completion_rows = db.conn.execute("SELECT COUNT(*) AS c FROM campaign_completion_rewards").fetchone()
    progress_rows = db.conn.execute("SELECT COUNT(*) AS c FROM campaign_progress").fetchone()
    duelist_rows = db.conn.execute("SELECT COUNT(*) AS c FROM campaign_duelist_progress").fetchone()
    db.conn.execute("DELETE FROM campaign_reward_claims")
    db.conn.execute("DELETE FROM campaign_completion_rewards")
    db.conn.execute("DELETE FROM campaign_progress")
    db.conn.execute("DELETE FROM campaign_duelist_progress")
    db.conn.execute("UPDATE users SET campaign_hard_mode_notified = 0")
    db.conn.commit()
    return {
        "reward_rows": int(reward_rows["c"] if reward_rows else 0),
        "completion_rows": int(completion_rows["c"] if completion_rows else 0),
        "progress_rows": int(progress_rows["c"] if progress_rows else 0),
        "duelist_rows": int(duelist_rows["c"] if duelist_rows else 0),
    }


def get_campaign_area(area_key: str) -> Optional[dict]:
    key = str(area_key or "").strip().lower()
    for area in CAMPAIGN_AREAS:
        if area["key"] == key:
            return area
    return None


def get_campaign_duelist_keys(area_key: str) -> List[str]:
    return list(CAMPAIGN_DUELIST_ORDER.get(str(area_key or "").strip().lower(), []))


def get_campaign_duelist(area_key: str, duelist_key: str) -> Optional[dict]:
    if duelist_key not in get_campaign_duelist_keys(area_key):
        return None
    return DUELIST_DATA.get(duelist_key)


def get_campaign_duelist_progress_row(user_id: int, area_key: str, duelist_key: str, hard_mode: bool = False) -> sqlite3.Row:
    area = get_campaign_area(area_key)
    if area is None:
        raise ValueError(f"Unknown campaign area: {area_key}")
    if get_campaign_duelist(area_key, duelist_key) is None:
        raise ValueError(f"Unknown campaign duelist: {area_key}:{duelist_key}")
    mode_key = campaign_mode_key(hard_mode)
    select_sql = "SELECT user_id, area_key, duelist_key, mode_key, wins, losses, draws FROM campaign_duelist_progress WHERE user_id = ? AND area_key = ? AND duelist_key = ? AND mode_key = ?"
    # Read first; only create the row when it is missing (this runs ~70x per campaign screen,
    # and used to insert + commit every single time).
    existing = db.conn.execute(select_sql, (user_id, area["key"], duelist_key, mode_key)).fetchone()
    if existing is not None:
        return existing
    db.ensure_user(user_id)
    db.conn.execute(
        "INSERT OR IGNORE INTO campaign_duelist_progress(user_id, area_key, duelist_key, mode_key, wins, losses, draws) VALUES (?, ?, ?, ?, 0, 0, 0)",
        (user_id, area["key"], duelist_key, mode_key),
    )
    db.conn.commit()
    row = db.conn.execute(
        "SELECT user_id, area_key, duelist_key, mode_key, wins, losses, draws FROM campaign_duelist_progress WHERE user_id = ? AND area_key = ? AND duelist_key = ? AND mode_key = ?",
        (user_id, area["key"], duelist_key, mode_key),
    ).fetchone()
    return row


def campaign_duelist_wins(user_id: int, area_key: str, duelist_key: str, hard_mode: bool = False) -> int:
    row = get_campaign_duelist_progress_row(user_id, area_key, duelist_key, hard_mode=hard_mode)
    return int(row["wins"] if row else 0)


def campaign_duelist_record(user_id: int, area_key: str, duelist_key: str, hard_mode: bool = False) -> Tuple[int, int, int]:
    row = get_campaign_duelist_progress_row(user_id, area_key, duelist_key, hard_mode=hard_mode)
    if row is None:
        return 0, 0, 0
    return int(row["wins"] or 0), int(row["losses"] or 0), int(row["draws"] or 0)


def campaign_duelist_record_text(user_id: int, area_key: str, duelist_key: str, hard_mode: bool = False) -> str:
    wins, losses, draws = campaign_duelist_record(user_id, area_key, duelist_key, hard_mode=hard_mode)
    return f"W/L/D: {wins}/{losses}/{draws}"


def campaign_area_total_wins(user_id: int, area_key: str, hard_mode: bool = False) -> int:
    return sum(campaign_duelist_wins(user_id, area_key, duelist_key, hard_mode=hard_mode) for duelist_key in get_campaign_duelist_keys(area_key))


def campaign_area_completed(user_id: int, area_key: str, hard_mode: bool = False) -> bool:
    duelist_keys = get_campaign_duelist_keys(area_key)
    return bool(duelist_keys) and all(campaign_duelist_wins(user_id, area_key, key, hard_mode=hard_mode) >= CAMPAIGN_REQUIRED_WINS_PER_DUELIST for key in duelist_keys)


def is_campaign_area_unlocked(user_id: int, area_key: str) -> bool:
    for idx, area in enumerate(CAMPAIGN_AREAS):
        if area["key"] != area_key:
            continue
        if idx == 0:
            return True
        previous = CAMPAIGN_AREAS[idx - 1]
        return campaign_area_completed(user_id, previous["key"])
    return False


def is_campaign_duelist_available(user_id: int, area_key: str, duelist_key: str) -> bool:
    return is_campaign_area_unlocked(user_id, area_key) and get_campaign_duelist(area_key, duelist_key) is not None


def campaign_hard_mode_unlocked(user_id: int) -> bool:
    try:
        normalized_user_id = int(user_id)
    except Exception:
        normalized_user_id = 0
    return normalized_user_id in DEBUG_HARD_MODE_UNLOCK_USER_IDS or campaign_area_completed(normalized_user_id, "battle_city")


def campaign_area_status_text(user_id: int, area_key: str, hard_mode: bool = False) -> str:
    area = get_campaign_area(area_key)
    if area is None:
        return "Unknown"
    if hard_mode and not campaign_hard_mode_unlocked(user_id):
        return "Locked"
    if not is_campaign_area_unlocked(user_id, area_key):
        return "Locked"
    total_needed = len(get_campaign_duelist_keys(area_key)) * CAMPAIGN_REQUIRED_WINS_PER_DUELIST
    total_wins = campaign_area_total_wins(user_id, area_key, hard_mode=hard_mode)
    if total_wins >= total_needed:
        return "Complete"
    return f"{total_wins}/{total_needed} wins"


def maybe_build_campaign_hard_mode_unlock_embed(user_id: int) -> Optional[discord.Embed]:
    if not campaign_hard_mode_unlocked(user_id):
        return None
    if db.campaign_hard_mode_notification_sent(user_id):
        return None
    db.mark_campaign_hard_mode_notified(user_id)
    embed = discord.Embed(
        title="Hard Mode Unlocked",
        description=(
            "Kuribot: You defeated every duelist in Battle City 5 times. "
            "Hard Mode is now available in Campaign. Expect aligned support decks, generated equip spells, and better rewards."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Open", value="Campaign → Hard Mode", inline=True)
    embed.add_field(name="Rewards", value="Higher gold, 1 ticket per duel, first-clear support/equip rewards, and a final reward choice", inline=True)
    return embed


def build_campaign_normal_completion_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Campaign Complete",
        description=(
            f"Kuribot: Congratulations, {user_name}! You cleared every area in Campaign Normal Mode and claimed the final completion reward."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Random Packs", value=f"+{math.ceil((CAMPAIGN_NORMAL_COMPLETION_TICKETS + CAMPAIGN_NORMAL_COMPLETION_PREMIUM_TICKETS) / PACK_TICKET_CONVERSION_RATE)}", inline=True)
    embed.add_field(name="Gold", value=f"+{CAMPAIGN_NORMAL_COMPLETION_GOLD:,}", inline=True)
    embed.set_footer(text="Campaign Normal completion rewards can only be claimed once.")
    return embed


def build_campaign_hard_completion_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Hard Mode Complete",
        description=(
            f"Kuribot: Incredible work, {user_name}. You defeated every Campaign Hard Mode area. Claim your Extra Deck Themed Pack below."
        ),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Reward",
        value="1 Extra Deck Themed Pack — contains 3 Extra Deck Monsters.",
        inline=False,
    )
    embed.set_footer(text="Campaign Hard Mode final reward can only be claimed once.")
    return embed


def campaign_hard_mode_campaign_completed(user_id: int) -> bool:
    return all(campaign_area_completed(user_id, area["key"], hard_mode=True) for area in CAMPAIGN_AREAS)


def _update_campaign_duelist_result(user_id: int, area_key: str, duelist_key: str, column: str, hard_mode: bool = False) -> sqlite3.Row:
    if column not in {"wins", "losses", "draws"}:
        raise ValueError(f"Unsupported campaign result column: {column}")
    mode_key = campaign_mode_key(hard_mode)
    row = get_campaign_duelist_progress_row(user_id, area_key, duelist_key, hard_mode=hard_mode)
    current_value = int(row[column] if row else 0)
    db.conn.execute(
        f"UPDATE campaign_duelist_progress SET {column} = ? WHERE user_id = ? AND area_key = ? AND duelist_key = ? AND mode_key = ?",
        (current_value + 1, user_id, area_key, duelist_key, mode_key),
    )
    db.conn.commit()
    return get_campaign_duelist_progress_row(user_id, area_key, duelist_key, hard_mode=hard_mode)


def campaign_battle_hearts_remaining(hearts_remaining: int, max_hearts: int = CAMPAIGN_BATTLE_HEARTS) -> int:
    if max_hearts <= 0:
        return 0
    return max(0, min(max_hearts, int(hearts_remaining)))


def award_campaign_duelist_loss(user_id: int, area_key: str, duelist_key: str, hard_mode: bool = False) -> Tuple[int, int, int]:
    row = _update_campaign_duelist_result(user_id, area_key, duelist_key, "losses", hard_mode=hard_mode)
    if row is None:
        return 0, 0, 0
    return int(row["wins"] or 0), int(row["losses"] or 0), int(row["draws"] or 0)


def award_campaign_duelist_draw(user_id: int, area_key: str, duelist_key: str, hard_mode: bool = False) -> Tuple[int, int, int]:
    row = _update_campaign_duelist_result(user_id, area_key, duelist_key, "draws", hard_mode=hard_mode)
    if row is None:
        return 0, 0, 0
    return int(row["wins"] or 0), int(row["losses"] or 0), int(row["draws"] or 0)


def award_campaign_duelist_win(user_id: int, user_name: str, area_key: str, duelist_key: str, hard_mode: bool = False) -> dict:
    area = get_campaign_area(area_key)
    if area is None:
        raise ValueError(f"Unknown campaign area: {area_key}")
    if get_campaign_duelist(area_key, duelist_key) is None:
        raise ValueError(f"Unknown campaign duelist: {area_key}:{duelist_key}")
    row = _update_campaign_duelist_result(user_id, area_key, duelist_key, "wins", hard_mode=hard_mode)
    new_wins = int(row["wins"] if row else 0)
    gold = campaign_gold_reward(area_key, hard_mode=hard_mode)
    tickets = campaign_stage_ticket_reward(hard_mode=hard_mode)
    db.add_gold(user_id, gold)
    if tickets:
        db.add_tickets(user_id, tickets)

    reward_summary = {
        "wins": new_wins,
        "gold": gold,
        "tickets": tickets,
        "area_clear_awarded": False,
        "area_clear_tickets": 0,
        "area_clear_premium_tickets": 0,
        "area_clear_support_cards": 0,
        "area_clear_equip_spells": 0,
        "duelist_milestone_awarded": False,
        "duelist_milestone_tickets": 0,
        "duelist_milestone_support_cards": 0,
        "normal_completion_awarded": False,
        "hard_completion_awarded": False,
    }

    if new_wins >= 10 and claim_campaign_reward(user_id, area_key, "ten_wins", duelist_key=duelist_key, hard_mode=hard_mode):
        reward_summary["duelist_milestone_awarded"] = True
        if hard_mode:
            reward_summary["duelist_milestone_support_cards"] = CAMPAIGN_HARD_DUELIST_10_WIN_SUPPORT_CARDS
            for _ in range(CAMPAIGN_HARD_DUELIST_10_WIN_SUPPORT_CARDS):
                db.add_support_card_to_inventory(user_id, random_support_card().support_card_id)
        else:
            reward_summary["duelist_milestone_tickets"] = CAMPAIGN_NORMAL_DUELIST_10_WIN_TICKETS
            db.add_tickets(user_id, CAMPAIGN_NORMAL_DUELIST_10_WIN_TICKETS)

    if campaign_area_completed(user_id, area_key, hard_mode=hard_mode) and claim_campaign_reward(user_id, area_key, "area_clear", hard_mode=hard_mode):
        reward_summary["area_clear_awarded"] = True
        if hard_mode:
            reward_summary["area_clear_support_cards"] = CAMPAIGN_HARD_AREA_CLEAR_SUPPORT_CARDS
            reward_summary["area_clear_equip_spells"] = CAMPAIGN_HARD_AREA_CLEAR_EQUIP_SPELLS
            reward_summary["area_clear_premium_tickets"] = CAMPAIGN_HARD_AREA_CLEAR_PREMIUM_TICKETS
            for _ in range(CAMPAIGN_HARD_AREA_CLEAR_SUPPORT_CARDS):
                db.add_support_card_to_inventory(user_id, random_support_card().support_card_id)
            for _ in range(CAMPAIGN_HARD_AREA_CLEAR_EQUIP_SPELLS):
                db.add_equip_spell_to_inventory(user_id, random_equip_spell().equip_spell_id)
            db.add_premium_tickets(user_id, CAMPAIGN_HARD_AREA_CLEAR_PREMIUM_TICKETS)
        else:
            reward_summary["area_clear_tickets"] = CAMPAIGN_NORMAL_AREA_CLEAR_TICKETS
            reward_summary["area_clear_premium_tickets"] = CAMPAIGN_NORMAL_AREA_CLEAR_PREMIUM_TICKETS
            db.add_tickets(user_id, CAMPAIGN_NORMAL_AREA_CLEAR_TICKETS)
            db.add_premium_tickets(user_id, CAMPAIGN_NORMAL_AREA_CLEAR_PREMIUM_TICKETS)

    if not hard_mode and all(campaign_area_completed(user_id, area["key"], hard_mode=False) for area in CAMPAIGN_AREAS):
        if mark_campaign_completion_claimed(user_id, hard_mode=False):
            db.add_tickets(user_id, CAMPAIGN_NORMAL_COMPLETION_TICKETS)
            db.add_gold(user_id, CAMPAIGN_NORMAL_COMPLETION_GOLD)
            db.add_premium_tickets(user_id, CAMPAIGN_NORMAL_COMPLETION_PREMIUM_TICKETS)
            reward_summary["normal_completion_awarded"] = True

    if hard_mode and all(campaign_area_completed(user_id, area["key"], hard_mode=True) for area in CAMPAIGN_AREAS):
        if mark_campaign_completion_claimed(user_id, hard_mode=True):
            reward_summary["hard_completion_awarded"] = True

    return reward_summary


CAMPAIGN_AREA_STAT_BONUS = 0.0
CAMPAIGN_DUELIST_STAT_BONUS = 0.0
RANKED_DUELIST_STAT_MULTIPLIER = 1.50
CAMPAIGN_NORMAL_ENEMY_STAT_MULTIPLIERS = {
    "domino_city": 1.00,
    "duelist_kingdom": 1.15,
    "battle_city": 1.30,
    "kaiba_corp": 1.45,
}
CAMPAIGN_HARD_ENEMY_STAT_MULTIPLIERS = {
    # Hard Mode already gains extra pressure from aligned generated support decks,
    # generated equip spells, and boosted rewards. Keep the base stat curve aligned
    # with the Normal Mode area ladder unless explicitly tuned further.
    **CAMPAIGN_NORMAL_ENEMY_STAT_MULTIPLIERS,
}


def campaign_enemy_stat_multiplier(area_key: str, hard_mode: bool = False) -> float:
    area_key = str(area_key or "").strip().lower()
    mapping = CAMPAIGN_HARD_ENEMY_STAT_MULTIPLIERS if hard_mode else CAMPAIGN_NORMAL_ENEMY_STAT_MULTIPLIERS
    return float(mapping.get(area_key, 1.0 + CAMPAIGN_AREA_STAT_BONUS + CAMPAIGN_DUELIST_STAT_BONUS))


def campaign_gold_reward(area_key: str, hard_mode: bool = False) -> int:
    area_key = str(area_key or "").strip().lower()
    mapping = CAMPAIGN_HARD_DUELIST_GOLD_RANGES if hard_mode else CAMPAIGN_NORMAL_DUELIST_GOLD_RANGES
    low, high = mapping.get(area_key, (100, 100))
    low = int(low)
    high = int(high)
    if high < low:
        low, high = high, low
    return RNG.randint(low, high)


def campaign_stage_ticket_reward(hard_mode: bool = False) -> int:
    return int(CAMPAIGN_HARD_DUELIST_TICKET_DROP if hard_mode else 0)


def _find_campaign_monster_rows(base_name: str) -> List[sqlite3.Row]:
    matches = [row for row in db.all_monsters() if str(row["name"]).strip().lower() == str(base_name).strip().lower()]
    return sorted(matches, key=lambda row: str(row["monster_id"]))


def build_campaign_enemy_team(area_key: str, duelist_key: str, hard_mode: bool = False) -> Tuple[List[Fighter], dict]:
    area = get_campaign_area(area_key)
    duelist = get_campaign_duelist(area_key, duelist_key)
    if area is None or duelist is None:
        raise ValueError(f"Unknown campaign duel: {area_key}:{duelist_key}")
    pool: List[sqlite3.Row] = []
    seen_ids = set()
    normalized_duelist_key = slugify_duelist_key(duelist_key)
    use_global_fallback = True
    if _is_zexal_utopia_duelist(normalized_duelist_key):
        _append_zexal_utopia_rows(pool, seen_ids)
        use_global_fallback = False
    else:
        for base_name in duelist.get("pool", []):
            for row in _find_campaign_monster_rows(base_name):
                monster_id = str(row["monster_id"])
                if monster_id in seen_ids:
                    continue
                seen_ids.add(monster_id)
                pool.append(row)
    if normalized_duelist_key == "tetsu_trudge":
        _append_duelist_name_prefix_rows(pool, seen_ids, ("Goyo", "Stygian", "Worm"))
        use_global_fallback = False
    if len(pool) < TEAM_SIZE and use_global_fallback:
        fallback_pool = sorted(db.all_monsters(), key=lambda row: str(row["monster_id"]))
        for row in fallback_pool:
            monster_id = str(row["monster_id"])
            if monster_id in seen_ids:
                continue
            seen_ids.add(monster_id)
            pool.append(row)
            if len(pool) >= TEAM_SIZE:
                break
    if len(pool) < TEAM_SIZE:
        raise RuntimeError("Not enough monsters in the pool for campaign battles.")
    seed = stable_seed_for_name(f"campaign:{area_key}:{duelist_key}:{int(time.time() // 60)}")
    rng = random.Random(seed)
    enemy_rows = _sample_enemy_rows_for_pool(pool, TEAM_SIZE, rng)
    multiplier = campaign_enemy_stat_multiplier(area_key, hard_mode=hard_mode)
    team: List[Fighter] = []
    for row in enemy_rows:
        fighter = make_fighter(row)
        scale_fighter_stats(fighter, multiplier)
        team.append(fighter)
    return team, duelist
