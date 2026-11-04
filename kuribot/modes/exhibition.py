"""Running custom lobby and duelist-vs-duelist exhibition battles."""

from __future__ import annotations

import asyncio
from typing import Callable, Dict, List, Optional

import discord

from kuribot.config import (
    BATTLE_TURN_DELAY, DUELING_GROUNDS_BATTLE_HEARTS, SPEED_GAUGE_MAX, TEAM_SIZE,
)
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.settings import battle_refresh_status_text, should_refresh_battle
from kuribot.database import db
from kuribot.monsters import format_monster_types
from kuribot.cards.support_decks import active_support_text, build_support_deck_runtime
from kuribot.battle.runtime import (
    _clear_runtime_cards, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, random_alive, register_battle_teams,
    team_alive,
)
from kuribot.battle.stats import support_card_stat_bonus
from kuribot.battle.mechanics import (
    _assign_battle_runtime_cards, action_target_override, advance_speed_gauges, any_ready_fighter,
    apply_all_pending_battle_start_heart_losses, apply_damage_with_runtime,
    apply_post_turn_status_effects, apply_special_support_draw_effects,
    attempt_ultimate_crystal_magic_save, build_battle_stats_payload, exact_time_to_next_turn,
    handle_spell_on_critical_taken, handle_spell_on_damage_dealt, handle_spell_on_damage_taken,
    handle_spell_on_turn_end, handle_spell_on_turn_start, handle_spell_when_targeted,
    handle_support_when_targeted, initialize_battle_start_effects,
    initialize_special_support_effects, on_fighter_attacked, process_bombs_for_all_fighters,
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
from kuribot.dueling_grounds import (
    build_dueling_grounds_enemy_support_runtime, build_dueling_grounds_enemy_team,
    choose_specific_dueling_grounds_duelist,
)
from kuribot.campaign import campaign_battle_hearts_remaining
from kuribot.render.battle import render_battle
from kuribot.ui.common import (
    _reset_discord_file_handles, render_discord_file_async, safe_acknowledge_interaction,
    safe_edit_interaction_message, safe_send_or_channel_message,
)
from kuribot.ui.battle_results import BattleResultView
from kuribot.ui.dueling_grounds import format_custom_lobby_settings


async def _run_dueling_grounds_exhibition_battle(
    interaction: discord.Interaction,
    *,
    owner_id: int,
    owner_name: str,
    enemy_name: str,
    player_team: List[Fighter],
    enemy_team: List[Fighter],
    player_runtime: Optional[SupportDeckRuntime],
    enemy_runtime: Optional[SupportDeckRuntime],
    player_avatar_path: str = '',
    enemy_avatar_path: str = '',
    mode_label: str = 'Dueling Grounds • Exhibition',
    battle_title: Optional[str] = None,
    player_header_note: str = '',
    enemy_header_note: str = '',
    hearts: int = DUELING_GROUNDS_BATTLE_HEARTS,
    disable_equips: bool = False,
    player_user_id: Optional[int] = None,
    enemy_user_id: Optional[int] = None,
    opening_lines: Optional[List[str]] = None,
    result_speech_win: Optional[str] = None,
    result_speech_loss: Optional[str] = None,
    result_speech_draw: Optional[str] = None,
    lobby_code: str = '',
    battle_speed_override: Optional[str] = None,
    result_embed_factory: Optional[Callable[[str, bool, bool], discord.Embed]] = None,
    result_view_factory: Optional[Callable[[str, List[str], Dict[str, List[Dict[str, int | str]]], str, bool, bool], discord.ui.View]] = None,
):
    # Acknowledge button interactions before any image rendering or battle loop work.
    # Without this, Discord can expire the interaction token and both
    # edit_original_response and followup.send raise Unknown Webhook.
    await safe_acknowledge_interaction(interaction, thinking=False)

    battle_theme_name = db.get_ui_theme(owner_id)
    battle_speed = str(battle_speed_override or db.get_battle_speed(owner_id) or 'normal').strip().lower()
    for fighter in [*player_team, *enemy_team]:
        fighter.speed_gauge = 0.0

    _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
    battle_title = battle_title or f'{owner_name} vs {enemy_name}'
    player_hearts = max(1, min(5, int(hearts or DUELING_GROUNDS_BATTLE_HEARTS)))
    enemy_hearts = player_hearts
    log_entries: List[str] = list(opening_lines or [])
    if not log_entries:
        log_entries = [
            f'{owner_name} sends out {", ".join(f"{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]" for f in player_team)}.',
            f'{enemy_name} sends out {", ".join(f"{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]" for f in enemy_team)}.',
        ]
    else:
        log_entries.extend([
            f'{owner_name} sends out {", ".join(f"{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]" for f in player_team)}.',
            f'{enemy_name} sends out {", ".join(f"{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]" for f in enemy_team)}.',
        ])
    log_entries.append(f"{owner_name} support deck: {active_support_text(player_runtime)}." if player_runtime else f"{owner_name} is battling without a Support Deck.")
    log_entries.append(f"{enemy_name} support deck: {active_support_text(enemy_runtime)}." if enemy_runtime else f"{enemy_name} is battling without a Support Deck.")
    if not disable_equips:
        initialize_battle_start_effects(player_team, player_user_id, enemy_team, enemy_user_id, log_entries)
        append_necrovalley_suppression_logs(owner_name, enemy_name, player_team, player_runtime, enemy_team, enemy_runtime, log_entries)
        player_hearts, enemy_hearts = apply_all_pending_battle_start_heart_losses(
            player_team, enemy_team, player_hearts, enemy_hearts, owner_name, enemy_name, log_entries, max_hearts=hearts
        )
    else:
        log_entries.append('Equip Spells are disabled for this match.')
        # Skip only the equip-spell part of battle start. Skipping all of it (as before)
        # also turned off every opening-hand Special Support effect and team protection.
        register_battle_teams(player_team, enemy_team)
        _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
        apply_special_support_draw_effects(player_team, enemy_team, player_runtime, enemy_runtime, "the player", "the opponent", log_entries)
        apply_special_support_draw_effects(enemy_team, player_team, enemy_runtime, player_runtime, "the opponent", "the player", log_entries)
        _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
        initialize_special_support_effects(player_team, enemy_team, player_runtime, log_entries)
        initialize_special_support_effects(enemy_team, player_team, enemy_runtime, log_entries)
        _assign_battle_runtime_cards(player_team, player_runtime, enemy_team, enemy_runtime)
        append_necrovalley_suppression_logs(owner_name, enemy_name, player_team, player_runtime, enemy_team, enemy_runtime, log_entries)
        player_hearts, enemy_hearts = apply_all_pending_battle_start_heart_losses(
            player_team, enemy_team, player_hearts, enemy_hearts, owner_name, enemy_name, log_entries, max_hearts=hearts
        )

    message_content = battle_refresh_status_text(mode_label, enemy_name, battle_speed)
    if battle_speed != 'instant':
        battle_file = await render_discord_file_async(
            render_battle,
            'dueling_grounds_custom_battle.png',
            owner_name,
            enemy_name,
            player_team,
            enemy_team,
            log_entries,
            tick_info='Battle start',
            round_info='Round 1',
            player_support_runtime=player_runtime,
            enemy_support_runtime=enemy_runtime,
            player_hearts=campaign_battle_hearts_remaining(player_hearts, hearts),
            enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, hearts),
            max_hearts=hearts,
            theme_name=battle_theme_name,
            player_header_note=player_header_note,
            enemy_header_note=enemy_header_note,
            player_avatar_path=player_avatar_path,
            enemy_avatar_path=enemy_avatar_path,
        )
    else:
        battle_file = None

    message: Optional[discord.Message] = None
    initial_files = [battle_file] if battle_file is not None else []
    if interaction.message is not None:
        try:
            _reset_discord_file_handles(initial_files)
            message = await interaction.message.edit(content=message_content, attachments=initial_files, embed=None, view=None)
        except (discord.NotFound, discord.HTTPException, TypeError, ValueError):
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
            active_name = owner_name if acting_team is player_team else enemy_name
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
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, owner_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids, total_hearts=hearts)
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
                    player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, owner_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids, total_hearts=hearts)
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
                    relation_text = ' (type advantage)' if relation == 'advantage' else (' (type resisted)' if relation == 'disadvantage' else '')
                    crit_text = " **Critical hit!**" if attack_result["crit"] else ""
                    log_entries.append(f"{active.name} hits {target.name} for **{dealt}** damage{relation_text}{hit_suffix}.{crit_text}")
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, owner_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids, total_hearts=hearts)
            handle_spell_on_turn_end(active, acting_team, defending_team, log_entries)
            apply_post_turn_status_effects([*player_team, *enemy_team], log_entries)
            player_hearts, enemy_hearts = resolve_fallen_fighters(player_team, enemy_team, owner_name, enemy_name, player_hearts, enemy_hearts, log_entries, defeated_fighter_ids, total_hearts=hearts)
            if active.alive and should_grant_neutron_blast_extra_turn(active, attacker_cards):
                active.speed_gauge = SPEED_GAUGE_MAX
                log_entries.append(f"**Neutron Blast** lets **{active.name}** act again!")
            else:
                active.speed_gauge = 0.0
            fighters_pending_in_round.discard(id(active))
            fighters_pending_in_round = {fighter_id for fighter_id in fighters_pending_in_round if any(id(fighter) == fighter_id and fighter.alive for fighter in [*player_team, *enemy_team])}
            player_hearts = attempt_ultimate_crystal_magic_save(player_team, player_runtime, player_hearts, owner_name, log_entries)
            enemy_hearts = attempt_ultimate_crystal_magic_save(enemy_team, enemy_runtime, enemy_hearts, enemy_name, log_entries)
            player_hearts, enemy_hearts = consume_blaze_accelerator_heart_losses(
                player_team,
                enemy_team,
                player_hearts,
                enemy_hearts,
                owner_name,
                enemy_name,
                log_entries,
                max_hearts=hearts,
            )
            if player_hearts <= 0 or enemy_hearts <= 0:
                break
            if not fighters_pending_in_round and team_alive(player_team) and team_alive(enemy_team):
                completed_rounds += 1
                log_entries.append(f'Round {completed_rounds} complete.')
                resolve_special_support_round_effects(player_team, enemy_team, player_runtime, enemy_runtime, log_entries)
                player_hearts = apply_support_round_end_heart_penalty(
                    player_hearts,
                    resolve_support_round_end(player_runtime, player_team, enemy_team, owner_name, log_entries, enemy_runtime),
                    hearts,
                    player_team,
                    enemy_team,
                    owner_name,
                    log_entries,
                )
                enemy_hearts = apply_support_round_end_heart_penalty(
                    enemy_hearts,
                    resolve_support_round_end(enemy_runtime, enemy_team, player_team, enemy_name, log_entries, player_runtime),
                    hearts,
                    enemy_team,
                    player_team,
                    enemy_name,
                    log_entries,
                )
                # Same rule as campaign, normal and ranked: a side with no support cards left loses a heart.
                player_hearts, enemy_hearts = apply_empty_support_deck_round_start_penalty(
                    player_runtime,
                    enemy_runtime,
                    owner_name,
                    enemy_name,
                    player_hearts,
                    enemy_hearts,
                    hearts,
                    log_entries,
                    player_team,
                    enemy_team,
                )
                resolve_round_end_spell_effects([*player_team, *enemy_team], log_entries)
                fighters_pending_in_round = initialize_battle_round([*player_team, *enemy_team])
            player_hearts = attempt_ultimate_crystal_magic_save(player_team, player_runtime, player_hearts, owner_name, log_entries)
            enemy_hearts = attempt_ultimate_crystal_magic_save(enemy_team, enemy_runtime, enemy_hearts, enemy_name, log_entries)
            player_hearts, enemy_hearts = consume_blaze_accelerator_heart_losses(
                player_team,
                enemy_team,
                player_hearts,
                enemy_hearts,
                owner_name,
                enemy_name,
                log_entries,
                max_hearts=hearts,
            )
            battle_over = player_hearts <= 0 or enemy_hearts <= 0 or not team_alive(player_team) or not team_alive(enemy_team)
            if should_refresh_battle(battle_speed, rounds, not fighters_pending_in_round, battle_over):
                battle_file = await render_discord_file_async(render_battle, 'dueling_grounds_custom_battle.png', owner_name, enemy_name, player_team, enemy_team, log_entries[-12:], active_fighter=active, target_fighter=target, tick_info=f'Turn {rounds}', round_info=f'Round {completed_rounds + 1}', player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, hearts), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, hearts), max_hearts=hearts, theme_name=battle_theme_name, player_header_note=player_header_note, enemy_header_note=enemy_header_note, player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path)
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
        if player_won:
            result_text = f"Victory! {result_speech_win or f'{owner_name} wins the exhibition duel.'}"
        elif enemy_won:
            result_text = f"Defeat. {result_speech_loss or f'{enemy_name} wins the exhibition duel.'}"
        else:
            result_text = f"Draw. {result_speech_draw or 'Both sides fought to a standstill.'}"

        result_embed = discord.Embed(title=enemy_name, description=result_text, color=discord.Color.blurple())
        result_embed.add_field(name='Mode', value=mode_label, inline=True)
        result_embed.add_field(name='Settings', value=format_custom_lobby_settings({'hearts': hearts, 'disable_cards': player_runtime is None and enemy_runtime is None, 'disable_equips': disable_equips}), inline=False)
        if result_embed_factory is not None:
            result_embed = result_embed_factory(result_text, player_won, enemy_won)
        log_entries.append(result_text)
        result_file = await render_discord_file_async(render_battle, 'dueling_grounds_custom_battle.png', owner_name, enemy_name, player_team, enemy_team, log_entries[-12:], tick_info='Battle complete', round_info=f'Round {max(1, completed_rounds + 1)}', player_support_runtime=player_runtime, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, hearts), enemy_hearts=campaign_battle_hearts_remaining(enemy_hearts, hearts), max_hearts=hearts, theme_name=battle_theme_name, player_header_note=player_header_note, enemy_header_note=enemy_header_note, player_avatar_path=player_avatar_path, enemy_avatar_path=enemy_avatar_path)
        battle_stats_payload = build_battle_stats_payload([*player_team, *enemy_team])
        result_view: discord.ui.View = BattleResultView(owner_id, battle_title, log_entries, user_name=owner_name, allow_back_to_lobby=(str(mode_label).strip().lower() == 'dueling grounds • custom match'.lower()), lobby_code=lobby_code, battle_stats=battle_stats_payload)
        if result_view_factory is not None:
            result_view = result_view_factory(battle_title, log_entries, battle_stats_payload, result_text, player_won, enemy_won)
        try:
            _reset_discord_file_handles([result_file])
            await message.edit(content=result_text, attachments=[result_file], embed=result_embed, view=result_view)
        except (discord.NotFound, discord.HTTPException, TypeError, ValueError, AttributeError):
            await safe_send_or_channel_message(interaction, content=result_text, files=[result_file], embed=result_embed, view=result_view, ephemeral=False)
    finally:
        _clear_runtime_cards(player_team)
        _clear_runtime_cards(enemy_team)


