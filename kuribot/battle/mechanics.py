"""The effect engine: damage, healing, status effects, bombs, buffs, equip spell and support card effects."""

from __future__ import annotations

import math
import sqlite3
import uuid
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from kuribot.config import (
    ATB_READY_EPSILON, ATB_TIME_ACCELERATION, BATTLE_HEARTS, RNG, SPEED_GAUGE_MAX,
    TYPE_SUPER_MULTIPLIER,
)
from kuribot.models import (
    Fighter, STATUS_BLOCKS_ACTION, StatusEffect, StatusState, SupportCardDef, SupportDeckRuntime,
)
from kuribot.cards.equip_spells import get_equip_spell_def
from kuribot.database import db
from kuribot.monsters import (
    extra_deck_effect_key, random_monster_attribute_for_row, roll_monster_types,
)
from kuribot.battle.archetypes import (
    fighter_is_albaz, fighter_is_ancient_gear, fighter_is_archfiend, fighter_is_blackwing,
    fighter_is_blue_eyes, fighter_is_charmer_familiar_channeler, fighter_is_clear_wing,
    fighter_is_clown_clan, fighter_is_crystal_beast, fighter_is_crystron, fighter_is_cyber_dragon,
    fighter_is_dark_magician, fighter_is_destiny_hero, fighter_is_dinowrestler,
    fighter_is_dracotail, fighter_is_fire_king, fighter_is_fossil, fighter_is_frightfur_or_fluffal,
    fighter_is_gem_knight, fighter_is_gravekeeper, fighter_is_hamon, fighter_is_infernoid,
    fighter_is_invoked, fighter_is_labrynth, fighter_is_lunalight, fighter_is_magician_girl,
    fighter_is_magnet_warrior, fighter_is_masked_hero, fighter_is_metalfoes, fighter_is_mimighoul,
    fighter_is_mitsurugi, fighter_is_myutant, fighter_is_neos, fighter_is_obelisk_the_tormentor,
    fighter_is_orcust, fighter_is_penguin, fighter_is_predaplant, fighter_is_primite,
    fighter_is_raviel, fighter_is_red_eyes, fighter_is_relinquished, fighter_is_ryzeal,
    fighter_is_shaddoll, fighter_is_silent_swordsman, fighter_is_speedroid,
    fighter_is_the_winged_dragon_of_ra, fighter_is_thunder_dragon, fighter_is_toon,
    fighter_is_trickstar, fighter_is_true_king_or_true_draco,
    fighter_is_uria_lord_of_searing_flames, fighter_is_utopia_or_utopic, fighter_is_vanquish_soul,
    fighter_is_vision_hero, fighter_is_volcanic, fighter_is_white_forest, fighter_is_yummy,
)
from kuribot.battle.runtime import (
    _active_equip_spell_name_base, _choose_enemy_team_by_identity, _fighter_in_team_by_identity,
    _fighter_statuses_store, active_equip_spell_name, ACTIVE_SUPPORT_CARDS_BY_FIGHTER,
    ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER, ACTIVE_TEAMMATES_BY_FIGHTER, battle_fighters_for,
    enemy_team_for_fighter, fighter_has_bomb, fighter_has_status, fighter_statuses,
    gain_flat_stat_bonus, gain_percent_stat_bonus, get_status_state, is_extra_deck_fighter,
    is_guild_boss_fighter, living_fighters, random_alive, register_battle_teams, runtime_flag,
    set_runtime_flag, team_alive,
)
from kuribot.battle.fighters import make_fighter
from kuribot.battle.special_support import (
    apply_special_support_fusion_bonus, set_support_runtime_flag,
    special_support_active_for_fighter, support_runtime_flag, team_has_active_special_support,
)
from kuribot.battle.stats import (
    current_attack_power, effective_atk, effective_defense, effective_spd, monster_type_multiplier,
)


def resolve_battle_outcome(
    player_team: Sequence[Fighter],
    enemy_team: Sequence[Fighter],
    player_hearts: int,
    enemy_hearts: int,
) -> Tuple[bool, bool]:
    player_won = (enemy_hearts <= 0) or (team_alive(player_team) and not team_alive(enemy_team))
    enemy_won = (player_hearts <= 0) or (team_alive(enemy_team) and not team_alive(player_team))
    if player_won and enemy_won:
        return False, False
    return player_won, enemy_won


def trigger_warning_point_on_dodge(target: Fighter, attacker: Fighter, log_entries: List[str]) -> None:
    if target.alive and attacker.alive and active_equip_spell_name(target) == "Warning Point" and RNG.random() < 0.45:
        if apply_status_effect(attacker, StatusEffect.STUN, source=target, log_entries=log_entries):
            log_entries.append(f"**Warning Point** stuns **{attacker.name}** after **{target.name}** dodges.")


def trigger_yummy_surprise_on_dodge(target: Fighter, attacker: Fighter, log_entries: List[str]) -> None:
    active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
    if not fighter_is_yummy(target) or not any(card.support_card_id == "YUMMY_SURPRISE" for card in active_cards):
        return
    retaliate = max(1, int(round(attacker.max_hp * 0.25)))
    dealt = apply_damage_with_runtime(attacker, retaliate, target, log_entries, "Yummy Surprise")
    if dealt > 0:
        log_entries.append(f"**Yummy Surprise** punishes **{attacker.name}** for **{dealt}** damage after **{target.name}** dodges the attack.")


def trigger_mound_ra_on_heart_loss(team: Sequence[Fighter], heart_owner_name: str, log_entries: List[str]) -> None:
    for fighter in team:
        if not fighter.alive or not fighter_is_the_winged_dragon_of_ra(fighter):
            continue
        active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])
        if not any(card.support_card_id == "MOUND_OF_THE_BOUND_CREATOR" for card in active_cards):
            continue
        delta = 100 if RNG.random() < 0.5 else -100
        total_pct = gain_percent_stat_bonus(fighter, "ATK", delta)
        direction = "gains" if delta >= 0 else "loses"
        log_entries.append(f"**Mound of the Bound Creator**: **{fighter.name}** {direction} **100% ATK** when {heart_owner_name} loses a heart (current bonus: {total_pct:+d}% ATK).")


def trigger_mound_obelisk_on_knockout(team: Sequence[Fighter], fallen: Fighter, log_entries: List[str]) -> None:
    atk_gain = max(1, current_attack_power(fallen))
    for fighter in team:
        if not fighter.alive or not fighter_is_obelisk_the_tormentor(fighter):
            continue
        active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])
        if not any(card.support_card_id == "MOUND_OF_THE_BOUND_CREATOR" for card in active_cards):
            continue
        gained = gain_flat_stat_bonus(fighter, "ATK", atk_gain)
        log_entries.append(f"**Mound of the Bound Creator** lets **{fighter.name}** absorb **{gained} ATK** from **{fallen.name}**.")


def _inferno_fire_blast_extra_hearts(source_team: Sequence[Fighter], hearts_lost: int) -> int:
    if hearts_lost <= 0:
        return 0
    return 1 if team_has_active_special_support(source_team, "INFERNO_FIRE_BLAST", fighter_is_red_eyes) else 0


def primary_status_source_monster_id(fighter: Fighter, effect: StatusEffect) -> Optional[str]:
    status = get_status_state(fighter, effect)
    return status.source_monster_id if status is not None else None


def clear_status(
    fighter: Fighter,
    log_entries: Optional[List[str]] = None,
    reason: Optional[str] = None,
    *,
    effect: Optional[StatusEffect] = None,
) -> None:
    statuses = _fighter_statuses_store(fighter)
    if not statuses:
        return
    if effect is None:
        removed = list(statuses)
        statuses.clear()
    else:
        removed = [status for status in statuses if status.effect is effect]
        if not removed:
            return
        statuses[:] = [status for status in statuses if status.effect is not effect]
    fighter.runtime["status_effects"] = statuses
    fighter.status = statuses[0] if statuses else None
    if log_entries is not None:
        for previous_state in removed:
            previous = previous_state.effect.value
            if reason:
                log_entries.append(f"**{fighter.name}** is no longer affected by **{previous}** ({reason}).")
            else:
                log_entries.append(f"**{fighter.name}** is no longer affected by **{previous}**.")
    if any(previous_state.effect is StatusEffect.SLEEP for previous_state in removed):
        if runtime_flag(fighter, "book_of_eclipse_pending_immunity", False):
            set_runtime_flag(fighter, "book_of_eclipse_pending_immunity", False)
            set_runtime_flag(fighter, "book_of_eclipse_sleep_turns", 0)
            set_runtime_flag(fighter, "immunity_turns", max(int(runtime_flag(fighter, "immunity_turns", 0)), 999))
            if log_entries is not None:
                log_entries.append(f"**Book of Eclipse** grants **IMMUNITY** to **{fighter.name}** after it wakes.")


def apply_status_effect(target: Fighter, effect: StatusEffect, *, source: Optional[Fighter] = None, log_entries: Optional[List[str]] = None) -> bool:
    if not target.alive:
        return False
    if int(runtime_flag(target, "immunity_turns", 0)) > 0 or int(runtime_flag(target, "full_debuff_immunity_turns", 0)) > 0:
        if log_entries is not None:
            log_entries.append(f"**{target.name}** ignores **{effect.value}** because of **IMMUNITY**.")
        return False
    immune_effects = set(runtime_flag(target, "immune_effects", set()))
    if effect.value in immune_effects:
        if log_entries is not None:
            log_entries.append(f"**{target.name}** ignores **{effect.value}**.")
        return False
    if fighter_has_status(target, StatusEffect.PETRIFY) and effect is not StatusEffect.PETRIFY:
        if log_entries is not None:
            log_entries.append(f"**{target.name}** ignores **{effect.value}** because **PETRIFY** blocks all other status effects.")
        return False
    if effect is StatusEffect.BURN and fighter_has_status(target, StatusEffect.FREEZE):
        clear_status(target, log_entries, reason="melted by BURN", effect=StatusEffect.FREEZE)

    statuses = _fighter_statuses_store(target)
    existing = get_status_state(target, effect)
    is_dot = effect in {StatusEffect.BURN, StatusEffect.POISON}
    is_first_burn_or_poison = is_dot and existing is None

    if existing is not None:
        existing.source_monster_id = source.monster_id if source is not None else None
        existing.source_name = source.name if source is not None else ""
        if effect is StatusEffect.POISON:
            existing.poison_pct = max(1, int(getattr(existing, "poison_pct", 1) or 1))
        target.runtime["status_effects"] = statuses
        target.status = statuses[0] if statuses else None
        if log_entries is not None:
            source_text = f" from **{source.name}**" if source is not None else ""
            log_entries.append(f"**{target.name}** is afflicted with **{effect.value}**{source_text}.")
    else:
        state = StatusState(
            effect=effect,
            source_monster_id=source.monster_id if source is not None else None,
            source_name=source.name if source is not None else "",
            poison_pct=1,
        )
        statuses.append(state)
        target.runtime["status_effects"] = statuses
        target.status = statuses[0] if statuses else None
        if log_entries is not None:
            source_text = f" from **{source.name}**" if source is not None else ""
            log_entries.append(f"**{target.name}** is afflicted with **{effect.value}**{source_text}.")

    if source is not None and active_equip_spell_name(target) == "Chain Energy" and not runtime_flag(target, "chain_energy_used", False):
        set_runtime_flag(target, "chain_energy_used", True)
        place_bomb(source, None, 0.30, target, log_entries or [])
    if source is not None and active_equip_spell_name(target) == "Curse Transfer" and source.alive:
        if log_entries is not None:
            log_entries.append(f"**Curse Transfer** sends **{effect.value}** back to **{source.name}**.")
        apply_status_effect(source, effect, source=target, log_entries=log_entries)
    if is_first_burn_or_poison and source is not None:
        source_allies = ACTIVE_TEAMMATES_BY_FIGHTER.get(id(source), [])
        target_allies = ACTIVE_TEAMMATES_BY_FIGHTER.get(id(target), [])
        maybe_trigger_dian_keto_cleanse_heal(target, target_allies, source_allies, effect, log_entries)
    if source is not None and is_guild_boss_fighter(target) and target.alive and source.alive:
        counter_result = resolve_attack_damage(target, source)
        if counter_result["hit"]:
            counter = apply_damage_with_runtime(source, int(counter_result["damage"]), target, log_entries or [], "guild boss debuff counterattack")
            crit_text = " **Critical hit!**" if counter_result["crit"] else ""
            if log_entries is not None:
                log_entries.append(f"**Guild Boss Counterattack**: **{target.name}** punishes **{source.name}** for inflicting **{effect.value}**, dealing **{counter}** damage.{crit_text}")
        elif log_entries is not None:
            log_entries.append(f"**Guild Boss Counterattack** triggers, but **{source.name}** avoids the counterattack.")
    return True


def apply_heal_block(target: Fighter, *, source: Optional[Fighter] = None, log_entries: Optional[List[str]] = None, flag_name: str = "heal_block_turns", source_label: str = "HEAL BLOCK") -> bool:
    if not target.alive:
        return False
    if int(runtime_flag(target, "immunity_turns", 0)) > 0 or int(runtime_flag(target, "full_debuff_immunity_turns", 0)) > 0:
        if log_entries is not None:
            log_entries.append(f"**{target.name}** ignores **HEAL BLOCK** because of **IMMUNITY**.")
        return False
    set_runtime_flag(target, flag_name, max(int(runtime_flag(target, flag_name, 0)), 999))
    if log_entries is not None:
        source_text = f" from **{source.name}**" if source is not None else ""
        log_entries.append(f"**{target.name}** receives **HEAL BLOCK**{source_text}.")
    return True


def status_prevents_action(fighter: Fighter) -> Optional[str]:
    for status in fighter_statuses(fighter):
        if status.effect in STATUS_BLOCKS_ACTION:
            return status.effect.value
    return None


def action_target_override(actor: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter]) -> Tuple[Sequence[Fighter], Optional[str]]:
    if fighter_has_status(actor, StatusEffect.CHARM) and living_fighters(allies) and RNG.random() < (1 / 3):
        return allies, f"**CHARM** twists **{actor.name}** into attacking an ally."
    taunt_source_monster_id = primary_status_source_monster_id(actor, StatusEffect.TAUNT)
    if taunt_source_monster_id:
        forced = next((fighter for fighter in enemies if fighter.alive and fighter.monster_id == taunt_source_monster_id), None)
        if forced is not None:
            return [forced], f"**{actor.name}** is forced by **TAUNT** to target **{forced.name}**."
    wave_motion_taunters = [fighter for fighter in enemies if fighter.alive and active_equip_spell_name(fighter) == "Wave-Motion Cannon"]
    if wave_motion_taunters:
        forced = RNG.choice(wave_motion_taunters)
        return [forced], f"**Wave-Motion Cannon** forces **{actor.name}** to target **{forced.name}**."
    return enemies, None


def resolve_start_of_turn_status(actor: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> bool:
    guild_boss_unstoppable = is_guild_boss_fighter(actor)
    pending_logs = list(runtime_flag(actor, "pending_log_entries", []))
    if pending_logs:
        log_entries.extend(str(entry) for entry in pending_logs)
        set_runtime_flag(actor, "pending_log_entries", [])
    blocked_turns = int(runtime_flag(actor, "cannot_act_turns", 0))
    if blocked_turns > 0:
        set_runtime_flag(actor, "cannot_act_turns", max(0, blocked_turns - 1))
        if guild_boss_unstoppable:
            log_entries.append(f"**{actor.name}** ignores an effect that would stop it from acting.")
        else:
            log_entries.append(f"**{actor.name}** cannot act this turn.")
            return False
    if not _there_can_be_only_one_allows_action(actor, allies):
        if guild_boss_unstoppable:
            log_entries.append(f"**{actor.name}** ignores **There Can Be Only One** and acts anyway.")
        else:
            log_entries.append(f"**There Can Be Only One** prevents **{actor.name}** from acting this turn.")
            return False
    toon_briefcase_turns = int(runtime_flag(actor, "toon_briefcase_turns", 0))
    if toon_briefcase_turns > 0:
        set_runtime_flag(actor, "toon_briefcase_turns", max(0, toon_briefcase_turns - 1))
        if fighter_has_status(actor, StatusEffect.STUN) and int(runtime_flag(actor, "toon_briefcase_turns", 0)) <= 0:
            clear_status(actor, log_entries, reason="Toon Briefcase expired", effect=StatusEffect.STUN)
        if guild_boss_unstoppable:
            log_entries.append(f"**{actor.name}** ignores **Toon Briefcase** and acts anyway.")
        else:
            log_entries.append(f"**{actor.name}** cannot act because of **Toon Briefcase**.")
            return False
    blocked_by = status_prevents_action(actor)
    if blocked_by:
        if blocked_by == StatusEffect.SLEEP.value and int(runtime_flag(actor, "book_of_eclipse_sleep_turns", 0)) > 1:
            set_runtime_flag(actor, "book_of_eclipse_sleep_turns", int(runtime_flag(actor, "book_of_eclipse_sleep_turns", 0)) - 1)
        else:
            clear_status(actor, log_entries, reason=f"{blocked_by} expired", effect=StatusEffect(blocked_by))
        if guild_boss_unstoppable:
            log_entries.append(f"**{actor.name}** ignores **{blocked_by}** and acts anyway.")
        else:
            log_entries.append(f"**{actor.name}** cannot act because of **{blocked_by}**.")
            return False
    if fighter_has_status(actor, StatusEffect.FEAR) and RNG.random() < (1 / 3):
        if guild_boss_unstoppable:
            log_entries.append(f"**{actor.name}** ignores **FEAR** and acts anyway.")
        else:
            log_entries.append(f"**{actor.name}** is overcome by **FEAR** and fails to act.")
            return False
    if fighter_has_status(actor, StatusEffect.SILENCE) and runtime_flag(actor, "equip_spell_name"):
        log_entries.append(f"**SILENCE** disables **{actor.name}**'s equip spell.")
    active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(actor), [])
    if fighter_is_cyber_dragon(actor) and any(card.support_card_id == "FUTURE_FUSION" for card in active_cards):
        if guild_boss_unstoppable:
            log_entries.append(f"**{actor.name}** ignores **Future Fusion** and acts anyway.")
        else:
            log_entries.append(f"**Future Fusion** prevents **{actor.name}** from acting.")
            return False
    return True


