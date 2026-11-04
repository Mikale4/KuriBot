"""Arena ladder: schema, daily reset, rankings and opponents."""

from __future__ import annotations

import logging
import random
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from kuribot.config import ATTRIBUTES, DUELING_GROUNDS_BATTLE_HEARTS, RNG, TEAM_SIZE
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.timeutil import current_et_date_string, current_reset_window_bounds, utc_now
from kuribot.database import db
from kuribot.discord_helpers import resolve_global_discord_user_name
from kuribot.monsters import decode_monster_types, resolve_row_combat_stats, team_slots_from_csv
from kuribot.cards.support_decks import build_support_runtime_from_cards
from kuribot.battle.fighters import make_fighter_from_id
from kuribot.opponents import build_aligned_support_cards_for_team, format_duelist_display_name
from kuribot.dueling_grounds import get_dueling_grounds_duelist_data
from kuribot.ranked import build_ranked_dueling_grounds_enemy_team, RANKED_DUELIST_SUPPORT_BONUSES
from kuribot.mail import reward_type_label


ARENA_STARTING_ELO = 500
ARENA_MATCH_COUNT = 9
ARENA_CHALLENGE_RANK_RANGE = 20
ARENA_LEADERBOARD_PAGE_SIZE = 8
ARENA_LEADERBOARD_LIMIT = 30
ARENA_BATTLE_HEARTS = DUELING_GROUNDS_BATTLE_HEARTS
ARENA_DUELIST_RANK_MIN = 1
ARENA_DUELIST_RANK_MAX = 174
ARENA_MIDNIGHT_REWARD_SENDER = 'Arena Committee'


