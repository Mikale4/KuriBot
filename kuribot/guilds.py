"""Guild boss fighters and boss buffs."""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Sequence

from kuribot.config import GUILD_BOSS_RANDOM_BUFFS, GUILD_BOSS_SUPPORT_DISABLED_ROUNDS, RNG
from kuribot.models import Fighter, StatusEffect, SupportDeckRuntime
from kuribot.timeutil import reset_countdown_text
from kuribot.monsters import decode_monster_types, roll_monster_types
from kuribot.battle.runtime import (
    ACTIVE_SUPPORT_CARDS_BY_FIGHTER, ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER, fighter_statuses,
    living_fighters, runtime_flag, set_runtime_flag,
)
from kuribot.battle.mechanics import apply_status_effect, clear_status


def guild_boss_row_to_fighter(row: sqlite3.Row) -> Fighter:
    monster_types = decode_monster_types(str(row["monster_types"] or ""))
    if not monster_types:
        monster_types = roll_monster_types()
    return Fighter(
        monster_id=str(row["monster_id"]),
        name=str(row["monster_name"]),
        attribute=str(row["monster_attribute"] or row["base_attribute"] or "DARK"),
        monster_types=tuple(monster_types),
        max_hp=max(1, int(row["max_hp"] or 1)),
        hp=max(0, int(row["current_hp"] or 0)),
        atk=max(1, int(row["atk"] or 1)),
        defense=max(1, int(row["def"] or 1)),
        spd=max(1, int(row["spd"] or 1)),
        image_path=str(row["image_path"] or ""),
        runtime={
            "battle_damage_dealt": 0,
            "battle_healing_done": 0,
            "battle_damage_taken": 0,
            "battle_kills": 0,
            "is_guild_boss": True,
        },
    )


def guild_boss_support_enabled(completed_rounds: int) -> bool:
    return int(completed_rounds) >= GUILD_BOSS_SUPPORT_DISABLED_ROUNDS


def _set_guild_boss_player_support_cards(team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime], enabled: bool):
    active_cards = list(runtime.active_cards) if enabled and runtime is not None else []
    for fighter in team:
        ACTIVE_SUPPORT_CARDS_BY_FIGHTER[id(fighter)] = list(active_cards)
        if runtime is not None and enabled:
            ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER[id(fighter)] = runtime
        else:
            ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.pop(id(fighter), None)


def apply_guild_boss_random_buff(boss: Fighter, log_entries: List[str], round_number: int) -> None:
    choice = RNG.choice(GUILD_BOSS_RANDOM_BUFFS)
    if choice == "IMMUNITY":
        set_runtime_flag(boss, "immunity_turns", max(1, int(runtime_flag(boss, "immunity_turns", 0))))
    elif choice == "PROTECTION":
        set_runtime_flag(boss, "protection_turns", max(1, int(runtime_flag(boss, "protection_turns", 0))))
    elif choice == "REFLECT":
        set_runtime_flag(boss, "reflect_turns", max(1, int(runtime_flag(boss, "reflect_turns", 0))))
    elif choice == "PRECOG":
        set_runtime_flag(boss, "precog_turns", max(1, int(runtime_flag(boss, "precog_turns", 0))))
    elif choice == "COUNTER":
        set_runtime_flag(boss, "counter_turns", max(1, int(runtime_flag(boss, "counter_turns", 0))))
    elif choice == "SHIELD":
        set_runtime_flag(boss, "shield", max(int(runtime_flag(boss, "shield", 0)), max(1, int(round(boss.max_hp * 0.02)))))
    log_entries.append(f"Round {round_number}: **Guild Boss aura** grants **{choice}** to **{boss.name}** for 1 round.")


def apply_guild_boss_two_round_cycle(boss: Fighter, player_team: Sequence[Fighter], log_entries: List[str], completed_rounds: int) -> None:
    if completed_rounds <= 0 or completed_rounds % 2 != 0 or not boss.alive:
        return

    statuses_before_cleanse = fighter_statuses(boss)
    removed_debuff_count = len(statuses_before_cleanse)
    if removed_debuff_count > 0:
        clear_status(boss, log_entries, reason="Guild Boss two-round cleanse")
        heal_amount = max(1, int(round(boss.max_hp * 0.15)))
        before_hp = boss.hp
        boss.hp = min(boss.max_hp, boss.hp + heal_amount)
        actual_heal = max(0, boss.hp - before_hp)
        log_entries.append(
            f"Round {completed_rounds}: **{boss.name}** purges **{removed_debuff_count}** debuff(s) "
            f"and heals **{actual_heal:,} HP**."
        )
    else:
        log_entries.append(f"Round {completed_rounds}: **{boss.name}** has no debuffs to purge, so it does not heal.")

    stunned: List[str] = []
    for fighter in living_fighters(player_team):
        if RNG.random() < 0.35:
            if apply_status_effect(fighter, StatusEffect.STUN, source=boss, log_entries=log_entries):
                stunned.append(fighter.name)
    if stunned:
        log_entries.append(f"**{boss.name}**'s shockwave stuns: {', '.join(stunned)}.")
    else:
        log_entries.append(f"**{boss.name}**'s shockwave fails to stun any monsters.")


def guild_shop_reset_text() -> str:
    return f"Refreshes in **{reset_countdown_text()}** (**12:00 AM / 12:00 PM ET**)."