def apply_post_turn_status_effects(all_fighters: Sequence[Fighter], log_entries: List[str]) -> None:
    for fighter in all_fighters:
        if not fighter.alive:
            continue
        effects = []
        for effect in list(runtime_flag(fighter, "recovery_effects", [])):
            turns = int(effect.get("turns", 0))
            pct = float(effect.get("pct", 0.0))
            if turns > 0 and pct > 0:
                heal_amount = max(1, int(round(fighter.max_hp * pct)))
                heal_fighter(fighter, heal_amount, all_fighters, [], log_entries, "Recovery", source=fighter)
                effect["turns"] = turns - 1
                if effect["turns"] > 0:
                    effects.append(effect)
        set_runtime_flag(fighter, "recovery_effects", effects)
        statuses = fighter_statuses(fighter)
        if not statuses:
            continue
        burn_state = get_status_state(fighter, StatusEffect.BURN)
        if burn_state is not None:
            damage = max(1, int(math.ceil(fighter.max_hp * 0.05)))
            if is_guild_boss_fighter(fighter):
                damage = max(1, int(math.ceil(damage * 0.5)))
            source_fighter = next((candidate for candidate in all_fighters if candidate.monster_id == burn_state.source_monster_id), None)
            dealt = apply_damage_with_runtime(fighter, damage, source_fighter, log_entries, "BURN")
            log_entries.append(f"**BURN** deals **{dealt}** damage to **{fighter.name}**.")
        poison_state = get_status_state(fighter, StatusEffect.POISON)
        if poison_state is not None:
            pct = max(1, min(10, int(poison_state.poison_pct)))
            damage = max(1, int(math.ceil(fighter.max_hp * (pct / 100.0))))
            if is_guild_boss_fighter(fighter):
                damage = max(1, int(math.ceil(damage * 0.5)))
            source_fighter = next((candidate for candidate in all_fighters if candidate.monster_id == poison_state.source_monster_id), None)
            dealt = apply_damage_with_runtime(fighter, damage, source_fighter, log_entries, "POISON")
            log_entries.append(f"**POISON** deals **{dealt}** damage to **{fighter.name}** ({pct}% max HP).")
            poison_state.poison_pct = min(10, pct + 1)


def on_fighter_attacked(target: Fighter, attacker: Fighter, log_entries: List[str]) -> None:
    if fighter_has_status(target, StatusEffect.SLEEP):
        clear_status(target, log_entries, reason="woke up after being attacked", effect=StatusEffect.SLEEP)


def _runtime_team_has_support_card(team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime], support_card_id: str, matcher=None) -> bool:
    if runtime is None or not runtime.active_cards:
        return False
    if not any(card.support_card_id == support_card_id for card in runtime.active_cards):
        return False
    for fighter in team:
        if not fighter.alive:
            continue
        if matcher is None or matcher(fighter):
            return True
    return False


def _runtime_team_disables_enemy_support_base(team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime]) -> bool:
    return _runtime_team_has_support_card(team, runtime, "NECROVALLEY", fighter_is_gravekeeper)


def _heart_loss_prevented(team: Sequence[Fighter]) -> bool:
    if any(f.alive and fighter_is_archfiend(f) and any(card.support_card_id == "PANDEMONIUM" for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(f), [])) for f in team):
        return True
    yummy_alive = sum(1 for f in team if f.alive and fighter_is_yummy(f) and any(card.support_card_id == "YUM_YUM_YUMMYS" for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(f), [])))
    return yummy_alive >= 2


def _find_monster_rows_by_name_fragment(*needles: str) -> List[sqlite3.Row]:
    rows = db.conn.execute("SELECT * FROM monster_defs ORDER BY name ASC").fetchall()
    lowered = [str(n).strip().lower() for n in needles if str(n).strip()]
    matches: List[sqlite3.Row] = []
    for row in rows:
        name = str(row["name"]).lower()
        if all(needle in name for needle in lowered):
            matches.append(row)
    return matches


def _replace_fighter_with_generated_monster(target: Fighter, name_needles: Sequence[str], log_entries: List[str], *, randomize_identity: bool = True, bonus_multiplier: float = 0.0, choose_random_match: bool = False) -> Optional[Fighter]:
    matches = _find_monster_rows_by_name_fragment(*name_needles)
    if not matches:
        return None
    row = RNG.choice(matches) if choose_random_match else matches[0]
    if row is None:
        return None
    fighter = make_fighter(row, monster_types=roll_monster_types(), instance_id=uuid.uuid4().hex)
    if randomize_identity:
        fighter.attribute = random_monster_attribute_for_row(row)
    if bonus_multiplier > 0:
        fighter.max_hp = max(1, int(round(fighter.max_hp * (1.0 + bonus_multiplier))))
        fighter.hp = fighter.max_hp
        fighter.atk = max(1, int(round(fighter.atk * (1.0 + bonus_multiplier))))
        fighter.defense = max(1, int(round(fighter.defense * (1.0 + bonus_multiplier))))
        fighter.spd = max(1, int(round(fighter.spd * (1.0 + bonus_multiplier))))
    target.monster_id = fighter.monster_id
    target.name = fighter.name
    target.attribute = fighter.attribute
    target.monster_types = fighter.monster_types
    target.max_hp = fighter.max_hp
    target.hp = fighter.hp
    target.atk = fighter.atk
    target.defense = fighter.defense
    target.spd = fighter.spd
    target.image_path = fighter.image_path
    target.crit_rate = fighter.crit_rate
    target.crit_damage = fighter.crit_damage
    target.accuracy = fighter.accuracy
    target.dodge = fighter.dodge
    target.status = None
    target.runtime = dict(fighter.runtime)
    return target


def _grant_token(target: Fighter, log_entries: List[str], source_name: str = "Tales of the White Forest") -> None:
    summon = {
        "hp": max(1, int(round(target.max_hp * 0.50))),
        "atk": max(1, int(round(target.atk * 0.50))),
        "def": max(1, int(round(target.defense * 0.50))),
        "spd": max(1, int(round(target.spd * 0.50))),
        "attribute": target.attribute,
        "monster_types": tuple(target.monster_types),
        "name": f"{target.name} Token",
        "image_path": target.image_path,
    }
    set_runtime_flag(target, "summon_data", summon)
    log_entries.append(f"**{source_name}** grants **TOKEN** to **{target.name}**.")


def _resolve_token_guard(target: Fighter, log_entries: List[str]) -> Optional[dict]:
    summon = runtime_flag(target, "summon_data", None)
    if isinstance(summon, dict) and int(summon.get("hp", 0)) > 0:
        return summon
    return None


def attempt_ultimate_crystal_magic_save(
    team: Sequence[Fighter],
    runtime: Optional[SupportDeckRuntime],
    hearts_remaining: int,
    owner_name: str,
    log_entries: List[str],
) -> int:
    if hearts_remaining > 0 or runtime is None:
        return hearts_remaining
    active_cards = list(runtime.active_cards)
    if not any(card.support_card_id == "ULTIMATE_CRYSTAL_MAGIC" for card in active_cards):
        return hearts_remaining
    if support_runtime_flag(runtime, "ultimate_crystal_magic_used", False):
        return hearts_remaining
    defeated = [fighter for fighter in team if fighter.hp <= 0 and fighter_is_crystal_beast(fighter)]
    if len(defeated) < 3:
        return hearts_remaining
    primary = defeated[0]
    if not _replace_fighter_with_generated_monster(primary, ["rainbow", "dragon"], log_entries, randomize_identity=True, bonus_multiplier=1.0):
        return hearts_remaining
    primary.hp = primary.max_hp
    for extra in defeated[1:3]:
        extra.hp = 0
        extra.status = None
        extra.runtime = dict(extra.runtime)
        extra.runtime.pop("summon_data", None)
    set_support_runtime_flag(runtime, "ultimate_crystal_magic_used", True)
    log_entries.append(f"**Ultimate Crystal Magic** activates instead of defeat. **{owner_name}** stays in the duel at **1 heart** and **{primary.name}** is opened with **+100% HP, ATK, DEF, and SPD**.")
    return 1


def effective_monster_types(fighter: Optional[Fighter]) -> Tuple[str, ...]:
    if fighter is None:
        return tuple()
    if any(other.alive and active_equip_spell_name(other) == "Clockwork Night" for other in battle_fighters_for(fighter)):
        return ("Machine",)
    return tuple(fighter.monster_types)


def has_shared_attribute_or_type(attacker: Fighter, defender: Fighter) -> bool:
    if attacker.attribute == defender.attribute:
        return True
    attacker_types = set(effective_monster_types(attacker))
    defender_types = set(effective_monster_types(defender))
    return bool(attacker_types & defender_types)


def _is_original_machine_type(fighter: Fighter) -> bool:
    return "Machine" in tuple(fighter.monster_types)


def _grant_random_turn_start_machine_effect(target: Fighter, source: Fighter, log_entries: List[str]) -> None:
    effects = ["IMMUNITY", "REFLECT", "PRECOG", "COUNTER", "BURN", "POISON", "STUN", "FEAR", "SILENCE", "TAUNT"]
    choice = RNG.choice(effects)
    if choice == "IMMUNITY":
        set_runtime_flag(target, "immunity_turns", max(1, int(runtime_flag(target, "immunity_turns", 0))))
        log_entries.append(f"**Dangerous Machine Type-6** grants **IMMUNITY** to **{target.name}** for 1 turn.")
    elif choice == "REFLECT":
        set_runtime_flag(target, "reflect_turns", max(1, int(runtime_flag(target, "reflect_turns", 0))))
        log_entries.append(f"**Dangerous Machine Type-6** grants **REFLECT** to **{target.name}** for 1 turn.")
    elif choice == "PRECOG":
        set_runtime_flag(target, "precog_turns", max(1, int(runtime_flag(target, "precog_turns", 0))))
        log_entries.append(f"**Dangerous Machine Type-6** grants **PRECOG** to **{target.name}** for 1 turn.")
    elif choice == "COUNTER":
        set_runtime_flag(target, "counter_turns", max(1, int(runtime_flag(target, "counter_turns", 0))))
        log_entries.append(f"**Dangerous Machine Type-6** grants **COUNTER** to **{target.name}** for 1 turn.")
    else:
        apply_status_effect(target, StatusEffect[choice], source=source, log_entries=log_entries)


def _team_has_attack_and_receive(team: Sequence[Fighter]) -> bool:
    return any(f.alive and active_equip_spell_name(f) == "Attack and Receive" for f in team)


def trigger_attack_and_receive(heart_losing_team: Sequence[Fighter], opposing_team: Sequence[Fighter], hearts_lost: int, opposing_hearts: int, opposing_name: str, log_entries: List[str], *, max_hearts: int = BATTLE_HEARTS) -> int:
    hearts_lost = max(0, int(hearts_lost))
    if hearts_lost <= 0 or not _team_has_attack_and_receive(heart_losing_team):
        return opposing_hearts
    if _heart_loss_prevented(opposing_team):
        log_entries.append(f"**Attack and Receive** would make {opposing_name} lose heart(s), but heart loss is prevented.")
        return opposing_hearts
    base_hearts_lost = hearts_lost
    hearts_lost += _inferno_fire_blast_extra_hearts(heart_losing_team, hearts_lost)
    updated = max(0, int(opposing_hearts) - hearts_lost)
    heart_label = "heart" if hearts_lost == 1 else "hearts"
    if hearts_lost > base_hearts_lost:
        log_entries.append(f"**Attack and Receive** makes {opposing_name} lose **{base_hearts_lost} {'heart' if base_hearts_lost == 1 else 'hearts'}**, and **Inferno Fire Blast** adds **1 extra heart** ({updated}/{max_hearts} left).")
    else:
        log_entries.append(f"**Attack and Receive** makes {opposing_name} lose **{hearts_lost} {heart_label}** ({updated}/{max_hearts} left).")
    trigger_mound_ra_on_heart_loss(heart_losing_team, opposing_name, log_entries)
    trigger_mound_ra_on_heart_loss(opposing_team, opposing_name, log_entries)
    return updated


def _team_has_morale_boost(team: Sequence[Fighter]) -> bool:
    return any(f.alive and active_equip_spell_name(f) == "Morale Boost" for f in team)


def _apply_morale_boost(team: Sequence[Fighter], hearts_remaining: int, owner_name: str, log_entries: List[str], *, max_hearts: int) -> int:
    if not _team_has_morale_boost(team):
        return hearts_remaining
    updated = min(max_hearts, int(hearts_remaining) + 1)
    if updated > hearts_remaining:
        log_entries.append(f"**Morale Boost** restores **1 heart** to {owner_name} ({updated}/{max_hearts} left).")
    return updated


def _apply_top_share(runtime: Optional[SupportDeckRuntime], owner_label: str, log_entries: List[str]) -> None:
    if runtime is None or not runtime.active_cards:
        return
    candidates = [card for card in runtime.shuffled_cards if str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL"]
    if not candidates:
        return
    chosen = RNG.choice(candidates)
    slot = RNG.randrange(len(runtime.active_cards))
    runtime.active_cards[slot] = chosen
    log_entries.append(f"**Top Share** ensures {owner_label} draws **{chosen.name}**.")


def _discard_random_special_from_hand(runtime: Optional[SupportDeckRuntime], owner_label: str, log_entries: List[str]) -> bool:
    if runtime is None or not runtime.active_cards:
        return False
    candidates = [card for card in runtime.active_cards if str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL"]
    if not candidates:
        return False
    chosen = RNG.choice(candidates)
    try:
        runtime.active_cards.remove(chosen)
    except ValueError:
        return False
    log_entries.append(f"**Delinquent Duo** discards **{chosen.name}** from {owner_label}'s hand.")
    return True


def _there_can_be_only_one_allows_action(actor: Fighter, allies: Sequence[Fighter]) -> bool:
    if not any(f.alive and active_equip_spell_name(f) == "There Can Be Only One" for f in [*allies, *enemy_team_for_fighter(actor)]):
        return True
    actor_types = set(effective_monster_types(actor))
    if not actor_types:
        return True
    group = [ally for ally in allies if ally.alive and set(effective_monster_types(ally)) & actor_types]
    if len(group) < 2:
        return True
    chosen = RNG.choice(group)
    return actor is chosen


def trigger_spell_absorption_on_buff_gain(buffed_fighter: Fighter) -> None:
    all_fighters = battle_fighters_for(buffed_fighter)
    if not all_fighters:
        return
    log_entries = buffed_fighter.runtime.setdefault("pending_log_entries", [])
    for observer in all_fighters:
        if not observer.alive or active_equip_spell_name(observer) != "Spell Absorption":
            continue
        allies = list(ACTIVE_TEAMMATES_BY_FIGHTER.get(id(observer), []))
        enemies = enemy_team_for_fighter(observer)
        heal_fighter(observer, max(1, int(round(observer.max_hp * 0.10))), allies, enemies, log_entries, "Spell Absorption", source=observer)


def fighter_has_debuff(fighter: Fighter) -> bool:
    return (
        bool(fighter_statuses(fighter))
        or int(runtime_flag(fighter, "heal_block_turns", 0)) > 0
        or int(runtime_flag(fighter, "damage_taken_up_turns", 0)) > 0
        or int(runtime_flag(fighter, "soul_taker_block_turns", 0)) > 0
        or fighter_has_bomb(fighter)
    )


def remove_bomb_debuff(
    fighter: Fighter,
    log_entries: Optional[List[str]] = None,
    *,
    reason: Optional[str] = None,
    count: int = 1,
) -> int:
    bombs = list(runtime_flag(fighter, "bombs", []))
    remove_count = max(0, int(count))
    if remove_count <= 0 or not bombs:
        return 0
    removed = min(remove_count, len(bombs))
    bombs = bombs[removed:]
    set_runtime_flag(fighter, "bombs", bombs)
    _sync_bomb_status_badge(fighter)
    if log_entries is not None:
        suffix = f" ({reason})." if reason else "."
        if removed == 1:
            log_entries.append(f"**{fighter.name}** is no longer affected by **BOMB**{suffix}")
        else:
            log_entries.append(f"**{fighter.name}** is no longer affected by **BOMB** x{removed}{suffix}")
    return removed


def _sync_bomb_status_badge(fighter: Fighter) -> None:
    existing = runtime_flag(fighter, "status_badges", [])
    if isinstance(existing, (list, tuple, set)):
        badges = [str(badge).strip() for badge in existing if str(badge).strip()]
    else:
        badges = []
    badges = [badge for badge in badges if not str(badge).strip().upper().startswith("BOMB")]
    bombs = runtime_flag(fighter, "bombs", [])
    if isinstance(bombs, (list, tuple)) and bombs:
        badges.insert(0, f"BOMB x{len(bombs)}")
    set_runtime_flag(fighter, "status_badges", badges)


def handle_support_when_targeted(target: Fighter, attacker: Fighter, log_entries: List[str]) -> bool:
    runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target))
    active_cards = list(runtime.active_cards) if runtime is not None else []
    if any(card.support_card_id == "MASK_CHANGE" for card in active_cards) and fighter_is_masked_hero(target):
        if not support_runtime_flag(runtime, "mask_change_used_this_round", False):
            set_support_runtime_flag(runtime, "mask_change_used_this_round", True)
            total_pct = gain_percent_stat_bonus(target, "ATK", 25)
            log_entries.append(f"**Mask Change** lets **{target.name}** dodge the attack and gain **25% ATK** (current bonus: {total_pct:+d}% ATK).")
            return True
    return False


