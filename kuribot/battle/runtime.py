"""Per-battle state: active teams, runtime flags, statuses and equip-spell availability."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from kuribot.config import RNG
from kuribot.models import Fighter, StatusEffect, StatusState, SupportCardDef, SupportDeckRuntime
from kuribot.monsters import is_extra_deck_monster_name
from kuribot.battle.archetypes import fighter_is_ryzeal, fighter_is_tearlaments


def gain_flat_stat_bonus(fighter: Fighter, stat_type: str, amount: int) -> int:
    normalized = str(stat_type or "").strip().lower()
    if normalized not in {"atk", "def", "spd"}:
        return 0
    bonus = max(0, int(amount))
    key = f"flat_{normalized}_bonus"
    set_runtime_flag(fighter, key, int(runtime_flag(fighter, key, 0)) + bonus)
    return bonus


def gain_percent_stat_bonus(fighter: Fighter, stat_type: str, amount: int) -> int:
    normalized = str(stat_type or "").strip().lower()
    if normalized not in {"atk", "def", "spd"}:
        return 0
    key = f"pct_{normalized}_bonus"
    set_runtime_flag(fighter, key, int(runtime_flag(fighter, key, 0)) + int(amount))
    return int(runtime_flag(fighter, key, 0))


ACTIVE_SUPPORT_CARDS_BY_FIGHTER: Dict[int, List[SupportCardDef]] = {}
ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER: Dict[int, SupportDeckRuntime] = {}
ACTIVE_TEAMMATES_BY_FIGHTER: Dict[int, List[Fighter]] = {}
# Each fighter's opposing team in ITS OWN battle. Before this existed, "who is my enemy?"
# was answered by scanning every battle in progress, so two players battling at the same
# time could hit, buff or disable each other's monsters.
ACTIVE_ENEMIES_BY_FIGHTER: Dict[int, List[Fighter]] = {}


def register_battle_teams(team_a: Sequence[Fighter], team_b: Sequence[Fighter]) -> None:
    list_a = team_a if isinstance(team_a, list) else list(team_a)
    list_b = team_b if isinstance(team_b, list) else list(team_b)
    for fighter in list_a:
        ACTIVE_TEAMMATES_BY_FIGHTER[id(fighter)] = list(list_a)
        ACTIVE_ENEMIES_BY_FIGHTER[id(fighter)] = list_b
    for fighter in list_b:
        ACTIVE_TEAMMATES_BY_FIGHTER[id(fighter)] = list(list_b)
        ACTIVE_ENEMIES_BY_FIGHTER[id(fighter)] = list_a


def battle_fighters_for(fighter: Optional[Fighter]) -> List[Fighter]:
    """Every fighter in the same battle as `fighter` (both teams)."""
    if fighter is None:
        return []
    allies = ACTIVE_TEAMMATES_BY_FIGHTER.get(id(fighter))
    enemies = ACTIVE_ENEMIES_BY_FIGHTER.get(id(fighter))
    if allies is None and enemies is None:
        return [fighter]
    combined: List[Fighter] = []
    seen: set[int] = set()
    for member in list(allies or []) + list(enemies or []):
        if id(member) not in seen:
            seen.add(id(member))
            combined.append(member)
    return combined


def _fighter_statuses_store(fighter: Fighter) -> List[StatusState]:
    runtime = fighter.runtime if fighter.runtime is not None else {}
    fighter.runtime = runtime
    raw_statuses = runtime.get("status_effects")
    if isinstance(raw_statuses, (list, tuple, set)):
        candidates = list(raw_statuses)
    else:
        candidates = []

    normalized: List[StatusState] = []
    seen_by_effect: dict[StatusEffect, StatusState] = {}

    def normalize_status(value: object) -> Optional[StatusState]:
        if isinstance(value, StatusState):
            return value
        if isinstance(value, dict):
            raw_effect = value.get("effect")
            try:
                effect = raw_effect if isinstance(raw_effect, StatusEffect) else StatusEffect(str(raw_effect).strip().upper())
            except Exception:
                return None
            poison_pct = value.get("poison_pct", 1)
            try:
                poison_pct = max(1, int(poison_pct))
            except Exception:
                poison_pct = 1
            return StatusState(
                effect=effect,
                source_monster_id=value.get("source_monster_id"),
                source_name=str(value.get("source_name") or ""),
                poison_pct=poison_pct,
            )
        if isinstance(value, str):
            cleaned = value.strip().upper()
            if not cleaned:
                return None
            poison_match = re.fullmatch(r"POISON\s+(\d+)%", cleaned)
            poison_pct = 1
            if poison_match:
                cleaned = "POISON"
                poison_pct = max(1, int(poison_match.group(1)))
            try:
                effect = StatusEffect(cleaned)
            except Exception:
                return None
            return StatusState(effect=effect, poison_pct=poison_pct)
        return None

    def add_status(state: Optional[StatusState]) -> None:
        if state is None:
            return
        # Status effects do not stack by count. Repeated applications of the
        # same effect are merged into one state so BURN/POISON refresh instead
        # of multiplying their end-of-turn damage.
        existing = seen_by_effect.get(state.effect)
        if existing is not None:
            if state.source_monster_id:
                existing.source_monster_id = state.source_monster_id
                existing.source_name = state.source_name
            if state.effect is StatusEffect.POISON:
                existing.poison_pct = max(int(getattr(existing, "poison_pct", 1) or 1), int(getattr(state, "poison_pct", 1) or 1))
            return
        seen_by_effect[state.effect] = state
        normalized.append(state)

    add_status(getattr(fighter, "status", None))
    for value in candidates:
        add_status(normalize_status(value))

    runtime["status_effects"] = normalized
    fighter.status = normalized[0] if normalized else None
    return normalized


def fighter_statuses(fighter: Fighter) -> List[StatusState]:
    return list(_fighter_statuses_store(fighter))


def fighter_has_status(fighter: Fighter, effect: StatusEffect) -> bool:
    return any(status.effect is effect for status in _fighter_statuses_store(fighter))


def get_status_state(fighter: Fighter, effect: StatusEffect) -> Optional[StatusState]:
    return next((status for status in _fighter_statuses_store(fighter) if status.effect is effect), None)


def _fighter_identity_set(team: Sequence[Fighter]) -> set[int]:
    return {id(member) for member in team}


def _same_fighter_team(left: Sequence[Fighter], right: Sequence[Fighter]) -> bool:
    return bool(left) and bool(right) and _fighter_identity_set(left) == _fighter_identity_set(right)


def _fighter_in_team_by_identity(fighter: Optional[Fighter], team: Sequence[Fighter]) -> bool:
    if fighter is None:
        return False
    fighter_id = id(fighter)
    return any(id(member) == fighter_id for member in team)


def _choose_enemy_team_by_identity(fighter: Optional[Fighter], allies: Sequence[Fighter]) -> List[Fighter]:
    for anchor in ([fighter] if fighter is not None else []) + list(allies or []):
        mapped = ACTIVE_ENEMIES_BY_FIGHTER.get(id(anchor))
        if mapped is not None:
            return list(mapped)
    for team in list(ACTIVE_TEAMMATES_BY_FIGHTER.values()):
        team_list = list(team)
        if not team_list:
            continue
        if _same_fighter_team(team_list, allies):
            continue
        if _fighter_in_team_by_identity(fighter, team_list):
            continue
        return team_list
    return []


def _enemy_support_disables_equip_spells(fighter: Optional[Fighter]) -> bool:
    if fighter is None or fighter.runtime is None:
        return False
    allies = list(ACTIVE_TEAMMATES_BY_FIGHTER.get(id(fighter), []))
    if not allies:
        return False
    mapped_enemies = ACTIVE_ENEMIES_BY_FIGHTER.get(id(fighter))
    candidate_teams = [list(mapped_enemies)] if mapped_enemies is not None else list(ACTIVE_TEAMMATES_BY_FIGHTER.values())
    for team in candidate_teams:
        enemy_team = list(team)
        if not enemy_team or _same_fighter_team(enemy_team, allies) or _fighter_in_team_by_identity(fighter, enemy_team):
            continue
        if any(
            enemy.alive and (
                (fighter_is_tearlaments(enemy) and any(card.support_card_id == "TEARLAMENTS_SULLIEK" for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(enemy), [])))
                or (fighter_is_ryzeal(enemy) and any(card.support_card_id == "RYZEAL_CROSS" for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(enemy), [])))
            )
            for enemy in enemy_team
        ):
            return fighter.alive and bool(str(runtime_flag(fighter, "equip_spell_name", ""))) and not bool(runtime_flag(fighter, "equip_spell_destroyed", False))
    return False


def enemy_team_for_fighter(fighter: Fighter) -> List[Fighter]:
    allies = list(ACTIVE_TEAMMATES_BY_FIGHTER.get(id(fighter), []))
    return _choose_enemy_team_by_identity(fighter, allies)


def _equip_spell_disabled_before_disarmament(fighter: Optional[Fighter]) -> bool:
    """Return True when a fighter's equip spell is disabled by non-Disarmament effects.

    This deliberately includes enemy special-support equip locks, so Disarmament
    cannot keep disabling other equip spells while its own equip spell is locked.
    """
    if fighter is None or fighter.runtime is None:
        return False
    if not bool(str(runtime_flag(fighter, "equip_spell_name", "") or "")):
        return False
    if bool(runtime_flag(fighter, "equip_spell_destroyed", False)):
        return True
    if fighter_has_status(fighter, StatusEffect.SILENCE):
        return True
    if _enemy_support_disables_equip_spells(fighter):
        return True
    return False


def _disarmament_disables_spell(fighter: Fighter, spell_name: str) -> bool:
    if spell_name == "Disarmament":
        return False
    for other in battle_fighters_for(fighter):
        if other is fighter or not other.alive:
            continue
        if str(other.runtime.get("equip_spell_name", "") or "") != "Disarmament":
            continue
        if _equip_spell_disabled_before_disarmament(other):
            continue
        return True
    return False


def _active_equip_spell_name_base(fighter: Optional[Fighter]) -> str:
    if fighter is None or fighter.runtime is None:
        return ""
    spell_name = str(fighter.runtime.get("equip_spell_name", "") or "")
    if not spell_name:
        return ""
    disabled = False
    if _equip_spell_disabled_before_disarmament(fighter):
        disabled = True
    elif _disarmament_disables_spell(fighter, spell_name):
        disabled = True
    if disabled:
        if spell_name == "Rose Bell of Revelation" and not bool(runtime_flag(fighter, "rose_bell_triggered", False)):
            set_runtime_flag(fighter, "rose_bell_triggered", True)
            gain_percent_stat_bonus(fighter, "ATK", 100)
            pending = fighter.runtime.setdefault("pending_log_entries", [])
            pending.append(f"**Rose Bell of Revelation** is disabled for **{fighter.name}**, granting **+100% ATK**.")
        return ""
    return spell_name


def _is_runtime_buff_key(key: str) -> bool:
    return key in {
        "immunity_turns", "full_debuff_immunity_turns", "protection_turns", "shield",
        "immune_effects", "precog_turns", "counter_turns", "reflect_turns",
        "recovery_effects", "mystik_wok_rounds",
    }


def _runtime_value_has_buff(key: str, value) -> bool:
    if key in {"immune_effects"}:
        return bool(value)
    if key in {"recovery_effects"}:
        return bool(list(value or []))
    try:
        return float(value) > 0
    except Exception:
        return bool(value)


def runtime_flag(fighter: Fighter, key: str, default=0):
    return fighter.runtime.get(key, default) if fighter.runtime is not None else default


def set_runtime_flag(fighter: Fighter, key: str, value) -> None:
    from kuribot.battle.mechanics import trigger_spell_absorption_on_buff_gain
    previous = fighter.runtime.get(key) if fighter.runtime is not None else None
    fighter.runtime[key] = value
    if _is_runtime_buff_key(key):
        had_buff = _runtime_value_has_buff(key, previous)
        has_buff = _runtime_value_has_buff(key, value)
        if has_buff and not had_buff:
            trigger_spell_absorption_on_buff_gain(fighter)


def fighter_has_bomb(fighter: Fighter) -> bool:
    return bool(runtime_flag(fighter, "bombs", []))


def living_fighters(team: List[Fighter]) -> List[Fighter]:
    return [fighter for fighter in team if fighter.alive]


def random_alive(team: List[Fighter]) -> Optional[Fighter]:
    alive = living_fighters(team)
    if not alive:
        return None
    return RNG.choice(alive)


def team_alive(team: List[Fighter]) -> bool:
    return any(f.alive for f in team)


def is_guild_boss_fighter(fighter: Fighter) -> bool:
    return bool(runtime_flag(fighter, "is_guild_boss", False))


def _clear_runtime_cards(team: Sequence[Fighter]):
    for fighter in team:
        ACTIVE_SUPPORT_CARDS_BY_FIGHTER.pop(id(fighter), None)
        ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.pop(id(fighter), None)
        ACTIVE_TEAMMATES_BY_FIGHTER.pop(id(fighter), None)
        ACTIVE_ENEMIES_BY_FIGHTER.pop(id(fighter), None)


def is_extra_deck_fighter(fighter: Optional[Fighter]) -> bool:
    if fighter is None:
        return False
    if bool(runtime_flag(fighter, "extra_deck_monster", False)):
        return True
    return is_extra_deck_monster_name(str(getattr(fighter, "name", "")))


def active_equip_spell_name(fighter: Optional[Fighter]) -> str:
    if is_extra_deck_fighter(fighter):
        return ""
    return _active_equip_spell_name_base(fighter)
