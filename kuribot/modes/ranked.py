"""Running a ranked Dueling Grounds battle."""

from __future__ import annotations

import asyncio
from typing import List, Optional

import discord

from kuribot.config import (
    BATTLE_TURN_DELAY, CAMPAIGN_BATTLE_HEARTS, DUELING_GROUNDS_BATTLE_HEARTS, RANKED_STARTING_ELO,
    RNG, SPEED_GAUGE_MAX, TEAM_SIZE,
)
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.settings import battle_refresh_status_text, should_refresh_battle
from kuribot.database import db
from kuribot.monsters import format_monster_types
from kuribot.cards.support_cards import award_support_deck_battle_exp
from kuribot.cards.support_decks import active_support_text, build_support_deck_runtime
from kuribot.battle.runtime import (
    _clear_runtime_cards, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, random_alive, team_alive,
)
from kuribot.battle.stats import support_card_stat_bonus
from kuribot.battle.mechanics import (
    _assign_battle_runtime_cards, action_target_override, advance_speed_gauges, any_ready_fighter,
    apply_all_pending_battle_start_heart_losses, apply_damage_with_runtime,
    apply_post_turn_status_effects, attempt_ultimate_crystal_magic_save,
    build_battle_stats_payload, exact_time_to_next_turn, handle_spell_on_critical_taken,
    handle_spell_on_damage_dealt, handle_spell_on_damage_taken, handle_spell_on_turn_end,
    handle_spell_on_turn_start, handle_spell_when_targeted, handle_support_when_targeted,
    initialize_battle_start_effects, on_fighter_attacked, process_bombs_for_all_fighters,
    resolve_attack_damage, resolve_battle_outcome, resolve_round_end_spell_effects,
    resolve_special_support_round_effects, resolve_start_of_turn_status, spell_of_pain_bearer,
    SPELL_OF_PAIN_REDIRECT_RATIO, trigger_precog_strike, trigger_warning_point_on_dodge,
    trigger_yummy_surprise_on_dodge,
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
from kuribot.dueling_grounds import (
    choose_random_dueling_grounds_duelist, make_dueling_grounds_duelist_portrait_file,
)
from kuribot.campaign import campaign_battle_hearts_remaining
from kuribot.ranked import (
    award_ranked_elo_threshold_rewards, build_ranked_dueling_grounds_enemy_team,
    choose_ranked_user_opponent, make_dueling_grounds_result_embed_mode, random_ranked_duelist_elo,
    ranked_duelist_support_mode, ranked_elo_delta, ranked_gxe_from_row, RANKED_MATCH_ELO_RANGE,
    ranked_outcome_score, RANKED_REWARD_EQUIP_SPELLS, RANKED_REWARD_GOLD,
    RANKED_REWARD_PREMIUM_TICKETS, RANKED_REWARD_SUPPORT_CARDS, RANKED_REWARD_TICKETS,
    resolve_ranked_dueling_grounds_user_label_for_battle, support_runtime_for_enemy_mode,
)
from kuribot.render.battle import render_battle
from kuribot.ui.common import (
    _reset_discord_file_handles, render_discord_file_async, safe_edit_interaction_message,
    safe_send_interaction_message, safe_send_or_channel_message,
)
from kuribot.ui.battle_results import BattleResultView
from kuribot.modes.common import single_battle_per_player


@single_battle_per_player("ranked")
async def start_dueling_grounds_ranked_battle(interaction: discord.Interaction, owner_id: int, user_name: str):
    player_team = _active_team_fighters_for_user(owner_id, "ranked")
    if len(player_team) < TEAM_SIZE:
        await safe_edit_interaction_message(
            interaction,
            content=f"You need an active Ranked team with {TEAM_SIZE} monsters before starting a Ranked duel.",
            attachments=[],
            embed=None,
            view=None,
        )
        return

    owner_row = db.get_user(owner_id)
    player_elo_before = int(owner_row['ranked_elo'] or RANKED_STARTING_ELO)
    player_gxe_before = ranked_gxe_from_row(owner_row)
    opponent_user_id = choose_ranked_user_opponent(owner_id)
    opponent_uses_user_team = opponent_user_id is not None and RNG.random() < 0.5
    opponent_user_row = None
    opponent_elo_before = RANKED_STARTING_ELO
    duelist_key = ''
    duelist = {'name': ''}
    enemy_name = ''
    enemy_team: List[Fighter] = []
    enemy_runtime: Optional[SupportDeckRuntime] = None
    enemy_support_label = 'No Support Deck'
    portrait_enabled = False
    player_deck_row = db.get_active_support_deck(owner_id, "ranked")
    player_runtime = build_support_deck_runtime(player_deck_row, user_id=owner_id)

    if opponent_uses_user_team and opponent_user_id is not None:
        opponent_user_row = db.get_user(opponent_user_id)
        enemy_team = _active_team_fighters_for_user(opponent_user_id, "ranked")
        if len(enemy_team) < TEAM_SIZE:
            opponent_uses_user_team = False
        else:
            opponent_elo_before = int(opponent_user_row['ranked_elo'] or RANKED_STARTING_ELO)
            if abs(opponent_elo_before - player_elo_before) > RANKED_MATCH_ELO_RANGE:
                opponent_uses_user_team = False
            else:
                enemy_name = await resolve_ranked_dueling_grounds_user_label_for_battle(interaction, opponent_user_id)
                enemy_runtime = build_support_deck_runtime(db.get_active_support_deck(opponent_user_id, "ranked"), user_id=opponent_user_id)
                enemy_support_label = active_support_text(enemy_runtime) if enemy_runtime else 'No Support Deck'
    if not opponent_uses_user_team:
        duelist_key, duelist = choose_random_dueling_grounds_duelist()
        opponent_elo_before = random_ranked_duelist_elo(player_elo_before)
        enemy_team, duelist = build_ranked_dueling_grounds_enemy_team(duelist_key, opponent_elo_before)
        enemy_name = duelist.get('name', duelist_key)
        enemy_runtime, enemy_support_label = support_runtime_for_enemy_mode(enemy_name, enemy_team, ranked_duelist_support_mode(opponent_elo_before))
        portrait_enabled = True

    battle_theme_name = db.get_ui_theme(owner_id)
    battle_speed = 'normal'  # Ranked battles always use normal speed, ignoring user battle speed settings.
    player_avatar_path = db.get_avatar_path(owner_id)
    enemy_avatar_path = db.get_avatar_path(opponent_user_id) if opponent_uses_user_team and opponent_user_id is not None else str(get_duelist_portrait_path(duelist_key, enemy_name) or "")

    for fighter in [*player_team, *enemy_team]:
        fighter.speed_gauge = 0.0

    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)

    battle_title = f"{user_name} vs {enemy_name}"
    mode_label = 'Dueling Grounds • Ranked'
    player_hearts = DUELING_GROUNDS_BATTLE_HEARTS
    enemy_hearts = DUELING_GROUNDS_BATTLE_HEARTS
    log_entries: List[str] = [
        f"Ranked match found. {user_name} enters at **{player_elo_before} ELO** / **{player_gxe_before:.1f} GXE**.",
        (
            f"{enemy_name} enters at **{int(opponent_user_row['ranked_elo'] or RANKED_STARTING_ELO)} ELO** / **{ranked_gxe_from_row(opponent_user_row):.1f} GXE**."
            if opponent_uses_user_team and opponent_user_row is not None
            else f"{enemy_name} takes the Ranked NPC slot at **{opponent_elo_before} ELO** difficulty."
        ),
        f"{user_name} sends out {', '.join(f'{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]' for f in player_team)}.",
        f"{enemy_name} sends out {', '.join(f'{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]' for f in enemy_team)}.",
        f"{user_name} support deck: {active_support_text(player_runtime)}." if player_runtime else f"{user_name} is battling without a Support Deck.",
        f"{enemy_name} support deck: {active_support_text(enemy_runtime)}." if enemy_runtime else f"{enemy_name} enters with {enemy_support_label}.",
    ]
    initialize_battle_start_effects(player_team, owner_id, enemy_team, opponent_user_id if opponent_uses_user_team else None, log_entries)
    append_necrovalley_suppression_logs(user_name, enemy_name, player_team, player_runtime, enemy_team, enemy_runtime, log_entries)
    player_hearts, enemy_hearts = apply_all_pending_battle_start_heart_losses(
        player_team, enemy_team, player_hearts, enemy_hearts, user_name, enemy_name, log_entries, max_hearts=DUELING_GROUNDS_BATTLE_HEARTS
    )

    message_content = battle_refresh_status_text(mode_label, enemy_name, battle_speed)
    if battle_speed != 'instant':
        battle_file = await render_discord_file_async(
            render_battle,
            'dueling_grounds_ranked_battle.png',
            user_name,
            enemy_name,
            player_team,
            enemy_team,
            log_entries,
            tick_info='Battle start',
            round_info='Round 1',
            player_support_runtime=player_runtime,
            enemy_support_runtime=enemy_runtime,
            player_hearts=campaign_battle_hearts_remaining(player_hearts, DUELING_GROUNDS_BATTLE_HEARTS),
            enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, DUELING_GROUNDS_BATTLE_HEARTS),
            max_hearts=DUELING_GROUNDS_BATTLE_HEARTS,
            theme_name=battle_theme_name,
            player_header_note=f'ELO: {player_elo_before}',
            enemy_header_note=f'ELO: {opponent_elo_before}',
            player_avatar_path=player_avatar_path,
            enemy_avatar_path=enemy_avatar_path,
        )
    else:
        battle_file = None

    message: Optional[discord.Message] = None
    initial_files = [battle_file] if battle_file is not None else []
    source_message = getattr(interaction, 'message', None)
    if source_message is not None:
        try:
            _reset_discord_file_handles(initial_files)
            message = await source_message.edit(content=message_content, attachments=initial_files, embed=None, view=None)
        except (discord.NotFound, discord.HTTPException, TypeError, ValueError, AttributeError):
            message = None
    if message is None:
        message = await safe_send_or_channel_message(
            interaction,
            content=message_content,
            file=battle_file if battle_file is not None else None,
            embed=None,
            view=None,
            ephemeral=False,
        )
    if message is None:
        # Nothing usable could be posted; stop cleanly instead of letting a view
        # callback crash and spam discord.ui.view tracebacks.
        _clear_runtime_cards(player_team)
        _clear_runtime_cards(enemy_team)
        return

    rounds = 1
    completed_rounds = 0
    fighters_pending_in_round = initialize_battle_round([*player_team, *enemy_team])
    defeated_fighter_ids: set[int] = set()
    result_text = ''

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

            atk_bonus = support_card_stat_bonus(active, attacker_cards, 'ATK')
            def_bonus = support_card_stat_bonus(active, attacker_cards, 'DEF')
            spd_bonus = support_card_stat_bonus(active, attacker_cards, 'SPD')
            log_entries.append(f"Turn {rounds}: **{active.name}** ({active_name}) takes the turn.")
            if atk_bonus:
                log_entries.append(f"{active.name} gains **+{atk_bonus} ATK** from Support Cards.")
            if def_bonus:
                log_entries.append(f"{active.name} gains **+{def_bonus} DEF** from Support Cards.")
            if spd_bonus:
                log_entries.append(f"{active.name} gains **+{spd_bonus} SPD** from Support Cards.")

            process_bombs_for_all_fighters([*acting_team, *defending_team], log_entries)
            handle_spell_on_turn_start(active, acting_team, defending_team, log_entries)
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)
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
                    player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)
                defender_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
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
                        dealt_redirect = apply_damage_with_runtime(pain_bearer, redirected, active, log_entries, 'Spell of Pain')
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
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)

            handle_spell_on_turn_end(active, acting_team, defending_team, log_entries)
            apply_post_turn_status_effects([*player_team, *enemy_team], log_entries)
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)

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
                log_entries.append(f'Round {completed_rounds} complete.')
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
                battle_file = await render_discord_file_async(render_battle, 'dueling_grounds_ranked_battle.png', user_name, enemy_name, player_team, enemy_team, log_entries[-12:], active_fighter=active, target_fighter=target, tick_info=f'Turn {rounds}', round_info=f'Round {completed_rounds + 1}', player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, DUELING_GROUNDS_BATTLE_HEARTS), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, DUELING_GROUNDS_BATTLE_HEARTS), max_hearts=DUELING_GROUNDS_BATTLE_HEARTS, theme_name=battle_theme_name, player_header_note=f'ELO: {player_elo_before}', enemy_header_note=f'ELO: {opponent_elo_before}', player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path)
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
        player_score = ranked_outcome_score(player_won, enemy_won)
        enemy_score = 1.0 - player_score if player_score in {0.0, 1.0} else 0.5
        player_delta = ranked_elo_delta(player_elo_before, opponent_elo_before, player_score)
        enemy_delta = ranked_elo_delta(opponent_elo_before, player_elo_before, enemy_score)
        player_elo_after = max(0, player_elo_before + player_delta)
        opponent_elo_after = max(0, opponent_elo_before + enemy_delta)

        if player_won:
            db.add_win(owner_id)
            db.update_ranked_record(owner_id, 'win')
            result_speech = f'You defeated {enemy_name}.' if opponent_uses_user_team else duelist.get('loss', 'You won the duel.')
            result_text = f"Victory! {result_speech}"
            if support_cards_used:
                result_text += f" Support cards equipped: {support_cards_used}."
                if support_stage_ups:
                    result_text += ""
        elif enemy_won:
            db.add_loss(owner_id)
            db.update_ranked_record(owner_id, 'loss')
            result_speech = f'{enemy_name} has defeated you.' if opponent_uses_user_team else duelist.get('win', 'Your opponent won the duel.')
            result_text = f"Defeat. {result_speech}"
            if support_cards_used:
                result_text += f" Support cards equipped: {support_cards_used}."
                if support_stage_ups:
                    result_text += ""
        else:
            db.update_ranked_record(owner_id, 'draw')
            result_speech = 'Both duelists were left standing amid the wreckage.' if rounds > 200 else 'Both teams were knocked out at the same time.'
            result_text = f"Draw. {result_speech}"
            if support_cards_used:
                result_text += f" Support cards equipped: {support_cards_used}."
                if support_stage_ups:
                    result_text += ""

        if opponent_uses_user_team and opponent_user_id is not None:
            if player_won:
                db.update_ranked_record(opponent_user_id, 'loss')
            elif enemy_won:
                db.update_ranked_record(opponent_user_id, 'win')
            else:
                db.update_ranked_record(opponent_user_id, 'draw')
            opponent_elo_after = db.add_ranked_elo_delta(opponent_user_id, enemy_delta)
            # Defenders cross rating milestones too; they used to never get those rewards.
            award_ranked_elo_threshold_rewards(opponent_user_id, opponent_elo_before, opponent_elo_after)

        player_elo_after = db.add_ranked_elo_delta(owner_id, player_delta)

        milestone_thresholds = award_ranked_elo_threshold_rewards(owner_id, player_elo_before, player_elo_after)
        updated_owner_row = db.get_user(owner_id)
        updated_player_gxe = ranked_gxe_from_row(updated_owner_row)
        elo_line = f"ELO: **{player_elo_before} → {player_elo_after}** ({player_delta:+d}) • GXE: **{player_gxe_before:.1f}% → {updated_player_gxe:.1f}%**"
        reward_segments = [elo_line]
        if opponent_uses_user_team and opponent_user_id is not None:
            reward_segments.append(f"Opponent ELO: **{opponent_elo_before} → {opponent_elo_after}** ({enemy_delta:+d})")
        if milestone_thresholds:
            reward_segments.append(
                f"Milestones reached: **{', '.join(str(value) for value in milestone_thresholds)}** (+{RANKED_REWARD_GOLD * len(milestone_thresholds)} gold, +{RANKED_REWARD_EQUIP_SPELLS * len(milestone_thresholds)} equip spells, +{RANKED_REWARD_SUPPORT_CARDS * len(milestone_thresholds)} support cards, +{RANKED_REWARD_TICKETS * len(milestone_thresholds)} tickets, +{RANKED_REWARD_PREMIUM_TICKETS * len(milestone_thresholds)} packs)"
            )
        reward_text = '\n'.join(reward_segments)
        result_text = f"{result_text} {elo_line}"

        result_embed = make_dueling_grounds_result_embed_mode(duelist_key or 'ranked_user', enemy_name, result_speech, player_won, reward_text, mode_label='Dueling Grounds • Ranked', thumbnail_allowed=portrait_enabled)
        log_entries.append(result_text)
        result_file = await render_discord_file_async(render_battle, 'dueling_grounds_ranked_battle.png', user_name, enemy_name, player_team, enemy_team, log_entries[-12:], tick_info='Battle complete', round_info=f'Round {max(1, completed_rounds + 1)}', player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, DUELING_GROUNDS_BATTLE_HEARTS), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, DUELING_GROUNDS_BATTLE_HEARTS), max_hearts=DUELING_GROUNDS_BATTLE_HEARTS, theme_name=battle_theme_name, player_header_note=f'ELO: {player_elo_after}', enemy_header_note=f'ELO: {opponent_elo_before}', player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path)
        result_attachments: List[discord.File] = [result_file]
        battle_stats_payload = build_battle_stats_payload([*player_team, *enemy_team])
        if portrait_enabled:
            portrait_file = make_dueling_grounds_duelist_portrait_file(duelist_key, enemy_name)
            if portrait_file is not None:
                result_attachments.append(portrait_file)
        try:
            await message.edit(content=result_text, attachments=result_attachments, embed=result_embed, view=BattleResultView(owner_id, battle_title, log_entries, user_name=user_name, allow_battle_again=True, battle_mode='ranked', battle_stats=battle_stats_payload))
        except Exception:
            await safe_send_interaction_message(interaction, content=result_text, files=result_attachments, embed=result_embed, view=BattleResultView(owner_id, battle_title, log_entries, user_name=user_name, allow_battle_again=True, battle_mode='ranked', battle_stats=battle_stats_payload), ephemeral=False)
    finally:
        _clear_runtime_cards(player_team)
        _clear_runtime_cards(enemy_team)
