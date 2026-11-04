"""Preparing teams and support decks for a battle, and cleaning up afterwards."""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from kuribot.config import ATTRIBUTES, EXTRA_DECK_SIZE, SUPPORT_DECK_SIZE, TEAM_SIZE
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.database import db
from kuribot.monsters import (
    decode_extra_deck_monsters, decode_monster_types, is_extra_deck_monster_row,
    team_slots_from_csv,
)
from kuribot.cards.support_cards import (
    get_support_card_def, support_card_family_key, support_card_stage_number,
)
from kuribot.battle.fighters import (
    build_fighter_from_inventory_row, make_fighter, make_fighter_from_id, mark_extra_deck_fighter,
)
from kuribot.battle.special_support import fighter_matches_special_support
from kuribot.battle.mechanics import _runtime_team_disables_enemy_support


def auto_build_support_deck_instance_ids(user_id: int, team_row: Optional[sqlite3.Row | dict] = None) -> List[str]:
    support_rows = db.user_support_cards(user_id)
    if team_row is None:
        team_row = db.get_active_team(user_id)
    if not team_row:
        return []

    member_ids = team_slots_from_csv(team_row["slots_csv"])
    team_members = [db.get_inventory_instance(instance_id, user_id) for instance_id in member_ids]
    team_members = [row for row in team_members if row is not None]
    if not team_members:
        return []

    team_fighters = [build_fighter_from_inventory_row(row) for row in team_members]
    attribute_counts = Counter(str(row["attribute"]).upper() for row in team_members)
    type_counts: Counter[str] = Counter()
    for row in team_members:
        for monster_type in decode_monster_types(row["monster_types"]):
            type_counts[monster_type] += 1

    best_instance_by_family: Dict[str, Tuple[Tuple[int, int, int, int, int, str], str]] = {}
    for row in support_rows:
        card = get_support_card_def(row["support_card_id"])
        if card is None:
            continue

        relevance = 0
        special_match = 0
        if str(card.stat_type).upper() == "SPECIAL":
            special_match = 1 if any(fighter_matches_special_support(card.support_card_id, fighter) for fighter in team_fighters) else 0
            relevance = special_match
        else:
            target = str(card.attribute or "").strip()
            target_upper = target.upper()
            if target_upper in ATTRIBUTES:
                relevance = int(attribute_counts.get(target_upper, 0))
            else:
                relevance = int(type_counts.get(target, 0))

        if relevance <= 0:
            continue

        family_key = support_card_family_key(card.support_card_id)
        stat_priority = {"ATK": 2, "SPD": 1, "DEF": 0, "SPECIAL": 3}
        score = (
            special_match,
            relevance,
            stat_priority.get(str(card.stat_type).upper(), 0),
            int(card.bonus_pct or 0),
            support_card_stage_number(card.support_card_id),
            str(card.name).lower(),
        )
        candidate = (score, str(row["instance_id"]))
        current = best_instance_by_family.get(family_key)
        if current is None or candidate > current:
            best_instance_by_family[family_key] = candidate

    ranked = sorted(best_instance_by_family.values(), reverse=True)
    picks = [instance_id for _, instance_id in ranked[:SUPPORT_DECK_SIZE]]
    return picks[:SUPPORT_DECK_SIZE]


def _active_team_fighters_for_user_base(user_id: int, mode: str = "campaign") -> List[Fighter]:
    active_team = db.get_active_team(user_id, mode)
    if not active_team:
        return []
    member_ids = team_slots_from_csv(active_team['slots_csv'])
    if db._team_duplicate_equip_spell_name(user_id, member_ids):
        return []
    fighters: List[Fighter] = []
    for instance_id in member_ids:
        fighter = make_fighter_from_id(str(instance_id), user_id=user_id)
        if fighter is not None:
            fighters.append(fighter)
    return fighters[:TEAM_SIZE]


def append_necrovalley_suppression_logs(
    player_name: str,
    enemy_name: str,
    player_team: Sequence[Fighter],
    player_runtime: Optional[SupportDeckRuntime],
    enemy_team: Sequence[Fighter],
    enemy_runtime: Optional[SupportDeckRuntime],
    log_entries: List[str],
) -> None:
    """Log when Necrovalley is actively sealing the opposing side's support cards."""
    if _runtime_team_disables_enemy_support(player_team, player_runtime) and enemy_runtime is not None and enemy_runtime.active_cards:
        log_entries.append(f"**Necrovalley** seals **{enemy_name}**'s support cards while a **Gravekeeper** monster is alive.")
    if _runtime_team_disables_enemy_support(enemy_team, enemy_runtime) and player_runtime is not None and player_runtime.active_cards:
        log_entries.append(f"**Necrovalley** seals **{player_name}**'s support cards while a **Gravekeeper** monster is alive.")


def cleanup_battle_context(fighters: Sequence[Fighter]) -> None:
    """Best-effort cleanup for per-battle transient fighter runtime fields."""
    for fighter in fighters or []:
        try:
            fighter.speed_gauge = 0.0
            if isinstance(fighter.runtime, dict):
                for key in list(fighter.runtime.keys()):
                    if str(key).startswith("battle_"):
                        fighter.runtime.pop(key, None)
        except Exception:
            continue


def _active_team_fighters_for_user(user_id: int, mode: str = "campaign") -> List[Fighter]:
    team_row = db.get_active_team(user_id, mode)
    fighters = _active_team_fighters_for_user_base(user_id, mode)
    if not fighters or team_row is None:
        return fighters
    deck_row = db.get_active_extra_deck(user_id, mode)
    pool: List[Fighter] = []
    if deck_row is not None:
        for instance_id in decode_extra_deck_monsters(str(deck_row["monsters_json"]))[:EXTRA_DECK_SIZE]:
            row = db.get_inventory_instance(instance_id, user_id)
            if row is not None and is_extra_deck_monster_row(row):
                fighter = make_fighter(row, instance_id=str(instance_id))
                mark_extra_deck_fighter(fighter)
                pool.append(fighter)
    for fighter in fighters:
        fighter.runtime["extra_deck_pool"] = pool
        fighter.runtime["extra_deck_team_ref"] = fighters
        fighter.runtime["extra_deck_owner_user_id"] = user_id
        fighter.runtime["extra_deck_deck_name"] = str(deck_row["name"]) if deck_row is not None else ""
    return fighters