async def start_dueling_grounds_custom_user_battle(
    interaction: discord.Interaction,
    creator_id: int,
    creator_name: str,
    joiner_id: int,
    joiner_name: str,
    settings: dict,
    lobby_code: str = '',
):
    player_team = _active_team_fighters_for_user(creator_id, "lobby")
    enemy_team = _active_team_fighters_for_user(joiner_id, "lobby")
    if len(player_team) < TEAM_SIZE or len(enemy_team) < TEAM_SIZE:
        await safe_edit_interaction_message(interaction, content=f'Both players need an active Lobby team with {TEAM_SIZE} monsters before starting a custom match.', attachments=[], embed=None, view=None)
        return
    disable_cards = bool(settings.get('disable_cards'))
    disable_equips = bool(settings.get('disable_equips'))
    hearts = max(1, min(5, int(settings.get('hearts', DUELING_GROUNDS_BATTLE_HEARTS) or DUELING_GROUNDS_BATTLE_HEARTS)))
    player_runtime = None if disable_cards else build_support_deck_runtime(db.get_active_support_deck(creator_id, "lobby"), user_id=creator_id)
    enemy_runtime = None if disable_cards else build_support_deck_runtime(db.get_active_support_deck(joiner_id, "lobby"), user_id=joiner_id)
    await _run_dueling_grounds_exhibition_battle(
        interaction,
        owner_id=creator_id,
        owner_name=creator_name,
        enemy_name=joiner_name,
        player_team=player_team,
        enemy_team=enemy_team,
        player_runtime=player_runtime,
        enemy_runtime=enemy_runtime,
        player_avatar_path=db.get_avatar_path(creator_id),
        enemy_avatar_path=db.get_avatar_path(joiner_id),
        mode_label='Dueling Grounds • Custom Match',
        battle_title=f'{creator_name} vs {joiner_name}',
        hearts=hearts,
        disable_equips=disable_equips,
        player_user_id=creator_id,
        enemy_user_id=joiner_id,
        opening_lines=[f'Custom lobby match found. {creator_name} vs {joiner_name}.'],
        result_speech_win=f'{creator_name} defeats {joiner_name} in the custom match.',
        result_speech_loss=f'{joiner_name} defeats {creator_name} in the custom match.',
        player_header_note='Player 1',
        enemy_header_note='Player 2',
        lobby_code=lobby_code,
    )