def resolve_special_support_round_effects(
    acting_team: Sequence[Fighter],
    defending_team: Sequence[Fighter],
    acting_runtime: Optional[SupportDeckRuntime],
    defending_runtime: Optional[SupportDeckRuntime],
    log_entries: List[str],
) -> None:
    for team, enemies, runtime in ((acting_team, defending_team, acting_runtime), (defending_team, acting_team, defending_runtime)):
        active_cards = list(runtime.active_cards) if runtime is not None else []
        if any(card.support_card_id == "MIMIGHOUL_CHARM" for card in active_cards):
            charmed_mimighouls = [fighter for fighter in team if fighter.alive and fighter_is_mimighoul(fighter) and fighter_has_status(fighter, StatusEffect.CHARM)]
            if charmed_mimighouls:
                source = charmed_mimighouls[0]
                damaged_targets = []
                for enemy in enemies:
                    if not enemy.alive:
                        continue
                    dealt = apply_damage_with_runtime(enemy, max(1, int(round(enemy.max_hp * 0.25))), source, log_entries, "Mimighoul Charm")
                    if dealt > 0:
                        damaged_targets.append(f"{enemy.name} (-{dealt} HP)")
                if damaged_targets:
                    log_entries.append(f"**Mimighoul Charm** damages all enemies at the end of the round: {', '.join(damaged_targets)}.")
        if runtime is not None:
            set_support_runtime_flag(runtime, "clock_tower_prison_used_this_round", False)
            set_support_runtime_flag(runtime, "dark_world_dealings_revive_used_this_round", False)
            set_support_runtime_flag(runtime, "mask_change_used_this_round", False)
            set_support_runtime_flag(runtime, "cyber_revsystem_used_this_round", False)
            set_support_runtime_flag(runtime, "black_sonic_used_this_round", False)
            set_support_runtime_flag(runtime, "wraitsoth_used_this_round", False)
            set_support_runtime_flag(runtime, "toon_briefcase_used", support_runtime_flag(runtime, "toon_briefcase_used", False))
            set_support_runtime_flag(runtime, "battle_round", int(support_runtime_flag(runtime, "battle_round", 1)) + 1)
            set_support_runtime_flag(runtime, "cerulean_skyfire_used_this_round", False)
            set_support_runtime_flag(runtime, "hyper_blaze_used_this_round", False)
            set_support_runtime_flag(runtime, "eternal_soul_used_this_round", False)
            set_support_runtime_flag(runtime, "clear_wing_wonder_used_this_round", False)
            set_support_runtime_flag(runtime, "fire_king_island_used_this_round", False)
            for fighter in team:
                rounds = int(runtime_flag(fighter, "hero_signal_rounds", 0))
                if rounds > 0:
                    set_runtime_flag(fighter, "hero_signal_rounds", rounds - 1)
            if support_runtime_flag(runtime, "void_purification_destroy_after_round", False):
                destroyed = []
                for fighter in team:
                    if fighter.alive and fighter_is_infernoid(fighter):
                        fighter.hp = 0
                        destroyed.append(fighter.name)
                set_support_runtime_flag(runtime, "void_purification_destroy_after_round", False)
                if destroyed:
                    log_entries.append(f"**Void Purification** destroys Infernoid monsters: {', '.join(destroyed)}.")


def place_bomb(target: Fighter, turns: Optional[int], damage_pct: float, source: Optional[Fighter], log_entries: List[str]) -> None:
    if not target.alive:
        return
    if int(runtime_flag(target, "immunity_turns", 0)) > 0 or int(runtime_flag(target, "full_debuff_immunity_turns", 0)) > 0:
        log_entries.append(f"**{target.name}** ignores **BOMB** because of **IMMUNITY**.")
        return
    immune_effects = set(runtime_flag(target, "immune_effects", set()))
    if "BOMB" in immune_effects:
        log_entries.append(f"**{target.name}** ignores **BOMB**.")
        return
    if fighter_has_status(target, StatusEffect.PETRIFY):
        log_entries.append(f"**{target.name}** ignores **BOMB** because **PETRIFY** blocks all other status effects.")
        return
    bomb_turns = max(1, int(turns)) if turns is not None else RNG.randint(1, 12)
    bomb_damage = current_attack_power(source) if source is not None else max(1, int(round(target.max_hp * max(0.01, float(damage_pct or 0.0)))))
    bombs = list(runtime_flag(target, "bombs", []))
    bombs.append({"turns": bomb_turns, "damage": bomb_damage, "source": source.name if source else "", "source_monster_id": source.monster_id if source else ""})
    set_runtime_flag(target, "bombs", bombs)
    _sync_bomb_status_badge(target)
    source_text = f" from **{source.name}**" if source is not None else ""
    log_entries.append(f"**{target.name}** is rigged with a **BOMB**{source_text} set to explode in **{bomb_turns}** turn(s).")


def add_battle_stat(fighter: Optional[Fighter], key: str, amount: int) -> None:
    if fighter is None:
        return
    runtime = fighter.runtime if isinstance(fighter.runtime, dict) else {}
    fighter.runtime = runtime
    runtime[key] = max(0, int(runtime.get(key, 0)) + max(0, int(amount)))


def build_battle_stats_payload(fighters: Sequence[Fighter]) -> Dict[str, List[Dict[str, int | str]]]:
    categories = {
        "damage_dealt": ("battle_damage_dealt", "Most Damage Dealt"),
        "healing_done": ("battle_healing_done", "Most Healing Done"),
        "damage_taken": ("battle_damage_taken", "Most Damage Taken"),
        "kills": ("battle_kills", "Most Kills"),
    }
    payload: Dict[str, List[Dict[str, int | str]]] = {}
    for category_key, (runtime_key, _label) in categories.items():
        ranked = []
        for fighter in fighters:
            runtime = fighter.runtime if isinstance(fighter.runtime, dict) else {}
            value = max(0, int(runtime.get(runtime_key, 0) or 0))
            ranked.append({
                "name": fighter.name,
                "attribute": fighter.attribute,
                "value": value,
            })
        ranked.sort(key=lambda item: (-int(item["value"]), str(item["name"]).lower(), str(item["attribute"]).lower()))
        payload[category_key] = ranked[:3]
    return payload


def heal_multiplier_for_target(target: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter]) -> float:
    if int(runtime_flag(target, "heal_block_turns", 0)) > 0 or int(runtime_flag(target, "soul_taker_block_turns", 0)) > 0:
        return 0.0
    if target.hp < max(1, int(target.max_hp * 0.5)) and any(enemy.alive and active_equip_spell_name(enemy) == "Skull Invitation" for enemy in enemies):
        return 0.0
    mult = 1.0
    if any(enemy.alive and active_equip_spell_name(enemy) == "Mask of Restrict" for enemy in enemies):
        mult *= 0.5
    mult *= float(runtime_flag(target, "heal_received_multiplier", 1.0))
    return max(0.0, mult)


def heal_fighter(target: Fighter, amount: int, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str], reason: str, source: Optional[Fighter] = None) -> int:
    if not target.alive or amount <= 0:
        return 0
    mult = heal_multiplier_for_target(target, allies, enemies)
    if mult <= 0:
        log_entries.append(f"**{target.name}** cannot be healed right now.")
        return 0
    actual = max(1, int(round(amount * mult)))
    healed = min(actual, target.max_hp - target.hp)
    if healed <= 0:
        return 0
    target.hp += healed
    add_battle_stat(source or target, "battle_healing_done", healed)
    log_entries.append(f"**{target.name}** heals **{healed} HP** ({reason}).")
    return healed


def decrement_runtime_counters(fighter: Fighter) -> None:
    for key in ("immunity_turns", "full_debuff_immunity_turns", "heal_block_turns", "damage_taken_up_turns", "protection_turns", "soul_taker_block_turns", "reflect_turns", "precog_turns", "counter_turns", "dark_world_dealings_rounds"):
        value = int(runtime_flag(fighter, key, 0))
        if value > 0:
            set_runtime_flag(fighter, key, value - 1)


def resolve_round_end_spell_effects(fighters: Sequence[Fighter], log_entries: List[str]) -> None:
    for fighter in fighters:
        if not fighter.alive:
            continue
        limiter_rounds = int(runtime_flag(fighter, "limiter_rounds", 0))
        if limiter_rounds <= 0:
            continue
        limiter_rounds -= 1
        set_runtime_flag(fighter, "limiter_rounds", limiter_rounds)
        if limiter_rounds <= 0 and not runtime_flag(fighter, "limiter_penalty_applied", False):
            set_runtime_flag(fighter, "limiter_penalty_applied", True)
            dealt = apply_damage_with_runtime(fighter, max(1, int(round(fighter.max_hp * 0.50))), fighter, log_entries, "Limiter Removal")
            if fighter.alive:
                apply_status_effect(fighter, StatusEffect.STUN, source=fighter, log_entries=log_entries)
            if dealt > 0:
                log_entries.append(f"**Limiter Removal** backfires on **{fighter.name}** for **{dealt}** damage.")


def process_bombs_for_turn_start(fighter: Fighter, log_entries: List[str]) -> None:
    bombs = []
    for bomb in list(runtime_flag(fighter, "bombs", [])):
        turns = int(bomb.get("turns", 1)) - 1
        if turns <= 0:
            damage = max(1, int(bomb.get("damage", 1)))
            shield = int(runtime_flag(fighter, "shield", 0))
            if shield > 0:
                absorbed = min(shield, damage)
                shield -= absorbed
                damage -= absorbed
                set_runtime_flag(fighter, "shield", shield)
                if absorbed > 0:
                    log_entries.append(f"**{fighter.name}**'s shield absorbs **{absorbed}** bomb damage.")
            if damage > 0:
                source_monster_id = str(bomb.get("source_monster_id", "") or "").strip()
                source_fighter = None
                if source_monster_id:
                    # Only look for the bomb's owner inside this fighter's own battle.
                    source_fighter = next((candidate for candidate in battle_fighters_for(fighter) if candidate.monster_id == source_monster_id), None)
                dealt = apply_damage_with_runtime(fighter, damage, source_fighter, log_entries, "BOMB")
                log_entries.append(f"A **BOMB** explodes on **{fighter.name}** for **{dealt}** damage.")
            if fighter.alive:
                apply_status_effect(fighter, StatusEffect.STUN, log_entries=log_entries)
        else:
            bomb["turns"] = turns
            bombs.append(bomb)
    set_runtime_flag(fighter, "bombs", bombs)
    _sync_bomb_status_badge(fighter)


def process_bombs_for_all_fighters(fighters: Sequence[Fighter], log_entries: List[str]) -> None:
    for fighter in fighters:
        if fighter.alive and runtime_flag(fighter, "bombs", []):
            process_bombs_for_turn_start(fighter, log_entries)


def buff_count(fighter: Fighter) -> int:
    count = 0
    count += 1 if int(runtime_flag(fighter, "immunity_turns", 0)) > 0 else 0
    count += 1 if int(runtime_flag(fighter, "full_debuff_immunity_turns", 0)) > 0 else 0
    count += 1 if int(runtime_flag(fighter, "protection_turns", 0)) > 0 else 0
    count += 1 if int(runtime_flag(fighter, "shield", 0)) > 0 else 0
    summon = runtime_flag(fighter, "summon_data", None)
    count += 1 if isinstance(summon, dict) and int(summon.get("hp", 0)) > 0 else 0
    count += 1 if bool(runtime_flag(fighter, "immune_effects", set())) else 0
    count += len(list(runtime_flag(fighter, "recovery_effects", [])))
    count += 1 if int(runtime_flag(fighter, "reflect_turns", 0)) > 0 else 0
    count += 1 if int(runtime_flag(fighter, "precog_turns", 0)) > 0 else 0
    count += 1 if int(runtime_flag(fighter, "counter_turns", 0)) > 0 else 0
    return count


def remove_random_buff(fighter: Fighter, log_entries: List[str], reason: str) -> bool:
    removable = []
    if int(runtime_flag(fighter, "immunity_turns", 0)) > 0:
        removable.append(("IMMUNITY", lambda: set_runtime_flag(fighter, "immunity_turns", 0)))
    if int(runtime_flag(fighter, "full_debuff_immunity_turns", 0)) > 0:
        removable.append(("FULL DEBUFF IMMUNITY", lambda: set_runtime_flag(fighter, "full_debuff_immunity_turns", 0)))
    if int(runtime_flag(fighter, "protection_turns", 0)) > 0:
        removable.append(("PROTECTION", lambda: set_runtime_flag(fighter, "protection_turns", 0)))
    if int(runtime_flag(fighter, "shield", 0)) > 0:
        removable.append(("SHIELD", lambda: set_runtime_flag(fighter, "shield", 0)))
    summon = runtime_flag(fighter, "summon_data", None)
    if isinstance(summon, dict) and int(summon.get("hp", 0)) > 0:
        removable.append(("TOKEN", lambda: set_runtime_flag(fighter, "summon_data", None)))
    if bool(runtime_flag(fighter, "immune_effects", set())):
        removable.append(("EFFECT IMMUNITY", lambda: set_runtime_flag(fighter, "immune_effects", set())))
    recovery_effects = list(runtime_flag(fighter, "recovery_effects", []))
    if recovery_effects:
        removable.append(("RECOVERY", lambda: set_runtime_flag(fighter, "recovery_effects", [])))
    if int(runtime_flag(fighter, "reflect_turns", 0)) > 0:
        removable.append(("REFLECT", lambda: set_runtime_flag(fighter, "reflect_turns", 0)))
    if int(runtime_flag(fighter, "precog_turns", 0)) > 0:
        removable.append(("PRECOG", lambda: set_runtime_flag(fighter, "precog_turns", 0)))
    if int(runtime_flag(fighter, "counter_turns", 0)) > 0:
        removable.append(("COUNTER", lambda: set_runtime_flag(fighter, "counter_turns", 0)))
    if not removable:
        return False
    buff_name, remover = RNG.choice(removable)
    remover()
    log_entries.append(f"**{reason}** removes **{buff_name}** from **{fighter.name}**.")
    return True


def remove_all_buffs(fighter: Fighter, log_entries: List[str], reason: str) -> bool:
    removed_any = False
    while remove_random_buff(fighter, log_entries, reason):
        removed_any = True
    return removed_any


def maybe_trigger_dian_keto_cleanse_heal(target: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], effect: StatusEffect, log_entries: Optional[List[str]] = None) -> None:
    if effect not in {StatusEffect.BURN, StatusEffect.POISON}:
        return
    for ally in allies:
        if ally.alive and active_equip_spell_name(ally) == "Dian Keto the Cure Master":
            if log_entries is None:
                log_entries = []
            if runtime_flag(ally, "dian_keto_round_used", False):
                continue
            set_runtime_flag(ally, "dian_keto_round_used", True)
            clear_status(target, log_entries, reason="Dian Keto the Cure Master", effect=effect)
            heal_fighter(target, max(1, int(round(target.max_hp * 0.25))), allies, enemies, log_entries, "Dian Keto the Cure Master", source=ally)
            break


