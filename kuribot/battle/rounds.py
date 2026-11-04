"""Round start/end support effects and heart penalties."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from kuribot.config import BATTLE_HEARTS, RNG, SUPPORT_DRAW_COUNT
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.cards.support_decks import discard_and_redraw_support_cards
from kuribot.battle.archetypes import (
    fighter_is_crystron, fighter_is_cyber_dragon, fighter_is_dark_magician, fighter_is_dark_world,
    fighter_is_destiny_hero, fighter_is_forbidden_one, fighter_is_infernoid, fighter_is_raviel,
    fighter_is_ryzeal, fighter_is_true_king_or_true_draco,
)
from kuribot.battle.runtime import (
    gain_flat_stat_bonus, gain_percent_stat_bonus, runtime_flag, set_runtime_flag,
)
from kuribot.battle.special_support import (
    set_support_runtime_flag, shuffle_special_support_back_into_deck, support_runtime_flag,
)
from kuribot.battle.stats import current_attack_power
from kuribot.battle.mechanics import (
    _assign_battle_runtime_cards, _heart_loss_prevented, _inferno_fire_blast_extra_hearts,
    _replace_fighter_with_generated_monster, apply_damage_with_runtime,
    apply_special_support_draw_effects, initialize_special_support_effects,
    trigger_attack_and_receive, trigger_mound_ra_on_heart_loss,
)


def initialize_battle_round(fighters: Sequence[Fighter]) -> set[int]:
    alive_fighters = [fighter for fighter in fighters if fighter.alive]
    for fighter in alive_fighters:
        set_runtime_flag(fighter, "solemn_wishes_used_this_round", False)
        set_runtime_flag(fighter, "ojama_trio_used", False)
        set_runtime_flag(fighter, "dian_keto_round_used", False)
        set_runtime_flag(fighter, "chain_energy_used", False)
        set_runtime_flag(fighter, "rain_of_mercy_used", False)
        set_runtime_flag(fighter, "book_of_moon_used", False)
        set_runtime_flag(fighter, "swords_of_concealing_light_used", False)
    return {id(fighter) for fighter in alive_fighters}


def consume_blaze_accelerator_heart_loss(
    team: Sequence[Fighter],
    hearts_remaining: int,
    owner_name: str,
    log_entries: List[str],
    *,
    max_hearts: int = BATTLE_HEARTS,
    source_team: Optional[Sequence[Fighter]] = None,
) -> int:
    trigger = False
    for fighter in team:
        if runtime_flag(fighter, "blaze_accelerator_heart_loss_triggered", False):
            set_runtime_flag(fighter, "blaze_accelerator_heart_loss_triggered", False)
            trigger = True
    if not trigger:
        return hearts_remaining
    if _heart_loss_prevented(team):
        log_entries.append(f"**Blaze Accelerator** would make {owner_name} lose a heart, but heart loss is prevented.")
        return hearts_remaining
    hearts_lost = 1
    if source_team is not None:
        hearts_lost += _inferno_fire_blast_extra_hearts(source_team, hearts_lost)
    updated = max(0, int(hearts_remaining) - hearts_lost)
    if hearts_lost > 1:
        log_entries.append(f"**Blaze Accelerator** makes {owner_name} lose **1 heart**, and **Inferno Fire Blast** adds **1 extra heart** ({updated}/{max_hearts} left).")
    else:
        log_entries.append(f"**Blaze Accelerator** makes {owner_name} lose **1 heart** ({updated}/{max_hearts} left).")
    trigger_mound_ra_on_heart_loss(team, owner_name, log_entries)
    if source_team is not None:
        trigger_mound_ra_on_heart_loss(source_team, owner_name, log_entries)
    return updated


def consume_blaze_accelerator_heart_losses(
    player_team: Sequence[Fighter],
    enemy_team: Sequence[Fighter],
    player_hearts: int,
    enemy_hearts: int,
    player_name: str,
    enemy_name: str,
    log_entries: List[str],
    *,
    max_hearts: int = BATTLE_HEARTS,
) -> Tuple[int, int]:
    previous_player_hearts = int(player_hearts)
    player_hearts = consume_blaze_accelerator_heart_loss(
        player_team,
        player_hearts,
        player_name,
        log_entries,
        max_hearts=max_hearts,
        source_team=enemy_team,
    )
    player_hearts_lost = max(0, previous_player_hearts - int(player_hearts))
    if player_hearts_lost > 0:
        enemy_hearts = trigger_attack_and_receive(
            player_team,
            enemy_team,
            player_hearts_lost,
            enemy_hearts,
            enemy_name,
            log_entries,
            max_hearts=max_hearts,
        )

    previous_enemy_hearts = int(enemy_hearts)
    enemy_hearts = consume_blaze_accelerator_heart_loss(
        enemy_team,
        enemy_hearts,
        enemy_name,
        log_entries,
        max_hearts=max_hearts,
        source_team=player_team,
    )
    enemy_hearts_lost = max(0, previous_enemy_hearts - int(enemy_hearts))
    if enemy_hearts_lost > 0:
        player_hearts = trigger_attack_and_receive(
            enemy_team,
            player_team,
            enemy_hearts_lost,
            player_hearts,
            player_name,
            log_entries,
            max_hearts=max_hearts,
        )
    return player_hearts, enemy_hearts


def apply_support_round_end_heart_penalty(
    hearts_remaining: int,
    hearts_lost: int,
    max_hearts: int,
    acting_team: Sequence[Fighter],
    defending_team: Sequence[Fighter],
    owner_name: str,
    log_entries: List[str],
) -> int:
    hearts_lost = max(0, int(hearts_lost))
    if hearts_lost <= 0:
        return hearts_remaining
    if _heart_loss_prevented(acting_team):
        log_entries.append(f"Heart loss is prevented for {owner_name}.")
        return hearts_remaining
    updated_hearts = max(0, int(hearts_remaining) - hearts_lost)
    heart_label = "heart" if hearts_lost == 1 else "hearts"
    log_entries.append(f"{owner_name} loses **{hearts_lost} {heart_label}** from a support card effect ({updated_hearts}/{max_hearts} left).")
    trigger_mound_ra_on_heart_loss(acting_team, owner_name, log_entries)
    trigger_mound_ra_on_heart_loss(defending_team, owner_name, log_entries)
    return updated_hearts


def resolve_support_round_end(
    runtime: Optional[SupportDeckRuntime],
    team: Sequence[Fighter],
    enemy_team: Sequence[Fighter],
    owner_name: str,
    log_entries: List[str],
    enemy_runtime: Optional[SupportDeckRuntime] = None,
) -> int:
    if runtime is None or not runtime.active_cards:
        return 0
    runtime.rounds_remaining -= 1
    if runtime.rounds_remaining > 0:
        return 0
    discarded_cards = list(runtime.active_cards)
    if any(card.support_card_id == "CLOCK_TOWER_PRISON" for card in discarded_cards):
        victims = [fighter for fighter in team if fighter.alive and fighter_is_destiny_hero(fighter)]
        if victims:
            destroyed = RNG.choice(victims)
            destroyed.hp = 0
            allies = [ally for ally in team if ally.alive and ally is not destroyed]
            if allies:
                chosen_ally = RNG.choice(allies)
                gained = gain_flat_stat_bonus(chosen_ally, "ATK", max(1, int(round(destroyed.atk * 0.50))))
                log_entries.append(f"**Clock Tower Prison** is discarded. **{destroyed.name}** is destroyed and **{chosen_ally.name}** gains **+{gained} ATK**.")
            else:
                log_entries.append(f"**Clock Tower Prison** is discarded. **{destroyed.name}** is destroyed.")
    if any(card.support_card_id == "CRYSTRON_INCLUSION" for card in discarded_cards):
        victims = [fighter for fighter in team if fighter.alive and fighter_is_crystron(fighter)]
        if victims:
            destroyed = RNG.choice(victims)
            destroyed.hp = 0
            damage = max(1, int(round(destroyed.max_hp * 0.25)))
            damaged = []
            for enemy in enemy_team:
                if not enemy.alive:
                    continue
                dealt = apply_damage_with_runtime(enemy, damage, destroyed, log_entries, "Crystron Inclusion")
                if dealt > 0:
                    damaged.append(f"{enemy.name} (-{dealt} HP)")
            if damaged:
                log_entries.append(f"**Crystron Inclusion** is discarded. **{destroyed.name}** is destroyed and all enemies take damage: {', '.join(damaged)}.")
            else:
                log_entries.append(f"**Crystron Inclusion** is discarded. **{destroyed.name}** is destroyed.")
    if any(card.support_card_id == "DARK_WORLD_DEALINGS" for card in discarded_cards):
        revived = []
        for fighter in team:
            if fighter.hp <= 0 and fighter_is_dark_world(fighter):
                fighter.hp = max(1, int(round(fighter.max_hp * 0.50)))
                revived.append(f"{fighter.name} ({fighter.hp} HP)")
        if revived:
            log_entries.append(f"**Dark World Dealings** is discarded. Dark World monsters revive with **50% HP**: {', '.join(revived)}.")
    if any(card.support_card_id == "LABRYNTH_BARRAGE" for card in discarded_cards) and not support_runtime_flag(runtime, "labrynth_barrage_shuffled_once", False):
        if shuffle_special_support_back_into_deck(runtime, "LABRYNTH_BARRAGE"):
            set_support_runtime_flag(runtime, "labrynth_barrage_shuffled_once", True)
            log_entries.append("**Labrynth Barrage** is discarded and shuffled back into the deck.")
    if any(card.support_card_id == "ORCUSTRATED_BABEL" for card in discarded_cards) and not support_runtime_flag(runtime, "orcustrated_babel_shuffled_once", False):
        if shuffle_special_support_back_into_deck(runtime, "ORCUSTRATED_BABEL"):
            set_support_runtime_flag(runtime, "orcustrated_babel_shuffled_once", True)
            log_entries.append("**Orcustrated Babel** is discarded and shuffled back into the deck.")
    if any(card.support_card_id == "DRAGONIC_DIAGRAM" for card in discarded_cards):
        victims = [fighter for fighter in team if fighter.alive and fighter_is_true_king_or_true_draco(fighter)]
        if victims:
            destroyed = RNG.choice(victims)
            destroyed.hp = 0
            log_entries.append(f"**Dragonic Diagram** is discarded. **{destroyed.name}** is destroyed.")
    if any(card.support_card_id == "FUTURE_FUSION" for card in discarded_cards):
        empowered = []
        enemy_choices = [enemy for enemy in enemy_team if enemy.alive]
        for fighter in team:
            if not fighter.alive or not fighter_is_cyber_dragon(fighter) or not enemy_choices:
                continue
            chosen_enemy = RNG.choice(enemy_choices)
            gained = gain_flat_stat_bonus(fighter, "ATK", max(1, current_attack_power(chosen_enemy)))
            empowered.append(f"{fighter.name} (+{gained} ATK from {chosen_enemy.name})")
        if empowered:
            log_entries.append(f"**Future Fusion** is discarded. Cyber Dragon monsters gain enemy ATK: {', '.join(empowered)}.")
    hearts_lost = 0
    if any(card.support_card_id == "POWER_BOND" for card in discarded_cards):
        target_name = str(support_runtime_flag(runtime, "power_bond_target_name", "")).strip()
        target_monster_id = str(support_runtime_flag(runtime, "power_bond_target_monster_id", "")).strip()
        victims = [fighter for fighter in team if fighter.alive and fighter_is_cyber_dragon(fighter)]
        if target_name or target_monster_id:
            matched_victims = [
                fighter for fighter in victims
                if (target_monster_id and fighter.monster_id == target_monster_id) or (target_name and fighter.name == target_name)
            ]
            if matched_victims:
                victims = matched_victims
        if victims:
            destroyed = RNG.choice(victims)
            destroyed.hp = 0
            if _heart_loss_prevented(team):
                log_entries.append(f"**Power Bond** is discarded. **{destroyed.name}** is destroyed, but heart loss is prevented for {owner_name}.")
            else:
                hearts_lost = 1
                log_entries.append(f"**Power Bond** is discarded. **{destroyed.name}** is destroyed and {owner_name} loses **1 heart**.")
        set_support_runtime_flag(runtime, "power_bond_target_monster_id", "")
        set_support_runtime_flag(runtime, "power_bond_target_name", "")
    if any(card.support_card_id == "GALAXY_EXPEDITION" for card in discarded_cards) and support_runtime_flag(runtime, "galaxy_expedition_shuffle_on_discard", False):
        if shuffle_special_support_back_into_deck(runtime, "GALAXY_EXPEDITION"):
            log_entries.append("**Galaxy Expedition** is discarded after a Galaxy/Photon knockout and shuffled back into the deck.")
        set_support_runtime_flag(runtime, "galaxy_expedition_shuffle_on_discard", False)
    if any(card.support_card_id == "RYZEAL_CROSS" for card in discarded_cards) and not support_runtime_flag(runtime, "ryzeal_cross_shuffled_once", False):
        if shuffle_special_support_back_into_deck(runtime, "RYZEAL_CROSS"):
            set_support_runtime_flag(runtime, "ryzeal_cross_shuffled_once", True)
            log_entries.append("**Ryzeal Cross** is discarded and shuffled back into the deck.")
    if any(card.support_card_id == "RYZEAL_PLUGIN" for card in discarded_cards):
        granted=[]
        for fighter in team:
            if fighter.alive and fighter_is_ryzeal(fighter):
                set_runtime_flag(fighter, "protection_turns", max(1, int(runtime_flag(fighter, "protection_turns", 0))))
                granted.append(fighter.name)
        if granted:
            log_entries.append(f"**Ryzeal Plugin** is discarded. Ryzeal monsters gain **PROTECTION**: {', '.join(granted)}.")
    if any(card.support_card_id == "PRIMEVAL_PLANET_PERLEREINO" for card in discarded_cards):
        pool=[fighter for fighter in [*team,*enemy_team] if fighter.alive]
        if pool:
            chosen=RNG.choice(pool)
            dealt=apply_damage_with_runtime(chosen, max(1,int(round(chosen.max_hp*0.50))), None, log_entries, "Primeval Planet Perlereino")
            log_entries.append(f"**Primeval Planet Perlereino** is discarded. **{chosen.name}** takes **{dealt}** damage.")
    if any(card.support_card_id == "MILLENNIUM_ANKH" for card in discarded_cards):
        victims=[fighter for fighter in team if fighter.alive and fighter_is_forbidden_one(fighter)]
        if len(victims) >= 3:
            for victim in victims[:3]:
                victim.hp = 0
            replacement = victims[0]
            if _replace_fighter_with_generated_monster(replacement, ["exodia"], log_entries, randomize_identity=True, bonus_multiplier=1.0, choose_random_match=True):
                log_entries.append(f"**Millennium Ankh** fuses three Forbidden One monsters into **{replacement.name}**.")
    if any(card.support_card_id == "BEWARE_THE_WHITE_FOREST" for card in discarded_cards) and not support_runtime_flag(runtime, "beware_the_white_forest_shuffled_once", False):
        if shuffle_special_support_back_into_deck(runtime, "BEWARE_THE_WHITE_FOREST"):
            set_support_runtime_flag(runtime, "beware_the_white_forest_shuffled_once", True)
            log_entries.append("**Beware the White Forest** is discarded and shuffled back into the deck.")
    if any(card.support_card_id == "TALES_OF_THE_WHITE_FOREST" for card in discarded_cards) and not support_runtime_flag(runtime, "tales_of_the_white_forest_shuffled_once", False):
        if shuffle_special_support_back_into_deck(runtime, "TALES_OF_THE_WHITE_FOREST"):
            set_support_runtime_flag(runtime, "tales_of_the_white_forest_shuffled_once", True)
            log_entries.append("**Tales of the White Forest** is discarded and shuffled back into the deck.")
    if any(card.support_card_id == "YUM_YUM_YUMMYS" for card in discarded_cards):
        set_support_runtime_flag(runtime, "restore_heart_on_discard", True)
    if any(card.support_card_id == "FALLEN_PARADISE" for card in discarded_cards) and any(f.alive and fighter_is_raviel(f) for f in team):
        set_support_runtime_flag(runtime, "extra_draw_count", 1)
    if any(card.support_card_id == "VOID_PURIFICATION" for card in discarded_cards):
        empowered = []
        for fighter in team:
            if fighter.alive and fighter_is_infernoid(fighter):
                gain_percent_stat_bonus(fighter, "ATK", 50)
                empowered.append(fighter.name)
        if empowered:
            set_support_runtime_flag(runtime, "void_purification_destroy_after_round", True)
            log_entries.append(f"**Void Purification** is discarded. Infernoid monsters gain **+50% ATK** for 1 round: {', '.join(empowered)}.")
    if any(card.support_card_id == "SOUL_SERVANT" for card in discarded_cards) and not support_runtime_flag(runtime, "soul_servant_shuffled_once", False) and any(f.alive and fighter_is_dark_magician(f) for f in team):
        for card in discarded_cards:
            remaining_count = max(0, len(runtime.shuffled_cards) - runtime.draw_index)
            insert_at = runtime.draw_index + (RNG.randint(0, remaining_count) if remaining_count else 0)
            runtime.shuffled_cards.insert(insert_at, card)
        set_support_runtime_flag(runtime, "soul_servant_shuffled_once", True)
        log_entries.append("**Soul Servant** shuffles itself and its companion support cards back into the deck.")
    new_cards = discard_and_redraw_support_cards(runtime)
    if support_runtime_flag(runtime, "extra_draw_count", 0) and runtime.has_cards_remaining():
        extra = runtime.shuffled_cards[runtime.draw_index:runtime.draw_index + int(support_runtime_flag(runtime, "extra_draw_count", 0))]
        runtime.active_cards.extend(extra)
        runtime.draw_index += len(extra)
        set_support_runtime_flag(runtime, "extra_draw_count", 0)
    _assign_battle_runtime_cards(team, runtime, enemy_team, enemy_runtime)
    apply_special_support_draw_effects(team, enemy_team, runtime, enemy_runtime, owner_name, "the opponent", log_entries)
    _assign_battle_runtime_cards(team, runtime, enemy_team, enemy_runtime)
    initialize_special_support_effects(team, enemy_team, runtime, log_entries)
    if new_cards:
        log_entries.append(f"{owner_name}'s support deck refreshed: " + ', '.join(card.name for card in new_cards[:SUPPORT_DRAW_COUNT]) + ".")
    else:
        log_entries.append(f"{owner_name}'s Support Deck has no more cards to draw.")
    return hearts_lost


def apply_empty_support_deck_round_start_penalty(
    player_runtime: Optional[SupportDeckRuntime],
    enemy_runtime: Optional[SupportDeckRuntime],
    player_name: str,
    enemy_name: str,
    player_hearts: int,
    enemy_hearts: int,
    max_hearts: int,
    log_entries: List[str],
    player_team: Optional[Sequence[Fighter]] = None,
    enemy_team: Optional[Sequence[Fighter]] = None,
) -> Tuple[int, int]:
    if player_runtime is not None and not player_runtime.active_cards and not player_runtime.has_cards_remaining():
        if player_team is not None and _heart_loss_prevented(player_team):
            log_entries.append(f"{player_name} starts the round with **0** cards left in their support deck, but heart loss is prevented.")
        else:
            player_hearts = max(0, player_hearts - 1)
            log_entries.append(f"{player_name} starts the round with **0** cards left in their support deck and loses **1 heart** ({player_hearts}/{max_hearts} left).")
    if enemy_runtime is not None and not enemy_runtime.active_cards and not enemy_runtime.has_cards_remaining():
        if enemy_team is not None and _heart_loss_prevented(enemy_team):
            log_entries.append(f"{enemy_name} starts the round with **0** cards left in their support deck, but heart loss is prevented.")
        else:
            enemy_hearts = max(0, enemy_hearts - 1)
            log_entries.append(f"{enemy_name} starts the round with **0** cards left in their support deck and loses **1 heart** ({enemy_hearts}/{max_hearts} left).")
    return player_hearts, enemy_hearts
