"""Generating NPC opponent teams' support cards and equip spells."""

from __future__ import annotations

import random
import re
import time
from collections import Counter
from typing import Dict, List, Optional, Sequence

from kuribot.config import (
    CAMPAIGN_HARD_MODE_ENEMY_SUPPORT_BONUSES, RNG, SUPPORT_DECK_SIZE, SUPPORT_DRAW_COUNT,
)
from kuribot.models import EquipSpellDef, Fighter, SupportCardDef, SupportDeckRuntime
from kuribot.cards.equip_spells import EQUIP_SPELL_DEFS, init_equip_spells
from kuribot.monsters import stable_seed_for_name
from kuribot.cards.support_cards import get_support_card_def, support_target_card_id
from kuribot.cards.support_decks import build_support_runtime_from_cards
from kuribot.battle.special_support import (
    fighter_matches_special_support, team_eligible_special_support_cards,
)


def support_targets_for_team(team: Sequence[Fighter]) -> List[str]:
    targets: List[str] = []
    seen = set()
    for fighter in team:
        for target_name in (fighter.attribute, *fighter.monster_types):
            normalized = str(target_name or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            targets.append(normalized)
    return targets


SUPPORT_CARD_TAG_OVERRIDES: Dict[str, Dict[str, object]] = {
    "CYBER_REVSYSTEM": {"tags": {"revive", "attrition"}},
    "INFERNO_FIRE_BLAST": {"tags": {"heart_pressure", "aggressive"}},
    "TEARLAMENTS_SULLIEK": {"tags": {"equip_hate", "control"}},
    "LABRYNTH_BARRAGE": {"tags": {"equip_hate", "control"}},
    "RYZEAL_CROSS": {"tags": {"equip_hate", "control"}},
    "NECROVALLEY": {"tags": {"support_hate", "control"}},
    "TRICKSTAR_REINCARNATION": {"tags": {"deck_hate", "heart_pressure", "control"}},
    "PUPPET_PARADE": {"tags": {"heart_pressure", "attrition"}},
    "KASHTIRA_BIG_BANG": {"tags": {"revive_hate", "control"}},
    "BLAZE_ACCELERATOR": {"tags": {"debuff", "burn", "heart_pressure"}},
    "BLACK_SONIC": {"tags": {"control", "reactive"}},
    "FALLEN_PARADISE": {"tags": {"immunity", "value"}},
    "HYPER_BLAZE": {"tags": {"equip_synergy", "aggressive"}},
    "MOUND_OF_THE_BOUND_CREATOR": {"tags": {"boss", "aggressive", "heart_pressure"}},
    "ULTIMATE_CRYSTAL_MAGIC": {"tags": {"revive", "boss", "attrition"}},
}


def _fighter_generation_role(fighter: Fighter) -> str:
    hp = max(1, int(fighter.max_hp))
    atk = max(1, int(fighter.atk))
    defense = max(1, int(fighter.defense))
    spd = max(1, int(fighter.spd))
    durability = hp + defense
    power = atk + spd
    if spd >= max(atk, defense) and spd >= max(140, int((atk + defense) / 2)):
        return "opener"
    if durability >= power * 1.45:
        return "tank"
    if atk >= defense * 1.30 and atk >= hp * 0.35:
        return "glass_cannon"
    if defense >= atk * 1.15 and spd >= 100:
        return "support"
    if spd >= 125 and atk >= 110:
        return "controller"
    return "bruiser"


def _fighter_archetype_flags(fighter: Fighter) -> set[str]:
    flags: set[str] = set()
    role = _fighter_generation_role(fighter)
    flags.add(role)
    if fighter.spd >= 125:
        flags.add("fast")
    if fighter.atk >= fighter.defense:
        flags.add("aggressive")
    if fighter.max_hp + fighter.defense >= fighter.atk + fighter.spd:
        flags.add("durable")
    if any(t in {"Machine"} for t in fighter.monster_types):
        flags.add("machine_team")
    if len(fighter.monster_types) == 1:
        flags.add("tribal")
    if any(t in {"Spellcaster", "Black Magic", "White Magic", "Illusion Magic", "Dragon Magic"} for t in fighter.monster_types):
        flags.add("secret_arts_type")
    return flags


def _special_support_tags(card: SupportCardDef) -> set[str]:
    tags: set[str] = set()
    desc = str(card.description or "").lower()
    name = str(card.name or "")
    card_id = str(card.support_card_id or "").upper()
    if "bomb" in desc:
        tags.update({"bomb_enabler", "debuff_enabler"})
    if any(word in desc for word in ["burn", "poison", "stun", "freeze", "petrif", "fear", "sleep", "charm", "silence", "debuff"]):
        tags.update({"debuff_enabler", "control_enabler"})
    if any(word in desc for word in ["immunity", "protection", "shield", "recovery", "heal", "revive"]):
        tags.add("sustain_enabler")
    if "revive" in desc or "replace" in desc:
        tags.add("revive_enabler")
    if any(word in desc for word in ["gain +", "gain ", "buff", "precog", "counter", "reflect", "lifesteal", "summon"]):
        tags.add("buff_enabler")
    if "equip spell" in desc and ("disable" in desc or "destroy" in desc):
        tags.add("equip_hate")
    if "heart" in desc:
        tags.add("heart_pressure")
    if "special" in str(card.attribute or "").upper():
        tags.add("special_support")
    tags.update(set(SUPPORT_CARD_TAG_OVERRIDES.get(card_id, {}).get("tags", set())))
    if "Fusion" in name:
        tags.update({"burst", "tribal_payoff"})
    return tags


def _build_team_generation_profile(team: Sequence[Fighter], special_cards: Optional[Sequence[SupportCardDef]] = None) -> Dict[str, object]:
    type_counter: Counter[str] = Counter()
    attr_counter: Counter[str] = Counter()
    role_counter: Counter[str] = Counter()
    flags: set[str] = set()
    for fighter in team:
        attr_counter.update([str(fighter.attribute)])
        type_counter.update(str(monster_type) for monster_type in fighter.monster_types)
        role = _fighter_generation_role(fighter)
        role_counter.update([role])
        flags.update(_fighter_archetype_flags(fighter))
    profile = {
        "roles": role_counter,
        "type_counter": type_counter,
        "attr_counter": attr_counter,
        "team_flags": set(flags),
        "special_support_tags": set(),
        "special_support_depth": 0,
        "bomb_enabler": False,
        "debuff_enabler": False,
        "control_enabler": False,
        "buff_enabler": False,
        "sustain_enabler": False,
        "revive_enabler": False,
        "heart_pressure": False,
    }
    if any(count >= 2 for count in type_counter.values()):
        profile["team_flags"].add("type_stack")
    if type_counter.get("Machine", 0) >= 1:
        profile["team_flags"].add("machine_team")
    if role_counter.get("tank", 0) >= 1:
        profile["team_flags"].add("attrition")
    if role_counter.get("controller", 0) + role_counter.get("opener", 0) >= 2:
        profile["team_flags"].add("control")
        profile["control_enabler"] = True
    if role_counter.get("glass_cannon", 0) + role_counter.get("bruiser", 0) >= 2:
        profile["team_flags"].add("burst")
        profile["team_flags"].add("aggressive")
    if role_counter.get("support", 0) >= 1 or role_counter.get("tank", 0) >= 1:
        profile["team_flags"].add("durable")
        profile["team_flags"].add("supportive")
    if special_cards:
        profile["special_support_depth"] = len(special_cards)
        for card in special_cards:
            tags = _special_support_tags(card)
            profile["special_support_tags"].update(tags)
            if "bomb_enabler" in tags:
                profile["bomb_enabler"] = True
            if "debuff_enabler" in tags:
                profile["debuff_enabler"] = True
            if "control_enabler" in tags:
                profile["control_enabler"] = True
            if "buff_enabler" in tags:
                profile["buff_enabler"] = True
            if "sustain_enabler" in tags:
                profile["sustain_enabler"] = True
            if "revive_enabler" in tags:
                profile["revive_enabler"] = True
            if "heart_pressure" in tags:
                profile["heart_pressure"] = True
    return profile


def _score_special_support_card(card: SupportCardDef, team: Sequence[Fighter], profile: Dict[str, object]) -> float:
    tags = _special_support_tags(card)
    score = 0.0
    matches = sum(1 for fighter in team if fighter_matches_special_support(card.support_card_id, fighter))
    score += matches * 24.0
    score += len(tags & set(profile.get("team_flags", set()))) * 10.0
    if "equip_hate" in tags:
        score += 16.0
    if "heart_pressure" in tags:
        score += 12.0
    if "revive_enabler" in tags and any(_fighter_generation_role(fighter) in {"glass_cannon", "bruiser"} for fighter in team):
        score += 14.0
    if "control_enabler" in tags and profile.get("roles", Counter()).get("controller", 0) > 0:
        score += 12.0
    if "sustain_enabler" in tags and profile.get("roles", Counter()).get("tank", 0) > 0:
        score += 10.0
    if str(card.support_card_id).upper() == "HYPER_BLAZE" and profile.get("special_support_depth", 0) > 0:
        score += 8.0
    return score


def _score_generated_target_card(card: SupportCardDef, team: Sequence[Fighter], profile: Dict[str, object]) -> float:
    score = 0.0
    target_name = str(card.attribute or "")
    stat_type = str(card.stat_type or "").upper()
    matching = 0
    for fighter in team:
        if fighter.attribute == target_name or target_name in fighter.monster_types:
            matching += 1
            role = _fighter_generation_role(fighter)
            if stat_type == "ATK" and role in {"glass_cannon", "bruiser", "opener"}:
                score += 8.0
            if stat_type == "DEF" and role in {"tank", "support"}:
                score += 8.0
            if stat_type == "SPD" and role in {"opener", "controller"}:
                score += 8.0
    score += matching * 10.0 + int(card.bonus_pct or 0)
    if matching >= 2:
        score += 10.0
    return score


def build_generated_support_cards_for_targets(target_names: Sequence[str], bonuses: Sequence[int], *, rng: Optional[random.Random] = None) -> List[SupportCardDef]:
    rng = rng or RNG
    pool = [str(name).strip() for name in target_names if str(name).strip()]
    if not pool or not bonuses:
        return []
    cards: List[SupportCardDef] = []
    stat_cycle = ["ATK", "DEF", "SPD"]
    while len(cards) < SUPPORT_DECK_SIZE:
        target_name = rng.choice(pool)
        bonus = rng.choice(list(bonuses))
        stat_type = stat_cycle[len(cards) % len(stat_cycle)]
        card = get_support_card_def(support_target_card_id(target_name, stat_type, bonus))
        if card is not None:
            cards.append(card)
    return cards


def build_aligned_support_cards_for_team(enemy_team: Sequence[Fighter], bonuses: Sequence[int], *, rng: Optional[random.Random] = None, deck_name: Optional[str] = None) -> List[SupportCardDef]:
    rng = rng or RNG
    targets = support_targets_for_team(enemy_team)
    if not targets:
        return []
    eligible_specials = team_eligible_special_support_cards(enemy_team)
    base_profile = _build_team_generation_profile(enemy_team, eligible_specials)
    scored_specials = sorted(
        eligible_specials,
        key=lambda card: (_score_special_support_card(card, enemy_team, base_profile), str(card.support_card_id)),
        reverse=True,
    )
    selected: List[SupportCardDef] = []
    selected_ids: set[str] = set()
    tag_caps = {"equip_hate": 2, "support_hate": 1, "revive_enabler": 2, "heart_pressure": 2, "control_enabler": 3}
    tag_counts: Counter[str] = Counter()
    for card in scored_specials:
        tags = _special_support_tags(card)
        blocked = False
        for tag, cap in tag_caps.items():
            if tag in tags and tag_counts[tag] >= cap:
                blocked = True
                break
        if blocked:
            continue
        selected.append(card)
        selected_ids.add(card.support_card_id)
        tag_counts.update(tags)
        if len(selected) >= min(5, SUPPORT_DRAW_COUNT + 2):
            break
    profile = _build_team_generation_profile(enemy_team, selected)
    generated_candidates: List[SupportCardDef] = []
    unique_targets = list(targets)
    bonus_values = sorted({int(value) for value in bonuses if int(value) > 0}) or [10]
    preferred_stats: List[str] = []
    roles = profile.get("roles", Counter())
    if roles.get("glass_cannon", 0) + roles.get("bruiser", 0) >= 2:
        preferred_stats.extend(["ATK", "ATK"])
    if roles.get("tank", 0) + roles.get("support", 0) >= 1:
        preferred_stats.append("DEF")
    if roles.get("opener", 0) + roles.get("controller", 0) >= 1:
        preferred_stats.append("SPD")
    preferred_stats.extend(["ATK", "DEF", "SPD"])
    for target in unique_targets:
        for bonus in bonus_values:
            for stat_type in preferred_stats:
                card = get_support_card_def(support_target_card_id(target, stat_type, bonus))
                if card is not None:
                    generated_candidates.append(card)
    generated_candidates.sort(
        key=lambda card: (_score_generated_target_card(card, enemy_team, profile), str(card.support_card_id)),
        reverse=True,
    )
    stat_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    for card in generated_candidates:
        if len(selected) >= SUPPORT_DECK_SIZE:
            break
        if card.support_card_id in selected_ids:
            continue
        if stat_counts[str(card.stat_type)] >= 6:
            continue
        if target_counts[str(card.attribute)] >= 4:
            continue
        selected.append(card)
        selected_ids.add(card.support_card_id)
        stat_counts.update([str(card.stat_type)])
        target_counts.update([str(card.attribute)])
    if len(selected) < SUPPORT_DECK_SIZE:
        fallback = build_generated_support_cards_for_targets(unique_targets, bonuses, rng=rng)
        for card in fallback:
            if len(selected) >= SUPPORT_DECK_SIZE:
                break
            if card.support_card_id in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(card.support_card_id)
    return selected[:SUPPORT_DECK_SIZE]


def build_campaign_enemy_support_runtime(area_key: str, duelist_name: str, enemy_team: Sequence[Fighter], hard_mode: bool = False) -> Optional[SupportDeckRuntime]:
    if not hard_mode:
        return None
    bonuses = [int(value) for value in CAMPAIGN_HARD_MODE_ENEMY_SUPPORT_BONUSES.get(area_key, [25])]
    seed = stable_seed_for_name(f"campaign_support:{area_key}:{duelist_name}:{int(time.time() // 60)}")
    cards = build_aligned_support_cards_for_team(enemy_team, bonuses, rng=random.Random(seed), deck_name=f"{duelist_name} Hard Mode Support Deck")
    unique_cards: List[SupportCardDef] = []
    seen_ids: set[str] = set()
    for card in cards:
        if card.support_card_id in seen_ids:
            continue
        seen_ids.add(card.support_card_id)
        unique_cards.append(card)
    if not unique_cards:
        return None
    return build_support_runtime_from_cards(f"{duelist_name} Hard Mode Support Deck", unique_cards[:SUPPORT_DECK_SIZE], special_first=True)


def remember_duelist_loss_against_player(memory_scope: str, duelist_key: str, player_user_id: int, player_team: Sequence[Fighter]) -> None:
    # Duelists no longer adapt to player losses or build counter profiles.
    # Enemy equip spells are generated fully at random each battle.
    return


def find_equip_spells_by_partial_name(query: str, limit: int = 10) -> List[EquipSpellDef]:
    init_equip_spells()
    normalized = str(query or "").strip().lower()
    if not normalized:
        return []
    exact = [spell for spell in EQUIP_SPELL_DEFS if str(spell.name).strip().lower() == normalized]
    starts = [spell for spell in EQUIP_SPELL_DEFS if str(spell.name).strip().lower().startswith(normalized) and spell not in exact]
    contains = [spell for spell in EQUIP_SPELL_DEFS if normalized in str(spell.name).strip().lower() and spell not in exact and spell not in starts]
    return [*exact, *starts, *contains][:max(1, int(limit))]


def assign_unique_generated_equip_spells(team: Sequence[Fighter], rng: random.Random, *, support_cards: Optional[Sequence[SupportCardDef]] = None, label_name: str = "Enemy Team", target_team: Optional[Sequence[Fighter]] = None, adaptation_memory: Optional[Dict[str, object]] = None) -> List[str]:
    """Assign fully random generated equip spells to duelist monsters.

    The older duelist generation system scored spells against the enemy team,
    support deck, player team, and adaptation memory. Duelists now ignore all of
    that and simply roll one random equip spell for each monster.
    """
    init_equip_spells()
    if not team or not EQUIP_SPELL_DEFS:
        return []
    rng = rng or RNG
    assigned: List[str] = []
    available_spells = list(EQUIP_SPELL_DEFS)
    rng.shuffle(available_spells)
    for fighter, chosen in zip(team, available_spells):
        fighter.runtime = dict(fighter.runtime)
        fighter.runtime["equip_spell_name"] = chosen.name
        fighter.runtime.pop("generated_role", None)
        assigned.append(f"{fighter.name} equips **{chosen.name}**.")
    return assigned


def assign_generated_campaign_hard_mode_equip_spells(team: Sequence[Fighter], area_key: str, duelist_key: str, support_runtime: Optional[SupportDeckRuntime] = None, *, target_team: Optional[Sequence[Fighter]] = None, adaptation_memory: Optional[Dict[str, object]] = None) -> None:
    seed = stable_seed_for_name(f"campaign_hard_equip:{area_key}:{duelist_key}:{int(time.time() // 60)}")
    rng = random.Random(seed)
    assign_unique_generated_equip_spells(
        team,
        rng,
        support_cards=(support_runtime.shuffled_cards if support_runtime else None),
        label_name=f"{area_key}:{duelist_key}",
        target_team=target_team,
        adaptation_memory=adaptation_memory,
    )


def format_duelist_display_name(value: str) -> str:
    pieces = [part for part in re.split(r"[_\-\s]+", str(value or "").strip()) if part]
    if not pieces:
        return "Unknown Duelist"
    return " ".join(piece.capitalize() if piece.lower() not in {"iv", "vi", "vii"} else piece.upper() for piece in pieces)