def _prepare_fighter_equip_spell_base(fighter: Fighter, user_id: Optional[int]) -> None:
    existing_runtime = fighter.runtime if isinstance(fighter.runtime, dict) else {}
    fighter.runtime = dict(existing_runtime)
    preserved_spell_name = existing_runtime.get("equip_spell_name")
    fighter.runtime.pop("equip_spell_name", None)
    if user_id is None:
        if preserved_spell_name:
            fighter.runtime["equip_spell_name"] = preserved_spell_name
        return
    equipped_row = db.get_equipped_spell_for_monster(fighter.monster_id, user_id)
    if not equipped_row:
        return
    spell = get_equip_spell_def(equipped_row["equip_spell_id"])
    if spell is None:
        return
    fighter.runtime["equip_spell_name"] = spell.name


def _destroy_random_enemy_equip_spell(enemy_team: Sequence[Fighter], log_entries: List[str], source_name: str) -> bool:
    candidates = [enemy for enemy in enemy_team if enemy.alive and str(runtime_flag(enemy, "equip_spell_name", "")) and not bool(runtime_flag(enemy, "equip_spell_destroyed", False))]
    if not candidates:
        return False
    return destroy_fighter_equip_spell(RNG.choice(candidates), log_entries, source_name)


def _discard_random_special_support_from_hand(runtime: Optional[SupportDeckRuntime], owner_label: str, log_entries: List[str], source_name: str) -> bool:
    if runtime is None or not runtime.active_cards:
        return False
    candidates = [card for card in runtime.active_cards if str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL"]
    if not candidates:
        return False
    chosen = RNG.choice(candidates)
    runtime.active_cards.remove(chosen)
    runtime.rounds_remaining = 0 if not runtime.active_cards else runtime.rounds_remaining
    log_entries.append(f"**{source_name}** destroys **{chosen.name}** from {owner_label}'s hand.")
    return True


DARK_RULER_DISABLE_SUPPORT_CARD_IDS = {"TEARLAMENTS_SULLIEK", "RYZEAL_CROSS", "NECROVALLEY"}
DARK_RULER_DISABLE_EQUIP_SPELL_NAMES = {"Disarmament"}


def _support_card_disables_support_or_equip(card: SupportCardDef) -> bool:
    return str(card.support_card_id) in DARK_RULER_DISABLE_SUPPORT_CARD_IDS


def _equip_spell_disables_support_or_equip(spell_name: str) -> bool:
    return str(spell_name or "").strip() in DARK_RULER_DISABLE_EQUIP_SPELL_NAMES


def _destroy_disable_support_cards_from_runtime(runtime: Optional[SupportDeckRuntime], owner_label: str, log_entries: List[str], source_name: str) -> int:
    if runtime is None or not runtime.active_cards:
        return 0
    destroyed = [card for card in list(runtime.active_cards) if _support_card_disables_support_or_equip(card)]
    if not destroyed:
        return 0
    for card in destroyed:
        try:
            runtime.active_cards.remove(card)
        except ValueError:
            continue
    runtime.rounds_remaining = 0 if not runtime.active_cards else runtime.rounds_remaining
    names = ", ".join(f"**{card.name}**" for card in destroyed)
    log_entries.append(f"**{source_name}** destroys {names} from {owner_label}'s hand.")
    return len(destroyed)


def _apply_dark_ruler_no_more(fighter: Fighter, team: Sequence[Fighter], enemy_team: Sequence[Fighter], log_entries: List[str]) -> None:
    destroyed_count = 0

    own_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(team[0])) if team else None
    enemy_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(enemy_team[0])) if enemy_team else None
    destroyed_count += _destroy_disable_support_cards_from_runtime(own_runtime, "its controller", log_entries, "Dark Ruler No More")
    destroyed_count += _destroy_disable_support_cards_from_runtime(enemy_runtime, "the opponent", log_entries, "Dark Ruler No More")

    for target in [*team, *enemy_team]:
        spell_name = str(runtime_flag(target, "equip_spell_name", "") or "").strip()
        if not target.alive or not spell_name or bool(runtime_flag(target, "equip_spell_destroyed", False)):
            continue
        if _equip_spell_disables_support_or_equip(spell_name):
            if destroy_fighter_equip_spell(target, log_entries, "Dark Ruler No More"):
                destroyed_count += 1

    if destroyed_count > 0 and fighter.alive:
        fighter.hp = 0
        set_runtime_flag(fighter, "last_hit_attacker_id", id(fighter))
        set_runtime_flag(fighter, "last_hit_attacker_name", "Dark Ruler No More")
        set_runtime_flag(fighter, "last_hit_reason", "Dark Ruler No More")
        card_label = "card" if destroyed_count == 1 else "cards"
        log_entries.append(f"**Dark Ruler No More** destroyed **{destroyed_count}** disabling {card_label}, then destroys its equipped monster **{fighter.name}**.")


def resolve_pending_dark_ruler_no_more_effects(player_team: Sequence[Fighter], enemy_team: Sequence[Fighter], log_entries: List[str]) -> None:
    """Resolve Dark Ruler No More before disabling cards can activate.

    Support Cards are already drawn into each side's active runtime before
    battle-start equip effects resolve. Dark Ruler No More must therefore scan
    those active cards and live equip spells first, destroy any support/equip
    locks, then destroy its equipped monster before the rest of the battle-start
    queue is allowed to activate.
    """
    queue: List[Tuple[int, float, Fighter, Sequence[Fighter], Sequence[Fighter]]] = []
    for team, opposing_team in ((player_team, enemy_team), (enemy_team, player_team)):
        for fighter in team:
            if not fighter.alive:
                continue
            if runtime_flag(fighter, "equip_spell_name") != "Dark Ruler No More":
                continue
            if bool(runtime_flag(fighter, "equip_spell_destroyed", False)):
                continue
            queue.append((-int(fighter.spd), RNG.random(), fighter, team, opposing_team))
    queue.sort(key=lambda entry: (entry[0], entry[1]))
    for _, _, fighter, team, opposing_team in queue:
        set_runtime_flag(fighter, "dark_ruler_no_more_pending", False)
        if fighter.alive and not bool(runtime_flag(fighter, "equip_spell_destroyed", False)):
            _apply_dark_ruler_no_more(fighter, team, opposing_team, log_entries)


def apply_special_support_draw_effects(team: Sequence[Fighter], enemy_team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime], enemy_runtime: Optional[SupportDeckRuntime], owner_label: str, opponent_label: str, log_entries: List[str]) -> None:
    if runtime is None or not runtime.active_cards:
        return
    support_ids = {card.support_card_id for card in runtime.active_cards}
    if "MITSURUGI_GREAT_PURIFICATION" in support_ids and not support_runtime_flag(runtime, "mitsurugi_great_purification_used", False):
        victims = [fighter for fighter in team if fighter.alive and fighter_is_mitsurugi(fighter)]
        if victims:
            destroyed = RNG.choice(victims)
            destroyed.hp = 0
            log_entries.append(f"**Mitsurugi Great Purification** destroys allied **{destroyed.name}**.")
        _destroy_random_enemy_equip_spell(enemy_team, log_entries, "Mitsurugi Great Purification")
        _discard_random_special_support_from_hand(enemy_runtime, opponent_label, log_entries, "Mitsurugi Great Purification")
        set_support_runtime_flag(runtime, "mitsurugi_great_purification_used", True)
    if "DRACOTAIL_FLAME" in support_ids and not support_runtime_flag(runtime, "dracotail_flame_used", False):
        _destroy_random_enemy_equip_spell(enemy_team, log_entries, "Dracotail Flame")
        candidates = [fighter for fighter in team if fighter.hp <= 0 and fighter_is_dracotail(fighter)]
        if candidates:
            chosen = RNG.choice(candidates)
            chosen.hp = chosen.max_hp
            log_entries.append(f"**Dracotail Flame** revives **{chosen.name}** at full HP.")
        else:
            living = [fighter for fighter in team if fighter.alive and fighter_is_dracotail(fighter)]
            if living:
                _grant_token(RNG.choice(living), log_entries)
        set_support_runtime_flag(runtime, "dracotail_flame_used", True)
    if "VOID_PURIFICATION" in support_ids and not support_runtime_flag(runtime, "void_purification_used", False):
        revived = []
        for fighter in team:
            if fighter.hp <= 0 and fighter_is_infernoid(fighter):
                fighter.hp = fighter.max_hp
                revived.append(fighter.name)
        if revived:
            log_entries.append(f"**Void Purification** revives Infernoid monsters at full HP: {', '.join(revived)}.")
        set_support_runtime_flag(runtime, "void_purification_used", True)


def apply_battle_start_equip_spell_effect(fighter: Fighter, team: Sequence[Fighter], enemy_team: Sequence[Fighter], log_entries: List[str]) -> None:
    spell_name = runtime_flag(fighter, "equip_spell_name")
    if not spell_name:
        return
    if bool(runtime_flag(fighter, "equip_spell_destroyed", False)):
        return
    if spell_name == "Dark Ruler No More":
        # Dark Ruler No More is resolved in the pre-start cleanup pass so it can
        # destroy support/equip locks before those locks activate.
        return
    if active_equip_spell_name(fighter) != spell_name:
        return
    if spell_name == "Imperial Iron Wall":
        set_runtime_flag(fighter, "immunity_turns", 999)
    elif spell_name == "Trap Jammer":
        for ally in team:
            set_runtime_flag(ally, "immunity_turns", max(int(runtime_flag(ally, "immunity_turns", 0)), 999))
    elif spell_name == "Battle Mania":
        for enemy in enemy_team:
            apply_status_effect(enemy, StatusEffect.TAUNT, source=fighter, log_entries=log_entries)
    elif spell_name == "Venom Shot":
        for enemy in enemy_team:
            apply_status_effect(enemy, StatusEffect.POISON, source=fighter, log_entries=log_entries)
    elif spell_name == "Tremendous Fire":
        for enemy in enemy_team:
            apply_status_effect(enemy, StatusEffect.BURN, source=fighter, log_entries=log_entries)
    elif spell_name == "Bad Reaction to Simochi":
        for enemy in enemy_team:
            apply_heal_block(enemy, source=fighter, log_entries=log_entries)
    elif spell_name == "Time Bomb":
        for enemy in enemy_team:
            place_bomb(enemy, None, 0.20, fighter, log_entries)
    elif spell_name == "Mirror Force":
        set_runtime_flag(fighter, "reflect_turns", 999)
    elif spell_name == "Lightforce Sword":
        set_runtime_flag(fighter, "precog_turns", 999)
    elif spell_name == "Sakuretsu Armor":
        set_runtime_flag(fighter, "counter_turns", 999)
    elif spell_name == "Swords of Revealing Light":
        for enemy in enemy_team:
            if enemy.alive and RNG.random() < 0.35:
                apply_status_effect(enemy, StatusEffect.STUN, source=fighter, log_entries=log_entries)
    elif spell_name == "Dark Sanctuary":
        for enemy in enemy_team:
            apply_status_effect(enemy, StatusEffect.SLEEP, source=fighter, log_entries=log_entries)
    elif spell_name == "Book of Eclipse":
        for enemy in enemy_team:
            set_runtime_flag(enemy, "book_of_eclipse_pending_immunity", True)
            set_runtime_flag(enemy, "book_of_eclipse_sleep_turns", 2)
            apply_status_effect(enemy, StatusEffect.SLEEP, source=fighter, log_entries=log_entries)
    elif spell_name == "Nightmare Wheel":
        for enemy in enemy_team:
            apply_status_effect(enemy, StatusEffect.FEAR, source=fighter, log_entries=log_entries)
    elif spell_name == "My Body as a Shield":
        set_runtime_flag(fighter, "shield", int(round(fighter.max_hp * 0.25)))
    elif spell_name == "Scrap-Iron Scarecrow":
        shield_amount = int(round(fighter.max_hp * 0.25))
        for ally in team:
            set_runtime_flag(ally, "shield", int(runtime_flag(ally, "shield", 0)) + shield_amount)
    elif spell_name == "Forbidden Lance":
        set_runtime_flag(fighter, "immune_effects", {StatusEffect.STUN.value, StatusEffect.FREEZE.value, StatusEffect.SLEEP.value})
    elif spell_name == "Forbidden Dress":
        set_runtime_flag(fighter, "full_debuff_immunity_turns", 999)
    elif spell_name == "Gift of the Mystical Elf":
        for ally in team:
            effects = list(runtime_flag(ally, "recovery_effects", []))
            effects.append({"turns": 999, "pct": 0.05})
            set_runtime_flag(ally, "recovery_effects", effects)
    elif spell_name == "Mystik Wok":
        sacrifice = max(1, int(round(fighter.max_hp * 0.25)))
        fighter.hp = max(1, fighter.hp - sacrifice)
        set_runtime_flag(fighter, "protection_turns", max(1, int(runtime_flag(fighter, "protection_turns", 0))))
        set_runtime_flag(fighter, "mystik_wok_rounds", 1)
        log_entries.append(f"**Mystik Wok** sacrifices **{sacrifice} HP** from **{fighter.name}** and grants **PROTECTION** plus **+50% damage** for **1 round**.")
    elif spell_name == "Noble Arms - Caliburn":
        effects = list(runtime_flag(fighter, "recovery_effects", []))
        effects.append({"turns": 999, "pct": 0.05})
        set_runtime_flag(fighter, "recovery_effects", effects)
    elif spell_name == "Heavy Storm":
        for enemy in enemy_team:
            remove_all_buffs(enemy, log_entries, "Heavy Storm")
    elif spell_name == "Shrink":
        target = None
        best_atk = -1
        for enemy in enemy_team:
            if not enemy.alive:
                continue
            atk_value = current_attack_power(enemy)
            if atk_value > best_atk:
                best_atk = atk_value
                target = enemy
        if target is not None:
            set_runtime_flag(target, "damage_taken_up_turns", max(int(runtime_flag(target, "damage_taken_up_turns", 0)), 999))
            log_entries.append(f"**Shrink** inflicts **MARK** on **{target.name}**.")
    elif spell_name == "Book of Secret Arts":
        if any(monster_type in {"Spellcaster", "Black Magic", "White Magic", "Illusion Magic", "Dragon Magic"} for monster_type in fighter.monster_types):
            set_runtime_flag(fighter, "immunity_turns", max(int(runtime_flag(fighter, "immunity_turns", 0)), 999))
            set_runtime_flag(fighter, "book_of_secret_arts_active", True)
            log_entries.append(f"**Book of Secret Arts** empowers **{fighter.name}** with **IMMUNITY** and **SILENCE** magic.")
    elif spell_name == "Limiter Removal":
        set_runtime_flag(fighter, "limiter_rounds", 1)
    elif spell_name == "Bottomless Trap Hole":
        candidates = trap_hole_target_candidates(enemy_team, lambda enemy: current_attack_power(enemy) > 150)
        target = RNG.choice(candidates) if candidates else None
        if target is not None:
            lost = max(1, int(round(target.hp * 0.50)))
            target.hp = max(1, target.hp - lost)
            add_battle_stat(target, "battle_damage_taken", lost)
            log_entries.append(f"**Bottomless Trap Hole** makes **{target.name}** lose **{lost} HP**.")
    elif spell_name == "Trap Hole":
        candidates = trap_hole_target_candidates(enemy_team, lambda enemy: current_attack_power(enemy) < 125)
        target = RNG.choice(candidates) if candidates else None
        if target is not None:
            lost = max(1, int(round(target.hp * 0.25)))
            target.hp = max(1, target.hp - lost)
            add_battle_stat(target, "battle_damage_taken", lost)
            log_entries.append(f"**Trap Hole** makes **{target.name}** lose **{lost} HP**.")
    elif spell_name == "Floodgate Trap Hole":
        candidates = trap_hole_target_candidates(enemy_team, lambda enemy: current_attack_power(enemy) > 175)
        target = RNG.choice(candidates) if candidates else None
        if target is not None:
            set_runtime_flag(target, "cannot_act_turns", max(1, int(runtime_flag(target, "cannot_act_turns", 0))))
            log_entries.append(f"**Floodgate Trap Hole** prevents **{target.name}** from acting for 1 round.")
    elif spell_name == "Ground Collapse":
        ally = RNG.choice([unit for unit in team if unit.alive]) if any(unit.alive for unit in team) else None
        enemy = RNG.choice([unit for unit in enemy_team if unit.alive]) if any(unit.alive for unit in enemy_team) else None
        for target in (ally, enemy):
            if target is not None:
                set_runtime_flag(target, "cannot_act_turns", max(1, int(runtime_flag(target, "cannot_act_turns", 0))))
                log_entries.append(f"**Ground Collapse** prevents **{target.name}** from acting for 1 round.")
    elif spell_name == "Vengeful Bog Spirit":
        for target in [*team, *enemy_team]:
            if target.alive:
                set_runtime_flag(target, "cannot_act_turns", max(1, int(runtime_flag(target, "cannot_act_turns", 0))))
        log_entries.append("**Vengeful Bog Spirit** prevents all monsters from acting for 1 round.")
    elif spell_name == "Hippo Carnival":
        allies_alive = [ally for ally in team if ally.alive]
        if allies_alive:
            target = min(allies_alive, key=lambda unit: (unit.hp / max(1, unit.max_hp), unit.hp, RNG.random()))
            shield_amount = max(1, int(round(fighter.max_hp * 0.25)))
            set_runtime_flag(target, "shield", int(runtime_flag(target, "shield", 0)) + shield_amount)
            log_entries.append(f"**Hippo Carnival** grants **{shield_amount} SHIELD** to **{target.name}**.")
    elif spell_name == "Delinquent Duo":
        set_runtime_flag(fighter, "delinquent_duo_pending", int(runtime_flag(fighter, "delinquent_duo_pending", 0)) + 1)
        set_runtime_flag(fighter, "pending_battle_start_heart_loss", int(runtime_flag(fighter, "pending_battle_start_heart_loss", 0)) + 2)
    elif spell_name == "Raigeki":
        if RNG.random() < 0.10:
            target = RNG.choice([enemy for enemy in enemy_team if enemy.alive]) if any(enemy.alive for enemy in enemy_team) else None
            if target is not None:
                target.hp = 0
                set_runtime_flag(target, "last_hit_attacker_id", id(fighter))
                set_runtime_flag(target, "last_hit_attacker_name", fighter.name)
                set_runtime_flag(target, "last_hit_reason", "Raigeki")
                log_entries.append(f"**Raigeki** destroys **{target.name}**.")


