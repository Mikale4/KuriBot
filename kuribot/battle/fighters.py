"""Building Fighter objects from inventory rows."""

from __future__ import annotations

import sqlite3
from typing import Optional, Sequence

from kuribot.models import Fighter
from kuribot.database import db
from kuribot.monsters import (
    decode_monster_types, extra_deck_effect_description, extra_deck_effect_key,
    is_extra_deck_monster_row, resolve_row_combat_stats, resolve_secondary_stat_bonuses,
    roll_monster_types,
)


def _make_fighter_base(row: sqlite3.Row, monster_types: Optional[Sequence[str]] = None, instance_id: Optional[str] = None) -> Fighter:
    resolved_types = tuple(monster_types) if monster_types else decode_monster_types(row["monster_types"]) if "monster_types" in row.keys() else roll_monster_types()
    name_key = "monster_name" if "monster_name" in row.keys() else "name"
    hp, atk, defense, spd = resolve_row_combat_stats(row, resolved_types)
    crit_rate, crit_damage, accuracy, dodge = resolve_secondary_stat_bonuses(row["attribute"])
    return Fighter(
        monster_id=instance_id or row["monster_id"],
        name=row[name_key],
        attribute=row["attribute"],
        monster_types=resolved_types,
        max_hp=hp,
        hp=hp,
        atk=atk,
        defense=defense,
        spd=spd,
        image_path=row["image_path"],
        crit_rate=crit_rate,
        crit_damage=crit_damage,
        accuracy=accuracy,
        dodge=dodge,
        runtime={
            "battle_damage_dealt": 0,
            "battle_healing_done": 0,
            "battle_damage_taken": 0,
            "battle_kills": 0,
        },
    )


def make_fighter_from_id(instance_id: str, user_id: Optional[int] = None) -> Optional[Fighter]:
    row = db.get_inventory_instance(instance_id, user_id=user_id)
    return make_fighter(row, instance_id=instance_id) if row else None


def build_fighter_from_inventory_row(row: sqlite3.Row) -> Fighter:
    hp, atk, defense, spd = resolve_row_combat_stats(row)
    return Fighter(
        monster_id=str(row["monster_id"]),
        name=str(row["monster_name"]),
        attribute=str(row["attribute"]),
        monster_types=decode_monster_types(row["monster_types"]),
        max_hp=hp,
        hp=hp,
        atk=atk,
        defense=defense,
        spd=spd,
        image_path=str(row["image_path"]),
    )


def mark_extra_deck_fighter(fighter: Fighter) -> Fighter:
    fighter.runtime["extra_deck_monster"] = True
    fighter.runtime["extra_deck_effect"] = extra_deck_effect_key(fighter.name)
    fighter.runtime["extra_deck_description"] = extra_deck_effect_description(fighter.name)
    fighter.runtime.pop("equip_spell_name", None)
    return fighter


def make_fighter(row: sqlite3.Row, monster_types: Optional[Sequence[str]] = None, instance_id: Optional[str] = None) -> Fighter:
    fighter = _make_fighter_base(row, monster_types=monster_types, instance_id=instance_id)
    if is_extra_deck_monster_row(row):
        mark_extra_deck_fighter(fighter)
    return fighter
