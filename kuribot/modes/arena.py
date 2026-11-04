"""Running an arena battle."""

from __future__ import annotations

import asyncio
from typing import Optional, Sequence

import discord

from kuribot.config import BATTLE_TURN_DELAY, SPEED_GAUGE_MAX, TEAM_SIZE
from kuribot.settings import battle_refresh_status_text, should_refresh_battle
from kuribot.database import db
from kuribot.monsters import format_monster_types
from kuribot.cards.support_decks import active_support_text, build_support_deck_runtime
from kuribot.battle.runtime import (
    _clear_runtime_cards, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, random_alive, team_alive,
)
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
    apply_empty_support_deck_round_start_penalty, apply_support_round_end_heart_penalty, consume_blaze_accelerator_heart_losses,
    initialize_battle_round, resolve_support_round_end,
)
from kuribot.battle.setup import _active_team_fighters_for_user, append_necrovalley_suppression_logs
from kuribot.portraits import get_duelist_portrait_path
from kuribot.campaign import campaign_battle_hearts_remaining
from kuribot.arena import (
    _arena_team_for_user, ARENA_BATTLE_HEARTS, arena_rank_label, ARENA_STARTING_ELO,
    build_arena_duelist_team, get_user_arena_rank_position,
)
from kuribot.render.battle import render_battle
from kuribot.ui.common import (
    _reset_discord_file_handles, render_discord_file_async, safe_edit_interaction_message,
    safe_send_interaction_message, safe_send_or_channel_message,
)
from kuribot.ui.battle_results import BattleResultView
from kuribot.modes.common import single_battle_per_player


