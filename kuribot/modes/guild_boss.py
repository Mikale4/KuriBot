"""Running a guild boss battle."""

from __future__ import annotations

import asyncio
from typing import List

import discord

from kuribot.config import (
    BATTLE_HEARTS, BATTLE_TURN_DELAY, GUILD_BOSS_ATTEMPT_GUILD_COIN_REWARD,
    GUILD_BOSS_KILLING_BLOW_BONUS_COINS, GUILD_BOSS_MAX_PARTICIPATION_CHANCES,
    GUILD_BOSS_MAX_ROUNDS, GUILD_BOSS_SUPPORT_DISABLED_ROUNDS, GUILD_BOSS_TOP_DAMAGE_BONUS_COINS,
    SPEED_GAUGE_MAX, TEAM_SIZE,
)
from kuribot.timeutil import eastern_midnight_countdown_text
from kuribot.settings import battle_speed_label, should_refresh_battle
from kuribot.database import db
from kuribot.discord_helpers import resolve_guild_member_name_map
from kuribot.monsters import format_monster_types
from kuribot.cards.support_cards import award_support_deck_battle_exp
from kuribot.cards.support_decks import active_support_text, build_support_deck_runtime
from kuribot.battle.runtime import (
    _clear_runtime_cards, ACTIVE_SUPPORT_CARDS_BY_FIGHTER, random_alive, runtime_flag,
    set_runtime_flag, team_alive,
)
from kuribot.battle.stats import support_card_stat_bonus
from kuribot.battle.mechanics import (
    action_target_override, advance_speed_gauges, any_ready_fighter,
    apply_all_pending_battle_start_heart_losses, apply_damage_with_runtime,
    apply_post_turn_status_effects, attempt_ultimate_crystal_magic_save,
    build_battle_stats_payload, decrement_runtime_counters, exact_time_to_next_turn,
    handle_spell_on_critical_taken, handle_spell_on_damage_dealt, handle_spell_on_damage_taken,
    handle_spell_on_turn_end, handle_spell_on_turn_start, handle_spell_when_targeted,
    handle_support_when_targeted, initialize_battle_start_effects,
    initialize_special_support_effects, on_fighter_attacked, prepare_fighter_equip_spell,
    process_bombs_for_all_fighters, resolve_attack_damage, resolve_round_end_spell_effects,
    resolve_special_support_round_effects, resolve_start_of_turn_status, spell_of_pain_bearer,
    SPELL_OF_PAIN_REDIRECT_RATIO, trigger_precog_strike, trigger_warning_point_on_dodge,
    trigger_yummy_surprise_on_dodge,
)
from kuribot.battle.knockouts import (
    build_attack_instances_for_fighter, resolve_fallen_fighters,
    should_grant_neutron_blast_extra_turn,
)
from kuribot.battle.rounds import (
    apply_support_round_end_heart_penalty, consume_blaze_accelerator_heart_losses,
    initialize_battle_round, resolve_support_round_end,
)
from kuribot.battle.setup import _active_team_fighters_for_user, cleanup_battle_context
from kuribot.campaign import campaign_battle_hearts_remaining
from kuribot.guilds import (
    _set_guild_boss_player_support_cards, apply_guild_boss_random_buff,
    apply_guild_boss_two_round_cycle, guild_boss_row_to_fighter, guild_boss_support_enabled,
)
from kuribot.arena import maybe_run_guild_boss_attempt_reset
from kuribot.render.battle import render_battle
from kuribot.ui.common import (
    _reset_discord_file_handles, render_discord_file_async, safe_edit_interaction_message,
    safe_send_interaction_message, safe_send_or_channel_message,
)
from kuribot.ui.battle_results import BattleResultView
from kuribot.ui.guild import GuildBossView
from kuribot.modes.common import single_battle_per_player


