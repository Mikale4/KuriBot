"""Attacks per turn and what happens when monsters fall (hearts, revives, Extra Deck summons)."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from kuribot.config import BATTLE_HEARTS, CAMPAIGN_BATTLE_HEARTS, DUELING_GROUNDS_BATTLE_HEARTS, RNG
from kuribot.models import Fighter, SupportCardDef, SupportDeckRuntime
from kuribot.monsters import extra_deck_effect_key
from kuribot.battle.archetypes import (
    fighter_is_blue_eyes, fighter_is_cyber_dragon, fighter_is_diabell, fighter_is_elemental_hero,
    fighter_is_fire_king, fighter_is_galaxy_or_photon, fighter_is_gimmick_puppet,
    fighter_is_kashtira, fighter_is_red_eyes, fighter_is_skull_servant_or_wight,
    fighter_is_trickstar, fighter_is_visas_starfrost,
)
from kuribot.battle.runtime import (
    active_equip_spell_name, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER,
    gain_flat_stat_bonus, gain_percent_stat_bonus, is_extra_deck_fighter, random_alive,
    runtime_flag, set_runtime_flag,
)
from kuribot.battle.special_support import (
    fighter_had_active_special_support, set_support_runtime_flag, support_runtime_flag,
    team_has_active_special_support,
)
from kuribot.battle.stats import current_attack_power
from kuribot.battle.mechanics import (
    _apply_morale_boost, _heart_loss_prevented, _replace_fighter_with_generated_monster,
    add_battle_stat, apply_damage_with_runtime, clear_status, handle_spell_on_ally_defeated,
    handle_spell_on_self_defeated, heal_fighter, trigger_mound_obelisk_on_knockout,
    trigger_mound_ra_on_heart_loss,
)
from kuribot.battle.extra_deck import (
    _apply_missing_extra_deck_knockout_heart_loss, maybe_summon_extra_deck_monster,
)


def _build_attack_instances_for_fighter_base(attacker: Fighter, attacker_cards: Sequence[SupportCardDef]) -> List[Tuple[float, str]]:
    attack_instances = [(1.0, '')]
    if active_equip_spell_name(attacker) == "Twin Swords of Flashing Light - Tryce":
        attack_instances.append((0.5, ' (second hit)'))
    elif active_equip_spell_name(attacker) == "Double Attack" and RNG.random() < 0.25:
        attack_instances.append((1.0, ' (extra attack)'))
    return attack_instances


def should_grant_neutron_blast_extra_turn(attacker: Fighter, attacker_cards: Sequence[SupportCardDef]) -> bool:
    return any(card.support_card_id == "NEUTRON_BLAST" for card in attacker_cards) and fighter_is_blue_eyes(attacker) and RNG.random() < 0.35


def handle_support_on_ally_defeated(runtime: Optional[SupportDeckRuntime], fallen: Fighter, allies: Sequence[Fighter], enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    active_cards = list(runtime.active_cards) if runtime is not None else []
    if any(card.support_card_id == "WRATH_OF_NEOS" for card in active_cards) and fighter_is_elemental_hero(fallen):
        target = random_alive(list(enemies))
        if target is not None:
            damage = max(1, int(round(fallen.max_hp * 0.5)))
            dealt = apply_damage_with_runtime(target, damage, fallen, log_entries, "Wrath of Neos")
            if dealt > 0:
                log_entries.append(f"**Wrath of Neos** blasts **{target.name}** for **{dealt}** damage after **{fallen.name}** is defeated.")
    if any(card.support_card_id == "HERO_SIGNAL" for card in active_cards) and fighter_is_elemental_hero(fallen):
        candidates = [ally for ally in allies if ally.alive and ally is not fallen]
        if candidates:
            chosen = max(candidates, key=lambda unit: (current_attack_power(unit), unit.spd, unit.name))
            set_runtime_flag(chosen, "hero_signal_rounds", max(2, int(runtime_flag(chosen, "hero_signal_rounds", 0))))
            log_entries.append(f"**HERO Signal** empowers **{chosen.name}** with **+100% ATK** and **+50% SPD** for **2 rounds** after **{fallen.name}** is defeated.")
    if any(card.support_card_id == "OVERLOAD_FUSION" for card in active_cards) and fighter_is_cyber_dragon(fallen):
        candidates = [ally for ally in allies if ally.alive and ally is not fallen]
        if candidates:
            chosen = RNG.choice(candidates)
            gained = gain_flat_stat_bonus(chosen, "ATK", max(1, current_attack_power(fallen)))
            log_entries.append(f"**Overload Fusion** transfers **{fallen.name}**'s ATK to **{chosen.name}** (+{gained} ATK).")
    if any(card.support_card_id == "WIGHT_REANIMATOR" for card in active_cards) and fighter_is_skull_servant_or_wight(fallen):
        fallen.hp = 1
        gain_percent_stat_bonus(fallen, "ATK", 25)
        log_entries.append(f"**Wight Reanimator** revives **{fallen.name}** with **1 HP** and **+25% ATK**.")
    if any(card.support_card_id == "FILIA_DIABELL" for card in active_cards) and fighter_is_diabell(fallen):
        if _replace_fighter_with_generated_monster(fallen, ["diabell"], log_entries, randomize_identity=True):
            fallen.hp = fallen.max_hp
            log_entries.append(f"**Filia Diabell** replaces the fallen monster with **{fallen.name}**.")
    if any(card.support_card_id == "TRICKSTAR_REINCARNATION" for card in active_cards) and fighter_is_trickstar(fallen) and RNG.random() < 0.25:
        for enemy in enemies:
            rt = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(enemy))
            if rt is not None:
                rt.draw_index = len(rt.shuffled_cards)
                rt.active_cards = []
                rt.rounds_remaining = 0
                rt.shuffled_cards = []
        log_entries.append("**Trickstar Reincarnation** discards the opponent's entire support deck.")
    if any(card.support_card_id == "PRESSURED_PLANET_WRAITSOTH" for card in active_cards) and (fighter_is_kashtira(fallen) or fighter_is_visas_starfrost(fallen)) and not support_runtime_flag(runtime, "wraitsoth_used", False):
        victims = [enemy for enemy in enemies if enemy.alive]
        if victims:
            chosen = RNG.choice(victims)
            chosen.hp = 0
            set_support_runtime_flag(runtime, "wraitsoth_used", True)
            log_entries.append(f"**Pressured Planet Wraitsoth** destroys **{chosen.name}** after a Kashtira monster is defeated.")


def handle_support_on_self_defeated(runtime: Optional[SupportDeckRuntime], fallen: Fighter, log_entries: List[str]) -> bool:
    active_cards = list(runtime.active_cards) if runtime is not None else []
    if any(card.support_card_id == "BLUE_EYES_SPIRIT_CALL" for card in active_cards) and fighter_is_blue_eyes(fallen) and not support_runtime_flag(runtime, "blue_eyes_spirit_call_used", False):
        set_support_runtime_flag(runtime, "blue_eyes_spirit_call_used", True)
        fallen.hp = 1
        set_runtime_flag(fallen, "shield", max(int(runtime_flag(fallen, "shield", 0)), max(1, int(round(fallen.max_hp * 0.25)))))
        log_entries.append(f"**Blue-Eyes Spirit Call** lets **{fallen.name}** survive at **1 HP** and gain **SHIELD**.")
        return True
    if any(card.support_card_id == "CYBER_REVSYSTEM" for card in active_cards) and fighter_is_cyber_dragon(fallen) and not support_runtime_flag(runtime, "cyber_revsystem_used_this_round", False):
        set_support_runtime_flag(runtime, "cyber_revsystem_used_this_round", True)
        fallen.hp = fallen.max_hp
        log_entries.append(f"**Cyber Revsystem** revives **{fallen.name}** at full HP.")
        return True
    if any(card.support_card_id == "FIRE_KING_ISLAND" for card in active_cards) and fighter_is_fire_king(fallen) and RNG.random() < 0.25:
        fallen.hp = fallen.max_hp
        log_entries.append(f"**Fire King Island** revives **{fallen.name}** at full HP.")
        return True
    return False


def trigger_soul_exchange_on_defeat(attacker: Fighter, fallen: Fighter, attacker_allies: Sequence[Fighter], attacker_enemies: Sequence[Fighter], log_entries: List[str]) -> None:
    if active_equip_spell_name(attacker) != "Soul Exchange":
        return
    heal_amount = max(1, int(round(fallen.max_hp * 0.25)))
    heal_fighter(attacker, heal_amount, attacker_allies, attacker_enemies, log_entries, "Soul Exchange", source=attacker)
    living_allies = [ally for ally in attacker_allies if ally.alive]
    if living_allies:
        lowest_hp_ally = min(living_allies, key=lambda unit: (unit.hp / max(1, unit.max_hp), unit.hp, unit.name))
        heal_fighter(lowest_hp_ally, heal_amount, attacker_allies, attacker_enemies, log_entries, "Soul Exchange", source=attacker)


def _resolve_fallen_fighters_base(
    player_team: Sequence[Fighter],
    enemy_team: Sequence[Fighter],
    user_name: str,
    enemy_name: str,
    player_hearts: int,
    enemy_hearts: int,
    log_entries: List[str],
    resolved_fighter_ids: set[int],
    total_hearts: Optional[int] = None,
) -> Tuple[int, int]:
    if total_hearts is None:
        total_hearts = max(BATTLE_HEARTS, CAMPAIGN_BATTLE_HEARTS, DUELING_GROUNDS_BATTLE_HEARTS)
    else:
        total_hearts = max(1, int(total_hearts))
    for fighter in [*player_team, *enemy_team]:
        if fighter.hp > 0:
            resolved_fighter_ids.discard(id(fighter))
    for fallen in [fighter for fighter in [*player_team, *enemy_team] if fighter.hp <= 0 and id(fighter) not in resolved_fighter_ids]:
        allies = player_team if fallen in player_team else enemy_team
        enemies = enemy_team if fallen in player_team else player_team
        team_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(fallen))
        clear_status(fallen)
        if fallen in player_team:
            for ally in player_team:
                handle_spell_on_ally_defeated(ally, fallen, player_team, enemy_team, log_entries)
            handle_support_on_ally_defeated(team_runtime, fallen, player_team, enemy_team, log_entries)
            hearts_lost = 1
            attacker_id = runtime_flag(fallen, "last_hit_attacker_id")
            if attacker_id is not None:
                for attacker in enemy_team:
                    if id(attacker) == attacker_id:
                        add_battle_stat(attacker, "battle_kills", 1)
                        attacker_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(attacker), [])
                        if any(card.support_card_id == "GALAXY_EXPEDITION" for card in attacker_cards) and fighter_is_galaxy_or_photon(attacker):
                            attacker_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(attacker))
                            set_support_runtime_flag(attacker_runtime, "galaxy_expedition_shuffle_on_discard", True)
                        if any(card.support_card_id == "INFERNO_FIRE_BLAST" for card in attacker_cards) and team_has_active_special_support(enemy_team, "INFERNO_FIRE_BLAST", fighter_is_red_eyes):
                            hearts_lost += 1
                        attacker_allies = list(player_team if attacker in player_team else enemy_team)
                        attacker_enemies = list(enemy_team if attacker in player_team else player_team)
                        trigger_soul_exchange_on_defeat(attacker, fallen, attacker_allies, attacker_enemies, log_entries)
                        break
            player_heart_loss_prevented = _heart_loss_prevented(player_team)
            if not player_heart_loss_prevented:
                player_hearts = max(0, player_hearts - hearts_lost)
            if fighter_had_active_special_support(fallen, "PUPPET_PARADE", fighter_is_gimmick_puppet):
                puppet_hearts_lost = 1
                if team_has_active_special_support(player_team, "INFERNO_FIRE_BLAST", fighter_is_red_eyes):
                    puppet_hearts_lost += 1
                if _heart_loss_prevented(enemy_team):
                    log_entries.append(f"**Puppet Parade** triggers when **{fallen.name}** is defeated, but heart loss is prevented for {enemy_name}.")
                else:
                    enemy_hearts = max(0, enemy_hearts - puppet_hearts_lost)
                    log_entries.append(f"**Puppet Parade** triggers when **{fallen.name}** is defeated. {enemy_name} also loses **{puppet_hearts_lost} {'heart' if puppet_hearts_lost == 1 else 'hearts'}** ({enemy_hearts}/{total_hearts} left).")
                    trigger_mound_ra_on_heart_loss(player_team, enemy_name, log_entries)
                    trigger_mound_ra_on_heart_loss(enemy_team, enemy_name, log_entries)
            if not player_heart_loss_prevented:
                trigger_mound_ra_on_heart_loss(player_team, user_name, log_entries)
                trigger_mound_ra_on_heart_loss(enemy_team, user_name, log_entries)
            enemy_hearts = _apply_morale_boost(enemy_team, enemy_hearts, enemy_name, log_entries, max_hearts=total_hearts)
            trigger_mound_obelisk_on_knockout(player_team, fallen, log_entries)
            trigger_mound_obelisk_on_knockout(enemy_team, fallen, log_entries)
            if player_heart_loss_prevented:
                log_entries.append(f"**{fallen.name}** was defeated, but heart loss is prevented for {user_name}.")
            else:
                heart_label = "heart" if hearts_lost == 1 else "hearts"
                log_entries.append(f"**{fallen.name}** was defeated. {user_name} loses **{hearts_lost} {heart_label}** ({player_hearts}/{total_hearts} left).")
        else:
            for ally in enemy_team:
                handle_spell_on_ally_defeated(ally, fallen, enemy_team, player_team, log_entries)
            handle_support_on_ally_defeated(team_runtime, fallen, enemy_team, player_team, log_entries)
            hearts_lost = 1
            attacker_id = runtime_flag(fallen, "last_hit_attacker_id")
            if attacker_id is not None:
                for attacker in player_team:
                    if id(attacker) == attacker_id:
                        add_battle_stat(attacker, "battle_kills", 1)
                        attacker_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(attacker), [])
                        if any(card.support_card_id == "GALAXY_EXPEDITION" for card in attacker_cards) and fighter_is_galaxy_or_photon(attacker):
                            attacker_runtime = ACTIVE_SUPPORT_RUNTIME_BY_FIGHTER.get(id(attacker))
                            set_support_runtime_flag(attacker_runtime, "galaxy_expedition_shuffle_on_discard", True)
                        if any(card.support_card_id == "INFERNO_FIRE_BLAST" for card in attacker_cards) and team_has_active_special_support(player_team, "INFERNO_FIRE_BLAST", fighter_is_red_eyes):
                            hearts_lost += 1
                        attacker_allies = list(player_team if attacker in player_team else enemy_team)
                        attacker_enemies = list(enemy_team if attacker in player_team else player_team)
                        trigger_soul_exchange_on_defeat(attacker, fallen, attacker_allies, attacker_enemies, log_entries)
                        break
            enemy_heart_loss_prevented = _heart_loss_prevented(enemy_team)
            if not enemy_heart_loss_prevented:
                enemy_hearts = max(0, enemy_hearts - hearts_lost)
            if fighter_had_active_special_support(fallen, "PUPPET_PARADE", fighter_is_gimmick_puppet):
                puppet_hearts_lost = 1
                if team_has_active_special_support(enemy_team, "INFERNO_FIRE_BLAST", fighter_is_red_eyes):
                    puppet_hearts_lost += 1
                if _heart_loss_prevented(player_team):
                    log_entries.append(f"**Puppet Parade** triggers when **{fallen.name}** is defeated, but heart loss is prevented for {user_name}.")
                else:
                    player_hearts = max(0, player_hearts - puppet_hearts_lost)
                    log_entries.append(f"**Puppet Parade** triggers when **{fallen.name}** is defeated. {user_name} also loses **{puppet_hearts_lost} {'heart' if puppet_hearts_lost == 1 else 'hearts'}** ({player_hearts}/{total_hearts} left).")
                    trigger_mound_ra_on_heart_loss(player_team, user_name, log_entries)
                    trigger_mound_ra_on_heart_loss(enemy_team, user_name, log_entries)
            if not enemy_heart_loss_prevented:
                trigger_mound_ra_on_heart_loss(player_team, enemy_name, log_entries)
                trigger_mound_ra_on_heart_loss(enemy_team, enemy_name, log_entries)
            player_hearts = _apply_morale_boost(player_team, player_hearts, user_name, log_entries, max_hearts=total_hearts)
            trigger_mound_obelisk_on_knockout(player_team, fallen, log_entries)
            trigger_mound_obelisk_on_knockout(enemy_team, fallen, log_entries)
            if enemy_heart_loss_prevented:
                log_entries.append(f"**{fallen.name}** was defeated, but heart loss is prevented for {enemy_name}.")
            else:
                heart_label = "heart" if hearts_lost == 1 else "hearts"
                log_entries.append(f"**{fallen.name}** was defeated. {enemy_name} loses **{hearts_lost} {heart_label}** ({enemy_hearts}/{total_hearts} left).")
        if fallen.hp <= 0:
            revive_locked = team_has_active_special_support(enemies, "KASHTIRA_BIG_BANG", fighter_is_kashtira)
            revived_by_spell = False if revive_locked else handle_spell_on_self_defeated(fallen, allies, enemies, log_entries)
            revived_by_support = False if revive_locked else handle_support_on_self_defeated(team_runtime, fallen, log_entries)
            if revive_locked:
                log_entries.append(f"**Kashtira Big Bang** prevents **{fallen.name}** from being revived.")
            if revived_by_spell or revived_by_support:
                resolved_fighter_ids.discard(id(fallen))
                continue
            fallen.hp = 0
            resolved_fighter_ids.add(id(fallen))
        else:
            resolved_fighter_ids.discard(id(fallen))
    return player_hearts, enemy_hearts


def resolve_fallen_fighters(player_team: Sequence[Fighter], enemy_team: Sequence[Fighter], user_name: str, enemy_name: str, player_hearts: int, enemy_hearts: int, log_entries: List[str], resolved_fighter_ids: set[int], total_hearts: Optional[int] = None) -> Tuple[int, int]:
    player_dead_before = {id(fighter) for fighter in player_team if fighter.hp <= 0 and id(fighter) not in resolved_fighter_ids}
    enemy_dead_before = {id(fighter) for fighter in enemy_team if fighter.hp <= 0 and id(fighter) not in resolved_fighter_ids}
    player_hearts_before = player_hearts
    enemy_hearts_before = enemy_hearts
    player_prevented_before = _heart_loss_prevented(player_team)
    enemy_prevented_before = _heart_loss_prevented(enemy_team)

    player_hearts, enemy_hearts = _resolve_fallen_fighters_base(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, resolved_fighter_ids, total_hearts)

    player_summon = maybe_summon_extra_deck_monster(player_team, enemy_team, user_name, log_entries)
    enemy_summon = maybe_summon_extra_deck_monster(enemy_team, player_team, enemy_name, log_entries)

    player_hearts = _apply_missing_extra_deck_knockout_heart_loss(
        summon_result=player_summon,
        unresolved_dead_ids_before=player_dead_before,
        hearts_before=player_hearts_before,
        hearts_after=player_hearts,
        heart_loss_prevented_before=player_prevented_before,
        owner_name=user_name,
        owner_team=player_team,
        opposing_team=enemy_team,
        log_entries=log_entries,
        total_hearts=total_hearts,
    )
    enemy_hearts = _apply_missing_extra_deck_knockout_heart_loss(
        summon_result=enemy_summon,
        unresolved_dead_ids_before=enemy_dead_before,
        hearts_before=enemy_hearts_before,
        hearts_after=enemy_hearts,
        heart_loss_prevented_before=enemy_prevented_before,
        owner_name=enemy_name,
        owner_team=enemy_team,
        opposing_team=player_team,
        log_entries=log_entries,
        total_hearts=total_hearts,
    )
    return player_hearts, enemy_hearts


def build_attack_instances_for_fighter(attacker: Fighter, attacker_cards: Sequence[SupportCardDef]) -> List[Tuple[float, str]]:
    if is_extra_deck_fighter(attacker):
        effect = extra_deck_effect_key(attacker.name)
        if effect in {"blue_eyes_ultimate", "leo_dancer", "frightfur_tiger"} and not runtime_flag(attacker, "extra_deck_multi_used", False):
            set_runtime_flag(attacker, "extra_deck_multi_used", True)
            return [(0.85, ""), (0.85, " (Extra Deck combo)"), (0.65, " (Extra Deck combo)")]
        if effect == "excalibur" and not runtime_flag(attacker, "excalibur_used", False):
            set_runtime_flag(attacker, "excalibur_used", True)
            return [(2.0, " (Heroic Blade)")]
        if effect == "armityle":
            every_other = bool(runtime_flag(attacker, "armityle_idle", False))
            set_runtime_flag(attacker, "armityle_idle", not every_other)
            return [(0.25 if every_other else 2.5, " (Chaos Phantasm)")]
    return _build_attack_instances_for_fighter_base(attacker, attacker_cards)
