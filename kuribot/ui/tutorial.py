"""Tutorial menus."""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Sequence

import discord

from kuribot.config import DUELING_GROUNDS_BATTLE_HEARTS, TEAM_SIZE
from kuribot.models import SupportCardDef
from kuribot.database import db
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.pack_opening import (
    build_pack_results_embed, format_pack_open_result, open_pack_for_user,
)
from kuribot.render.collection import render_team_page
from kuribot.ui.common import safe_edit_interaction_message, safe_send_interaction_message
from kuribot.ui.glossary import GlossaryView
from kuribot.tutorial import (
    build_tutorial_monster_generation_base_stats_embed,
    build_tutorial_monster_generation_complete_embed,
    build_tutorial_monster_generation_intro_embed, build_tutorial_premium_complete_embed,
    build_tutorial_summon_monsters_embed, build_tutorial_support_cards_complete_embed,
    build_tutorial_support_cards_intro_embed, build_tutorial_support_cards_show_embed,
    build_tutorial_team_complete_embed, build_tutorial_team_creation_intro_embed,
    build_tutorial_welcome_embed, make_tutorial_monster_pool,
    make_tutorial_support_card_showcase_cards, make_tutorial_support_cards_file,
    tutorial_example_team_rows, tutorial_monster_generation_progress_flags,
    tutorial_progress_flags, tutorial_support_cards_progress_flags,
)
from kuribot.ui.teams import TeamBuilderView
from kuribot.ui.battle_results import BattleResultView


class TutorialBattleHeartsView(BattleResultView):
    def __init__(self, owner_id: int, user_name: str, battle_title: str, log_entries: Sequence[str], battle_stats: Optional[Dict[str, List[Dict[str, int | str]]]] = None):
        super().__init__(owner_id, battle_title, log_entries, user_name=user_name, battle_stats=battle_stats)
        self.user_name = user_name
        hearts_button = discord.ui.Button(label='Hearts', style=discord.ButtonStyle.success, custom_id='tutorial_hearts_button')
        hearts_button.callback = self.hearts_button_callback
        self.add_item(hearts_button)

    async def hearts_button_callback(self, interaction: discord.Interaction):
        from kuribot.modes.tutorial import start_tutorial_battle
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This tutorial belongs to someone else.', ephemeral=True)
            return
        await interaction.response.defer()
        await start_tutorial_battle(interaction, self.owner_id, self.user_name, hearts=2, hearts_lesson=True)


class TutorialBattleFinishedView(BattleResultView):
    def __init__(self, owner_id: int, user_name: str, battle_title: str, log_entries: Sequence[str], battle_stats: Optional[Dict[str, List[Dict[str, int | str]]]] = None):
        super().__init__(owner_id, battle_title, log_entries, user_name=user_name, battle_stats=battle_stats)
        self.user_name = user_name
        menu_button = discord.ui.Button(label='Tutorial Menu', style=discord.ButtonStyle.secondary, custom_id='tutorial_back_to_menu')
        menu_button.callback = self.menu_button_callback
        self.add_item(menu_button)

    async def menu_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This tutorial belongs to someone else.', ephemeral=True)
            return
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=None, embed=build_tutorial_welcome_embed(self.user_name), attachments=[], view=view)


class TutorialTeamsHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, sample_rows: Sequence[sqlite3.Row]):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.user_name = user_name
        self.sample_rows = list(sample_rows)
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        if self.message is None:
            self.message = interaction.message
        return True

    def summary_text(self) -> str:
        names = ", ".join(str(row["monster_name"]) for row in self.sample_rows[:10])
        return (
            "Tutorial Teams hub • This is an example hub. "
            "It shows one sample team and gives you 10 tutorial monsters to choose from.\n"
            f"Sample choices: {names}"
        )

    def make_file(self) -> discord.File:
        rows = tutorial_example_team_rows(self.sample_rows)
        buffer = render_team_page("Tutorial", rows, 1, per_page=8, filter_label="Tutorial Teams", sort_label="Example", theme_name=db.get_ui_theme(self.owner_id))
        return discord.File(buffer, filename="tutorial_teams.png")

    @discord.ui.button(label="Create Team", style=discord.ButtonStyle.success, row=0)
    async def create_team_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        builder = TeamBuilderView(
            self.owner_id,
            "Tutorial Team",
            inventory_rows=self.sample_rows,
            save_callback=self.after_team_saved,
        )
        await safe_send_interaction_message(
            interaction,
            content=f"Kuribot picked **{len(self.sample_rows)}** sample monsters for you. Pick **{TEAM_SIZE}** monsters, then press **Save Team**.",
            view=builder,
            ephemeral=True,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialTeamCreationIntroView(self.owner_id, self.user_name, self.sample_rows)
        await safe_edit_interaction_message(interaction, content=None, embed=build_tutorial_team_creation_intro_embed(self.user_name), attachments=[], view=view)

    async def after_team_saved(self, interaction: discord.Interaction, builder: TeamBuilderView):
        db.mark_tutorial_team_done(self.owner_id)
        rewarded = db.claim_tutorial_team_ticket_reward(self.owner_id, 10)
        embed = build_tutorial_team_complete_embed(self.user_name, builder.team_name, rewarded)
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            pass
        if self.message is not None:
            try:
                complete_view = TutorialView(self.owner_id, self.user_name)
                await self.message.edit(embed=embed, attachments=[], view=complete_view)
            except (discord.NotFound, discord.HTTPException, AttributeError):
                pass


class TutorialTeamCreationIntroView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, sample_rows: Sequence[sqlite3.Row]):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.user_name = user_name
        self.sample_rows = list(sample_rows)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Teams", style=discord.ButtonStyle.success, row=0)
    async def teams_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialTeamsHubView(self.owner_id, self.user_name, self.sample_rows)
        view.message = interaction.message
        await safe_edit_interaction_message(interaction, content=view.summary_text(), embed=None, attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=None, embed=build_tutorial_welcome_embed(self.user_name), attachments=[], view=view)


class TutorialMonsterGenerationIntroView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Stat Bonuses", style=discord.ButtonStyle.blurple, row=0)
    async def stat_bonuses_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        glossary_view = GlossaryView(self.owner_id)
        glossary_view.set_section("stat_bonuses")
        glossary_view._sync_buttons()
        await interaction.response.send_message(
            content=fit_discord_message_content(glossary_view.content()),
            view=glossary_view,
            ephemeral=True,
        )

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success, row=0)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialMonsterGenerationFinishView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_monster_generation_base_stats_embed(self.user_name),
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=None, embed=build_tutorial_welcome_embed(self.user_name), attachments=[], view=view)


class TutorialMonsterGenerationFinishView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self._sync_button_states()

    def _sync_button_states(self):
        _, reward_claimed = tutorial_monster_generation_progress_flags(self.owner_id)
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "Finish":
                child.disabled = reward_claimed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, row=0)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        rewarded = db.claim_tutorial_monster_generation_premium_reward(self.owner_id, 3)
        db.mark_tutorial_monster_generation_done(self.owner_id)
        embed = build_tutorial_monster_generation_complete_embed(self.user_name, rewarded)
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=embed,
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialMonsterGenerationIntroView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_monster_generation_intro_embed(self.user_name),
            attachments=[],
            view=view,
        )


class TutorialSupportCardsIntroView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Show", style=discord.ButtonStyle.success, row=0)
    async def show_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cards = make_tutorial_support_card_showcase_cards()
        view = TutorialSupportCardsFinishView(self.owner_id, self.user_name, cards)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_support_cards_show_embed(self.user_name, cards),
            attachments_factory=lambda: [make_tutorial_support_cards_file(self.user_name, self.owner_id, cards)],
            view=view,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=None, embed=build_tutorial_welcome_embed(self.user_name), attachments=[], view=view)


class TutorialSupportCardsFinishView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, cards: Sequence[SupportCardDef]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.cards = list(cards)
        self._sync_button_states()

    def _sync_button_states(self):
        _, reward_claimed = tutorial_support_cards_progress_flags(self.owner_id)
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "Finish":
                child.disabled = reward_claimed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, row=0)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        rewarded = db.claim_tutorial_support_cards_reward(self.owner_id, 30)
        db.mark_tutorial_support_cards_done(self.owner_id)
        embed = build_tutorial_support_cards_complete_embed(self.user_name, rewarded)
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=embed,
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialSupportCardsIntroView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_support_cards_intro_embed(self.user_name),
            attachments=[],
            view=view,
        )


