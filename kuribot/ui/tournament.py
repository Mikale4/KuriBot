"""Tournament menus."""

from __future__ import annotations

import io
from typing import List, Tuple

import discord
from PIL import Image, ImageDraw

from kuribot.config import TEAM_SIZE
from kuribot.timeutil import eastern_midnight_countdown_text
from kuribot.database import db
from kuribot.render.fonts import FONT_M_BOLD, FONT_S_BOLD, FONT_XL_BOLD, FONT_XS_BOLD
from kuribot.render.themes import get_user_ui_theme_palette
from kuribot.render.text import ellipsize_text
from kuribot.tournament import (
    _ACTIVE_TOURNAMENTS, _TOURNAMENT_SETUPS, _tourney_active_status_label,
    _tourney_attachment_suffix, _tourney_champion, _tourney_completed_today,
    _tourney_current_match, _tourney_debug_mode_enabled, _tourney_delete_persisted,
    _tourney_finish, _tourney_get, _tourney_is_complete, _tourney_is_player_match, _tourney_name,
    _tourney_new, _tourney_persist, _tourney_player_team, _tourney_round_label, _tourney_score,
    _tourney_setup, _tourney_skip, _tourney_team_label, _tourney_team_rows,
    _view_remove_item_if_present, TOURNAMENT_WINNER_GOLD, TOURNAMENT_WINNER_PREMIUM_TICKETS,
    TOURNAMENT_WINNER_TICKETS,
)
from kuribot.render.hubs import render_tournament_hub_page
from kuribot.ui.common import safe_edit_interaction_message
from kuribot.ui.dueling_grounds import ArenaEnabledDuelingGroundsHubView