def ensure_arena_schema():
    user_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(users)").fetchall()}
    if user_columns and "arena_wins" not in user_columns:
        db.conn.execute("ALTER TABLE users ADD COLUMN arena_wins INTEGER NOT NULL DEFAULT 0")
    if user_columns and "arena_losses" not in user_columns:
        db.conn.execute("ALTER TABLE users ADD COLUMN arena_losses INTEGER NOT NULL DEFAULT 0")
    if user_columns and "arena_draws" not in user_columns:
        db.conn.execute("ALTER TABLE users ADD COLUMN arena_draws INTEGER NOT NULL DEFAULT 0")
    if user_columns and "arena_elo" not in user_columns:
        db.conn.execute(f"ALTER TABLE users ADD COLUMN arena_elo INTEGER NOT NULL DEFAULT {ARENA_STARTING_ELO}")
    if user_columns and "arena_peak_elo" not in user_columns:
        db.conn.execute(f"ALTER TABLE users ADD COLUMN arena_peak_elo INTEGER NOT NULL DEFAULT {ARENA_STARTING_ELO}")
    if user_columns and "arena_tickets" not in user_columns:
        db.conn.execute("ALTER TABLE users ADD COLUMN arena_tickets INTEGER NOT NULL DEFAULT 0")

    mailbox_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(mailbox_messages)").fetchall()}
    if mailbox_columns and "reward_bundle_json" not in mailbox_columns:
        db.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN reward_bundle_json TEXT NOT NULL DEFAULT ''")

    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_teams (
            user_id INTEGER PRIMARY KEY,
            slots_csv TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_duelists (
            duelist_key TEXT PRIMARY KEY,
            arena_elo INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.conn.commit()
    ensure_arena_duelist_state()


def arena_duelist_base_elo_from_rank(rank_number: int) -> int:
    return 720 + ((ARENA_DUELIST_RANK_MAX - int(rank_number)) * 38)


def ensure_arena_duelist_state():
    duelists = list(sorted(get_dueling_grounds_duelist_data().items(), key=lambda item: str(item[1].get('name', item[0])).lower()))
    existing_rows = {str(row['duelist_key']): row for row in db.arena_duelist_rows()}
    rank_numbers = list(range(ARENA_DUELIST_RANK_MIN, ARENA_DUELIST_RANK_MAX + 1))
    random.Random(174).shuffle(rank_numbers)
    if len(rank_numbers) < len(duelists):
        raise ValueError('Not enough Arena duelist rank slots for the current duelist roster.')
    elo_map: Dict[str, int] = {}
    changed = False
    for (duelist_key, _duelist), rank_number in zip(duelists, rank_numbers, strict=False):
        if duelist_key in existing_rows:
            elo_map[duelist_key] = int(existing_rows[duelist_key]['arena_elo'] or arena_duelist_base_elo_from_rank(rank_number))
        else:
            elo_map[duelist_key] = arena_duelist_base_elo_from_rank(rank_number)
            changed = True
    if set(existing_rows) != set(elo_map):
        changed = True
    if changed:
        db.conn.execute('DELETE FROM arena_duelists')
        if elo_map:
            db.bulk_set_arena_duelist_elos(elo_map)
        else:
            db.conn.commit()


# Do not normalize user Arena ELO on every bot boot.
# That reset should only happen during the daily Arena reset window,
# otherwise a restart wipes out the ladder climb users made since the last reset.
def arena_reward_bundle_for_title(rank_title: str) -> List[Dict[str, int | str]]:
    normalized = str(rank_title or '').strip().lower()
    if normalized in {'king of games', 'legend', 'champion'}:
        return [
            {'type': 'tickets', 'amount': 10},
            {'type': 'premium_tickets', 'amount': 5},
            {'type': 'cards', 'amount': 10},
            {'type': 'gold', 'amount': 20000},
            {'type': 'equip_spells', 'amount': 3},
        ]
    if normalized in {'diamond', 'platinum'}:
        return [
            {'type': 'tickets', 'amount': 10},
            {'type': 'premium_tickets', 'amount': 3},
            {'type': 'cards', 'amount': 5},
            {'type': 'gold', 'amount': 10000},
            {'type': 'equip_spells', 'amount': 1},
        ]
    if normalized == 'copper':
        return []
    return [
        {'type': 'tickets', 'amount': 10},
        {'type': 'premium_tickets', 'amount': 1},
        {'type': 'cards', 'amount': 1},
        {'type': 'gold', 'amount': 5000},
    ]


def format_reward_bundle(bundle: Sequence[Dict[str, int | str]]) -> str:
    parts: List[str] = []
    for reward in bundle:
        reward_type = str(reward.get('type') or '').strip().lower()
        amount = int(reward.get('amount') or 0)
        if amount <= 0:
            continue
        parts.append(reward_type_label(reward_type, amount))
    return ', '.join(parts) if parts else 'No rewards'


def build_arena_reward_mail_body(user_name: str, rank_number: int, rank_title: str, bundle: Sequence[Dict[str, int | str]]) -> str:
    reward_text = format_reward_bundle(bundle)
    greeting = f'Congratulations, {user_name}!\n\n' if user_name else 'Congratulations!\n\n'
    base = (
        greeting
        + f"You finished the Arena reward cycle at {rank_title} (Rank #{rank_number}).\n\n"
    )
    if bundle:
        return base + f'Your Arena rewards are attached to this mail:\n{reward_text}\n\nKeep climbing the ladder.'
    return base + 'Copper ranks do not receive item rewards this cycle, but your Arena standing has been recorded. Keep climbing.'


def maybe_run_daily_arena_reset(now_utc: Optional[datetime] = None) -> Tuple[int, bool]:
    """Award Arena rewards once per Eastern calendar day without resetting player ranks."""
    now_utc = now_utc or utc_now()
    today_et = current_et_date_string(now_utc)
    last_reward_date = db.get_arena_meta('last_daily_reward_date', '')
    if last_reward_date == today_et:
        return 0, False

    try:
        # Snapshot the live ladder exactly as it stands at the midnight reward cycle.
        # Player and NPC Arena ELO/rank positions are intentionally preserved so
        # players keep their current rank after rewards are issued.
        entries = assign_arena_rank_positions([*build_arena_duelist_profiles(), *build_arena_user_leaderboard_entries()])
        # Claim the day BEFORE mailing. The date used to be saved only after every mail was
        # sent, so one failure part-way through made the once-a-minute loop re-mail every
        # player before the failing one (again and again) until the error went away.
        db.set_arena_meta('last_daily_reward_date', today_et)
        awarded = 0
        failed = 0
        created_at = now_utc.isoformat()
        for entry in entries:
            if entry.get('kind') != 'user':
                continue
            user_id = int(entry.get('id') or 0)
            if user_id <= 0:
                continue
            try:
                rank_number = int(entry.get('rank_position') or get_user_arena_rank_position(user_id))
                rank_title = arena_rank_title(rank_number)
                bundle = arena_reward_bundle_for_title(rank_title)
                if not any(int(item.get('amount') or 0) > 0 for item in bundle):
                    # Ranks with no reward used to get an empty mail every day (16k+ of them).
                    continue
                db.create_mail(
                    user_id,
                    build_arena_reward_mail_body('', rank_number, rank_title, bundle),
                    sender_name=ARENA_MIDNIGHT_REWARD_SENDER,
                    reward_bundle=bundle,
                    created_at=created_at,
                )
                awarded += 1
            except Exception as entry_exc:
                failed += 1
                logging.getLogger("kuribot").error("Arena daily reward failed for user %s", user_id, exc_info=entry_exc)

        print(f"Arena daily rewards processed for {today_et}: {awarded} player(s){f', {failed} failed' if failed else ''}; Arena ranks preserved.")
        return awarded, True
    except Exception as exc:
        print(f"Arena daily reward processing failed: {exc}")
        return 0, False


def maybe_run_guild_boss_attempt_reset(now_utc: Optional[datetime] = None) -> Tuple[int, bool]:
    now_utc = now_utc or utc_now()
    window_start_utc, _window_end_utc = current_reset_window_bounds(now_utc)
    try:
        return db.reset_guild_boss_attempts_for_window(window_start_utc)
    except Exception as exc:
        print(f"Guild Boss attempt reset failed: {exc}")
        return 0, False


def arena_record_from_row(user_row: sqlite3.Row) -> str:
    return f"{int(user_row['arena_wins'] or 0)}W-{int(user_row['arena_losses'] or 0)}L-{int(user_row['arena_draws'] or 0)}D"


def arena_rank_title(rank_number: int | None) -> str:
    if rank_number is None:
        return "Unranked"
    rank_number = max(1, int(rank_number))
    if rank_number == 1:
        return "King of Games"
    if rank_number <= 10:
        return "Legend"
    if rank_number <= 30:
        return "Champion"
    if rank_number <= 50:
        return "Diamond"
    if rank_number <= 75:
        return "Platinum"
    if rank_number <= 100:
        return "Gold"
    if rank_number <= 125:
        return "Silver"
    if rank_number <= 150:
        return "Bronze"
    return "Copper"


def arena_rank_label(rank_number: int | None) -> str:
    if rank_number is None:
        return "Unranked"
    rank_number = max(1, int(rank_number))
    return f"{arena_rank_title(rank_number)} • Rank #{rank_number}"


def _arena_entry_sort_key(entry: dict) -> tuple:
    return (
        -int(entry.get('elo', ARENA_STARTING_ELO)),
        0 if entry.get('kind') == 'duelist' else 1,
        str(entry.get('name', '')).lower(),
        str(entry.get('id', '')),
    )


def assign_arena_rank_positions(entries: Sequence[dict]) -> List[dict]:
    ranked_entries: List[dict] = []
    for rank_number, entry in enumerate(sorted((dict(item) for item in entries), key=_arena_entry_sort_key), start=1):
        entry['rank_position'] = rank_number
        entry['rank_label'] = arena_rank_label(rank_number)
        ranked_entries.append(entry)
    return ranked_entries


def build_arena_user_leaderboard_entries() -> List[dict]:
    entries: List[dict] = []
    for row in db.arena_leaderboard_users():
        entries.append({
            'kind': 'user',
            'id': int(row['user_id']),
            'name': str(row['user_id']),
            'elo': int(row['arena_elo'] or ARENA_STARTING_ELO),
            'record': arena_record_from_row(row),
        })
    return entries


def get_user_arena_rank_position(user_id: int) -> int:
    entries = assign_arena_rank_positions([*build_arena_duelist_profiles(), *build_arena_user_leaderboard_entries()])
    for entry in entries:
        if entry.get('kind') == 'user' and int(entry.get('id', 0)) == int(user_id):
            return int(entry['rank_position'])
    return len(build_arena_duelist_profiles()) + 1


def _arena_team_from_slots(slots_csv: str, user_id: Optional[int] = None) -> List[Fighter]:
    fighters = [make_fighter_from_id(monster_id, user_id=user_id) for monster_id in team_slots_from_csv(slots_csv)]
    return [fighter for fighter in fighters if fighter is not None]


def _arena_team_for_user(user_id: int) -> List[Fighter]:
    row = db.get_arena_team(user_id)
    if not row:
        return []
    return _arena_team_from_slots(str(row['slots_csv'] or ''), user_id=user_id)


def _arena_owned_rows(user_id: int, filter_key: str = 'all', sort_key: str = 'name_asc') -> List[sqlite3.Row]:
    rows = list(db.user_inventory(user_id))
    normalized_filter = str(filter_key or 'all').strip().lower()
    attribute_filters = {attr.lower() for attr in ATTRIBUTES}
    if normalized_filter != 'all':
        if normalized_filter in attribute_filters:
            rows = [row for row in rows if str(row['attribute']).strip().lower() == normalized_filter]
        else:
            rows = [row for row in rows if normalized_filter in {t.lower() for t in decode_monster_types(row['monster_types'])}]
    if sort_key == 'name_desc':
        return sorted(rows, key=lambda row: (str(row['monster_name']).lower(), str(row['instance_id'])), reverse=True)
    if sort_key == 'name_asc':
        return sorted(rows, key=lambda row: (str(row['monster_name']).lower(), str(row['instance_id'])))
    stat_index = {'hp': 0, 'atk': 1, 'def': 2, 'spd': 3}.get(sort_key, 0)
    return sorted(rows, key=lambda row: (-resolve_row_combat_stats(row)[stat_index], str(row['monster_name']).lower(), str(row['instance_id'])))


def build_arena_duelist_profiles() -> List[dict]:
    ensure_arena_duelist_state()
    duelists = get_dueling_grounds_duelist_data()
    profiles: List[dict] = []
    for row in db.arena_duelist_rows():
        duelist_key = str(row['duelist_key'])
        duelist = duelists.get(duelist_key, {})
        profiles.append({
            'kind': 'duelist',
            'id': duelist_key,
            'name': duelist.get('name', format_duelist_display_name(duelist_key)),
            'elo': int(row['arena_elo'] or ARENA_STARTING_ELO),
        })
    return assign_arena_rank_positions(profiles)


def build_arena_duelist_team(duelist_key: str, elo: int) -> tuple[list[Fighter], Optional[SupportDeckRuntime], str]:
    team, duelist = build_ranked_dueling_grounds_enemy_team(duelist_key, elo)
    runtime = build_support_runtime_from_cards(
        f"{duelist.get('name', duelist_key)} Arena Support Deck",
        build_aligned_support_cards_for_team(team, RANKED_DUELIST_SUPPORT_BONUSES),
        special_first=True,
    )
    return team, runtime, duelist.get('name', format_duelist_display_name(duelist_key))


async def build_arena_pool_entries(owner_id: int) -> List[dict]:
    owner_rank_position = get_user_arena_rank_position(owner_id)
    entries: List[dict] = []
    for row in db.arena_leaderboard_users():
        user_id = int(row['user_id'])
        if user_id == owner_id:
            continue
        arena_team_row = db.get_arena_team(user_id)
        if arena_team_row is None:
            continue
        team = _arena_team_from_slots(str(arena_team_row['slots_csv'] or ''), user_id=user_id)
        if len(team) != TEAM_SIZE:
            continue
        elo = int(row['arena_elo'] or ARENA_STARTING_ELO)
        entries.append({
            'kind': 'user',
            'id': user_id,
            'name': await resolve_global_discord_user_name(user_id),
            'elo': elo,
            'team': team,
            'team_slots_csv': str(arena_team_row['slots_csv'] or ''),
            'record': arena_record_from_row(row),
        })
    for profile in build_arena_duelist_profiles():
        entries.append(dict(profile))
    ranked_entries = assign_arena_rank_positions(entries)
    ranked_entries.sort(key=lambda entry: (abs(int(entry.get('rank_position', 10 ** 9)) - owner_rank_position), int(entry.get('rank_position', 10 ** 9)), str(entry['name']).lower()))
    return ranked_entries


def arena_entry_key(entry: dict) -> tuple[str, str]:
    return (str(entry.get('kind') or ''), str(entry.get('id') or ''))


def select_arena_opponents(
    entries: Sequence[dict],
    owner_rank_position: int,
    count: int = ARENA_MATCH_COUNT,
    *,
    previous_opponents: Optional[Sequence[dict]] = None,
) -> List[dict]:
    candidates = list(entries)
    if len(candidates) <= count:
        RNG.shuffle(candidates)
        return candidates

    owner_rank_position = max(1, int(owner_rank_position))
    min_rank = max(1, owner_rank_position - ARENA_CHALLENGE_RANK_RANGE)
    max_rank = owner_rank_position + ARENA_CHALLENGE_RANK_RANGE
    ranked_window = [
        entry for entry in candidates
        if min_rank <= int(entry.get('rank_position', 10 ** 9)) <= max_rank
    ]
    if ranked_window:
        source = ranked_window
    else:
        source = sorted(
            candidates,
            key=lambda entry: (abs(int(entry.get('rank_position', 10 ** 9)) - owner_rank_position), int(entry.get('rank_position', 10 ** 9))),
        )[:max(count * 3, count)]

    previous_keys = {arena_entry_key(entry) for entry in (previous_opponents or [])}
    fresh_pool = [entry for entry in source if arena_entry_key(entry) not in previous_keys]
    if len(fresh_pool) >= count:
        return RNG.sample(fresh_pool, count)
    if len(source) >= count:
        return RNG.sample(source, count)
    RNG.shuffle(source)
    return source[:count]


def arena_entry_team(entry: dict) -> List[Fighter]:
    if entry.get('kind') == 'user':
        return list(entry.get('team') or [])[:TEAM_SIZE]
    preview_team, _, _ = build_arena_duelist_team(str(entry['id']), int(entry.get('elo', ARENA_STARTING_ELO)))
    return list(preview_team)[:TEAM_SIZE]


def arena_entry_defense_team_text(entry: dict) -> str:
    fighters = arena_entry_team(entry)
    if not fighters:
        return 'No defense team set'
    if entry.get('kind') == 'duelist':
        return 'Defense Team: ???, ???, ???'
    visible = [fighter.name for fighter in fighters[:max(0, TEAM_SIZE - 1)]]
    while len(visible) < max(0, TEAM_SIZE - 1):
        visible.append('???')
    visible.append('???')
    return ', '.join(visible[:TEAM_SIZE])