class TutorialView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Pack Opening", style=discord.ButtonStyle.success, row=0)
    async def summon_monsters_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pool = db.all_monsters()
        if not pool:
            await interaction.response.send_message("No monster pool was loaded. Check your CARDS_DIR path in .env.", ephemeral=True)
            return
        view = TutorialSummonMonstersView(self.owner_id, self.user_name, pool)
        view.message = interaction.message
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_summon_monsters_embed(self.user_name),
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Monster Generation", style=discord.ButtonStyle.primary, row=0)
    async def monster_generation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialMonsterGenerationIntroView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_monster_generation_intro_embed(self.user_name),
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Team Creation", style=discord.ButtonStyle.primary, row=0)
    async def team_creation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        sample_rows = make_tutorial_monster_pool(self.owner_id, 10)
        if len(sample_rows) < TEAM_SIZE:
            await interaction.response.send_message("Kuribot could not prepare enough tutorial monsters. Check your CARDS_DIR path in .env.", ephemeral=True)
            return
        view = TutorialTeamCreationIntroView(self.owner_id, self.user_name, sample_rows)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_team_creation_intro_embed(self.user_name),
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Support Cards", style=discord.ButtonStyle.blurple, row=1)
    async def support_cards_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialSupportCardsIntroView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_support_cards_intro_embed(self.user_name),
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Battle", style=discord.ButtonStyle.danger, row=0)
    async def battle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.tutorial import start_tutorial_battle
        await interaction.response.defer()
        await start_tutorial_battle(interaction, self.owner_id, self.user_name, hearts=DUELING_GROUNDS_BATTLE_HEARTS, hearts_lesson=False)


class TutorialSummonMonstersView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, pool: Sequence[sqlite3.Row]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.pool = list(pool)
        self.message: Optional[discord.Message] = None
        self._sync_button_states()

    def _sync_button_states(self):
        pack_done, finish_done, _ = tutorial_progress_flags(self.owner_id)
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.label == "Open Pack":
                child.disabled = pack_done
            elif child.label == "Finish":
                child.disabled = (not pack_done) or finish_done

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial belongs to someone else.", ephemeral=True)
            return False
        if self.message is None:
            self.message = interaction.message
        return True

    @discord.ui.button(label="Open Pack", style=discord.ButtonStyle.success, row=0)
    async def open_pack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pack_done, _, _ = tutorial_progress_flags(self.owner_id)
        if pack_done:
            button.disabled = True
            await interaction.response.send_message("You already completed the tutorial pack opening on this account.", ephemeral=True)
            return
        db.add_pack(self.owner_id, "random_themed", 1)
        try:
            result = open_pack_for_user(self.owner_id, "random_themed")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        db.mark_tutorial_summon_done(self.owner_id)
        embed = build_pack_results_embed(self.user_name, [result])
        embed.title = "Tutorial • Pack Opened!"
        embed.description = (
            "You opened **1 Random Pack** and received **3 monsters** plus **3 support cards**.\n\n"
            f"{format_pack_open_result(result)}\n\n"
            "Packs are now the main way to collect monsters and support cards. Use `;packs` any time to view, open, gift, or sell your packs.\n\n"
            "Press **Finish** to complete this tutorial section."
        )[:4096]
        view = TutorialSummonMonstersView(self.owner_id, self.user_name, self.pool)
        view.message = interaction.message
        await safe_edit_interaction_message(interaction, content=None, embed=embed, attachments=[], view=view)

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.primary, row=0)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pack_done, finish_done, _ = tutorial_progress_flags(self.owner_id)
        if not pack_done:
            await interaction.response.send_message("Complete the tutorial **Open Pack** step first.", ephemeral=True)
            return
        if finish_done:
            button.disabled = True
            await interaction.response.send_message("You already completed the tutorial Pack Opening section on this account.", ephemeral=True)
            return
        db.mark_tutorial_premium_done(self.owner_id)
        completion_embed = build_tutorial_premium_complete_embed(self.user_name)
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=None, embed=completion_embed, attachments=[], view=view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=None, embed=build_tutorial_welcome_embed(self.user_name), attachments=[], view=view)