def initialize_battle_start_effects(
    player_team: Sequence[Fighter],
    player_user_id: Optional[int],
    enemy_team: Sequence[Fighter],
    enemy_user_id: Optional[int],
    log_entries: List[str],
) -> None:
    register_battle_teams(player_team, enemy_team)
    for fighter in player_team:
        prepare_fighter_equip_spell(fighter, player_user_id)
    for fighter in enemy_team:
        prepare_fighter_equip_spell(fighter, enemy_user_id)

    battle_start_queue: List[Tuple[int, float, Fighter, Sequence[Fighter], Sequence[Fighter]]] = []
    for team, opposing_team in ((player_team, enemy_team), (enemy_team, player_team)):
        for fighter in team:
            spell_name = runtime_flag(fighter, "equip_spell_name")
            if not spell_name:
                continue
            battle_start_queue.append((-int(fighter.spd), RNG.random(), fighter, team, opposing_team))

    player_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(player_team[0])) if player_team else None
    enemy_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(enemy_team[0])) if enemy_team else None

    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
    resolve_pending_dark_ruler_no_more_effects(player_team, enemy_team, log_entries)
    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)

    battle_start_queue.sort(key=lambda entry: (entry[0], entry[1]))
    for _, _, fighter, team, opposing_team in battle_start_queue:
        apply_battle_start_equip_spell_effect(fighter, team, opposing_team, log_entries)

    if any(f.alive and runtime_flag(f, "equip_spell_name") == "Top Share" for f in [*player_team, *enemy_team]):
        _apply_top_share(player_runtime, "the player", log_entries)
        _apply_top_share(enemy_runtime, "the opponent", log_entries)
        _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)

    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)

    apply_special_support_draw_effects(player_team, enemy_team, player_runtime, enemy_runtime, "the player", "the opponent", log_entries)
    apply_special_support_draw_effects(enemy_team, player_team, enemy_runtime, player_runtime, "the opponent", "the player", log_entries)
    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)

    initialize_special_support_effects(player_team, enemy_team, player_runtime, log_entries)
    initialize_special_support_effects(enemy_team, player_team, enemy_runtime, log_entries)
    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)


def apply_pending_battle_start_heart_losses(
    team: Sequence[Fighter],
    opposing_team: Sequence[Fighter],
    hearts_remaining: int,
    opposing_hearts: int,
    owner_name: str,
    opposing_name: str,
    log_entries: List[str],
    *,
    max_hearts: int,
) -> Tuple[int, int]:
    pending_fighters: List[Tuple[Fighter, int, int]] = []
    total_hearts_to_lose = 0
    for fighter in team:
        pending_hearts = int(runtime_flag(fighter, "pending_battle_start_heart_loss", 0))
        pending_duos = int(runtime_flag(fighter, "delinquent_duo_pending", 0) or 0)
        if pending_hearts > 0 or pending_duos > 0:
            pending_fighters.append((fighter, pending_hearts, pending_duos))
            total_hearts_to_lose += max(0, pending_hearts)
            set_runtime_flag(fighter, "pending_battle_start_heart_loss", 0)
            set_runtime_flag(fighter, "delinquent_duo_pending", 0)

    if total_hearts_to_lose <= 0:
        return hearts_remaining, opposing_hearts

    if _heart_loss_prevented(team):
        duo_count = sum(max(0, duo_count) for _, _, duo_count in pending_fighters)
        if duo_count > 0:
            log_entries.append(f"**Delinquent Duo** cannot resolve for {owner_name} because heart loss is prevented.")
        else:
            log_entries.append(f"Battle-start heart loss is prevented for {owner_name}.")
        return hearts_remaining, opposing_hearts

    updated = max(0, int(hearts_remaining) - total_hearts_to_lose)
    heart_label = "heart" if total_hearts_to_lose == 1 else "hearts"
    log_entries.append(f"**Delinquent Duo** makes {owner_name} lose **{total_hearts_to_lose} {heart_label}** ({updated}/{max_hearts} left).")

    opposing_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(opposing_team[0])) if opposing_team else None
    duo_resolutions = sum(max(0, duo_count) for _, _, duo_count in pending_fighters)
    for _ in range(duo_resolutions):
        _discard_random_special_from_hand(opposing_runtime, opposing_name, log_entries)

    trigger_mound_ra_on_heart_loss(team, owner_name, log_entries)
    trigger_mound_ra_on_heart_loss(opposing_team, owner_name, log_entries)
    opposing_hearts = trigger_attack_and_receive(team, opposing_team, total_hearts_to_lose, opposing_hearts, opposing_name, log_entries, max_hearts=max_hearts)
    return updated, opposing_hearts


def apply_all_pending_battle_start_heart_losses(
    player_team: Sequence[Fighter],
    enemy_team: Sequence[Fighter],
    player_hearts: int,
    enemy_hearts: int,
    player_name: str,
    enemy_name: str,
    log_entries: List[str],
    *,
    max_hearts: int,
) -> Tuple[int, int]:
    player_hearts, enemy_hearts = apply_pending_battle_start_heart_losses(
        player_team,
        enemy_team,
        player_hearts,
        enemy_hearts,
        player_name,
        enemy_name,
        log_entries,
        max_hearts=max_hearts,
    )
    enemy_hearts, player_hearts = apply_pending_battle_start_heart_losses(
        enemy_team,
        player_team,
        enemy_hearts,
        player_hearts,
        enemy_name,
        player_name,
        log_entries,
        max_hearts=max_hearts,
    )
    return player_hearts, enemy_hearts


def resolve_runtime_teams_for_fighter(target: Fighter, attacker: Optional[Fighter] = None) -> Tuple[List[Fighter], List[Fighter]]:
    target_allies = list(ACTIVE_TEAMMATES_BY_FIGHTER.get(id(target), []))
    if attacker is not None and attacker is not target:
        attacker_team = list(ACTIVE_TEAMMATES_BY_FIGHTER.get(id(attacker), []))
        if attacker_team and not _fighter_in_team_by_identity(attacker, target_allies):
            return target_allies, attacker_team
    for team in list(ACTIVE_TEAMMATES_BY_FIGHTER.values()):
        team_list = list(team)
        if _fighter_in_team_by_identity(target, team_list):
            target_allies = team_list
            break
    enemy_team = _choose_enemy_team_by_identity(target, target_allies)
    return target_allies, enemy_team


def trigger_solemn_wishes_on_damage(target: Fighter, damage_taken: int, attacker: Optional[Fighter], log_entries: List[str]) -> int:
    if damage_taken <= 0 or active_equip_spell_name(target) != "Solemn Wishes":
        return 0
    target_allies, target_enemies = resolve_runtime_teams_for_fighter(target, attacker)
    heal_amount = max(1, int(round(damage_taken * 0.50)))
    return heal_fighter(target, heal_amount, target_allies, target_enemies, log_entries, "Solemn Wishes", source=target)


def destroy_fighter_equip_spell(target: Fighter, log_entries: List[str], source_name: str) -> bool:
    spell_name = str(runtime_flag(target, "equip_spell_name", "") or "")
    if not spell_name or bool(runtime_flag(target, "equip_spell_destroyed", False)):
        return False
    set_runtime_flag(target, "equip_spell_destroyed", True)
    log_entries.append(f"**{source_name}** destroys **{target.name}**'s equip spell (**{spell_name}**) for the rest of the battle.")
    return True


def clear_runtime_debuffs(fighter: Fighter) -> None:
    for key in ("damage_taken_up_turns", "curse_of_darkness_dealt_down", "lightforce_sword_damage_down", "mark_turns", "heal_block_turns"):
        if runtime_flag(fighter, key, 0):
            set_runtime_flag(fighter, key, 0)