async def start_duelist_vs_duelist_battle(interaction: discord.Interaction, owner_id: int, user_name: str, first_duelist_key: str, second_duelist_key: str):
    _, first_duelist = choose_specific_dueling_grounds_duelist(first_duelist_key)
    _, second_duelist = choose_specific_dueling_grounds_duelist(second_duelist_key)
    first_team, first_duelist = build_dueling_grounds_enemy_team(first_duelist_key)
    second_team, second_duelist = build_dueling_grounds_enemy_team(second_duelist_key)
    first_name = str(first_duelist.get('name', first_duelist_key))
    second_name = str(second_duelist.get('name', second_duelist_key))
    first_runtime, _ = build_dueling_grounds_enemy_support_runtime(first_name, first_team)
    second_runtime, _ = build_dueling_grounds_enemy_support_runtime(second_name, second_team)
    await _run_dueling_grounds_exhibition_battle(
        interaction,
        owner_id=owner_id,
        owner_name=first_name,
        enemy_name=second_name,
        player_team=first_team,
        enemy_team=second_team,
        player_runtime=first_runtime,
        enemy_runtime=second_runtime,
        player_avatar_path=str(get_duelist_portrait_path(first_duelist_key, first_name) or ''),
        enemy_avatar_path=str(get_duelist_portrait_path(second_duelist_key, second_name) or ''),
        mode_label='Dueling Grounds • Duelist vs Duelist',
        battle_title=f'{first_name} vs {second_name}',
        hearts=DUELING_GROUNDS_BATTLE_HEARTS,
        disable_equips=False,
        opening_lines=[first_duelist.get('pre', "Let's duel!"), second_duelist.get('pre', "Let's duel!")],
        result_speech_win=f'{first_name} wins the Duelist vs Duelist exhibition.',
        result_speech_loss=f'{second_name} wins the Duelist vs Duelist exhibition.',
    )