def render_tournament_bracket_page(tourney: dict) -> io.BytesIO:
    width, height = 1320, 900
    img = Image.new('RGBA', (width, height), (10, 12, 28, 255))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(int(tourney['owner_id']))
    draw.text((40, 26), 'KuriBot Tournament Bracket', font=FONT_XL_BOLD, fill=(245, 238, 255, 255))
    draw.text((40, 76), 'Best-of-3 • Single Elimination • Matches go in order', font=FONT_S_BOLD, fill=(215, 194, 255, 255))
    current = _tourney_current_match(tourney)
    xs = [40, 360, 680, 1000]
    bracket_top, bracket_bottom = 160, 765
    bracket_h = bracket_bottom - bracket_top

    def draw_current_match_arrow(box: Tuple[int, int, int, int]):
        cy = (box[1] + box[3]) // 2
        if box[2] + 52 < width - 18:
            arrow = [(box[2] + 6, cy), (box[2] + 22, cy - 11), (box[2] + 22, cy - 5), (box[2] + 48, cy - 5), (box[2] + 48, cy + 5), (box[2] + 22, cy + 5), (box[2] + 22, cy + 11)]
        else:
            arrow = [(box[0] + 10, cy), (box[0] + 28, cy - 11), (box[0] + 28, cy - 5), (box[0] + 58, cy - 5), (box[0] + 58, cy + 5), (box[0] + 28, cy + 5), (box[0] + 28, cy + 11)]
        draw.polygon(arrow, fill=(255, 220, 120, 255), outline=(82, 58, 12, 255))

    for ri, matches in enumerate(tourney.get('rounds', [])[:4]):
        x = xs[ri]
        draw.text((x, 122), _tourney_round_label(ri), font=FONT_M_BOLD, fill=palette['title'])
        count = max(1, len(matches))
        slot_h = 62 if count >= 8 else (96 if count >= 4 else (138 if count >= 2 else 210))
        gap = 10 if count >= 8 else (18 if count >= 4 else 28)
        total_h = count * slot_h + (count - 1) * gap
        y = bracket_top + max(0, (bracket_h - total_h) // 2)
        for mi, m in enumerate(matches):
            box = (x, y, x + 260, y + slot_h)
            is_current = bool(current and current[0] == ri and current[1] == mi)
            outline = (255, 220, 120, 255) if is_current else ((90, 220, 140, 255) if m.get('winner') else (120, 132, 180, 255))
            draw.rounded_rectangle(box, 16, fill=(25, 28, 58, 245), outline=outline, width=3)
            wins = m.get('wins') or {}; a, b = m.get('a'), m.get('b')
            top_offset = 11 if slot_h <= 70 else 18
            bottom_offset = max(top_offset + 24, slot_h - (27 if slot_h <= 70 else 32))
            for offset, cid in ((top_offset, a), (bottom_offset, b)):
                fill = (110, 240, 150, 255) if m.get('winner') and str(m.get('winner')) == str(cid) else (235, 238, 255, 255)
                draw.text((box[0] + 16, box[1] + offset), ellipsize_text(draw, _tourney_name(tourney, cid), FONT_XS_BOLD, 190), font=FONT_XS_BOLD, fill=fill)
                draw.text((box[2] - 22, box[1] + offset), str(int(wins.get(cid, 0))) if cid else '-', font=FONT_XS_BOLD, fill=fill, anchor='ra')
            if is_current:
                draw_current_match_arrow(box)
            y += slot_h + gap
    champ = _tourney_champion(tourney)
    panel_text = f"Champion: {_tourney_name(tourney, champ)}" if champ else 'Press Battle to play the current match.'
    bbox = draw.textbbox((0, 0), panel_text, font=FONT_M_BOLD)
    panel_w = min(width - 80, max(360, (bbox[2] - bbox[0]) + 64))
    panel_x1 = (width - panel_w) // 2
    panel_x2 = panel_x1 + panel_w
    draw.rounded_rectangle((panel_x1, 815, panel_x2, 872), 20, fill=(25, 28, 58, 245), outline=(190, 150, 255, 255), width=2)
    draw.text((width // 2, 844), panel_text, font=FONT_M_BOLD, fill=(245, 238, 255, 255), anchor='mm')
    buffer = io.BytesIO(); img.save(buffer, format='PNG'); buffer.seek(0); return buffer


class TournamentTeamSelect(discord.ui.Select):
    def __init__(self, view: 'TournamentTeamSetupView', role: str):
        self.setup_view = view; self.role = role
        options = [] if role == 'main' else [discord.SelectOption(label='None', value='0', description='Leave this slot empty.')]
        for row in _tourney_team_rows(view.owner_id):
            tid = int(row['id'])
            if role != 'main' and tid == view.main_team_id:
                continue
            options.append(discord.SelectOption(label=str(row['name'])[:100], value=str(tid), description=_tourney_team_label(row)[:100]))
        if not options:
            options = [discord.SelectOption(label='No valid saved teams found', value='0')]
        super().__init__(placeholder={'main': 'Choose Main Team', 's1': 'Choose Secondary Team 1', 's2': 'Choose Secondary Team 2'}[role], min_values=1, max_values=1, options=options[:25], row={'main':0,'s1':1,'s2':2}[role])
    async def callback(self, interaction: discord.Interaction):
        val = int(self.values[0] or 0)
        if self.role == 'main':
            self.setup_view.main_team_id = val
            self.setup_view.secondary_ids = [x for x in self.setup_view.secondary_ids if x != val]
        else:
            idx = 0 if self.role == 's1' else 1
            while len(self.setup_view.secondary_ids) < 2: self.setup_view.secondary_ids.append(0)
            self.setup_view.secondary_ids[idx] = 0 if val == self.setup_view.main_team_id else val
            seen = []
            for x in self.setup_view.secondary_ids:
                if x and x not in seen and x != self.setup_view.main_team_id: seen.append(x)
            self.setup_view.secondary_ids = seen[:2]
        self.setup_view.rebuild()
        await safe_edit_interaction_message(interaction, content=self.setup_view.summary_text(), attachments_factory=lambda: [self.setup_view.make_file()], embed=None, view=self.setup_view)


class TournamentTeamSetupView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=600); self.owner_id=int(owner_id); self.user_name=user_name
        setup = _tourney_setup(owner_id); self.main_team_id=int(setup.get('main_team_id') or 0); self.secondary_ids=list(setup.get('secondary_team_ids') or [])[:2]
        self.rebuild()
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Tournament setup belongs to someone else.', ephemeral=True); return False
        return True
    def rebuild(self):
        self.clear_items(); self.add_item(TournamentTeamSelect(self,'main')); self.add_item(TournamentTeamSelect(self,'s1')); self.add_item(TournamentTeamSelect(self,'s2')); self.add_item(self.save_button); self.add_item(self.back_button); self.save_button.disabled = not bool(self.main_team_id)
    def summary_text(self) -> str:
        rows = {int(r['id']): r for r in db.list_teams(self.owner_id)}; secs=[_tourney_team_label(rows.get(x)) for x in self.secondary_ids if x in rows]
        return f"Tournament Team Setup\nMain: **{_tourney_team_label(rows.get(self.main_team_id))}**\nSecondary Teams: **{', '.join(secs) if secs else 'None'}**"
    def make_file(self) -> discord.File:
        setup = {'main_team_id': self.main_team_id, 'secondary_team_ids': [x for x in self.secondary_ids if x]}
        return discord.File(render_tournament_hub_page(self.user_name, self.owner_id, setup), filename=f'tournament_team_setup_{_tourney_attachment_suffix(self.owner_id)}.png')
    @discord.ui.button(label='Save Tournament Teams', style=discord.ButtonStyle.success, row=3)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        valid = {int(r['id']) for r in _tourney_team_rows(self.owner_id)}
        if self.main_team_id not in valid:
            await interaction.response.send_message('Choose a valid Main Team first.', ephemeral=True); return
        secs = [x for x in self.secondary_ids if x in valid and x != self.main_team_id][:2]
        _TOURNAMENT_SETUPS[self.owner_id] = {'main_team_id': self.main_team_id, 'secondary_team_ids': secs}
        view = TournamentHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content='Saved Tournament teams. Start is unlocked.\n' + view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TournamentHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class TournamentHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300); self.owner_id=int(owner_id); self.user_name=user_name; self.start_button.disabled = not bool(_tourney_setup(owner_id).get('main_team_id'))
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Tournament hub belongs to someone else.', ephemeral=True); return False
        return True
    def make_file(self) -> discord.File:
        return discord.File(render_tournament_hub_page(self.user_name, self.owner_id), filename=f'tournament_hub_{_tourney_attachment_suffix(self.owner_id)}.png')
    def summary_text(self) -> str:
        return f"Tournament hub • Main Team: **{'Set' if _tourney_setup(self.owner_id).get('main_team_id') else 'Not set'}** • Bracket: **{_tourney_active_status_label(self.owner_id)}**"
    @discord.ui.button(label='Details', style=discord.ButtonStyle.secondary, disabled=True)
    async def details_button(self, interaction: discord.Interaction, button: discord.ui.Button): pass
    @discord.ui.button(label='Sign-up', style=discord.ButtonStyle.secondary, disabled=True)
    async def signup_button(self, interaction: discord.Interaction, button: discord.ui.Button): pass
    @discord.ui.button(label='Teams', style=discord.ButtonStyle.primary)
    async def teams_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TournamentTeamSetupView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
    @discord.ui.button(label='Start', style=discord.ButtonStyle.success)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing = _tourney_get(self.owner_id)
        if not _tourney_debug_mode_enabled() and not existing and _tourney_completed_today(self.owner_id):
            await interaction.response.send_message(f'You already completed a Tournament today. Try again after the daily reset in {eastern_midnight_countdown_text()}.', ephemeral=True)
            return
        if existing and not _tourney_is_complete(existing):
            view = TournamentBracketView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content='Resuming your active Tournament. Finish it before starting a new bracket.\n' + view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
            return
        setup = _tourney_setup(self.owner_id)
        if not setup.get('main_team_id') or len(_tourney_player_team(self.owner_id, setup['main_team_id'])) != TEAM_SIZE:
            await interaction.response.send_message('Save a valid Main Team before starting.', ephemeral=True); return
        if existing and _tourney_is_complete(existing):
            if not _tourney_debug_mode_enabled() and _tourney_completed_today(self.owner_id):
                await interaction.response.send_message(f'You already completed a Tournament today. Try again after the daily reset in {eastern_midnight_countdown_text()}.', ephemeral=True)
                return
            _ACTIVE_TOURNAMENTS.pop(self.owner_id, None)
            _tourney_delete_persisted(self.owner_id)
        tourney = _tourney_new(self.owner_id, self.user_name)
        _ACTIVE_TOURNAMENTS[self.owner_id] = tourney
        _tourney_persist(tourney)
        view = TournamentBracketView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content='Started a new Tournament bracket. Progress will be saved automatically.\n' + view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
    @discord.ui.button(label='Bracket', style=discord.ButtonStyle.secondary, row=1)
    async def bracket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _tourney_get(self.owner_id):
            await interaction.response.send_message('Start a Tournament first.', ephemeral=True); return
        view = TournamentBracketView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ArenaEnabledDuelingGroundsHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class TournamentBracketView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=600); self.owner_id=int(owner_id); self.user_name=user_name; self._sync()
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Tournament bracket belongs to someone else.', ephemeral=True); return False
        return True
    def _sync(self):
        t=_tourney_get(self.owner_id); cur=_tourney_current_match(t) if t else None; champ=_tourney_champion(t)
        if not _tourney_debug_mode_enabled():
            _view_remove_item_if_present(self, self.finish_button)
        no_active_match = not bool(cur) or bool(champ)
        if no_active_match:
            self.battle_button.disabled = True
            self.watch_button.disabled = True
            self.skip_button.disabled = True
            self.finish_button.disabled = True
            return
        if _tourney_debug_mode_enabled():
            self.finish_button.disabled = False
        if _tourney_is_player_match(cur[2]):
            self.battle_button.disabled = False
            _view_remove_item_if_present(self, self.watch_button)
            _view_remove_item_if_present(self, self.skip_button)
        else:
            # NPC-vs-NPC Tournament matches should offer only Watch here.
            # Watch opens the Spectate/Skip choice view.
            self.watch_button.disabled = False
            self.watch_button.label = 'Watch'
            _view_remove_item_if_present(self, self.battle_button)
            _view_remove_item_if_present(self, self.skip_button)
    def make_file(self) -> discord.File:
        t=_tourney_get(self.owner_id) or _tourney_new(self.owner_id, self.user_name)
        return discord.File(render_tournament_bracket_page(t), filename=f'tournament_bracket_{_tourney_attachment_suffix(self.owner_id)}.png')
    def summary_text(self) -> str:
        t=_tourney_get(self.owner_id)
        if not t: return 'No active Tournament. Use Start from the Tournament hub.'
        champ=_tourney_champion(t)
        if champ: return f"Tournament complete! Champion: **{_tourney_name(t, champ)}**" + (f" • Prize paid: **{TOURNAMENT_WINNER_GOLD:,} Gold, {TOURNAMENT_WINNER_TICKETS} Tickets, {TOURNAMENT_WINNER_PREMIUM_TICKETS} Random Packs**" if champ == 'player' else '')
        cur=_tourney_current_match(t)
        return f"{_tourney_round_label(cur[0])} Match {cur[1]+1}: **{_tourney_score(t, cur[2])}**" if cur else 'Tournament bracket is waiting for the next round.'
    @discord.ui.button(label='Battle', style=discord.ButtonStyle.success)
    async def battle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.tournament import start_tournament_visible_battle
        t=_tourney_get(self.owner_id); cur=_tourney_current_match(t) if t else None
        if not cur: await interaction.response.send_message('No playable Tournament match right now.', ephemeral=True); return
        if _tourney_is_player_match(cur[2]):
            await start_tournament_visible_battle(interaction, self.owner_id, self.user_name, cur[0], cur[1], player_team_id=t['setup'].get('main_team_id')); return
        view = TournamentWatchChoiceView(self.owner_id, self.user_name, cur[0], cur[1])
        await safe_edit_interaction_message(interaction, content='This match does not involve you. Watch it at normal speed or skip one battle result.', attachments_factory=lambda: [self.make_file()], embed=None, view=view)
    @discord.ui.button(label='Watch', style=discord.ButtonStyle.primary)
    async def watch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        t=_tourney_get(self.owner_id); cur=_tourney_current_match(t) if t else None
        if not cur or _tourney_is_player_match(cur[2]): await interaction.response.send_message('Watch is only for NPC vs NPC matches.', ephemeral=True); return
        view = TournamentWatchChoiceView(self.owner_id, self.user_name, cur[0], cur[1])
        await safe_edit_interaction_message(interaction, content='Choose how to watch this NPC vs NPC Tournament battle.', attachments_factory=lambda: [self.make_file()], embed=None, view=view)
    @discord.ui.button(label='Skip Result', style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        t=_tourney_get(self.owner_id); cur=_tourney_current_match(t) if t else None
        if not cur or _tourney_is_player_match(cur[2]): await interaction.response.send_message('Skip is only for NPC vs NPC matches.', ephemeral=True); return
        winner=_tourney_skip(t, cur[0], cur[1]); view=TournamentBracketView(self.owner_id,self.user_name)
        await safe_edit_interaction_message(interaction, content=f"Skipped one battle. Winner: **{_tourney_name(t, winner)}**\n" + view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
    @discord.ui.button(label='Finish', style=discord.ButtonStyle.danger, row=1)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _tourney_debug_mode_enabled():
            await interaction.response.send_message('Finish is only available while debug mode is ON.', ephemeral=True); return
        t=_tourney_get(self.owner_id)
        if not t or _tourney_is_complete(t):
            await interaction.response.send_message('No active Tournament to finish.', ephemeral=True); return
        champion = _tourney_finish(t)
        view=TournamentBracketView(self.owner_id,self.user_name)
        await safe_edit_interaction_message(interaction, content=f"Debug Finish skipped all remaining Tournament battles. Champion: **{_tourney_name(t, champion)}**\n" + view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)

    @discord.ui.button(label='Hub', style=discord.ButtonStyle.secondary, row=1)
    async def hub_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view=TournamentHubView(self.owner_id,self.user_name); await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class TournamentWatchChoiceView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, ri: int, mi: int):
        super().__init__(timeout=300); self.owner_id=int(owner_id); self.user_name=user_name; self.ri=int(ri); self.mi=int(mi)
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id: await interaction.response.send_message('This Tournament choice belongs to someone else.', ephemeral=True); return False
        return True
    @discord.ui.button(label='Spectate', style=discord.ButtonStyle.primary)
    async def watch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.tournament import start_tournament_visible_battle
        await start_tournament_visible_battle(interaction,self.owner_id,self.user_name,self.ri,self.mi)
    @discord.ui.button(label='Skip', style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        t=_tourney_get(self.owner_id); winner=_tourney_skip(t,self.ri,self.mi); view=TournamentBracketView(self.owner_id,self.user_name)
        await safe_edit_interaction_message(interaction, content=f"Skipped one battle. Winner: **{_tourney_name(t, winner)}**\n" + view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view=TournamentBracketView(self.owner_id,self.user_name); await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class TournamentContinueTeamSelect(discord.ui.Select):
    def __init__(self, view: 'TournamentBetweenBattleView'):
        self.swap_view = view
        t = _tourney_get(view.owner_id) or {}
        setup = t.get('setup') or _tourney_setup(view.owner_id)
        rows = {int(r['id']): r for r in db.list_teams(view.owner_id)}
        options: List[discord.SelectOption] = []

        main_team_id = int(setup.get('main_team_id') or 0)
        main_row = rows.get(main_team_id)
        if main_team_id:
            options.append(discord.SelectOption(
                label=('Main Team: ' + (_tourney_team_label(main_row) if main_row is not None else f'Team {main_team_id}'))[:100],
                value=str(main_team_id),
                description='Use your Main Team for this battle.',
            ))

        for idx, tid in enumerate(setup.get('secondary_team_ids', [])[:2], start=1):
            tid = int(tid or 0)
            if not tid or tid == main_team_id:
                continue
            row = rows.get(tid) or db.get_team(view.owner_id, tid)
            if row:
                options.append(discord.SelectOption(
                    label=(f'Secondary Team {idx}: ' + str(row['name']))[:100],
                    value=str(tid),
                    description=_tourney_team_label(row)[:100],
                ))

        if not options:
            options = [discord.SelectOption(label='No valid Tournament team found', value='0', description='Return to Teams and save a Main Team.')]
        super().__init__(placeholder='Choose the team to use for the next battle', min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        from kuribot.modes.tournament import start_tournament_visible_battle
        selected_team_id = int(self.values[0] or 0)
        if not selected_team_id:
            await interaction.response.send_message('No valid Tournament team is saved. Open Teams and save a Main Team first.', ephemeral=True)
            return
        await start_tournament_visible_battle(interaction, self.swap_view.owner_id, self.swap_view.user_name, self.swap_view.ri, self.swap_view.mi, player_team_id=selected_team_id)


class TournamentBetweenBattleView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, ri: int, mi: int):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = user_name
        self.ri = int(ri)
        self.mi = int(mi)
        self.add_item(TournamentContinueTeamSelect(self))
        self.add_item(self.bracket_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Tournament team menu belongs to someone else.', ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return TournamentBracketView(self.owner_id, self.user_name).make_file()

    @discord.ui.button(label='Bracket', style=discord.ButtonStyle.secondary, row=1)
    async def bracket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TournamentBracketView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class TournamentAfterBattleView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, ri: int, mi: int, allow_continue: bool):
        super().__init__(timeout=600); self.owner_id=int(owner_id); self.user_name=user_name; self.ri=int(ri); self.mi=int(mi); self.continue_button.disabled=not allow_continue
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id: await interaction.response.send_message('This Tournament result belongs to someone else.', ephemeral=True); return False
        return True
    @discord.ui.button(label='Continue Match', style=discord.ButtonStyle.success)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = _tourney_get(self.owner_id)
        if not t or self.ri >= len(t.get('rounds') or []) or self.mi >= len(t['rounds'][self.ri]):
            await interaction.response.send_message('Tournament match not found. Open the bracket again from the Tournament hub.', ephemeral=True)
            return
        m = t['rounds'][self.ri][self.mi]
        if _tourney_is_player_match(m):
            view = TournamentBetweenBattleView(self.owner_id, self.user_name, self.ri, self.mi)
            await safe_edit_interaction_message(interaction, content='Choose which Tournament team to use for the next battle.', attachments_factory=lambda: [view.make_file()], embed=None, view=view)
        else:
            view = TournamentWatchChoiceView(self.owner_id, self.user_name, self.ri, self.mi)
            await safe_edit_interaction_message(interaction, content='This best-of-3 match is not finished. Watch or skip the next battle.', attachments_factory=lambda: [TournamentBracketView(self.owner_id,self.user_name).make_file()], embed=None, view=view)
    @discord.ui.button(label='Bracket', style=discord.ButtonStyle.secondary)
    async def bracket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view=TournamentBracketView(self.owner_id,self.user_name); await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
