"""Running the tutorial battle."""

from __future__ import annotations

from typing import Dict, List

import discord

from kuribot.database import db
from kuribot.portraits import get_duelist_portrait_path
from kuribot.dueling_grounds import build_dueling_grounds_enemy_team
from kuribot.tutorial import (
    build_tutorial_battle_complete_embed, build_tutorial_hearts_complete_embed,
    choose_two_tutorial_duelists,
)
from kuribot.ui.tutorial import TutorialBattleFinishedView, TutorialBattleHeartsView
from kuribot.modes.exhibition import _run_dueling_grounds_exhibition_battle


async def start_tutorial_battle(interaction: discord.Interaction, owner_id: int, user_name: str, *, hearts: int, hearts_lesson: bool = False):
    first_key, first_duelist, second_key, second_duelist = choose_two_tutorial_duelists()
    first_team, first_duelist = build_dueling_grounds_enemy_team(first_key)
    second_team, second_duelist = build_dueling_grounds_enemy_team(second_key)
    first_name = str(first_duelist.get('name', first_key))
    second_name = str(second_duelist.get('name', second_key))

    def result_embed_factory(result_text: str, player_won: bool, enemy_won: bool) -> discord.Embed:
        if hearts_lesson:
            db.mark_tutorial_hearts_done(owner_id)
            rewarded = db.claim_tutorial_battle_ticket_reward(owner_id, 10)
            return build_tutorial_hearts_complete_embed(user_name, result_text, rewarded)
        db.mark_tutorial_battle_done(owner_id)
        return build_tutorial_battle_complete_embed(user_name, result_text)

    def result_view_factory(battle_title: str, log_entries: List[str], battle_stats: Dict[str, List[Dict[str, int | str]]], result_text: str, player_won: bool, enemy_won: bool) -> discord.ui.View:
        if hearts_lesson:
            return TutorialBattleFinishedView(owner_id, user_name, battle_title, log_entries, battle_stats)
        return TutorialBattleHeartsView(owner_id, user_name, battle_title, log_entries, battle_stats)

    await _run_dueling_grounds_exhibition_battle(
        interaction,
        owner_id=owner_id,
        owner_name=first_name,
        enemy_name=second_name,
        player_team=first_team,
        enemy_team=second_team,
        player_runtime=None,
        enemy_runtime=None,
        player_avatar_path=str(get_duelist_portrait_path(first_key, first_name) or ''),
        enemy_avatar_path=str(get_duelist_portrait_path(second_key, second_name) or ''),
        mode_label='Tutorial • Battle' if not hearts_lesson else 'Tutorial • Hearts',
        battle_title=f'{first_name} vs {second_name}',
        hearts=hearts,
        disable_equips=True,
        opening_lines=[
            'Kuribot starts a tutorial battle between two random duelists.',
            f'{first_name}: {first_duelist.get("pre", "Let\'s duel!")}',
            f'{second_name}: {second_duelist.get("pre", "Let\'s duel!")}',
        ],
        result_speech_win=f'{first_name} wins the tutorial duel.',
        result_speech_loss=f'{second_name} wins the tutorial duel.',
        result_speech_draw='Both tutorial duelists fought to a standstill.',
        battle_speed_override='normal',
        result_embed_factory=result_embed_factory,
        result_view_factory=result_view_factory,
    )