@single_battle_per_player("arena")
async def start_arena_battle(interaction: discord.Interaction, owner_id: int, user_name: str, opponent_entry: dict, *, roster_opponents: Optional[Sequence[dict]] = None):
    player_team = _active_team_fighters_for_user(owner_id, "arena_offense")
    if len(player_team) != TEAM_SIZE:
        await safe_edit_interaction_message(interaction, content=f'You need an active Arena Offense team with {TEAM_SIZE} monsters before entering Arena.', attachments=[], embed=None, view=None)
        return
    db.get_user(owner_id)  # makes sure the player's row exists
    player_runtime = build_support_deck_runtime(db.get_active_support_deck(owner_id, "arena_offense"), user_id=owner_id)
    enemy_name = str(opponent_entry['name'])
    enemy_elo_before = int(opponent_entry['elo'])
    enemy_user_id: Optional[int] = None
    enemy_avatar_path = ''
    if opponent_entry.get('kind') == 'user':
        enemy_user_id = int(opponent_entry['id'])
        enemy_team = _arena_team_for_user(enemy_user_id)
        enemy_runtime = build_support_deck_runtime(db.get_active_support_deck(enemy_user_id, "arena_offense"), user_id=enemy_user_id)
        enemy_avatar_path = db.get_avatar_path(enemy_user_id)
        enemy_header_note = 'Arena Defense Team'
    else:
        enemy_team, enemy_runtime, enemy_name = build_arena_duelist_team(str(opponent_entry['id']), enemy_elo_before)
        enemy_header_note = 'Arena Duelist'
        enemy_avatar_path = str(get_duelist_portrait_path(str(opponent_entry['id']), enemy_name) or '')
    if len(enemy_team) != TEAM_SIZE:
        await safe_edit_interaction_message(interaction, content='That Arena opponent is unavailable right now. Refresh the list and try again.', attachments=[], embed=None, view=None)
        return
    if not db.spend_arena_ticket(owner_id, 1):
        await safe_edit_interaction_message(interaction, content="You need **1 Arena Ticket** to battle in Arena. Claim `;daily` or buy Arena Tickets from the Guild Shop.", attachments=[], embed=None, view=None)
        return

    battle_theme_name = db.get_ui_theme(owner_id)
    battle_speed = 'normal'  # Arena battles always use normal speed, matching the Ranked battle speed lock.
    player_avatar_path = db.get_avatar_path(owner_id)
    for fighter in [*player_team, *enemy_team]:
        fighter.speed_gauge = 0.0
    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
    battle_title = f'{user_name} vs {enemy_name}'
    player_hearts = ARENA_BATTLE_HEARTS
    enemy_hearts = ARENA_BATTLE_HEARTS
    log_entries = [
        f'Arena battle found. {user_name} enters at **{arena_rank_label(get_user_arena_rank_position(owner_id))}**.',
        f"{enemy_name} defends at **{opponent_entry.get('rank_label', arena_rank_label(opponent_entry.get('rank_position')))}**.",
        f'{user_name} sends out {", ".join(f"{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]" for f in player_team)}.',
        f'{enemy_name} sends out {", ".join(f"{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]" for f in enemy_team)}.',
        f"{user_name} support deck: {active_support_text(player_runtime)}." if player_runtime else f"{user_name} is battling without a Support Deck.",
        f"{enemy_name} support deck: {active_support_text(enemy_runtime)}." if enemy_runtime else f"{enemy_name} is battling without a Support Deck.",
    ]
    initialize_battle_start_effects(player_team, owner_id, enemy_team, enemy_user_id, log_entries)
    append_necrovalley_suppression_logs(user_name, enemy_name, player_team, player_runtime, enemy_team, enemy_runtime, log_entries)
    player_hearts, enemy_hearts = apply_all_pending_battle_start_heart_losses(
        player_team, enemy_team, player_hearts, enemy_hearts, user_name, enemy_name, log_entries, max_hearts=ARENA_BATTLE_HEARTS
    )

    message_content = battle_refresh_status_text('Arena', enemy_name, battle_speed)
    battle_file = await render_discord_file_async(
        render_battle,
        'arena_battle.png',
        user_name,
        enemy_name,
        player_team,
        enemy_team,
        log_entries,
        tick_info='Battle start',
        round_info='Round 1',
        player_support_runtime=player_runtime,
        enemy_support_runtime=enemy_runtime,
        player_hearts=campaign_battle_hearts_remaining(player_hearts, ARENA_BATTLE_HEARTS),
        enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, ARENA_BATTLE_HEARTS),
        max_hearts=ARENA_BATTLE_HEARTS,
        theme_name=battle_theme_name,
        player_header_note='Arena Offense',
        enemy_header_note=enemy_header_note,
        player_avatar_path=player_avatar_path,
        enemy_avatar_path=enemy_avatar_path,
    )
    try:
        await safe_edit_interaction_message(interaction, content=message_content, attachments=[battle_file], embed=None, view=None)
        try:
            message = await interaction.original_response()
        except (discord.NotFound, discord.HTTPException):
            message = interaction.message
    except Exception:
        message = await safe_send_interaction_message(interaction, content=message_content, file=battle_file, ephemeral=False)

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
            log_entries.append(f'Turn {rounds}: **{active.name}** ({active_name}) takes the turn.')
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
                log_entries.append(f'{active.name} targets **{target.name}**.')
            if can_act and target is not None and active.alive:
                trigger_precog_strike(target, active, defending_team, acting_team, log_entries)
                if not active.alive or not target.alive:
                    player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)
                if active.alive and target.alive:
                    attack_instances = build_attack_instances_for_fighter(active, attacker_cards)
                    for hit_multiplier, hit_suffix in attack_instances:
                        if not active.alive or not target.alive:
                            break
                        target, incoming_multiplier, attack_canceled = handle_spell_when_targeted(target, active, defending_team, acting_team, log_entries)
                        if attack_canceled or not active.alive or not target.alive:
                            continue
                        defender_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(target), [])
                        attack_result = resolve_attack_damage(active, target, attacker_cards, defender_cards, hit_multiplier=hit_multiplier)
                        if not attack_result['hit']:
                            # Arena was missing the dodge triggers every other mode runs.
                            trigger_yummy_surprise_on_dodge(target, active, log_entries)
                            trigger_warning_point_on_dodge(target, active, log_entries)
                            log_entries.append(f'{active.name} attacks {target.name}, but misses{hit_suffix}.')
                            continue
                        damage = max(1, int(round(int(attack_result['damage']) * incoming_multiplier)))
                        relation = str(attack_result['relation'])
                        pain_bearer = spell_of_pain_bearer(target, defending_team)
                        if pain_bearer is not None:
                            redirected = max(1, int(round(damage * SPELL_OF_PAIN_REDIRECT_RATIO)))
                            damage -= redirected
                            dealt_redirect = apply_damage_with_runtime(pain_bearer, redirected, active, log_entries, 'Spell of Pain')
                            if dealt_redirect > 0:
                                log_entries.append(f'**Spell of Pain** redirects **{dealt_redirect}** damage to **{pain_bearer.name}**.')
                        if handle_support_when_targeted(target, active, log_entries):
                            dealt = 0
                        else:
                            dealt = apply_damage_with_runtime(target, damage, active, log_entries, "attack")
                        on_fighter_attacked(target, active, log_entries)
                        handle_spell_on_damage_dealt(active, target, dealt, acting_team, defending_team, log_entries)
                        handle_spell_on_damage_taken(target, active, dealt, defending_team, acting_team, log_entries)
                        if attack_result['crit'] and dealt > 0:
                            handle_spell_on_critical_taken(target, active, defending_team, acting_team, log_entries)
                        relation_text = ' (type advantage)' if relation == 'advantage' else (' (type resisted)' if relation == 'disadvantage' else '')
                        crit_text = ' **Critical hit!**' if attack_result['crit'] else ''
                        log_entries.append(f'{active.name} hits {target.name} for **{dealt}** damage{relation_text}{hit_suffix}.{crit_text}')
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)
            handle_spell_on_turn_end(active, acting_team, defending_team, log_entries)
            apply_post_turn_status_effects([*player_team, *enemy_team], log_entries)
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, user_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids)
            # Arena never granted the Neutron Blast extra turn that other modes do.
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
                    ARENA_BATTLE_HEARTS,
                    player_team,
                    enemy_team,
                    user_name,
                    log_entries,
                )
                enemy_hearts = apply_support_round_end_heart_penalty(
                    enemy_hearts,
                    resolve_support_round_end(enemy_runtime, enemy_team, player_team, enemy_name, log_entries, player_runtime),
                    ARENA_BATTLE_HEARTS,
                    enemy_team,
                    player_team,
                    enemy_name,
                    log_entries,
                )
                # Same rule as campaign, normal and ranked: a side with no support cards left loses a heart.
                player_hearts, enemy_hearts = apply_empty_support_deck_round_start_penalty(
                    player_runtime,
                    enemy_runtime,
                    user_name,
                    enemy_name,
                    player_hearts,
                    enemy_hearts,
                    ARENA_BATTLE_HEARTS,
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
            if should_refresh_battle(battle_speed, rounds, not fighters_pending_in_round, battle_over):
                battle_file = await render_discord_file_async(render_battle, 'arena_battle.png', user_name, enemy_name, player_team, enemy_team, log_entries[-12:], active_fighter=active, target_fighter=target, tick_info=f'Turn {rounds}', round_info=f'Round {completed_rounds + 1}', player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, ARENA_BATTLE_HEARTS), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, ARENA_BATTLE_HEARTS), max_hearts=ARENA_BATTLE_HEARTS, theme_name=battle_theme_name, player_header_note='Arena Offense', enemy_header_note=enemy_header_note, player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path)
                status_text = battle_refresh_status_text('Arena', enemy_name, battle_speed, rounds)
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
        pre_battle_rank = get_user_arena_rank_position(owner_id)
        post_battle_rank = pre_battle_rank
        opponent_rank_before = int(opponent_entry.get('rank_position', 0) or 0)  # shown in the result embed
        # Compare CURRENT ratings on one scale. The old check compared the opponent's rank in
        # the roster list (arena-team players only) against the player's rank among ALL
        # players, so a win could swap a player DOWN the ladder. Ratings are also re-read
        # here because the roster snapshot can be minutes old.
        owner_row_now = db.get_user(owner_id)
        current_player_elo = int((owner_row_now['arena_elo'] if owner_row_now is not None else 0) or ARENA_STARTING_ELO)
        if opponent_entry.get('kind') == 'user':
            enemy_row_now = db.get_user(int(opponent_entry['id']))
            current_enemy_elo = int((enemy_row_now['arena_elo'] if enemy_row_now is not None else 0) or enemy_elo_before)
        else:
            duelist_row_now = db.get_arena_duelist(str(opponent_entry['id']))
            current_enemy_elo = int(duelist_row_now['arena_elo']) if duelist_row_now is not None else int(enemy_elo_before)
        should_swap_ranks = current_enemy_elo > current_player_elo
        if player_won:
            db.update_arena_record(owner_id, 'win')
            if should_swap_ranks:
                if opponent_entry.get('kind') == 'user':
                    enemy_user_id = int(opponent_entry['id'])
                    db.set_arena_elo(owner_id, current_enemy_elo)
                    db.set_arena_elo(enemy_user_id, current_player_elo)
                else:
                    db.set_arena_elo(owner_id, current_enemy_elo)
                    db.set_arena_duelist_elo(str(opponent_entry['id']), current_player_elo)
                post_battle_rank = get_user_arena_rank_position(owner_id)
                result_text = f'Victory! {user_name} swaps ranks with {enemy_name} and moves to **Rank #{post_battle_rank}** in Arena.'
            else:
                result_text = f'Victory! {user_name} remains at **Rank #{post_battle_rank}** in Arena because {enemy_name} was not ranked above them.'
        elif enemy_won:
            db.update_arena_record(owner_id, 'loss')
            result_text = f'Defeat. {user_name} remains at **Rank #{post_battle_rank}** in Arena.'
        else:
            db.update_arena_record(owner_id, 'draw')
            result_text = f'Draw. {user_name} remains at **Rank #{post_battle_rank}** in Arena.'
        log_entries.append(result_text)
        result_embed = discord.Embed(title='Arena Battle Complete', description=result_text, color=discord.Color.blurple())
        result_embed.add_field(name='Your Rank', value=f"#{pre_battle_rank} → #{post_battle_rank}", inline=True)
        result_embed.add_field(name='Opponent', value=f"{enemy_name} • Rank #{opponent_rank_before}", inline=True)
        result_file = await render_discord_file_async(render_battle, 'arena_battle.png', user_name, enemy_name, player_team, enemy_team, log_entries[-12:], tick_info='Battle complete', round_info=f'Round {max(1, completed_rounds + 1)}', player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, ARENA_BATTLE_HEARTS), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, ARENA_BATTLE_HEARTS), max_hearts=ARENA_BATTLE_HEARTS, theme_name=battle_theme_name, player_header_note='Arena Offense', enemy_header_note=enemy_header_note, player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path)
        battle_stats_payload = build_battle_stats_payload([*player_team, *enemy_team])
        try:
            await message.edit(content=result_text, attachments=[result_file], embed=result_embed, view=BattleResultView(owner_id, battle_title, log_entries, user_name=user_name, allow_back_to_arena_roster=True, arena_roster_opponents=roster_opponents, battle_stats=battle_stats_payload))
        except Exception:
            await safe_send_interaction_message(interaction, content=result_text, files=[result_file], embed=result_embed, view=BattleResultView(owner_id, battle_title, log_entries, user_name=user_name, allow_back_to_arena_roster=True, arena_roster_opponents=roster_opponents, battle_stats=battle_stats_payload), ephemeral=False)
    finally:
        _clear_runtime_cards(player_team)
        _clear_runtime_cards(enemy_team)
