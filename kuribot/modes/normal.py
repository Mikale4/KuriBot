"""Running a normal Dueling Grounds battle."""

from __future__ import annotations

import asyncio
import random
import time
from typing import List, Optional

import discord

from kuribot.config import (
    BATTLE_TURN_DELAY, CAMPAIGN_BATTLE_HEARTS, DUELING_GROUNDS_BATTLE_HEARTS, SPEED_GAUGE_MAX,
    TEAM_SIZE,
)
from kuribot.settings import battle_refresh_status_text, should_refresh_battle
from kuribot.database import db
from kuribot.monsters import format_monster_types, stable_seed_for_name
from kuribot.cards.support_cards import award_support_deck_battle_exp
from kuribot.cards.support_decks import active_support_text, build_support_deck_runtime
from kuribot.battle.runtime import (
    _clear_runtime_cards, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, random_alive, runtime_flag, team_alive,
)
from kuribot.battle.stats import support_card_stat_bonus
from kuribot.battle.mechanics import (
    _assign_battle_runtime_cards, _assign_runtime_cards, action_target_override,
    advance_speed_gauges, any_ready_fighter, apply_all_pending_battle_start_heart_losses,
    apply_damage_with_runtime, apply_post_turn_status_effects, attempt_ultimate_crystal_magic_save,
    build_battle_stats_payload, exact_time_to_next_turn, handle_spell_on_critical_taken,
    handle_spell_on_damage_dealt, handle_spell_on_damage_taken, handle_spell_on_turn_end,
    handle_spell_on_turn_start, handle_spell_when_targeted, handle_support_when_targeted,
    initialize_battle_start_effects, on_fighter_attacked, prepare_fighter_equip_spell,
    process_bombs_for_all_fighters, resolve_attack_damage, resolve_battle_outcome,
    resolve_round_end_spell_effects, resolve_special_support_round_effects,
    resolve_start_of_turn_status, spell_of_pain_bearer, SPELL_OF_PAIN_REDIRECT_RATIO,
    trigger_precog_strike, trigger_warning_point_on_dodge, trigger_yummy_surprise_on_dodge,
)
from kuribot.battle.knockouts import (
    build_attack_instances_for_fighter, resolve_fallen_fighters,
    should_grant_neutron_blast_extra_turn,
)
from kuribot.battle.rounds import (
    apply_empty_support_deck_round_start_penalty, apply_support_round_end_heart_penalty,
    consume_blaze_accelerator_heart_losses, initialize_battle_round, resolve_support_round_end,
)
from kuribot.battle.setup import _active_team_fighters_for_user, append_necrovalley_suppression_logs
from kuribot.portraits import get_duelist_portrait_path
from kuribot.opponents import (
    assign_unique_generated_equip_spells, remember_duelist_loss_against_player,
)
from kuribot.dueling_grounds import (
    build_dueling_grounds_enemy_support_runtime, build_dueling_grounds_enemy_team,
    choose_random_dueling_grounds_duelist, choose_specific_dueling_grounds_duelist,
    dueling_grounds_reward, make_dueling_grounds_duelist_portrait_file,
)
from kuribot.campaign import campaign_battle_hearts_remaining
from kuribot.ranked import make_dueling_grounds_result_embed
from kuribot.quests import maybe_complete_player_quest
from kuribot.render.battle import render_battle
from kuribot.ui.common import (
    _reset_discord_file_handles, render_discord_file_async, safe_edit_interaction_message,
    safe_send_interaction_message, safe_send_or_channel_message,
)
from kuribot.ui.battle_results import BattleResultView
from kuribot.modes.common import single_battle_per_player


