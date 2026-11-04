"""Running a tournament battle."""

from __future__ import annotations

from typing import Dict, List, Optional

import discord

from kuribot.config import DUELING_GROUNDS_BATTLE_HEARTS, RNG, TEAM_SIZE
from kuribot.tournament import (
    _ACTIVE_TOURNAMENTS, _tourney_avatar_path, _tourney_battle_sides, _tourney_get, _tourney_name,
    _tourney_record, _tourney_round_label, _tourney_score,
)
from kuribot.ui.common import safe_edit_interaction_message
from kuribot.ui.tournament import TournamentAfterBattleView
from kuribot.modes.common import single_battle_per_player
from kuribot.modes.exhibition import _run_dueling_grounds_exhibition_battle


@single_battle_per_player("tournament")
async def start_tournament_visible_battle(interaction: discord.Interaction, owner_id: int, user_name: str, ri: int, mi: int, *, player_team_id: Optional[int] = None):
    t=_tourney_get(int(owner_id))
    if not t:
        await safe_edit_interaction_message(interaction, content='Tournament not found. Start a new Tournament from the hub.', attachments=[], embed=None, view=None); return
    m=t['rounds'][ri][mi]; a_id=str(m.get('a')); b_id=str(m.get('b'))
    a_name,a_team,a_runtime,a_note,b_name,b_team,b_runtime,b_note=_tourney_battle_sides(t,m,player_team_id)
    if len(a_team)!=TEAM_SIZE or len(b_team)!=TEAM_SIZE:
        await safe_edit_interaction_message(interaction, content='A Tournament team is invalid. Re-save teams and restart the bracket.', attachments=[], embed=None, view=None); return
    def result_embed_factory(result_text: str, a_won: bool, b_won: bool) -> discord.Embed:
        winner=a_id if a_won else b_id if b_won else (a_id if RNG.random()<0.5 else b_id); loser=b_id if winner==a_id else a_id
        _tourney_record(t,ri,mi,winner,loser,'Watched'); _ACTIVE_TOURNAMENTS[int(owner_id)]=t
        embed=discord.Embed(title='Tournament Battle Complete', description=f"{result_text}\n\nMatch score: **{_tourney_score(t,m)}**", color=discord.Color.blurple())
        embed.add_field(name='Round', value=_tourney_round_label(ri), inline=True); embed.add_field(name='Battle Winner', value=_tourney_name(t,winner), inline=True)
        if m.get('winner'): embed.add_field(name='Match Winner', value=_tourney_name(t,m.get('winner')), inline=False)
        return embed
    def result_view_factory(battle_title: str, log_entries: List[str], battle_stats_payload: Dict[str, List[Dict[str, int | str]]], result_text: str, a_won: bool, b_won: bool) -> discord.ui.View:
        return TournamentAfterBattleView(int(owner_id), user_name, ri, mi, not bool(m.get('winner')))
    await _run_dueling_grounds_exhibition_battle(
        interaction, owner_id=int(owner_id), owner_name=a_name, enemy_name=b_name, player_team=a_team, enemy_team=b_team, player_runtime=a_runtime, enemy_runtime=b_runtime,
        player_user_id=int(owner_id) if a_id=='player' else None, enemy_user_id=int(owner_id) if b_id=='player' else None,
        player_avatar_path=_tourney_avatar_path(t, a_id, int(owner_id)), enemy_avatar_path=_tourney_avatar_path(t, b_id, int(owner_id)), mode_label='Dueling Grounds • Tournament',
        battle_title=f'Tournament: {a_name} vs {b_name}', player_header_note=a_note, enemy_header_note=b_note, hearts=DUELING_GROUNDS_BATTLE_HEARTS, battle_speed_override='normal',
        opening_lines=[f"{_tourney_round_label(ri)} Match {mi + 1} — Battle {len(m.get('games') or []) + 1} of a best-of-3."], result_speech_win=f'{a_name} wins this tournament battle.', result_speech_loss=f'{b_name} wins this tournament battle.', result_speech_draw='Kuribot breaks the draw by sudden-death tournament ruling.', result_embed_factory=result_embed_factory, result_view_factory=result_view_factory)