def initialize_special_support_effects(team: Sequence[Fighter], enemy_team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime], log_entries: List[str]) -> None:
    if runtime is None or not runtime.active_cards:
        return
    active_cards = list(ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(team[0]), [])) if team else []
    if not active_cards:
        return
    support_ids = {card.support_card_id for card in active_cards}
    if "ANCIENT_GEAR_FORTRESS" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and fighter_is_ancient_gear(fighter):
                set_runtime_flag(fighter, "protection_turns", max(int(runtime_flag(fighter, "protection_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Ancient Gear Fortress** grants **PROTECTION** to: {', '.join(granted)}.")
    if "ETERNAL_SOUL" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and fighter_is_dark_magician(fighter):
                set_runtime_flag(fighter, "immunity_turns", max(int(runtime_flag(fighter, "immunity_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Eternal Soul** grants **IMMUNITY** to: {', '.join(granted)}.")
    if "SPEEDROID_CLEAR_WING_WONDER" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and (fighter_is_speedroid(fighter) or fighter_is_clear_wing(fighter)):
                set_runtime_flag(fighter, "precog_turns", max(int(runtime_flag(fighter, "precog_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Speedroid Clear Wing Wonder** grants **PRECOG** to: {', '.join(granted)}.")
    if "RELINQUISHED_FUSION" in support_ids and not support_runtime_flag(runtime, "relinquished_fusion_used", False):
        relics = [fighter for fighter in team if fighter.alive and fighter_is_relinquished(fighter)]
        enemies = [enemy for enemy in enemy_team if enemy.alive]
        if relics and enemies:
            for enemy in enemies:
                stolen = max(1, int(round(enemy.atk * 0.15)))
                enemy.atk = max(1, enemy.atk - stolen)
                recipient = RNG.choice(relics)
                recipient.atk += stolen
                log_entries.append(f"**Relinquished Fusion** transfers **{stolen} ATK** from **{enemy.name}** to **{recipient.name}**.")
            set_support_runtime_flag(runtime, "relinquished_fusion_used", True)
    if "ORCUSTRATED_BABEL" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and fighter_is_orcust(fighter):
                set_runtime_flag(fighter, "precog_turns", max(int(runtime_flag(fighter, "precog_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Orcustrated Babel** grants **PRECOG** to: {', '.join(granted)}.")
    if "DRAGONIC_DIAGRAM" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and fighter_is_true_king_or_true_draco(fighter):
                set_runtime_flag(fighter, "immunity_turns", max(int(runtime_flag(fighter, "immunity_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Dragonic Diagram** grants **IMMUNITY** to: {', '.join(granted)}.")
    if "LIGHT_WING_SHIELD" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and fighter_is_utopia_or_utopic(fighter):
                set_runtime_flag(fighter, "reflect_turns", max(int(runtime_flag(fighter, "reflect_turns", 0)), 999))
                set_runtime_flag(fighter, "counter_turns", max(int(runtime_flag(fighter, "counter_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Light Wing Shield** grants **COUNTER** and **REFLECT** to: {', '.join(granted)}.")
    if "FALLEN_PARADISE" in support_ids:
        granted = []
        for fighter in team:
            if fighter.alive and fighter_is_raviel(fighter):
                set_runtime_flag(fighter, "immunity_turns", max(int(runtime_flag(fighter, "immunity_turns", 0)), 999))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Fallen Paradise** grants **IMMUNITY** to: {', '.join(granted)}.")
    if "RYZEAL_PLUGIN" in support_ids and not support_runtime_flag(runtime, "ryzeal_plugin_used", False):
        candidates = [fighter for fighter in team if fighter.hp <= 0 and fighter_is_ryzeal(fighter)]
        if candidates:
            chosen = RNG.choice(candidates)
            chosen.hp = chosen.max_hp
            set_support_runtime_flag(runtime, "ryzeal_plugin_used", True)
            log_entries.append(f"**Ryzeal Plugin** revives **{chosen.name}** at full HP.")
    if "VANQUISH_SOUL_CALAMITY_CAESAR" in support_ids and not support_runtime_flag(runtime, "vanquish_soul_calamity_caesar_used", False):
        cleaned=[]
        for fighter in team:
            if fighter.alive and fighter_is_vanquish_soul(fighter) and (fighter.status is not None or fighter_has_debuff(fighter)):
                removed_statuses = len(fighter_statuses(fighter))
                had_equip_spell = bool(str(runtime_flag(fighter, "equip_spell_name", ""))) and not bool(runtime_flag(fighter, "equip_spell_destroyed", False))
                fighter.status = None
                fighter.runtime["status_effects"] = []
                clear_runtime_debuffs(fighter)
                remove_bomb_debuff(fighter)
                cleaned.append((fighter, removed_statuses, had_equip_spell))
        if cleaned:
            set_support_runtime_flag(runtime, "vanquish_soul_calamity_caesar_used", True)
            for fighter, removed_statuses, had_equip_spell in cleaned:
                if had_equip_spell:
                    set_runtime_flag(fighter, "equip_spell_destroyed", True)
                    log_entries.append(f"**Vanquish Soul Calamity Caesar** removes debuffs from **{fighter.name}** and destroys its equip spell.")
                else:
                    log_entries.append(f"**Vanquish Soul Calamity Caesar** removes debuffs from **{fighter.name}**.")
    if "TALES_OF_THE_WHITE_FOREST" in support_ids and not support_runtime_flag(runtime, "tales_white_forest_used", False):
        candidates = [fighter for fighter in team if fighter.alive and fighter_is_white_forest(fighter)]
        if candidates:
            _grant_token(RNG.choice(candidates), log_entries)
            set_support_runtime_flag(runtime, "tales_white_forest_used", True)
    if "TOON_BRIEFCASE" in support_ids and not support_runtime_flag(runtime, "toon_briefcase_used", False):
        round_number = int(support_runtime_flag(runtime, "battle_round", 0))
        duration = 1 if round_number == 3 else 2 if round_number == 5 else 0
        if duration > 0 and any(f.alive and fighter_is_toon(f) for f in team):
            victims = [enemy for enemy in enemy_team if enemy.alive]
            RNG.shuffle(victims)
            victims = victims[:2]
            for victim in victims:
                apply_status_effect(victim, StatusEffect.STUN, source=victim, log_entries=log_entries)
                set_runtime_flag(victim, "toon_briefcase_turns", max(int(runtime_flag(victim, "toon_briefcase_turns", 0)), duration))
            if victims:
                set_support_runtime_flag(runtime, "toon_briefcase_used", True)
                log_entries.append(f"**Toon Briefcase** disables **{', '.join(v.name for v in victims)}** for **{duration} turn{'s' if duration != 1 else ''}**.")
    fusion_support_effects = [
        ("BRANDED_FUSION", "Branded Fusion", fighter_is_albaz, 0.50, 0.0),
        ("RED_EYES_FUSION", "Red-Eyes Fusion", fighter_is_red_eyes, 0.50, 0.0),
        ("GEM_KNIGHT_FUSION", "Gem-Knight Fusion", fighter_is_gem_knight, 0.50, 0.0),
        ("FRIGHTFUR_FUSION", "Frightfur Fusion", fighter_is_frightfur_or_fluffal, 0.50, 0.0),
        ("NEOS_FUSION", "Neos Fusion", fighter_is_neos, 0.50, 0.0),
        ("POWER_BOND", "Power Bond", fighter_is_cyber_dragon, 1.00, 0.0),
        ("MYUTANT_FUSION", "Myutant Fusion", fighter_is_myutant, 0.50, 0.0),
        ("SHADDOLL_FUSION", "Shaddoll Fusion", fighter_is_shaddoll, 0.50, 0.25),
        ("FUSION_DESTINY", "Fusion Destiny", fighter_is_destiny_hero, 0.50, 0.0),
        ("VISION_FUSION", "Vision Fusion", fighter_is_vision_hero, 0.50, 0.0),
        ("FOSSIL_FUSION", "Fossil Fusion", fighter_is_fossil, 0.50, 0.25),
        ("ANCIENT_GEAR_FUSION", "Ancient Gear Fusion", fighter_is_ancient_gear, 0.50, 0.0),
        ("METALFOES_FUSION", "Metalfoes Fusion", fighter_is_metalfoes, 0.50, 0.0),
        ("INVOCATION", "Invocation", fighter_is_invoked, 0.50, 0.25),
        ("LUNALIGHT_FUSION", "Lunalight Fusion", fighter_is_lunalight, 0.50, 0.0),
        ("TRICKSTAR_FUSION", "Trickstar Fusion", fighter_is_trickstar, 0.50, 0.0),
        ("THUNDER_DRAGON_FUSION", "Thunder Dragon Fusion", fighter_is_thunder_dragon, 0.50, 0.0),
        ("PRIMITE_FUSION", "Primite Fusion", fighter_is_primite, 0.50, 0.0),
        ("PREDAPRIME_FUSION", "Predaprime Fusion", fighter_is_predaplant, 0.50, 0.0),
        ("TYRANT_DINO_FUSION", "Tyrant Dino Fusion", fighter_is_dinowrestler, 0.50, 0.0),
    ]
    for support_card_id, card_name, matcher, ally_pct, enemy_pct in fusion_support_effects:
        apply_special_support_fusion_bonus(team, enemy_team, runtime, support_card_id, card_name, matcher, ally_pct, enemy_pct, log_entries)
    if "MIMIGHOUL_CHARM" in support_ids and not support_runtime_flag(runtime, "mimighoul_charm_used", False):
        candidates = [fighter for fighter in team if fighter.alive and fighter_is_mimighoul(fighter)]
        if candidates:
            chosen = RNG.choice(candidates)
            apply_status_effect(chosen, StatusEffect.CHARM, source=chosen, log_entries=log_entries)
            set_support_runtime_flag(runtime, "mimighoul_charm_used", True)
            log_entries.append(f"**Mimighoul Charm** charms **{chosen.name}**.")
    if "CLOWN_CREW_REHEARSAL" in support_ids and not support_runtime_flag(runtime, "clown_crew_rehearsal_used", False):
        allies = [fighter for fighter in team if fighter.alive and fighter_is_clown_clan(fighter)]
        foes = [fighter for fighter in enemy_team if fighter.alive]
        if allies and foes:
            ally = RNG.choice(allies)
            foe = RNG.choice(foes)
            ally.atk = foe.atk
            ally.defense = foe.defense
            ally.spd = foe.spd
            set_support_runtime_flag(runtime, "clown_crew_rehearsal_used", True)
            log_entries.append(f"**Clown Clan Rehersal** makes **{ally.name}** copy **{foe.name}**'s ATK, DEF, and SPD.")


def apply_low_hp_equip_triggers(target: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    if not target.alive or target.hp <= 0:
        return
    active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
    if (
        target.hp <= int(target.max_hp * 0.50)
        and fighter_is_cyber_dragon(target)
        and any(card.support_card_id == "CYBER_EMERGENCY" for card in active_cards)
        and not runtime_flag(target, "cyber_emergency_used", False)
    ):
        set_runtime_flag(target, "cyber_emergency_used", True)
        _grant_token(target, log_entries, "Cyber Emergency")
    if target.hp > int(target.max_hp * 0.25):
        return
    for ally in allies:
        if ally.alive and active_equip_spell_name(ally) == "Rain of Mercy" and not runtime_flag(ally, "rain_of_mercy_used", False):
            set_runtime_flag(ally, "rain_of_mercy_used", True)
            effects = list(runtime_flag(target, "recovery_effects", []))
            effects.append({"turns": 999, "pct": 0.05})
            set_runtime_flag(target, "recovery_effects", effects)
            log_entries.append(f"**Rain of Mercy** grants **RECOVERY** to **{target.name}**.")
    if active_equip_spell_name(target) == "Blast Held by a Tribute" and not runtime_flag(target, "blast_held_used", False):
        set_runtime_flag(target, "blast_held_used", True)
        for enemy in enemies:
            if enemy.alive:
                place_bomb(enemy, None, 0.30, target, log_entries)
        set_runtime_flag(target, "protection_turns", max(2, int(runtime_flag(target, "protection_turns", 0))))
        log_entries.append(f"**Blast Held by a Tribute** places **BOMB** on all enemies and grants **PROTECTION** to **{target.name}**.")


def apply_damage_with_runtime(target: Fighter, damage: int, attacker: Optional[Fighter], log_entries: List[str], reason: str = "") -> int:
    remaining = max(0, int(damage))
    target_allies, target_enemies = resolve_runtime_teams_for_fighter(target, attacker)
    summon = runtime_flag(target, "summon_data", None)
    if isinstance(summon, dict) and int(summon.get("hp", 0)) > 0 and remaining > 0 and reason in {"attack", "PRECOG", "counterattack", "TOKEN"}:
        absorbed = min(int(summon.get("hp", 0)), remaining)
        remaining -= absorbed
        summon["hp"] = max(0, int(summon.get("hp", 0)) - absorbed)
        set_runtime_flag(target, "summon_data", summon if summon["hp"] > 0 else None)
        log_entries.append(f"**TOKEN** takes **{absorbed}** damage for **{target.name}**.")
        if summon["hp"] <= 0:
            log_entries.append(f"**{target.name}**'s **TOKEN** is defeated.")
        if remaining <= 0:
            return 0
    if any(ally.alive and active_equip_spell_name(ally) == "Waboku" for ally in target_allies):
        remaining = max(1, int(round(remaining * 0.75)))
    if active_equip_spell_name(target) == "Messenger of Peace" and remaining > 0:
        remaining = max(0, int(round(remaining * 0.25)))
    if int(runtime_flag(target, "protection_turns", 0)) > 0 and remaining > 0:
        log_entries.append(f"**PROTECTION** nullifies all damage to **{target.name}**.")
        return 0
    shield = int(runtime_flag(target, "shield", 0))
    if shield > 0 and remaining > 0:
        absorbed = min(shield, remaining)
        remaining -= absorbed
        set_runtime_flag(target, "shield", shield - absorbed)
        log_entries.append(f"**{target.name}**'s shield absorbs **{absorbed}** damage.")
    if remaining <= 0:
        return 0
    if special_support_active_for_fighter(target, "CLOCK_TOWER_PRISON", fighter_is_destiny_hero) and fighter_is_destiny_hero(target):
        log_entries.append(f"**Clock Tower Prison** grants **PROTECTION** to **{target.name}**.")
        return 0
    if special_support_active_for_fighter(target, "CRYSTRON_INCLUSION", fighter_is_crystron) and fighter_is_crystron(target):
        log_entries.append(f"**Crystron Inclusion** grants **PROTECTION** to **{target.name}**.")
        return 0
    if special_support_active_for_fighter(target, "DRAGONIC_DIAGRAM", fighter_is_true_king_or_true_draco) and fighter_is_true_king_or_true_draco(target):
        log_entries.append(f"**Dragonic Diagram** grants **PROTECTION** to **{target.name}**.")
        return 0
    if remaining >= target.hp and reason == "attack" and active_equip_spell_name(target) == "Magic Cylinder" and not runtime_flag(target, "magic_cylinder_used", False):
        enemy_team = enemy_team_for_fighter(target)
        redirect_target = random_alive(list(enemy_team))
        if redirect_target is not None:
            set_runtime_flag(target, "magic_cylinder_used", True)
            redirected = max(1, int(round(remaining * 1.5)))
            dealt_redirect = apply_damage_with_runtime(redirect_target, redirected, attacker, log_entries, "Magic Cylinder")
            log_entries.append(f"**Magic Cylinder** redirects the attack to **{redirect_target.name}** for **{dealt_redirect}** damage.")
            return 0
    if remaining >= target.hp and active_equip_spell_name(target) == "Dimension Magic" and not runtime_flag(target, "dimension_magic_used", False):
        allies = [ally for ally in ACTIVE_TEAMMATES_BY_FIGHTER.get(id(target), []) if ally.alive and ally is not target]
        if allies:
            redirect_ally = RNG.choice(allies)
            set_runtime_flag(target, "dimension_magic_used", True)
            transferred = apply_damage_with_runtime(redirect_ally, remaining, attacker, log_entries, "Dimension Magic")
            set_runtime_flag(target, "precog_turns", max(int(runtime_flag(target, "precog_turns", 0)), 999))
            log_entries.append(f"**Dimension Magic** transfers **{transferred}** damage from **{target.name}** to **{redirect_ally.name}** and grants **PRECOG**.")
            return 0
    if remaining >= target.hp and active_equip_spell_name(target) == "Rainbow Life" and not runtime_flag(target, "rainbow_life_used", False):
        set_runtime_flag(target, "rainbow_life_used", True)
        set_runtime_flag(target, "protection_turns", max(2, int(runtime_flag(target, "protection_turns", 0))))
        target.hp = 1
        log_entries.append(f"**Rainbow Life** lets **{target.name}** survive at **1 HP** and grants **PROTECTION** for **2 rounds**.")
        return max(0, damage - 1)
    target.hp = max(0, target.hp - remaining)
    add_battle_stat(target, "battle_damage_taken", remaining)
    if attacker is not None:
        add_battle_stat(attacker, "battle_damage_dealt", remaining)
    if attacker is not None and target.hp <= 0:
        set_runtime_flag(target, "last_hit_attacker_id", id(attacker))
        set_runtime_flag(target, "last_hit_attacker_name", attacker.name)
        set_runtime_flag(target, "last_hit_reason", reason)
    apply_low_hp_equip_triggers(target, target_allies, target_enemies, log_entries)
    trigger_solemn_wishes_on_damage(target, remaining, attacker, log_entries)
    return remaining


def trigger_precog_strike(target: Fighter, attacker: Fighter, target_allies: Sequence[Fighter], attacker_allies: Sequence[Fighter], log_entries: List[str]) -> bool:
    spell_name = active_equip_spell_name(target)
    if int(runtime_flag(target, "precog_turns", 0)) <= 0:
        return False
    if not target.alive or not attacker.alive:
        return False
    attack_result = resolve_attack_damage(target, attacker)
    if not attack_result["hit"]:
        log_entries.append(f"**PRECOG** lets **{target.name}** strike first, but **{attacker.name}** dodges the attack.")
        return True
    dealt = apply_damage_with_runtime(attacker, int(attack_result["damage"]), target, log_entries, "PRECOG")
    if spell_name == "Nobleman of Crossout" and dealt > 0:
        remove_random_buff(attacker, log_entries, "Nobleman of Crossout")
    on_fighter_attacked(attacker, target, log_entries)
    handle_spell_on_damage_dealt(target, attacker, dealt, target_allies, attacker_allies, log_entries)
    handle_spell_on_damage_taken(attacker, target, dealt, attacker_allies, target_allies, log_entries)
    relation = str(attack_result.get("relation", "neutral"))
    relation_text = ""
    if relation == "advantage":
        relation_text = " (type advantage)"
    elif relation == "disadvantage":
        relation_text = " (type resisted)"
    log_entries.append(f"**PRECOG** lets **{target.name}** strike **{attacker.name}** first for **{dealt}** damage{relation_text}.")
    return True


def _handle_spell_when_targeted_base(target: Fighter, attacker: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> Tuple[Fighter, float, bool]:
    damage_multiplier = 1.0
    canceled = False
    if not target.alive or not attacker.alive:
        return target, damage_multiplier, canceled
    active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
    if any(card.support_card_id == "SPEEDROID_CLEAR_WING_WONDER" for card in active_cards) and fighter_is_clear_wing(target) and not support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target)), "clear_wing_wonder_used_this_round", False):
        total_pct = gain_percent_stat_bonus(target, "ATK", 50)
        set_support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target)), "clear_wing_wonder_used_this_round", True)
        log_entries.append(f"**Speedroid Clear Wing Wonder** grants **{target.name}** **+50% ATK** before **PRECOG** (current bonus: {total_pct:+d}% ATK).")
    if any(card.support_card_id == "FIRE_KING_ISLAND" for card in active_cards) and fighter_is_fire_king(target) and not support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target)), "fire_king_island_used_this_round", False):
        if destroy_fighter_equip_spell(attacker, log_entries, "Fire King Island"):
            set_support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target)), "fire_king_island_used_this_round", True)
    if any(card.support_card_id == "BLACK_SONIC" for card in active_cards) and fighter_is_blackwing(target) and not support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target)), "black_sonic_used_this_round", False):
        set_support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(target)), "black_sonic_used_this_round", True)
        reflected = max(1, int(round(attacker.max_hp * 0.25)))
        dealt = apply_damage_with_runtime(attacker, reflected, target, log_entries, "Black Sonic")
        log_entries.append(f"**Black Sonic** negates the attack on **{target.name}** and deals **{dealt}** damage to **{attacker.name}**.")
        return target, damage_multiplier, True
    protected_targets = [candidate for candidate in allies if candidate.alive and (int(runtime_flag(candidate, "shield", 0)) > 0 or (isinstance(runtime_flag(candidate, "summon_data", None), dict) and int(runtime_flag(candidate, "summon_data", {}).get("hp", 0)) > 0))]
    if protected_targets and target not in protected_targets and any(unit.alive and active_equip_spell_name(unit) == "Hippo Carnival" for unit in allies):
        target = RNG.choice(protected_targets)
        log_entries.append(f"**Hippo Carnival** redirects the attack to **{target.name}**.")
    summon = _resolve_token_guard(target, log_entries)
    if summon is not None:
        log_entries.append(f"**TOKEN** jumps in front of **{target.name}** to guard the attack.")
    if any(card.support_card_id == "ROYAL_PENGUINS_GARDEN" for card in active_cards) and fighter_is_penguin(target) and RNG.random() < 0.50:
        if apply_status_effect(attacker, StatusEffect.FREEZE, source=target, log_entries=log_entries):
            log_entries.append(f"**Royal Penguins Garden** freezes **{attacker.name}** before damage calculation.")
    if any(card.support_card_id == "GRAND_SPIRITUAL_ART_ICHIRIN" for card in active_cards) and fighter_is_charmer_familiar_channeler(target) and RNG.random() < 0.50:
        if apply_status_effect(attacker, StatusEffect.CHARM, source=target, log_entries=log_entries):
            log_entries.append(f"**Grand Spiritual Art - Ichirin** charms **{attacker.name}** before damage calculation.")
    if any(card.support_card_id == "STAKE_YOUR_SOUL" for card in active_cards) and fighter_is_vanquish_soul(target):
        redirect_candidates = [ally for ally in allies if ally.alive and ally is not target]
        if redirect_candidates:
            redirected_target = RNG.choice(redirect_candidates)
            damage_multiplier *= 0.50
            log_entries.append(f"**Stake your Soul** redirects the attack from **{target.name}** to **{redirected_target.name}** and reduces the damage by **50%**.")
            target = redirected_target
    spell_name = active_equip_spell_name(target)
    if not spell_name or not target.alive or not attacker.alive:
        return target, damage_multiplier, canceled
    if spell_name == "Book of Moon" and not runtime_flag(target, "book_of_moon_used", False) and RNG.random() < 0.35:
        set_runtime_flag(target, "book_of_moon_used", True)
        apply_status_effect(attacker, StatusEffect.SLEEP, source=target, log_entries=log_entries)
    if spell_name == "Swords of Concealing Light" and not runtime_flag(target, "swords_of_concealing_light_used", False) and RNG.random() < 0.35:
        set_runtime_flag(target, "swords_of_concealing_light_used", True)
        apply_status_effect(attacker, StatusEffect.FEAR, source=target, log_entries=log_entries)
    if spell_name == "Wave-Motion Cannon":
        charges = int(runtime_flag(target, "wave_motion_cannon_charges", 0)) + 1
        set_runtime_flag(target, "wave_motion_cannon_charges", charges)
        if charges >= 10 and target.alive:
            set_runtime_flag(target, "wave_motion_cannon_charges", 0)
            blast = max(1, int(round(target.max_hp * 1.25)))
            for enemy in enemies:
                if enemy.alive:
                    dealt = apply_damage_with_runtime(enemy, blast, target, log_entries, "Wave-Motion Cannon")
                    if dealt > 0:
                        log_entries.append(f"**Wave-Motion Cannon** explodes on **{enemy.name}** for **{dealt}** damage.")
            target.hp = 0
            log_entries.append(f"**{target.name}** reaches **10 charges** and is **KO'd** by **Wave-Motion Cannon**.")
    return target, damage_multiplier, canceled


def handle_spell_on_critical_taken(target: Fighter, attacker: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    if not target.alive or not attacker.alive:
        return
    if active_equip_spell_name(target) == "Darklord Enchantment":
        apply_status_effect(attacker, StatusEffect.CHARM, source=target, log_entries=log_entries)


def _handle_spell_on_damage_taken_base(target: Fighter, attacker: Fighter, damage_taken: int, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    spell_name = active_equip_spell_name(target)
    if not spell_name or damage_taken <= 0:
        return
    if int(runtime_flag(target, "reflect_turns", 0)) > 0 and attacker.alive:
        reflect = max(1, int(round(damage_taken * 0.50)))
        attacker.hp = max(0, attacker.hp - reflect)
        log_entries.append(f"**Mirror Force** reflects **{reflect}** damage to **{attacker.name}**.")
    if spell_name == "Ojama Trio" and not runtime_flag(target, "ojama_trio_used", False) and attacker.alive:
        set_runtime_flag(target, "ojama_trio_used", True)
        place_bomb(attacker, None, 0.30, target, log_entries)
    if spell_name == "Rising Energy":
        stacks = min(4, int(runtime_flag(target, "rising_energy_stacks", 0)) + 1)
        set_runtime_flag(target, "rising_energy_stacks", stacks)
    if spell_name == "Typhoon" and attacker.alive:
        remove_random_buff(attacker, log_entries, "Typhoon")
    if any(card.support_card_id == "MAGNET_CONVERSION" for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])) and fighter_is_magnet_warrior(target) and target.alive:
        healed = heal_fighter(target, max(1, int(round(damage_taken * 0.50))), allies, enemies, log_entries, "Magnet Conversion", source=target)
        if healed > 0:
            log_entries.append(f"**Magnet Conversion** heals **{target.name}** for **{healed} HP**.")
    if any(card.support_card_id == "BLAZE_ACCELERATOR" for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])) and fighter_is_volcanic(target):
        if RNG.random() < 0.50:
            apply_status_effect(attacker, StatusEffect.BURN, source=target, log_entries=log_entries)
        if attacker is not None and RNG.random() < 0.25:
            for enemy in enemies:
                set_runtime_flag(enemy, "blaze_accelerator_heart_loss_triggered", True)
            log_entries.append("**Blaze Accelerator** threatens the opposing side with **1 heart** loss.")
    if spell_name == "Nutrient Z" and not runtime_flag(target, "nutrient_z_used", False) and target.hp > 0 and target.hp <= int(target.max_hp * 0.25):
        set_runtime_flag(target, "nutrient_z_used", True)
        heal_fighter(target, target.max_hp, allies, enemies, log_entries, "Nutrient Z", source=target)
    if int(runtime_flag(target, "counter_turns", 0)) > 0 and target.alive and attacker.alive:
        counter_result = resolve_attack_damage(target, attacker)
        if counter_result["hit"]:
            counter = apply_damage_with_runtime(attacker, int(counter_result["damage"]), target, log_entries, "counterattack")
            crit_text = " **Critical hit!**" if counter_result["crit"] else ""
            log_entries.append(f"**COUNTER** lets **{target.name}** counterattack for **{counter}** damage.{crit_text}")
        else:
            log_entries.append(f"**COUNTER** triggers, but **{attacker.name}** avoids the counterattack.")


def _handle_spell_on_damage_dealt_base(attacker: Fighter, target: Fighter, damage_dealt: int, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    if damage_dealt <= 0:
        return
    set_runtime_flag(attacker, "last_damaged_target_id", id(target))
    attacker_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(attacker), [])
    if any(card.support_card_id == "DARK_BURNING_ATTACK" for card in attacker_cards) and fighter_is_magician_girl(attacker):
        apply_status_effect(target, StatusEffect.BURN, source=attacker, log_entries=log_entries)
    if any(card.support_card_id == "RED_EYES_INFERNO_CHAIN" for card in attacker_cards) and fighter_is_red_eyes(attacker):
        apply_status_effect(target, StatusEffect.BURN, source=attacker, log_entries=log_entries)
    spell_name = active_equip_spell_name(attacker)
    if not spell_name:
        return
    if any(card.support_card_id == "LABRYNTH_BARRAGE" for card in attacker_cards) and fighter_is_labrynth(attacker) and RNG.random() < 0.50:
        destroy_fighter_equip_spell(target, log_entries, "Labrynth Barrage")
    if any(card.support_card_id == "BURST_STREAM_OF_DESTRUCTION" for card in attacker_cards) and fighter_is_blue_eyes(attacker) and RNG.random() < 0.25:
        destroy_fighter_equip_spell(target, log_entries, "Burst Stream of Destruction")
    if any(card.support_card_id == "ETERNAL_SOUL" for card in attacker_cards) and fighter_is_dark_magician(attacker) and not support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(attacker)), "eternal_soul_used_this_round", False):
        if apply_status_effect(target, StatusEffect.SILENCE, source=attacker, log_entries=log_entries):
            set_support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(attacker)), "eternal_soul_used_this_round", True)
    chance_map = {
        "Poison of the Old Man": (0.35, StatusEffect.POISON),
        "Hinotama": (0.35, StatusEffect.BURN),
        "Frozen Soul": (0.35, StatusEffect.FREEZE),
        "Thunder Vassal": (0.35, StatusEffect.STUN),
        "Curse of Fiend": (0.35, StatusEffect.PETRIFY),
        "Spellbinding Circle": (0.35, StatusEffect.SLEEP),
        "Shadow Spell": (0.45, StatusEffect.FEAR),
        "Brain Control": (0.45, StatusEffect.CHARM),
        "Cursed Seal of the Forbidden Spell": (0.55, StatusEffect.SILENCE),
        "Enemy Controller": (0.35, StatusEffect.TAUNT),
    }
    if spell_name in chance_map:
        chance, effect = chance_map[spell_name]
        if RNG.random() < chance:
            apply_status_effect(target, effect, source=attacker, log_entries=log_entries)
    if spell_name == "Forbidden Chalice":
        apply_heal_block(target, source=attacker, log_entries=log_entries)
    if spell_name == "Blast with Chain" and RNG.random() < 0.55:
        place_bomb(target, None, 0.30, attacker, log_entries)
    if spell_name == "Curse of Darkness" and RNG.random() < 0.35:
        set_runtime_flag(target, "damage_taken_up_turns", max(int(runtime_flag(target, "damage_taken_up_turns", 0)), 999))
    if spell_name == "Mystical Space Typhoon" and RNG.random() < 0.35:
        effect = RNG.choice([StatusEffect.BURN, StatusEffect.POISON, StatusEffect.FEAR, StatusEffect.SILENCE])
        apply_status_effect(target, effect, source=attacker, log_entries=log_entries)
    if spell_name == "Book of Secret Arts" and runtime_flag(attacker, "book_of_secret_arts_active", False) and RNG.random() < 0.55:
        apply_status_effect(target, StatusEffect.SILENCE, source=attacker, log_entries=log_entries)
    if spell_name == "Spell Shattering Arrow" and RNG.random() < 0.55:
        remove_random_buff(target, log_entries, "Spell Shattering Arrow")
    if spell_name == "Diffusion Wave-Motion":
        splash_damage = max(1, int(round(damage_dealt * 0.25)))
        for enemy in enemies:
            if enemy.alive and enemy is not target:
                dealt = apply_damage_with_runtime(enemy, splash_damage, attacker, log_entries, "Diffusion Wave-Motion")
                if dealt > 0:
                    log_entries.append(f"**Diffusion Wave-Motion** splashes **{enemy.name}** for **{dealt}** damage.")
    if spell_name == "Malevolent Nuzzler":
        heal_amount = max(1, int(round(damage_dealt * 0.30)))
        heal_fighter(attacker, heal_amount, allies, enemies, log_entries, "Malevolent Nuzzler", source=attacker)