@single_battle_per_player("guild_boss")
async def start_guild_boss_battle(interaction: discord.Interaction, owner_id: int, user_name: str):
    membership = db.get_user_guild_membership(owner_id)
    if not membership:
        await interaction.response.send_message("You are not in a guild.", ephemeral=True)
        return

    guild_id = int(membership["guild_id"])
    guild_name = str(membership["name"] or "Guild")
    if db.guild_boss_defeated_today(guild_id):
        await safe_edit_interaction_message(
            interaction,
            content=(
                "Your guild has already defeated a Guild Boss today. "
                f"The next Guild Boss can be challenged after **12:00 AM ET** (in {eastern_midnight_countdown_text()})."
            ),
            attachments=[],
            embed=None,
            view=GuildBossView(owner_id, user_name),
        )
        return
    boss_row = db.get_or_create_guild_boss(guild_id)
    if boss_row is None:
        await interaction.response.send_message("No monsters are available to create a Guild Boss.", ephemeral=True)
        return
    if int(boss_row["current_hp"] or 0) <= 0:
        boss_row = db.get_or_create_guild_boss(guild_id)
        if boss_row is None:
            await interaction.response.send_message("No monsters are available to create a Guild Boss.", ephemeral=True)
            return

    player_team = _active_team_fighters_for_user(owner_id, "guild_boss")
    if len(player_team) < TEAM_SIZE:
        await safe_edit_interaction_message(
            interaction,
            content=f"You need an active Guild Boss team with {TEAM_SIZE} monsters before challenging the Guild Boss.",
            attachments=[],
            embed=None,
            view=GuildBossView(owner_id, user_name),
        )
        return

    boss = guild_boss_row_to_fighter(boss_row)
    boss.hp = max(0, int(boss_row["current_hp"] or boss.max_hp))
    boss_team = [boss]
    boss_name = boss.name
    boss_starting_hp = boss.hp
    guild_boss_id = int(boss_row["id"])
    maybe_run_guild_boss_attempt_reset()
    participation_chances = db.get_guild_boss_participation_chances(guild_boss_id, owner_id)
    if participation_chances >= GUILD_BOSS_MAX_PARTICIPATION_CHANCES:
        await safe_edit_interaction_message(
            interaction,
            content=f"You have already used all {GUILD_BOSS_MAX_PARTICIPATION_CHANCES} Guild Boss chances for this boss.",
            attachments=[],
            embed=None,
            view=GuildBossView(owner_id, user_name),
        )
        return

    player_deck_row = db.get_active_support_deck(owner_id, "guild_boss")
    player_runtime = build_support_deck_runtime(player_deck_row, user_id=owner_id)
    enemy_runtime = None
    battle_theme_name = db.get_ui_theme(owner_id)
    battle_speed = db.get_battle_speed(owner_id)
    player_avatar_path = db.get_avatar_path(owner_id)

    for fighter in [*player_team, *boss_team]:
        fighter.speed_gauge = 0.0

    _set_guild_boss_player_support_cards(player_team, player_runtime, enabled=False)
    for fighter in player_team:
        prepare_fighter_equip_spell(fighter, owner_id)

    # Guild Boss rules: no deck, no hearts, starts with IMMUNITY.
    set_runtime_flag(boss, "immunity_turns", max(1, int(runtime_flag(boss, "immunity_turns", 0))))

    battle_title = f"{guild_name} Guild Boss: {boss_name}"
    player_hearts = BATTLE_HEARTS
    boss_hearts = BATTLE_HEARTS
    log_entries: List[str] = [
        f"**{guild_name}** sounds the alarm! Guild members must defeat **{boss_name}**.",
        f"{user_name} challenges the Guild Boss with {', '.join(f'{f.name} ({f.attribute}) [{format_monster_types(f.monster_types)}]' for f in player_team)}.",
        f"**{boss_name}** has **{boss.hp:,}/{boss.max_hp:,} HP**, **50x HP**, **2x ATK**, no deck, and cannot lose hearts.",
        f"Support cards are disabled only for rounds 1-{GUILD_BOSS_SUPPORT_DISABLED_ROUNDS}, then return for the rest of the battle.",
        "If the Guild Boss receives a debuff, it immediately counterattacks the monster that inflicted it.",
        "BURN and POISON deal half damage against Guild Bosses.",
        "At the end of every 2 rounds, the Guild Boss cleanses debuffs; it only heals 15% HP if at least one debuff was removed, then may STUN each monster.",
        f"{user_name} support deck will unlock after round {GUILD_BOSS_SUPPORT_DISABLED_ROUNDS}: {active_support_text(player_runtime)}." if player_runtime else f"{user_name} is battling without a Support Deck.",
        f"**{boss_name}** starts the battle with **IMMUNITY**.",
    ]

    initialize_battle_start_effects(player_team, owner_id, boss_team, None, log_entries)
    _set_guild_boss_player_support_cards(player_team, player_runtime, enabled=False)
    player_hearts, boss_hearts = apply_all_pending_battle_start_heart_losses(
        player_team, boss_team, player_hearts, boss_hearts, user_name, boss_name, log_entries, max_hearts=BATTLE_HEARTS
    )
    boss_hearts = BATTLE_HEARTS
    apply_guild_boss_random_buff(boss, log_entries, 1)

    battle_file = await render_discord_file_async(
        render_battle,
        'guild_boss_battle.png',
        user_name,
        boss_name,
        player_team,
        boss_team,
        log_entries,
        tick_info="Battle start",
        round_info=f"Round 1/{GUILD_BOSS_MAX_ROUNDS}",
        player_support_runtime=None,
        enemy_support_runtime=enemy_runtime,
        player_hearts=campaign_battle_hearts_remaining(player_hearts, BATTLE_HEARTS),
        enemy_hearts=None,
        max_hearts=BATTLE_HEARTS,
        player_avatar_path=player_avatar_path,
        enemy_avatar_path=str(boss.image_path or ""),
        player_header_note=None,
        enemy_header_note="No hearts • No deck",
        theme_name=battle_theme_name,
    )

    try:
        await safe_edit_interaction_message(
            interaction,
            content=f"🐉 Guild Boss • {boss_name} battle in progress...",
            attachments=[battle_file],
            embed=None,
            view=None,
        )
        try:
            message = await interaction.original_response()
        except (discord.NotFound, discord.HTTPException):
            message = interaction.message
    except Exception:
        message = await safe_send_interaction_message(
            interaction,
            content=f"🐉 Guild Boss • {boss_name} battle in progress...",
            file=battle_file,
            ephemeral=False,
        )

    turns = 1
    completed_rounds = 0
    fighters_pending_in_round = initialize_battle_round([*player_team, *boss_team])
    defeated_fighter_ids: set[int] = set()
    result_text = ""

    try:
        while player_hearts > 0 and team_alive(player_team) and boss.alive and completed_rounds < GUILD_BOSS_MAX_ROUNDS and turns <= 300:
            active = any_ready_fighter((player_team, boss_team))
            if active is None:
                elapsed = exact_time_to_next_turn((player_team, boss_team))
                active, _ = advance_speed_gauges((player_team, boss_team), elapsed)
                if active is None:
                    continue

            support_enabled = guild_boss_support_enabled(completed_rounds)
            _set_guild_boss_player_support_cards(player_team, player_runtime, enabled=support_enabled)
            acting_team = player_team if active in player_team else boss_team
            defending_team = boss_team if acting_team is player_team else player_team
            active_name = user_name if acting_team is player_team else boss_name
            attacker_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(active), [])

            atk_bonus = support_card_stat_bonus(active, attacker_cards, "ATK")
            def_bonus = support_card_stat_bonus(active, attacker_cards, "DEF")
            spd_bonus = support_card_stat_bonus(active, attacker_cards, "SPD")
            log_entries.append(f"Turn {turns}: **{active.name}** ({active_name}) takes the turn.")
            if atk_bonus:
                log_entries.append(f"{active.name} gains **+{atk_bonus} ATK** from Support Cards.")
            if def_bonus:
                log_entries.append(f"{active.name} gains **+{def_bonus} DEF** from Support Cards.")
            if spd_bonus:
                log_entries.append(f"{active.name} gains **+{spd_bonus} SPD** from Support Cards.")

            process_bombs_for_all_fighters([*acting_team, *defending_team], log_entries)
            handle_spell_on_turn_start(active, acting_team, defending_team, log_entries)
            player_hearts, boss_hearts = resolve_fallen_fighters(
                player_team, boss_team, user_name, boss_name, player_hearts, boss_hearts, log_entries, defeated_fighter_ids,
            )
            boss_hearts = BATTLE_HEARTS
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
                    player_hearts, boss_hearts = resolve_fallen_fighters(
                        player_team, boss_team, user_name, boss_name, player_hearts, boss_hearts, log_entries, defeated_fighter_ids,
                    )
                    boss_hearts = BATTLE_HEARTS
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

            player_hearts, boss_hearts = resolve_fallen_fighters(
                player_team, boss_team, user_name, boss_name, player_hearts, boss_hearts, log_entries, defeated_fighter_ids,
            )
            boss_hearts = BATTLE_HEARTS
            handle_spell_on_turn_end(active, acting_team, defending_team, log_entries)
            apply_post_turn_status_effects([*player_team, *boss_team], log_entries)
            player_hearts, boss_hearts = resolve_fallen_fighters(
                player_team, boss_team, user_name, boss_name, player_hearts, boss_hearts, log_entries, defeated_fighter_ids,
            )
            boss_hearts = BATTLE_HEARTS

            if active.alive and should_grant_neutron_blast_extra_turn(active, attacker_cards):
                active.speed_gauge = SPEED_GAUGE_MAX
                log_entries.append(f"**Neutron Blast** lets **{active.name}** act again!")
            else:
                active.speed_gauge = 0.0

            fighters_pending_in_round.discard(id(active))
            fighters_pending_in_round = {fighter_id for fighter_id in fighters_pending_in_round if any(id(fighter) == fighter_id and fighter.alive for fighter in [*player_team, *boss_team])}
            player_hearts = attempt_ultimate_crystal_magic_save(player_team, player_runtime, player_hearts, user_name, log_entries)
            boss_hearts = BATTLE_HEARTS
            player_hearts, boss_hearts = consume_blaze_accelerator_heart_losses(
                player_team, boss_team, player_hearts, boss_hearts, user_name, boss_name, log_entries,
            )
            boss_hearts = BATTLE_HEARTS
            if player_hearts <= 0 or not boss.alive:
                break

            if not fighters_pending_in_round and team_alive(player_team) and boss.alive:
                completed_rounds += 1
                log_entries.append(f"Round {completed_rounds} complete.")
                apply_guild_boss_two_round_cycle(boss, player_team, log_entries, completed_rounds)
                if completed_rounds == GUILD_BOSS_SUPPORT_DISABLED_ROUNDS:
                    _set_guild_boss_player_support_cards(player_team, player_runtime, enabled=True)
                    initialize_special_support_effects(player_team, boss_team, player_runtime, log_entries)
                    log_entries.append(f"Support cards are now enabled for **{user_name}** starting next round.")
                elif guild_boss_support_enabled(completed_rounds):
                    resolve_special_support_round_effects(player_team, boss_team, player_runtime, enemy_runtime, log_entries)
                    player_hearts = apply_support_round_end_heart_penalty(
                        player_hearts,
                        resolve_support_round_end(player_runtime, player_team, boss_team, user_name, log_entries, None),
                        BATTLE_HEARTS,
                        player_team,
                        boss_team,
                        user_name,
                        log_entries,
                    )
                resolve_round_end_spell_effects([*player_team, *boss_team], log_entries)
                decrement_runtime_counters(boss)
                if completed_rounds < GUILD_BOSS_MAX_ROUNDS and boss.alive:
                    apply_guild_boss_random_buff(boss, log_entries, completed_rounds + 1)
                fighters_pending_in_round = initialize_battle_round([*player_team, *boss_team])

            player_hearts = attempt_ultimate_crystal_magic_save(player_team, player_runtime, player_hearts, user_name, log_entries)
            boss_hearts = BATTLE_HEARTS
            battle_over = player_hearts <= 0 or not team_alive(player_team) or not boss.alive or completed_rounds >= GUILD_BOSS_MAX_ROUNDS
            should_refresh = should_refresh_battle(battle_speed, turns, not fighters_pending_in_round, battle_over)
            if should_refresh:
                battle_file = await render_discord_file_async(render_battle, 'guild_boss_battle.png', user_name, boss_name, player_team, boss_team, log_entries[-12:], active_fighter=active, target_fighter=target, tick_info=f"Turn {turns}", round_info=f"Round {min(GUILD_BOSS_MAX_ROUNDS, completed_rounds + 1)}/{GUILD_BOSS_MAX_ROUNDS}", player_support_runtime=player_runtime if guild_boss_support_enabled(completed_rounds) else None, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, BATTLE_HEARTS), enemy_hearts=None, max_hearts=BATTLE_HEARTS, player_avatar_path=player_avatar_path, enemy_avatar_path=str(boss.image_path or ""), enemy_header_note="No hearts • No deck", theme_name=battle_theme_name)
                status_text = f"🐉 Guild Boss • {boss_name} battle in progress... Turn {turns} ({battle_speed_label(battle_speed)})"
                try:
                    _reset_discord_file_handles([battle_file])
                    await message.edit(content=status_text, attachments=[battle_file], view=None)
                except (discord.NotFound, discord.HTTPException, TypeError, ValueError, AttributeError):
                    replacement = await safe_send_or_channel_message(interaction, content=status_text, file=battle_file, ephemeral=False)
                    if replacement is not None:
                        message = replacement
                await asyncio.sleep(BATTLE_TURN_DELAY)

            turns += 1
            if turns % 4 == 0:
                await asyncio.sleep(0)  # let other players' commands run during long/instant battles

        local_final_hp = max(0, min(int(boss.hp), int(boss_starting_hp)))
        damage_dealt = max(0, int(boss_starting_hp) - local_final_hp)
        db.record_guild_boss_damage(guild_boss_id, guild_id, owner_id, damage_dealt)
        boss_after, boss_defeated = db.apply_guild_boss_damage(guild_boss_id, damage_dealt)
        boss_final_hp = max(0, int(boss_after["current_hp"])) if boss_after is not None else local_final_hp
        player_won = boss_defeated
        support_cards_used, support_exp_gained, support_stage_ups = ("", 0, "")
        if guild_boss_support_enabled(completed_rounds):
            support_cards_used, support_exp_gained, support_stage_ups = award_support_deck_battle_exp(owner_id, player_deck_row, player_won)

        image_result_text = ""
        if boss_defeated:
            reward_lines = []
            image_reward_lines = []
            participants = db.unrewarded_guild_boss_participants(guild_boss_id)
            participant_ids = [int(participant["user_id"]) for participant in participants]
            participant_name_map = await resolve_guild_member_name_map(participant_ids, interaction.guild)
            for participant in participants:
                participant_id = int(participant["user_id"])
                reward_text = db.grant_guild_boss_defeat_reward(guild_boss_id, participant_id)
                if reward_text == "already rewarded":
                    continue
                participant_name =participant_name_map.get(participant_id, f"User {participant_id}")
                reward_lines.append(f"<@{participant_id}>: {reward_text}")
                image_reward_lines.append(f"{participant_name}: {reward_text}")
            top_participant = db.top_guild_boss_damage_participant(guild_boss_id)
            if top_participant is not None:
                top_user_id = int(top_participant["user_id"])
                if db.grant_guild_boss_bonus_reward(guild_boss_id, top_user_id, "top_damage", GUILD_BOSS_TOP_DAMAGE_BONUS_COINS):
                    top_name = participant_name_map.get(top_user_id, f"User {top_user_id}")
                    bonus_text = f"Top damage bonus: +{GUILD_BOSS_TOP_DAMAGE_BONUS_COINS} guild coins"
                    reward_lines.append(f"<@{top_user_id}>: {bonus_text}")
                    image_reward_lines.append(f"{top_name}: {bonus_text}")
            killer_user_id = int(owner_id)
            if db.grant_guild_boss_bonus_reward(guild_boss_id, killer_user_id, "killing_blow", GUILD_BOSS_KILLING_BLOW_BONUS_COINS):
                killer_name = participant_name_map.get(killer_user_id, user_name)
                bonus_text = f"Killing blow bonus: +{GUILD_BOSS_KILLING_BLOW_BONUS_COINS} guild coins"
                reward_lines.append(f"<@{killer_user_id}>: {bonus_text}")
                image_reward_lines.append(f"{killer_name}: {bonus_text}")
            result_text = f"Victory! **{guild_name}** defeated **{boss_name}**. Rewards delivered to participating guild members."
            image_result_text = result_text
            if reward_lines:
                result_text += "\n" + "\n".join(reward_lines[:12])
                image_result_text += "\n" + "\n".join(image_reward_lines[:12])
        elif player_hearts <= 0 or not team_alive(player_team):
            result_text = f"Defeat. **{boss_name}** remains at **{boss_final_hp:,}/{boss.max_hp:,} HP** for the guild."
        else:
            result_text = f"Battle ended after **{GUILD_BOSS_MAX_ROUNDS} rounds**. **{boss_name}** remains at **{boss_final_hp:,}/{boss.max_hp:,} HP** for the guild."

        attempt_reward_text = f"\nAttempt complete: <@{owner_id}> gained **{GUILD_BOSS_ATTEMPT_GUILD_COIN_REWARD} guild coins**."
        result_text += attempt_reward_text
        if image_result_text:
            image_result_text += f"\nAttempt complete: {user_name} gained {GUILD_BOSS_ATTEMPT_GUILD_COIN_REWARD} guild coins."

        if support_cards_used:
            support_result_text = f" Support cards equipped: {support_cards_used}."
            result_text += support_result_text
            if image_result_text:
                image_result_text += support_result_text
            if support_stage_ups:
                stage_up_text = ""
                result_text += stage_up_text
                if image_result_text:
                    image_result_text += stage_up_text

        log_entries.append(image_result_text or result_text)
        result_file = await render_discord_file_async(render_battle, 'guild_boss_battle.png', user_name, boss_name, player_team, boss_team, log_entries[-12:], tick_info="Battle complete", round_info=f"Round {min(GUILD_BOSS_MAX_ROUNDS, completed_rounds + 1)}/{GUILD_BOSS_MAX_ROUNDS}", player_support_runtime=player_runtime if guild_boss_support_enabled(completed_rounds) else None, enemy_support_runtime=enemy_runtime, player_hearts=campaign_battle_hearts_remaining(player_hearts, BATTLE_HEARTS), enemy_hearts=None, max_hearts=BATTLE_HEARTS, player_avatar_path=player_avatar_path, enemy_avatar_path=str(boss.image_path or ""), enemy_header_note="No hearts • No deck", theme_name=battle_theme_name)
        battle_stats_payload = build_battle_stats_payload([*player_team, *boss_team])
        try:
            await message.edit(
                content=result_text,
                attachments=[result_file],
                embed=None,
                view=BattleResultView(
                    owner_id,
                    battle_title,
                    log_entries,
                    user_name=user_name,
                    allow_battle_again=True,
                    battle_mode='guild_boss',
                    battle_stats=battle_stats_payload,
                ),
            )
        except Exception:
            await interaction.followup.send(
                content=result_text,
                file=result_file,
                view=BattleResultView(
                    owner_id,
                    battle_title,
                    log_entries,
                    user_name=user_name,
                    allow_battle_again=True,
                    battle_mode='guild_boss',
                    battle_stats=battle_stats_payload,
                ),
            )

    finally:
        cleanup_battle_context([*player_team, *boss_team])
        # Guild boss battles never released their fighters from the global battle maps, so
        # a surviving boss-battle monster (e.g. one holding Disarmament or Clockwork Night)
        # kept affecting every later battle until the bot restarted.
        _clear_runtime_cards(player_team)
        _clear_runtime_cards(boss_team)
