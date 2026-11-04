"""Dueling Grounds tournaments: brackets, NPC teams and results."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Tuple

import discord
from PIL import Image, ImageDraw

from kuribot.config import RANKED_STARTING_ELO, RNG, TEAM_SIZE
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.timeutil import is_same_eastern_calendar_day, utc_now
from kuribot.core import debug_channel_lock_enabled
from kuribot.database import db
from kuribot.render.fonts import FONT_M_BOLD, FONT_S_BOLD, FONT_XS_BOLD
from kuribot.render.images import open_card_image_square_cover
from kuribot.render.text import ellipsize_text
from kuribot.monsters import display_monster_label_from_id, team_slots_from_csv
from kuribot.cards.support_decks import build_support_deck_runtime
from kuribot.battle.fighters import make_fighter_from_id
from kuribot.battle.stats import scale_fighter_base_stats_exact
from kuribot.portraits import get_duelist_portrait_path
from kuribot.opponents import format_duelist_display_name
from kuribot.dueling_grounds import (
    build_dueling_grounds_enemy_team, get_dueling_grounds_duelist_data,
)
from kuribot.ranked import (
    assign_enemy_team_equip_spells, build_ranked_dueling_grounds_enemy_team,
    support_runtime_for_enemy_mode,
)


TOURNAMENT_SIZE = 16
TOURNAMENT_MATCH_WINS_NEEDED = 2
TOURNAMENT_NPC_STAT_MULTIPLIER = 1.50
TOURNAMENT_WINNER_GOLD = 100000
TOURNAMENT_WINNER_TICKETS = 25
TOURNAMENT_WINNER_PREMIUM_TICKETS = 3
_ACTIVE_TOURNAMENTS: Dict[int, dict] = {}
_TOURNAMENT_SETUPS: Dict[int, dict] = {}


def _tourney_debug_mode_enabled() -> bool:
    return debug_channel_lock_enabled()


def _view_remove_item_if_present(view: discord.ui.View, item: discord.ui.Item) -> None:
    try:
        if item in view.children:
            view.remove_item(item)
    except Exception:
        pass


def _tourney_storage_setup():
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tournament_progress (
            user_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    db.conn.commit()


def _tourney_persist(tourney: Optional[dict]):
    if not tourney:
        return
    owner_id = int(tourney.get('owner_id') or 0)
    if not owner_id:
        return
    now_iso = utc_now().isoformat()
    completed_at = tourney.get('completed_at')
    if _tourney_is_complete(tourney) and not completed_at:
        completed_at = now_iso
        tourney['completed_at'] = completed_at
    db.conn.execute(
        """
        INSERT INTO tournament_progress(user_id, state_json, updated_at, completed_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at,
            completed_at = excluded.completed_at
        """,
        (owner_id, json.dumps(tourney, separators=(',', ':')), now_iso, completed_at),
    )
    db.conn.commit()


def _tourney_delete_persisted(owner_id: int):
    db.conn.execute("DELETE FROM tournament_progress WHERE user_id = ?", (int(owner_id),))
    db.conn.commit()


def _tourney_load_persisted(owner_id: int) -> Optional[dict]:
    row = db.conn.execute("SELECT state_json FROM tournament_progress WHERE user_id = ?", (int(owner_id),)).fetchone()
    if not row or not str(row['state_json'] or '').strip():
        return None
    try:
        tourney = json.loads(str(row['state_json']))
    except Exception:
        return None
    if isinstance(tourney, dict):
        return tourney
    return None


def _tourney_get(owner_id: int) -> Optional[dict]:
    owner_id = int(owner_id)
    tourney = _ACTIVE_TOURNAMENTS.get(owner_id)
    if tourney is not None:
        return tourney
    tourney = _tourney_load_persisted(owner_id)
    if tourney is not None:
        _ACTIVE_TOURNAMENTS[owner_id] = tourney
    return tourney


def _tourney_completed_today(owner_id: int) -> bool:
    tourney = _tourney_get(owner_id)
    if tourney and _tourney_is_complete(tourney):
        completed_at = str(tourney.get('completed_at') or '')
        return is_same_eastern_calendar_day(completed_at)
    row = db.conn.execute("SELECT completed_at FROM tournament_progress WHERE user_id = ?", (int(owner_id),)).fetchone()
    return bool(row and is_same_eastern_calendar_day(str(row['completed_at'] or '')))


def _tourney_attachment_suffix(owner_id: int) -> str:
    return f"{int(owner_id)}_{int(time.time() * 1000)}"


def _tourney_champion(tourney: Optional[dict]) -> Optional[str]:
    try:
        if not tourney or not tourney.get('rounds'):
            return None
        return tourney['rounds'][-1][0].get('winner')
    except Exception:
        return None


def _tourney_is_complete(tourney: Optional[dict]) -> bool:
    return bool(_tourney_champion(tourney))


def _tourney_active_status_label(owner_id: int) -> str:
    t = _tourney_get(int(owner_id))
    if not t:
        return 'Not started'
    return 'Complete' if _tourney_is_complete(t) else 'Active'


def _tourney_team_rows(user_id: int) -> List[sqlite3.Row]:
    rows = []
    for row in db.list_teams(user_id):
        slots = team_slots_from_csv(str(row['slots_csv'] or ''))
        if len(slots) == TEAM_SIZE and not db._team_duplicate_equip_spell_name(user_id, slots):
            rows.append(row)
    return rows


def _tourney_setup(user_id: int) -> dict:
    raw = dict(_TOURNAMENT_SETUPS.get(int(user_id), {}))
    main = int(raw.get('main_team_id') or 0)
    secondaries = [int(x) for x in raw.get('secondary_team_ids', []) if int(x or 0) and int(x or 0) != main][:2]
    return {'main_team_id': main, 'secondary_team_ids': secondaries}


def _tourney_team_label(row: Optional[sqlite3.Row]) -> str:
    if not row:
        return 'Not selected'
    names = ', '.join(display_monster_label_from_id(mid) for mid in team_slots_from_csv(str(row['slots_csv'] or ''))[:TEAM_SIZE])
    return f"{row['name']} — {names}" if names else str(row['name'])


def _tourney_team_image_paths(row: Optional[sqlite3.Row], owner_id: int) -> List[str]:
    if not row:
        return []
    paths: List[str] = []
    for instance_id in team_slots_from_csv(str(row['slots_csv'] or ''))[:TEAM_SIZE]:
        fighter = make_fighter_from_id(instance_id, user_id=owner_id)
        if fighter is not None and fighter.image_path:
            paths.append(str(fighter.image_path))
    return paths


def _draw_tourney_team_image_row(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    row: Optional[sqlite3.Row],
    owner_id: int,
    label: str,
    box: Tuple[int, int, int, int],
    palette: dict,
):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, 18, fill=(20, 20, 42, 235), outline=palette['info_outline'], width=2)
    draw.text((x1 + 18, y1 + 14), label, font=FONT_S_BOLD, fill=palette['title'])
    if row:
        draw.text((x1 + 18, y1 + 39), ellipsize_text(draw, str(row['name']), FONT_XS_BOLD, max(80, x2 - x1 - 36)), font=FONT_XS_BOLD, fill=palette['accent_dust'])
    else:
        draw.text((x1 + 18, y1 + 42), 'Not selected', font=FONT_XS_BOLD, fill=palette['info_text'])

    paths = _tourney_team_image_paths(row, owner_id)
    slot_size = 62
    gap = 12
    total_w = (TEAM_SIZE * slot_size) + ((TEAM_SIZE - 1) * gap)
    start_x = x2 - total_w - 18
    slot_y = y1 + ((y2 - y1 - slot_size) // 2)
    for idx in range(TEAM_SIZE):
        sx = start_x + idx * (slot_size + gap)
        slot_box = (sx, slot_y, sx + slot_size, slot_y + slot_size)
        draw.rounded_rectangle(slot_box, 14, fill=(36, 36, 58, 255), outline=palette['outer_outline'], width=2)
        if idx < len(paths):
            card = open_card_image_square_cover(paths[idx], (slot_size - 8, slot_size - 8), radius=12)
            img.paste(card, (sx + 4, slot_y + 4), card)
        else:
            draw.text((sx + slot_size // 2, slot_y + slot_size // 2), '—', font=FONT_M_BOLD, fill=palette['info_text'], anchor='mm')


def _tourney_player_team(user_id: int, team_id: int) -> List[Fighter]:
    row = db.get_team(user_id, int(team_id))
    if not row:
        return []
    team = [make_fighter_from_id(mid, user_id=user_id) for mid in team_slots_from_csv(str(row['slots_csv'] or ''))[:TEAM_SIZE]]
    return [f for f in team if f is not None][:TEAM_SIZE]


def _tourney_player_runtime(user_id: int, team_id: int) -> Optional[SupportDeckRuntime]:
    row = db.get_team(user_id, int(team_id))
    return build_support_deck_runtime(db.get_support_deck_for_team(user_id, row), user_id=user_id) if row else None


def _tourney_power(team: Sequence[Fighter]) -> int:
    return sum(max(1, f.max_hp) + max(1, f.atk) * 3 + max(1, f.defense) * 2 + max(1, f.spd) * 2 for f in team)


def _tourney_npc_team(contestant: dict) -> Tuple[List[Fighter], Optional[SupportDeckRuntime], str]:
    key = str(contestant.get('duelist_key') or contestant.get('id') or '')
    try:
        team, _duelist = build_ranked_dueling_grounds_enemy_team(key, RANKED_STARTING_ELO + 300)
    except Exception:
        team, _duelist = build_dueling_grounds_enemy_team(key)
        for fighter in team:
            scale_fighter_base_stats_exact(fighter, TOURNAMENT_NPC_STAT_MULTIPLIER)
    name = str(contestant.get('name') or key)
    runtime, _ = support_runtime_for_enemy_mode(name, team, 'aligned')
    assign_enemy_team_equip_spells(team, RANKED_STARTING_ELO + 300, rng=RNG, support_runtime=runtime)
    return team, runtime, ''


def _tourney_contestant(tourney: dict, cid: str) -> Optional[dict]:
    for c in tourney.get('contestants', []):
        if str(c.get('id')) == str(cid):
            return c
    return None


def _tourney_name(tourney: dict, cid: Optional[str]) -> str:
    if not cid:
        return 'TBD'
    c = _tourney_contestant(tourney, str(cid))
    return str((c or {}).get('name') or cid)


def _tourney_round_label(index: int) -> str:
    return ['Opening Round', 'Quarterfinals', 'Semifinals', 'Finals'][min(max(0, int(index)), 3)]


def _tourney_new(owner_id: int, user_name: str) -> dict:
    data = get_dueling_grounds_duelist_data()
    keys = list(data.keys())
    RNG.shuffle(keys)
    contestants = [{'id': 'player', 'kind': 'player', 'name': user_name}]
    i = 0
    while len(contestants) < TOURNAMENT_SIZE and keys:
        key = keys[i % len(keys)]
        suffix = '' if i < len(keys) else f" {i // len(keys) + 1}"
        contestants.append({'id': f'npc:{i}:{key}', 'kind': 'npc', 'duelist_key': key, 'name': str(data[key].get('name', format_duelist_display_name(key))) + suffix})
        i += 1
    RNG.shuffle(contestants)
    rounds = []
    first = []
    for i in range(0, TOURNAMENT_SIZE, 2):
        first.append({'a': contestants[i]['id'], 'b': contestants[i + 1]['id'], 'winner': None, 'wins': {}, 'games': []})
    rounds.append(first)
    n = len(first) // 2
    while n >= 1:
        rounds.append([{'a': None, 'b': None, 'winner': None, 'wins': {}, 'games': []} for _ in range(n)])
        n //= 2
    return {'owner_id': int(owner_id), 'user_name': user_name, 'setup': _tourney_setup(owner_id), 'contestants': contestants, 'rounds': rounds, 'prize_claimed': False}


def _tourney_current_match(tourney: dict) -> Optional[Tuple[int, int, dict]]:
    for ri, matches in enumerate(tourney.get('rounds', [])):
        for mi, m in enumerate(matches):
            if m.get('a') and m.get('b') and not m.get('winner'):
                return ri, mi, m
        if any(not m.get('winner') for m in matches):
            return None
    return None


def _tourney_score(tourney: dict, match: dict) -> str:
    wins = match.get('wins') or {}
    a, b = match.get('a'), match.get('b')
    return f"{_tourney_name(tourney, a)} {int(wins.get(a, 0))} - {int(wins.get(b, 0))} {_tourney_name(tourney, b)}"


def _tourney_is_player_match(match: dict) -> bool:
    return str(match.get('a')) == 'player' or str(match.get('b')) == 'player'


def _tourney_advance(tourney: dict, ri: int, mi: int, winner: str):
    tourney['rounds'][ri][mi]['winner'] = winner
    if ri + 1 < len(tourney['rounds']):
        nm = tourney['rounds'][ri + 1][mi // 2]
        nm['a' if mi % 2 == 0 else 'b'] = winner


def _tourney_record(tourney: dict, ri: int, mi: int, winner: str, loser: str, method: str):
    m = tourney['rounds'][ri][mi]
    if m.get('winner'):
        # This match is already decided. A second, overlapping battle (double-click) used to
        # keep counting games and could flip the result and pay the prize.
        return
    wins = dict(m.get('wins') or {})
    wins[winner] = int(wins.get(winner, 0)) + 1
    m['wins'] = wins
    m.setdefault('games', []).append({'winner': winner, 'loser': loser, 'method': method})
    if wins[winner] >= TOURNAMENT_MATCH_WINS_NEEDED:
        _tourney_advance(tourney, ri, mi, winner)
        if winner == 'player' and ri == len(tourney['rounds']) - 1 and not tourney.get('prize_claimed'):
            db.add_gold(int(tourney['owner_id']), TOURNAMENT_WINNER_GOLD)
            db.add_tickets(int(tourney['owner_id']), TOURNAMENT_WINNER_TICKETS)
            db.add_premium_tickets(int(tourney['owner_id']), TOURNAMENT_WINNER_PREMIUM_TICKETS)
            tourney['prize_claimed'] = True
    _tourney_persist(tourney)


def _tourney_battle_sides(tourney: dict, match: dict, player_team_id: Optional[int] = None):
    owner_id = int(tourney['owner_id'])
    setup = tourney.get('setup') or _tourney_setup(owner_id)
    chosen_team = int(player_team_id or setup.get('main_team_id') or 0)
    def side(cid: str):
        c = _tourney_contestant(tourney, cid) or {'id': cid, 'kind': 'npc', 'duelist_key': cid, 'name': cid}
        name = str(c.get('name') or cid)
        if cid == 'player':
            return name, _tourney_player_team(owner_id, chosen_team), _tourney_player_runtime(owner_id, chosen_team), 'Tournament Team'
        team, runtime, note = _tourney_npc_team(c)
        return name, team, runtime, note
    a = str(match.get('a'))
    b = str(match.get('b'))
    return (*side(a), *side(b))


def _tourney_skip(tourney: dict, ri: int, mi: int) -> str:
    m = tourney['rounds'][ri][mi]
    a_id, b_id = str(m['a']), str(m['b'])
    _an, at, _ar, _ao, _bn, bt, _br, _bo = _tourney_battle_sides(tourney, m)
    ap = max(1, _tourney_power(at)); bp = max(1, _tourney_power(bt))
    winner = a_id if RNG.random() <= max(0.15, min(0.85, ap / (ap + bp))) else b_id
    loser = b_id if winner == a_id else a_id
    _tourney_record(tourney, ri, mi, winner, loser, 'Skipped')
    return winner


def _tourney_finish(tourney: dict) -> Optional[str]:
    if not tourney:
        return None
    guard = (TOURNAMENT_SIZE * TOURNAMENT_MATCH_WINS_NEEDED * 4) + 16
    while not _tourney_is_complete(tourney) and guard > 0:
        cur = _tourney_current_match(tourney)
        if not cur:
            break
        _tourney_skip(tourney, cur[0], cur[1])
        guard -= 1
    _tourney_persist(tourney)
    return _tourney_champion(tourney)


def _tourney_avatar_path(tourney: dict, cid: str, owner_id: int) -> str:
    if str(cid) == 'player':
        return db.get_avatar_path(int(owner_id))
    contestant = _tourney_contestant(tourney, str(cid)) or {}
    duelist_key = str(contestant.get('duelist_key') or contestant.get('id') or cid)
    duelist_name = str(contestant.get('name') or format_duelist_display_name(duelist_key))
    return str(get_duelist_portrait_path(duelist_key, duelist_name) or '')
