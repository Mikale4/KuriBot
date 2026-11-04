"""Stat calculations: effective ATK/DEF/SPD, support bonuses and type matchups."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from kuribot.config import (
    ATTRIBUTES, TYPE_EFFECTIVENESS, TYPE_RESIST_MULTIPLIER, TYPE_SUPER_MULTIPLIER,
)
from kuribot.models import Fighter, StatusEffect, SupportCardDef
from kuribot.battle.archetypes import (
    fighter_is_blackwing, fighter_is_dark_magician, fighter_is_dark_world,
    fighter_is_forbidden_one, fighter_is_galaxy_or_photon, fighter_is_kashtira,
    fighter_is_kewl_tune, fighter_is_magician_girl, fighter_is_magnet_warrior,
    fighter_is_silent_swordsman, fighter_is_slifer_the_sky_dragon, fighter_is_tearlaments,
    fighter_is_visas_starfrost,
)
from kuribot.battle.runtime import (
    active_equip_spell_name, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER,
    ACTIVE_TEAMMATES_BY_FIGHTER, enemy_team_for_fighter, fighter_has_bomb, fighter_has_status,
    runtime_flag,
)
from kuribot.battle.special_support import global_ojama_country_active


def support_target_matches_fighter(target_name: str, fighter: Fighter) -> bool:
    normalized_target = str(target_name or "").strip()
    if not normalized_target:
        return False
    if normalized_target.upper() in ATTRIBUTES:
        return fighter.attribute == normalized_target.upper()
    return normalized_target in fighter.monster_types


def support_card_stat_bonus(fighter: Fighter, active_cards: Sequence[SupportCardDef], stat_type: str) -> int:
    if not active_cards:
        active_cards = []
    stat_type = stat_type.upper()
    total_pct = sum(
        card.bonus_pct
        for card in active_cards
        if card.stat_type == stat_type and support_target_matches_fighter(card.attribute, fighter)
    )
    flat_bonus = max(0, int(runtime_flag(fighter, f"flat_{stat_type.lower()}_bonus", 0)))
    total_pct += int(runtime_flag(fighter, f"pct_{stat_type.lower()}_bonus", 0))
    support_ids = {card.support_card_id for card in active_cards}
    if "JJ_KEWL_TUNE" in support_ids and fighter_is_kewl_tune(fighter):
        opponent_cards: List[SupportCardDef] = []
        for enemy in enemy_team_for_fighter(fighter):
            enemy_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(enemy))
            if enemy_runtime is not None:
                opponent_cards = list(enemy_runtime.active_cards)
                break
        if not opponent_cards:
            for enemy in enemy_team_for_fighter(fighter):
                opponent_cards = list(ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(enemy), []))
                if opponent_cards:
                    break
        for enemy_card in opponent_cards:
            if (str(enemy_card.attribute).upper() != "SPECIAL" and str(enemy_card.stat_type).upper() == stat_type and support_target_matches_fighter(enemy_card.attribute, fighter)):
                total_pct += int(enemy_card.bonus_pct)
    if stat_type == "ATK" and "DARK_BURNING_ATTACK" in support_ids and fighter_is_magician_girl(fighter):
        total_pct += 50
    if stat_type == "ATK" and "GALAXY_EXPEDITION" in support_ids and fighter_is_galaxy_or_photon(fighter):
        total_pct += 50
    if stat_type == "ATK" and int(runtime_flag(fighter, "hero_signal_rounds", 0)) > 0:
        total_pct += 100
    if stat_type == "SPD" and "BLACKWING_WHIRLWIND" in support_ids and fighter_is_blackwing(fighter):
        total_pct += 100
    if stat_type == "SPD" and int(runtime_flag(fighter, "hero_signal_rounds", 0)) > 0:
        total_pct += 50
    if stat_type == "ATK" and "DARK_MAGIC_ATTACK" in support_ids and fighter_is_dark_magician(fighter):
        total_pct += 50
    if stat_type == "ATK" and ("DARK_WORLD_DEALINGS" in support_ids or int(runtime_flag(fighter, "dark_world_dealings_rounds", 0)) > 0) and fighter_is_dark_world(fighter):
        total_pct += 25
    if stat_type == "ATK" and fighter_is_silent_swordsman(fighter):
        total_pct += max(0, int(runtime_flag(fighter, "silent_sword_slash_pct", 0)))
    if stat_type == "ATK" and "PRIMEVAL_PLANET_PERLEREINO" in support_ids and (fighter_is_tearlaments(fighter) or fighter_is_visas_starfrost(fighter)):
        total_pct += 50
    if stat_type == "ATK" and "PRESSURED_PLANET_WRAITSOTH" in support_ids and (fighter_is_kashtira(fighter) or fighter_is_visas_starfrost(fighter)):
        total_pct += 50
    if stat_type == "ATK" and "MILLENNIUM_ANKH" in support_ids and fighter_is_forbidden_one(fighter):
        total_pct -= 50
    if stat_type == "ATK" and "MOUND_OF_THE_BOUND_CREATOR" in support_ids and fighter_is_slifer_the_sky_dragon(fighter):
        living_count = len([unit for unit in [*ACTIVE_TEAMMATES_BY_FIGHTER.get(id(fighter), []), *enemy_team_for_fighter(fighter)] if unit.alive])
        total_pct += living_count * 20
    if stat_type == "DEF" and "MAGNET_CONVERSION" in support_ids and fighter_is_magnet_warrior(fighter):
        total_pct += 50
    if stat_type == "DEF" and active_equip_spell_name(fighter) == "Wave-Motion Cannon":
        total_pct += 25
    if stat_type == "DEF" and "MILLENNIUM_ANKH" in support_ids and fighter_is_forbidden_one(fighter):
        total_pct -= 50
    if stat_type == "ATK":
        base_stat = fighter.atk
    elif stat_type == "DEF":
        base_stat = fighter.defense
    elif stat_type == "SPD":
        base_stat = fighter.spd
    else:
        return flat_bonus
    pct_bonus = int(round(base_stat * (total_pct / 100.0))) if total_pct else 0
    return flat_bonus + pct_bonus


def effective_atk(fighter: Fighter, active_cards: Sequence[SupportCardDef]) -> int:
    atk_value = fighter.atk + support_card_stat_bonus(fighter, active_cards, "ATK")
    if global_ojama_country_active(fighter):
        atk_value = fighter.defense + support_card_stat_bonus(fighter, active_cards, "DEF")
    return max(1, atk_value)


def effective_defense(fighter: Fighter, active_cards: Sequence[SupportCardDef]) -> int:
    defense_value = fighter.defense + support_card_stat_bonus(fighter, active_cards, "DEF")
    if global_ojama_country_active(fighter):
        defense_value = fighter.atk + support_card_stat_bonus(fighter, active_cards, "ATK")
    return max(1, defense_value)


def effective_spd(fighter: Fighter, active_cards: Sequence[SupportCardDef]) -> int:
    spd_value = fighter.spd + support_card_stat_bonus(fighter, active_cards, "SPD")
    enemy_team = enemy_team_for_fighter(fighter)
    if any(enemy.alive and active_equip_spell_name(enemy) == "Ground Collapse" for enemy in enemy_team):
        if fighter_has_bomb(fighter) or fighter_has_status(fighter, StatusEffect.STUN):
            spd_value = int(round(spd_value * 0.75))
    return max(1, spd_value)


def scale_fighter_stats(fighter: Fighter, multiplier: float):
    fighter.max_hp = max(1, int(round(fighter.max_hp * multiplier)))
    fighter.hp = fighter.max_hp
    fighter.atk = max(1, int(round(fighter.atk * multiplier)))
    fighter.defense = max(1, int(round(fighter.defense * multiplier)))
    fighter.spd = max(1, int(round(fighter.spd * max(1.0, 0.9 + (multiplier - 1.0) * 0.45))))


def scale_fighter_base_stats_exact(fighter: Fighter, multiplier: float):
    """Scale every base combat stat by the same exact multiplier."""
    fighter.max_hp = max(1, int(round(fighter.max_hp * multiplier)))
    fighter.hp = fighter.max_hp
    fighter.atk = max(1, int(round(fighter.atk * multiplier)))
    fighter.defense = max(1, int(round(fighter.defense * multiplier)))
    fighter.spd = max(1, int(round(fighter.spd * multiplier)))


def type_multiplier_against(attacking_type: str, defending_type: str) -> float:
    chart = TYPE_EFFECTIVENESS.get(attacking_type, {})
    if defending_type in chart.get("strong", set()):
        return TYPE_SUPER_MULTIPLIER
    if defending_type in chart.get("weak", set()):
        return TYPE_RESIST_MULTIPLIER
    return 1.0


def monster_type_multiplier(attacker_types: Sequence[str], defender_types: Sequence[str]) -> Tuple[float, str]:
    if not attacker_types or not defender_types:
        return 1.0, "neutral"

    multipliers = []
    for attacking_type in attacker_types:
        multiplier = 1.0
        for defending_type in defender_types:
            multiplier *= type_multiplier_against(attacking_type, defending_type)
        multipliers.append(multiplier)

    best_multiplier = max(multipliers) if multipliers else 1.0
    if best_multiplier > 1.0:
        relation = "advantage"
    elif best_multiplier < 1.0:
        relation = "disadvantage"
    else:
        relation = "neutral"
    return best_multiplier, relation


def current_attack_power(fighter: Optional[Fighter]) -> int:
    if fighter is None:
        return 0
    active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])
    return max(1, effective_atk(fighter, active_cards))