def handle_spell_on_turn_start(actor: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    if not actor.alive:
        return
    spell_name = active_equip_spell_name(actor)
    if spell_name == "Dust Tornado":
        if fighter_has_bomb(actor):
            remove_bomb_debuff(actor, log_entries, reason="Dust Tornado")
        elif fighter_statuses(actor):
            clear_status(actor, log_entries, reason="Dust Tornado")
    elif spell_name == "Harpie's Feather Duster":
        for ally in allies:
            if fighter_has_bomb(ally):
                remove_bomb_debuff(ally, log_entries, reason="Harpie's Feather Duster")
            elif fighter_statuses(ally):
                clear_status(ally, log_entries, reason="Harpie's Feather Duster")
    elif spell_name == "Drastic Drop Off":
        for fighter in [*allies, *enemies]:
            if int(runtime_flag(fighter, "shield", 0)) > 0:
                set_runtime_flag(fighter, "shield", 0)
                log_entries.append(f"**Drastic Drop Off** removes **SHIELD** from **{fighter.name}**.")
            if int(runtime_flag(fighter, "protection_turns", 0)) > 0:
                set_runtime_flag(fighter, "protection_turns", 0)
                log_entries.append(f"**Drastic Drop Off** removes **PROTECTION** from **{fighter.name}**.")
    elif spell_name == "Night Beam":
        for enemy in enemies:
            if enemy.alive:
                remove_random_buff(enemy, log_entries, "Night Beam")
    elif spell_name == "Fairy Box" and RNG.random() < 0.50:
        set_runtime_flag(actor, "reflect_turns", max(1, int(runtime_flag(actor, "reflect_turns", 0))))
        log_entries.append(f"**Fairy Box** grants **REFLECT** to **{actor.name}**.")
    elif spell_name == "Nobleman of Crossout" and RNG.random() < 0.50:
        set_runtime_flag(actor, "precog_turns", max(1, int(runtime_flag(actor, "precog_turns", 0))))
        log_entries.append(f"**Nobleman of Crossout** grants **PRECOG** to **{actor.name}**.")
    elif spell_name == "Kunai with Chain" and RNG.random() < 0.50:
        set_runtime_flag(actor, "counter_turns", max(1, int(runtime_flag(actor, "counter_turns", 0))))
        log_entries.append(f"**Kunai with Chain** grants **COUNTER** to **{actor.name}**.")
    elif spell_name == "Soul Taker" and RNG.random() < 0.50:
        target = random_alive(list(enemies))
        if target is not None:
            apply_heal_block(target, source=actor, log_entries=log_entries, flag_name="soul_taker_block_turns")
    elif spell_name == "Dangerous Machine Type-6":
        ally_target = RNG.choice([ally for ally in allies if ally.alive]) if any(ally.alive for ally in allies) else None
        enemy_target = RNG.choice([enemy for enemy in enemies if enemy.alive]) if any(enemy.alive for enemy in enemies) else None
        if ally_target is not None:
            _grant_random_turn_start_machine_effect(ally_target, actor, log_entries)
        if enemy_target is not None:
            _grant_random_turn_start_machine_effect(enemy_target, actor, log_entries)
    elif spell_name == "Galaxy Cyclone":
        target = None
        max_buffs = 0
        for enemy in enemies:
            if not enemy.alive:
                continue
            count = buff_count(enemy)
            if count > max_buffs:
                max_buffs = count
                target = enemy
        if target is not None and max_buffs > 0:
            remove_random_buff(target, log_entries, "Galaxy Cyclone")
    for enemy in enemies:
        if enemy.alive and active_equip_spell_name(enemy) == "Dark Snake Syndrome" and fighter_has_debuff(actor):
            dealt = apply_damage_with_runtime(actor, max(1, int(round(actor.max_hp * 0.05))), enemy, log_entries, "Dark Snake Syndrome")
            if dealt > 0:
                log_entries.append(f"**Dark Snake Syndrome** deals **{dealt}** damage to **{actor.name}**.")
    active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(actor), [])
    if any(card.support_card_id == "CERULEAN_SKYFIRE" for card in active_cards) and fighter_is_hamon(actor) and not support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(actor)), "cerulean_skyfire_used_this_round", False):
        removed = len(fighter_statuses(actor))
        removed += int(bool(runtime_flag(actor, "heal_block_turns", 0)))
        removed += int(bool(runtime_flag(actor, "damage_taken_up_turns", 0)))
        removed += int(bool(runtime_flag(actor, "soul_taker_block_turns", 0)))
        removed += len(list(runtime_flag(actor, "bombs", [])))
        if removed:
            actor.status = None
            actor.runtime["status_effects"] = []
            clear_runtime_debuffs(actor)
            remove_bomb_debuff(actor)
            gain_percent_stat_bonus(actor, "ATK", 25 * removed)
            gain_percent_stat_bonus(actor, "DEF", 25 * removed)
            set_support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(actor)), "cerulean_skyfire_used_this_round", True)
            log_entries.append(f"**Cerulean Skyfire** removes **{removed}** debuff(s) from **{actor.name}** and grants **+{25*removed}% ATK/DEF**.")
    if any(card.support_card_id == "HYPER_BLAZE" for card in active_cards) and fighter_is_uria_lord_of_searing_flames(actor) and not support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(actor)), "hyper_blaze_used_this_round", False):
        allies = ACTIVE_TEAMMATES_BY_FIGHTER.get(id(actor), [])
        donor = next((ally for ally in allies if ally.alive and ally is not actor and str(runtime_flag(ally, "equip_spell_name", "")) and not runtime_flag(ally, "equip_spell_destroyed", False)), None)
        if donor is not None:
            set_runtime_flag(donor, "equip_spell_destroyed", True)
            gain_percent_stat_bonus(actor, "ATK", 100)
            set_support_runtime_flag(ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(actor)), "hyper_blaze_used_this_round", True)
            log_entries.append(f"**Hyper Blaze** destroys **{donor.name}**'s equip spell and grants **+100% ATK** to **{actor.name}**.")
    summon = runtime_flag(actor, "summon_data", None)
    if isinstance(summon, dict) and int(summon.get("hp", 0)) > 0:
        enemies = enemy_team_for_fighter(actor)
        target = random_alive(list(enemies))
        if target is not None:
            dealt = apply_damage_with_runtime(target, max(1, int(summon.get("atk", 1))), actor, log_entries, "TOKEN")
            log_entries.append(f"**TOKEN** attacks **{target.name}** for **{dealt}** damage.")
    decrement_runtime_counters(actor)


def handle_spell_on_turn_end(actor: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    if actor.alive:
        for fighter in [*allies, *enemies]:
            if not fighter.alive or active_equip_spell_name(fighter) != "Rain of Mercy":
                continue
            fighter_is_actor_ally = _fighter_in_team_by_identity(fighter, allies)
            fighter_allies = ACTIVE_TEAMMATES_BY_FIGHTER.get(id(fighter), allies if fighter_is_actor_ally else enemies)
            fighter_enemies = enemies if fighter_is_actor_ally else allies
            living = living_fighters(list(fighter_allies))
            if living:
                target = min(living, key=lambda unit: (unit.hp / max(1, unit.max_hp), unit.hp, unit.name))
                heal_fighter(target, max(1, int(round(target.max_hp * 0.05))), fighter_allies, fighter_enemies, log_entries, "Rain of Mercy", source=actor)
        for fighter in [*allies, *enemies]:
            active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])
            if fighter.alive and fighter_is_silent_swordsman(fighter) and any(card.support_card_id == "SILENT_SWORD_SLASH" for card in active_cards):
                set_runtime_flag(fighter, "silent_sword_slash_pct", int(runtime_flag(fighter, "silent_sword_slash_pct", 0)) + 10)
    spell_name = active_equip_spell_name(actor)
    if not spell_name or not actor.alive:
        return