@single_battle_per_player("normal")
async def start_dueling_grounds_normal_battle(interaction: discord.Interaction, owner_id: int, user_name: str, forced_duelist_key: Optional[str] = None):
    player_team = _active_team_fighters_for_user(owner_id, "lobby")
    if len(player_team) < TEAM_SIZE:
        await safe_edit_interaction_message(
            interaction,
            content=f"You need an active Lobby team with {TEAM_SIZE} monsters before starting a Dueling Grounds duel.",
            attachments=[],
            embed=None,
            view=None,
        )
        return

    if forced_duelist_key:
        duelist_key, duelist = choose_specific_dueling_grounds_duelist(forced_duelist_key)
    else:
        duelist_key, duelist = choose_random_dueling_grounds_duelist()
    enemy_team, duelist = build_dueling_grounds_enemy_team(duelist_key)
    enemy_name = duelist.get("name", duelist_key)
    player_deck_row = db.get_active_support_deck(owner_id, "lobby")
    player_runtime = build_support_deck_runtime(player_deck_row, user_id=owner_id)
    enemy_runtime, enemy_support_label = build_dueling_grounds_enemy_support_runtime(enemy_name, enemy_team)
    _assign_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
    for fighter in player_team:
        prepare_fighter_equip_spell(fighter, owner_id)
    dueling_memory_scope = "dueling_grounds"
    dueling_memory_key = str(duelist_key)
    dueling_adaptation_memory = db.get_duelist_adaptation_memory(dueling_memory_scope, dueling_memory_key, owner_id)
    generated_enemy_spells = assign_unique_generated_equip_spells(
        enemy_team,
        random.Random(stable_seed_for_name(f"dueling_grounds_equip:{duelist_key}:{int(time.time() // 60)}")),
        support_cards=(enemy_runtime.shuffled_cards if enemy_runtime else None),
        label_name=enemy_name,
        target_team=player_team,
        adaptation_memory=dueling_adaptation_memory,
    )
    battle_theme_name = db.get_ui_theme(owner_id)
    battle_speed = db.get_battle_speed(owner_id)
    player_avatar_path = db.get_avatar_path(owner_id)
    enemy_avatar_path = str(get_duelist_portrait_path(duelist_key, enemy_name) or "")

    for fighter in [*player_team, *enemy_team]:
        fighter.speed_gauge = 0.0

    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)

    battle_title = f"{user_name} vs {enemy_name}"
    mode_label = "Dueling Grounds • Normal"
    player_hearts = DUELING_GROUNDS_BATTLE_HEARTS
    enemy_hearts = DUELING_GROUNDS_BATTLE_HEARTS
    log_entries: List[str] = [
        duelist.get("pre", "Let's duel!"),
        f"{user_name} sends out {', '.join(f'{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]' for f in player_team)}.",
        f"{enemy_name} sends out {', '.join(f'{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]' for f in enemy_team)}.",
        f"{user_name} support deck: {active_support_text(player_runtime)}." if player_runtime else f"{user_name} is battling without a Support Deck.",
        f"{enemy_name} support deck: {active_support_text(enemy_runtime)}." if enemy_runtime else f"{enemy_name} enters with {enemy_support_label}.",
    ]
    if generated_enemy_spells:
        log_entries.append(f"{enemy_name} generated equip spells: {', '.join(str(runtime_flag(fighter, 'equip_spell_name')) for fighter in enemy_team if runtime_flag(fighter, 'equip_spell_name'))}.")
    initialize_battle_start_effects(player_team, owner_id, enemy_team, None, log_entries)
    append_necrovalley_suppression_logs(user_name, enemy_name, player_team, player_runtime, enemy_team, enemy_runtime, log_entries)
    player_hearts, enemy_hearts = apply_all_pending_battle_start_heart_losses(
        player_team, enemy_team, player_hearts, enemy_hearts, user_name, enemy_name, log_entries, max_hearts=DUELING_GROUNDS_BATTLE_HEARTS
    )

    message_content = battle_refresh_status_text(mode_label, enemy_name, battle_speed)
    if battle_speed != "instant":
        battle_file = await render_discord_file_async(
            render_battle,
            "dueling_grounds_battle.png",
            user_name,
            enemy_name,
            player_team,
            enemy_team,
            log_entries,
            tick_info="Battle start",
            round_info="Round 1",
            player_support_runtime=player_runtime,
            enemy_support_runtime=enemy_runtime,
            player_hearts=campaign_battle_hearts_remaining(player_hearts, DUELING_GROUNDS_BATTLE_HEARTS),
            enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, DUELING_GROUNDS_BATTLE_HEARTS),
            max_hearts=DUELING_GROUNDS_BATTLE_HEARTS,
            player_avatar_path=player_avatar_path,
            enemy_avatar_path=enemy_avatar_path,
            theme_name=battle_theme_name,
        )
    else:
        battle_file = None

    try:
        await safe_edit_interaction_message(
            interaction,
            content=message_content,
            attachments=[battle_file] if battle_file is not None else [],
            embed=None,
            view=None,
        )
        try:
            message = await interaction.original_response()
        except (discord.NotFound, discord.HTTPException):
            message = interaction.message
    except Exception:
        if battle_file is not None:
            message = await safe_send_interaction_message(
                interaction,
                content=message_content,
                file=battle_file,
                ephemeral=False,
            )
        else:
            message = await safe_send_interaction_message(interaction, content=message_content, ephemeral=False)

    rounds = 1
    completed_rounds = 0
    fighters_pending_in_round = initialize_battle_round([*player_team, *enemy_team])
    defeated_fighter_ids: set[int] = set()
    result_text = ""

    try:
        while player_hearts > 0 and enemy_hearts > 0 and team_alive(player_team) and team_alive(enemy_team) and rounds <= 200:
            active = any_ready_fighter((player_team, enemy_team))
            if active is None:
                elapsed = exact_time_to_next_turn((player_team, enemy_team))
                active, _ = advance_speed_gauges((player_team, enemy_team), elapsed)
                if active is None:
                    continue

            acting_team = player_team if active in player_team else enemy_team
            defending_team = enemy_team if acting_team is player_team else player_team
            active_name = user_name if acting_team is player_team else enemy_name
            attacker_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(active), [])

            atk_bonus = support_card_stat_bonus(active, attacker_cards, "ATK")
            def_bonus = support_card_stat_bonus(active, attacker_cards, "DEF")
            spd_bonus = support_card_stat_bonus(active, attacker_cards, "SPD")
            log_entries.append(f"Turn {rounds}: **{active.name}** ({active_name}) takes the turn.")
            if atk_bonus:
                log_entries.append(f"{active.name} gains **+{atk_bonus} ATK** from Support Cards.")
            if def_bonus:
                log_entries.append(f"{active.name} gains **+{def_bonus} DEF** from Support Cards.")
            if spd_bonus:
                log_entries.append(f"{active.name} gains **+{spd_bonus} SPD** from Support Cards.")

            process_bombs_for_all_fighters([*acting_team, *defending_team], log_entries)
            handle_spell_on_turn_start(active, acting_team, defending_team, log_entries)
            player_hearts, enemy_hearts = resolve_fallen_fighters(
                player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids,
            )
            can_act = active.alive and resolve_start_of_turn_status(active, acting_team, defending_team, log_entries)
            target = None
            if can_act:
                target_pool, status_target_log = action_target_override(active, acting_team, defending_team)
                if status_target_log:
                    log_entries.append(status_target_log)
                target = random_alive(list(target_pool))
                if target is None:
                    break
                log_entries.append(f"{active.name} targets **{target.name}**.")

            if can_act and target is not None and active.alive:
                trigger_precog_strike(target, active, defending_team, acting_team, log_entries)
                if not active.alive or not target.alive:
                    player_hearts, enemy_hearts = resolve_fallen_fighters(
                        player_team,
                        enemy_team,
                        user_name,
                        enemy_name,
                        player_hearts,
                        enemy_hearts,
                        log_entries,
                        defeated_fighter_ids,
                    )
                defender_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
                # Same helper as every other mode (normal mode had an inline copy, so Extra
                # Deck combos like Excalibur/Armityle never triggered here).
                # A PRECOG counter-strike can knock out the attacker or the target first. Then there is
                # no attack, but the rest of the turn (turn-end spells, status ticks, speed gauge reset,
                # heart checks) still happens - arena battles already worked this way.
                attack_instances = build_attack_instances_for_fighter(active, attacker_cards) if active.alive and target.alive else []
                for hit_multiplier, hit_suffix in attack_instances:
                    if not active.alive or not target.alive:
                        break
                    target, incoming_multiplier, attack_canceled = handle_spell_when_targeted(target, active, defending_team, acting_team, log_entries)
                    if attack_canceled or not active.alive or not target.alive:
                        continue
                    defender_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
                    attack_result = resolve_attack_damage(active, target, attacker_cards, defender_cards, hit_multiplier=hit_multiplier)
                    if not attack_result["hit"]:
                        trigger_yummy_surprise_on_dodge(target, active, log_entries)
                        trigger_warning_point_on_dodge(target, active, log_entries)
                        log_entries.append(f"{active.name} attacks {target.name}, but misses{hit_suffix}.")
                        continue
                    damage = max(1, int(round(int(attack_result["damage"]) * incoming_multiplier)))
                    relation = str(attack_result["relation"])
                    pain_bearer = spell_of_pain_bearer(target, defending_team)
                    if pain_bearer is not None:
                        redirected = max(1, int(round(damage * SPELL_OF_PAIN_REDIRECT_RATIO)))
                        damage -= redirected
                        dealt_redirect = apply_damage_with_runtime(pain_bearer, redirected, active, log_entries, "Spell of Pain")
                        if dealt_redirect > 0:
                            log_entries.append(f"**Spell of Pain** redirects **{dealt_redirect}** damage to **{pain_bearer.name}**.")
                    if handle_support_when_targeted(target, active, log_entries):
                        dealt = 0
                    else:
                        dealt = apply_damage_with_runtime(target, damage, active, log_entries, "attack")
                    on_fighter_attacked(target, active, log_entries)
                    handle_spell_on_damage_dealt(active, target, dealt, acting_team, defending_team, log_entries)
                    handle_spell_on_damage_taken(target, active, dealt, defending_team, acting_team, log_entries)
                    if attack_result["crit"] and dealt > 0:
                        handle_spell_on_critical_taken(target, active, defending_team, acting_team, log_entries)
                    if relation == 'advantage':
                        relation_text = ' (type advantage)'
                    elif relation == 'disadvantage':
                        relation_text = ' (type resisted)'
                    else:
                        relation_text = ''
                    crit_text = " **Critical hit!**" if attack_result["crit"] else ""
                    log_entries.append(f"{active.name} hits {target.name} for **{dealt}** damage{relation_text}{hit_suffix}.{crit_text}")
            player_hearts, enemy_hearts = resolve_fallen_fighters(
                player_team,
                enemy_team,
                user_name,
                enemy_name,
                player_hearts,
                enemy_hearts,
                log_entries,
                defeated_fighter_ids,
            )

            handle_spell_on_turn_end(active, acting_team, defending_team, log_entries)
            apply_post_turn_status_effects([*player_team, *enemy_team], log_entries)
            player_hearts, enemy_hearts = resolve_fallen_fighters(
                player_team,
                enemy_team,
                user_name,
                enemy_name,
                player_hearts,
                enemy_hearts,
                log_entries,
                defeated_fighter_ids,
            )

            if active.alive and should_grant_neutron_blast_extra_turn(active, attacker_cards):
                active.speed_gauge = SPEED_GAUGE_MAX
                log_entries.append(f"**Neutron Blast** lets **{active.name}** act again!")
            else:
                active.speed_gauge = 0.0

            fighters_pending_in_round.discard(id(active))
            fighters_pending_in_round = {fighter_id for fighter_id in fighters_pending_in_round if any(id(fighter) == fighter_id and fighter.alive for fighter in [*player_team, *enemy_team])}
            player_hearts = attempt_ultimate_crystal_magic_save(player_team, player_runtime, player_hearts, user_name, log_entries)
            enemy_hearts = attempt_ultimate_crystal_magic_save(enemy_team, enemy_runtime, enemy_hearts, enemy_name, log_entries)
            player_hearts, enemy_hearts = consume_blaze_accelerator_heart_losses(
                player_team,
                enemy_team,
                player_hearts,
                enemy_hearts,
                user_name,
                enemy_name,
                log_entries,
            )
            if player_hearts <= 0 or enemy_hearts <= 0:
                break

            if not fighters_pending_in_round and team_alive(player_team) and team_alive(enemy_team):
                completed_rounds += 1
                log_entries.append(f"Round {completed_rounds} complete.")
                resolve_special_support_round_effects(player_team, enemy_team, player_runtime, enemy_runtime, log_entries)
                player_hearts = apply_support_round_end_heart_penalty(
                    player_hearts,
                    resolve_support_round_end(player_runtime, player_team, enemy_team, user_name, log_entries, enemy_runtime),
                    CAMPAIGN_BATTLE_HEARTS,
                    player_team,
                    enemy_team,
                    user_name,
                    log_entries,
                )
                enemy_hearts = apply_support_round_end_heart_penalty(
                    enemy_hearts,
                    resolve_support_round_end(enemy_runtime, enemy_team, player_team, enemy_name, log_entries, player_runtime),
                    CAMPAIGN_BATTLE_HEARTS,
                    enemy_team,
                    player_team,
                    enemy_name,
                    log_entries,
                )
                player_hearts, enemy_hearts = apply_empty_support_deck_round_start_penalty(
                    player_runtime,
                    enemy_runtime,
                    user_name,
                    enemy_name,
                    player_hearts,
                    enemy_hearts,
                    DUELING_GROUNDS_BATTLE_HEARTS,
                    log_entries,
                    player_team,
                    enemy_team,
                )
                resolve_round_end_spell_effects([*player_team, *enemy_team], log_entries)
                fighters_pending_in_round = initialize_battle_round([*player_team, *enemy_team])

            player_hearts = attempt_ultimate_crystal_magic_save(player_team, player_runtime, player_hearts, user_name, log_entries)
            enemy_hearts = attempt_ultimate_crystal_magic_save(enemy_team, enemy_runtime, enemy_hearts, enemy_name, log_entries)
            player_hearts, enemy_hearts = consume_blaze_accelerator_heart_losses(
                player_team,
                enemy_team,
                player_hearts,
                enemy_hearts,
                user_name,
                enemy_name,
                log_entries,
            )
            battle_over = player_hearts <= 0 or enemy_hearts <= 0 or not team_alive(player_team) or not team_alive(enemy_team)
            should_refresh = should_refresh_battle(battle_speed, rounds, not fighters_pending_in_round, battle_over)
            if should_refresh:
                battle_file = await render_discord_file_async(render_battle, "dueling_grounds_battle.png", user_name, enemy_name, player_team, enemy_team, log_entries[-12:], active_fighter=active, target_fighter=target, tick_info=f"Turn {rounds}", round_info=f"Round {completed_rounds + 1}", player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, DUELING_GROUNDS_BATTLE_HEARTS), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, DUELING_GROUNDS_BATTLE_HEARTS), max_hearts=DUELING_GROUNDS_BATTLE_HEARTS, player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path, theme_name=battle_theme_name)
                status_text = battle_refresh_status_text(mode_label, enemy_name, battle_speed, rounds)
                try:
                    _reset_discord_file_handles([battle_file])
                    await message.edit(content=status_text, attachments=[battle_file], view=None)
                except (discord.NotFound, discord.HTTPException, TypeError, ValueError, AttributeError):
                    replacement = await safe_send_or_channel_message(interaction, content=status_text, file=battle_file, ephemeral=False)
                    if replacement is not None:
                        message = replacement
                await asyncio.sleep(BATTLE_TURN_DELAY)

            rounds += 1
            if rounds % 4 == 0:
                await asyncio.sleep(0)  # let other players' commands run during long/instant battles

        player_won, enemy_won = resolve_battle_outcome(player_team, enemy_team, player_hearts, enemy_hearts)
        support_cards_used, support_exp_gained, support_stage_ups = award_support_deck_battle_exp(owner_id, player_deck_row, player_won)
        reward_text = ""
        result_speech = ""
        if player_won:
            remember_duelist_loss_against_player(dueling_memory_scope, dueling_memory_key, owner_id, player_team)
            db.add_win(owner_id)
            gold, tickets = dueling_grounds_reward()
            db.add_gold(owner_id, gold)
            if tickets:
                db.add_tickets(owner_id, tickets)
            result_speech = duelist.get("loss", "You won the duel.")
            reward_bits = [f"+{gold} gold"]
            if tickets:
                reward_bits.append(f"+{tickets} ticket")
            reward_text = ", ".join(reward_bits)
            result_text = f"Victory! {result_speech} Rewards: {reward_text}."
            quest_reward_text = maybe_complete_player_quest(owner_id, "normal", duelist_key)
            if quest_reward_text:
                result_text += quest_reward_text
            if support_cards_used:
                result_text += f" Support cards equipped: {support_cards_used}."
                if support_stage_ups:
                    result_text += ""
        elif enemy_won:
            db.add_loss(owner_id)
            result_speech = duelist.get("win", "Your opponent won the duel.")
            result_text = f"Defeat. {result_speech}"
            if support_cards_used:
                result_text += f" Support cards equipped: {support_cards_used}."
                if support_stage_ups:
                    result_text += ""
        else:
            result_speech = "Both duelists were left standing amid the wreckage." if rounds > 200 else "Both teams were knocked out at the same time."
            result_text = f"Draw. {result_speech}"
            if support_cards_used:
                result_text += f" Support cards equipped: {support_cards_used}."
                if support_stage_ups:
                    result_text += ""

        result_embed = make_dueling_grounds_result_embed(duelist_key, enemy_name, result_speech, player_won, reward_text)
        log_entries.append(result_text)
        result_file = await render_discord_file_async(render_battle, "dueling_grounds_battle.png", user_name, enemy_name, player_team, enemy_team, log_entries[-12:], tick_info="Battle complete", round_info=f"Round {max(1, completed_rounds + 1)}", player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, DUELING_GROUNDS_BATTLE_HEARTS), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, DUELING_GROUNDS_BATTLE_HEARTS), max_hearts=DUELING_GROUNDS_BATTLE_HEARTS, player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path, theme_name=battle_theme_name)
        result_attachments: List[discord.File] = [result_file]
        battle_stats_payload = build_battle_stats_payload([*player_team, *enemy_team])
        portrait_file = make_dueling_grounds_duelist_portrait_file(duelist_key, enemy_name)
        if portrait_file is not None:
            result_attachments.append(portrait_file)
        try:
            await message.edit(
                content=result_text,
                attachments=result_attachments,
                embed=result_embed,
                view=BattleResultView(owner_id, battle_title, log_entries, user_name=user_name, allow_battle_again=(forced_duelist_key is None), allow_npc_menu=(forced_duelist_key is not None), battle_mode='normal', battle_stats=battle_stats_payload),
            )
        except Exception:
            followup_files: List[discord.File] = [result_file]
            followup_portrait = make_dueling_grounds_duelist_portrait_file(duelist_key, enemy_name)
            if followup_portrait is not None:
                followup_files.append(followup_portrait)
            await interaction.followup.send(
                content=result_text,
                files=followup_files,
                embed=result_embed,
                view=BattleResultView(owner_id, battle_title, log_entries, user_name=user_name, allow_battle_again=(forced_duelist_key is None), allow_npc_menu=(forced_duelist_key is not None), battle_mode='normal', battle_stats=battle_stats_payload),
            )
    finally:
        _clear_runtime_cards(player_team)
        _clear_runtime_cards(enemy_team)