def handle_spell_on_ally_defeated(observer: Fighter, fallen: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    spell_name = active_equip_spell_name(observer)
    if not spell_name or not observer.alive or observer is fallen:
        return
    if spell_name == "Burden of the Mighty":
        heal_fighter(observer, max(1, int(round(observer.max_hp * 0.25))), allies, enemies, log_entries, "Burden of the Mighty", source=observer)
        set_runtime_flag(observer, "burden_bonus", 0.50)
    elif spell_name == "Soul Resurrection" and not runtime_flag(observer, "soul_res_used", False):
        set_runtime_flag(observer, "soul_res_used", True)
        fallen.hp = max(1, int(round(fallen.max_hp * 0.50)))
        log_entries.append(f"**Soul Resurrection** revives **{fallen.name}** with **{fallen.hp} HP**.")


def _handle_spell_on_self_defeated_base(fallen: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> bool:
    spell_name = active_equip_spell_name(fallen)
    if spell_name == "Monster Reborn" and not runtime_flag(fallen, "monster_reborn_used", False):
        set_runtime_flag(fallen, "monster_reborn_used", True)
        fallen.hp = fallen.max_hp
        log_entries.append(f"**Monster Reborn** revives **{fallen.name}** at full HP.")
        return True
    if spell_name == "Return from the Different Dimension" and not runtime_flag(fallen, "return_dimension_used", False):
        set_runtime_flag(fallen, "return_dimension_used", True)
        fallen.hp = max(1, int(round(fallen.max_hp * 0.50)))
        set_runtime_flag(fallen, "immunity_turns", max(int(runtime_flag(fallen, "immunity_turns", 0)), 999))
        log_entries.append(f"**Return from the Different Dimension** revives **{fallen.name}** with **{fallen.hp} HP** and grants **IMMUNITY**.")
        return True
    if spell_name == "Ring of Destruction":
        for enemy in enemies:
            if enemy.alive:
                apply_status_effect(enemy, StatusEffect.STUN, source=fallen, log_entries=log_entries)
    elif spell_name == "Torrential Tribute":
        for enemy in enemies:
            if enemy.alive:
                dealt = apply_damage_with_runtime(enemy, max(1, int(round(fallen.max_hp * 0.25))), fallen, log_entries, "Torrential Tribute")
                if dealt > 0:
                    log_entries.append(f"**Torrential Tribute** hits **{enemy.name}** for **{dealt}** damage.")
    elif spell_name == "Draining Shield":
        for ally in allies:
            if ally.alive:
                heal_fighter(ally, max(1, int(round(ally.max_hp * 0.25))), allies, enemies, log_entries, "Draining Shield", source=fallen)
    return False


def battle_damage(attacker: Fighter, defender: Fighter, attacker_support_cards: Sequence[SupportCardDef] = (), defender_support_cards: Sequence[SupportCardDef] = ()) -> Tuple[int, str]:
    attacker_atk = effective_atk(attacker, attacker_support_cards)
    defender_def = effective_defense(defender, defender_support_cards)
    attacker_support_ids = {card.support_card_id for card in attacker_support_cards}
    if (
        "ANCIENT_GEAR_FORTRESS" in attacker_support_ids
        and fighter_is_ancient_gear(attacker)
        and int(runtime_flag(attacker, "protection_turns", 0)) > 0
    ):
        defender_def = 0
    base = attacker_atk - int(defender_def * 0.45)
    variance = RNG.randint(-12, 12)
    raw_damage = max(1, base + variance)
    type_multiplier, relation = monster_type_multiplier(effective_monster_types(attacker), effective_monster_types(defender))
    if active_equip_spell_name(attacker) == "Super Rush Headlong":
        type_multiplier, relation = TYPE_SUPER_MULTIPLIER, "advantage"
    final_damage = max(1, int(round(raw_damage * type_multiplier)))
    spell_name = active_equip_spell_name(attacker)
    if "RED_EYES_INFERNO_CHAIN" in attacker_support_ids and fighter_is_red_eyes(attacker) and fighter_has_status(defender, StatusEffect.BURN):
        final_damage = int(round(final_damage * 1.5))
    if spell_name == "Solidarity" and has_shared_attribute_or_type(attacker, defender):
        final_damage = int(round(final_damage * 2.0))
    if spell_name == "Axe of Despair":
        final_damage = int(round(final_damage * 1.5))
    elif spell_name == "Messenger of Peace":
        final_damage = int(round(final_damage * 0.25))
    elif spell_name == "Megamorph" and defender.hp <= int(defender.max_hp * 0.5):
        final_damage = int(round(final_damage * 1.5))
    elif spell_name == "Last Turn" and attacker.hp <= int(attacker.max_hp * 0.5):
        final_damage = int(round(final_damage * 1.5))
    elif spell_name == "Dark Room of Nightmare" and fighter_has_debuff(defender):
        final_damage = int(round(final_damage * 1.05))
    elif spell_name == "Inferno Tempest" and runtime_flag(defender, "bombs", []):
        bombs = list(runtime_flag(defender, "bombs", []))
        set_runtime_flag(defender, "bombs", [])
        _sync_bomb_status_badge(defender)
        final_damage += sum(max(1, int(b.get("damage", 1))) for b in bombs)
    if spell_name == "Limiter Removal" and int(runtime_flag(attacker, "limiter_rounds", 0)) > 0:
        final_damage = int(round(final_damage * 3.5))
    if spell_name == "Mystik Wok" and int(runtime_flag(attacker, "mystik_wok_rounds", 0)) > 0:
        final_damage = int(round(final_damage * 1.5))
    bonus_mult = 1.0 + float(runtime_flag(attacker, "burden_bonus", 0.0)) + min(1.00, 0.25 * int(runtime_flag(attacker, "rising_energy_stacks", 0)))
    if any(other.alive and active_equip_spell_name(other) == "Clockwork Night" for other in battle_fighters_for(attacker)) and _is_original_machine_type(attacker):
        bonus_mult += 0.25
    final_damage = int(round(final_damage * bonus_mult))
    if float(runtime_flag(attacker, "curse_of_darkness_dealt_down", 0.0)) > 0:
        final_damage = max(1, int(round(final_damage * 0.5)))
    if active_equip_spell_name(defender) == "Rainbow Veil":
        final_damage = int(round(final_damage * 0.75))
    if active_equip_spell_name(defender) == "Stronghold the Moving Fortress" and defender.hp >= int(defender.max_hp * 0.5):
        final_damage = int(round(final_damage * 0.5))
    if int(runtime_flag(defender, "damage_taken_up_turns", 0)) > 0:
        final_damage = int(round(final_damage * 1.10))
    if float(runtime_flag(attacker, "lightforce_sword_damage_down", 0.0)) > 0:
        final_damage = int(round(final_damage * (1.0 - float(runtime_flag(attacker, "lightforce_sword_damage_down", 0.0)))))
    return max(1, final_damage), relation


def attack_hit_chance(attacker: Fighter, defender: Fighter) -> float:
    attacker_accuracy = float(attacker.accuracy) + (0.10 if active_equip_spell_name(attacker) == "Lucky Iron Axe" else 0.0)
    dodge_bonus = max(0.0, float(defender.dodge) + (0.05 if active_equip_spell_name(defender) == "Lucky Iron Axe" else 0.0))
    defender_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(defender), [])
    if fighter_is_yummy(defender) and any(card.support_card_id == "YUMMY_SURPRISE" for card in defender_cards):
        dodge_bonus += 0.50
    if fighter_is_blackwing(defender) and any(card.support_card_id == "BLACKWING_WHIRLWIND" for card in defender_cards):
        dodge_bonus += 0.25
    miss_chance = max(0.0, 1.0 - attacker_accuracy) + dodge_bonus
    return min(1.0, max(0.0, 1.0 - miss_chance))


def attack_lands(attacker: Fighter, defender: Fighter) -> bool:
    return RNG.random() < attack_hit_chance(attacker, defender)


def resolve_attack_damage(attacker: Fighter, defender: Fighter, attacker_support_cards: Sequence[SupportCardDef] = (), defender_support_cards: Sequence[SupportCardDef] = (), hit_multiplier: float = 1.0) -> Dict[str, object]:
    landed = attack_lands(attacker, defender)
    if not landed:
        return {"hit": False, "crit": False, "damage": 0, "relation": "neutral"}
    damage, relation = battle_damage(attacker, defender, attacker_support_cards, defender_support_cards)
    damage = max(1, int(round(damage * hit_multiplier)))
    crit_chance = min(1.0, max(0.0, float(attacker.crit_rate)))
    spell_name = active_equip_spell_name(attacker)
    if spell_name == "Lucky Iron Axe":
        crit_chance = min(1.0, crit_chance + 0.05)
    if spell_name == "Rush Recklessly":
        crit_chance = min(1.0, crit_chance + 0.25)
    if spell_name == "Book of Taiyou" and any(fighter_has_status(defender, effect) for effect in (StatusEffect.SLEEP, StatusEffect.FREEZE, StatusEffect.STUN, StatusEffect.PETRIFY)):
        crit_chance = min(1.0, crit_chance + 1.0)
    crit = RNG.random() < crit_chance
    if crit:
        crit_damage_value = float(attacker.crit_damage)
        if active_equip_spell_name(attacker) == "Rush Recklessly":
            crit_damage_value += 0.50
        damage = max(1, int(round(damage * crit_damage_value)))
    return {"hit": True, "crit": crit, "damage": damage, "relation": relation}


def _raw_turn_speed(fighter: Fighter) -> int:
    return max(1, effective_spd(fighter, ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])))


def _speed_order_reversed(teams: Sequence[List[Fighter]]) -> bool:
    return any(fighter.alive and active_equip_spell_name(fighter) == "Time Seal" for team in teams for fighter in team)


def _turn_charge_speeds(alive: Sequence[Fighter], reversed_order: bool) -> Dict[int, int]:
    raw = {id(fighter): _raw_turn_speed(fighter) for fighter in alive}
    if not reversed_order or not raw:
        return raw
    highest = max(raw.values())
    return {fighter_id: max(1, highest + 1 - speed) for fighter_id, speed in raw.items()}


def any_ready_fighter(teams: Sequence[List[Fighter]]) -> Optional[Fighter]:
    alive = [fighter for team in teams for fighter in team if fighter.alive]
    ready = [fighter for fighter in alive if fighter.speed_gauge >= SPEED_GAUGE_MAX - ATB_READY_EPSILON]
    if not ready:
        return None
    speeds = _turn_charge_speeds(alive, _speed_order_reversed(teams))
    ready.sort(key=lambda fighter: (fighter.speed_gauge, speeds.get(id(fighter), 1), RNG.random()), reverse=True)
    return ready[0]


def time_until_next_turn(teams: Sequence[List[Fighter]]) -> float:
    alive = [fighter for team in teams for fighter in team if fighter.alive]
    if not alive:
        return 0.0
    speeds = _turn_charge_speeds(alive, _speed_order_reversed(teams))
    times_to_full = []
    for fighter in alive:
        fighter_spd = max(1, speeds.get(id(fighter), 1))
        remaining = max(0.0, SPEED_GAUGE_MAX - fighter.speed_gauge)
        times_to_full.append(remaining / fighter_spd)
    return min(times_to_full) if times_to_full else 0.0


SPELL_OF_PAIN_REDIRECT_RATIO = 0.50


def spell_of_pain_bearer(target: Fighter, defending_team: Sequence[Fighter]) -> Optional[Fighter]:
    """Spell of Pain: "The equipped monster takes 50% of damage redirected from the ally with the lowest HP."

    One rule for every mode. Ranked, exhibition and arena had it backwards (the weakest ally
    had to hold the spell itself) and every mode redirected 30% instead of the stated 50%.
    """
    lowest_hp_ally = min(living_fighters(list(defending_team)) or [target], key=lambda fighter: (fighter.hp / max(1, fighter.max_hp), fighter.hp))
    if target is not lowest_hp_ally:
        return None
    for ally in defending_team:
        if ally.alive and ally is not target and active_equip_spell_name(ally) == "Spell of Pain":
            return ally
    return None


def exact_time_to_next_turn(teams: Sequence[List[Fighter]]) -> float:
    """Advance the battle clock exactly to the moment the next gauge fills.

    The old fixed-size steps overshot by up to ~3x and the overflow was thrown away when a
    fighter acted, which rounded turn frequency instead of following SPD precisely.
    """
    return time_until_next_turn(teams) / ATB_TIME_ACCELERATION + 1e-9


def advance_speed_gauges(teams: Sequence[List[Fighter]], elapsed: float) -> Tuple[Optional[Fighter], float]:
    alive = [fighter for team in teams for fighter in team if fighter.alive]
    if not alive:
        return None, 0.0

    effective_elapsed = max(0.0, elapsed) * ATB_TIME_ACCELERATION
    if effective_elapsed > 0:
        speeds = _turn_charge_speeds(alive, _speed_order_reversed(teams))
        for fighter in alive:
            fighter.speed_gauge = min(SPEED_GAUGE_MAX, fighter.speed_gauge + max(1, speeds.get(id(fighter), 1)) * effective_elapsed)
    return any_ready_fighter(teams), effective_elapsed


def trap_hole_target_candidates(enemy_team: Sequence[Fighter], predicate: Callable[[Fighter], bool]) -> List[Fighter]:
    return [
        enemy
        for enemy in enemy_team
        if enemy.alive
        and not is_guild_boss_fighter(enemy)
        and predicate(enemy)
    ]


def _assign_runtime_cards_base(
    team: Sequence[Fighter],
    runtime: Optional[SupportDeckRuntime],
    opposing_team: Optional[Sequence[Fighter]] = None,
    opposing_runtime: Optional[SupportDeckRuntime] = None,
):
    active_cards = list(runtime.active_cards) if runtime is not None else []
    support_disabled_by_opponent = (
        opposing_team is not None
        and _runtime_team_disables_enemy_support(opposing_team, opposing_runtime)
    )
    filtered_cards = [] if support_disabled_by_opponent else list(active_cards)
    for fighter in team:
        ACTIVE_SUPPORT_CARDS_BY_FIGHTER[id(fighter)] = list(filtered_cards)
        if runtime is not None:
            ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER[id(fighter)] = runtime


def _assign_battle_runtime_cards(
    player_team: Sequence[Fighter],
    player_runtime: Optional[SupportDeckRuntime],
    enemy_team: Sequence[Fighter],
    enemy_runtime: Optional[SupportDeckRuntime],
):
    _assign_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
    _assign_runtime_cards(enemy_team, enemy_runtime, player_team, player_runtime)


def prepare_fighter_equip_spell(fighter: Fighter, user_id: Optional[int]) -> None:
    if is_extra_deck_fighter(fighter):
        fighter.runtime.pop("equip_spell_name", None)
        fighter.runtime["extra_deck_monster"] = True
        return
    return _prepare_fighter_equip_spell_base(fighter, user_id)


def _assign_runtime_cards(team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime], opposing_team: Optional[Sequence[Fighter]] = None, opposing_runtime: Optional[SupportDeckRuntime] = None):
    _assign_runtime_cards_base(team, runtime, opposing_team, opposing_runtime)
    if opposing_team is not None:
        register_battle_teams(team, opposing_team)
    for fighter in team:
        if is_extra_deck_fighter(fighter):
            ACTIVE_SUPPORT_CARDS_BY_FIGHTER[id(fighter)] = []
            ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.pop(id(fighter), None)


def handle_spell_when_targeted(target: Fighter, attacker: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]):
    if is_extra_deck_fighter(target) and bool(runtime_flag(target, "dodge_next_attack", False)):
        set_runtime_flag(target, "dodge_next_attack", False)
        log_entries.append(f"**{target.name}** uses its Extra Deck effect to avoid the attack.")
        return target, 1.0, True
    if is_extra_deck_fighter(attacker) and bool(runtime_flag(attacker, "extra_deck_lockout", False)):
        set_runtime_flag(attacker, "extra_deck_lockout", False)
    return _handle_spell_when_targeted_base(target, attacker, allies, enemies, log_entries)


def handle_spell_on_damage_dealt(attacker: Fighter, target: Fighter, damage: int, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    _handle_spell_on_damage_dealt_base(attacker, target, damage, allies, enemies, log_entries)
    if not is_extra_deck_fighter(attacker) or damage <= 0:
        return
    effect = extra_deck_effect_key(attacker.name)
    if effect in {"red_eyes", "uria"} and target.alive:
        apply_status_effect(target, StatusEffect.BURN, source=attacker, log_entries=log_entries)
    elif effect == "starving_venom" and target.alive:
        apply_status_effect(target, StatusEffect.POISON, source=attacker, log_entries=log_entries)
    elif effect == "moon_dragon":
        weakest = min((ally for ally in allies if ally.alive), key=lambda f: (f.hp / max(1, f.max_hp), f.hp), default=None)
        if weakest:
            heal_fighter(weakest, max(1, int(round(damage * 0.20))), allies, enemies, log_entries, "Lunar Recovery", source=attacker)
    elif effect == "slifer" and target.alive:
        gain_percent_stat_bonus(target, "ATK", -10)
    elif effect == "zarc":
        for enemy in enemies:
            if enemy.alive:
                set_runtime_flag(enemy, "extra_deck_lockout", True)


def handle_spell_on_damage_taken(target: Fighter, attacker: Fighter, damage: int, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    _handle_spell_on_damage_taken_base(target, attacker, damage, allies, enemies, log_entries)
    if not is_extra_deck_fighter(target) or damage <= 0:
        return
    effect = extra_deck_effect_key(target.name)
    if effect in {"dyson", "bloom_diva", "hamon", "chrononaut"} and attacker.alive:
        reflected = max(1, int(round(damage * 0.20)))
        dealt = apply_damage_with_runtime(attacker, reflected, target, log_entries, "Extra Deck reflection")
        if dealt:
            log_entries.append(f"**{target.name}** reflects **{dealt}** damage to **{attacker.name}**.")
    elif effect == "black_winged":
        gain_flat_stat_bonus(target, "ATK", max(1, int(round(damage * 0.25))))


def handle_spell_on_self_defeated(fallen: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> bool:
    if is_extra_deck_fighter(fallen):
        effect = extra_deck_effect_key(fallen.name)
        if effect in {"wicked_eraser", "sun_dragon"}:
            for enemy in enemies:
                if enemy.alive:
                    dealt = apply_damage_with_runtime(enemy, max(1, int(round(fallen.max_hp * (0.30 if effect == "wicked_eraser" else 0.15)))), fallen, log_entries, fallen.name)
                    if dealt:
                        log_entries.append(f"**{fallen.name}**'s final effect hits **{enemy.name}** for **{dealt}** damage.")
        return False
    # Revives are nullified if every monster zone on that side is currently full.
    if all(ally.alive for ally in allies):
        spell_name = _active_equip_spell_name_base(fallen)
        if spell_name in {"Monster Reborn", "Return from the Different Dimension"}:
            log_entries.append(f"**{spell_name}** tries to revive **{fallen.name}**, but every monster zone is full. The revive is nullified.")
            return False
    return _handle_spell_on_self_defeated_base(fallen, allies, enemies, log_entries)


def _runtime_team_disables_enemy_support(team: Sequence[Fighter], runtime: Optional[SupportDeckRuntime]) -> bool:
    if any(f.alive and is_extra_deck_fighter(f) and bool(runtime_flag(f, "disable_enemy_support", False)) for f in team):
        return True
    return _runtime_team_disables_enemy_support_base(team, runtime)
