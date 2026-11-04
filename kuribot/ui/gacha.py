"""Summoning menus."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Sequence

import discord

from kuribot.config import ATTRIBUTES, GACHA_COST, GACHA_X10_COST
from kuribot.models import SupportCardDef
from kuribot.database import db
from kuribot.monsters import (
    apply_stat_bonuses, find_monster_by_name_and_attribute, monster_category_for_row,
    resolve_monster_type_name,
)
from kuribot.gacha import (
    create_boss_cutscene_embed, create_boss_summon_result_embed, create_gacha_banner_embed,
    create_gacha_result_embed, create_summon_all_result_embed, get_featured_monsters,
    grant_support_cards_for_pulls, has_boss_pull, perform_boss_monster_ticket_pull,
    perform_gacha_pulls,
)
from kuribot.render.gacha import (
    make_boss_cutscene_file, make_gacha_banner_file, make_gacha_results_file,
    make_single_summon_portrait_file,
)
from kuribot.ui.common import safe_acknowledge_interaction, safe_edit_interaction_message


class BossCutsceneView(discord.ui.View):
    def __init__(self, user_id: int, user_name: str, pool: Sequence[sqlite3.Row], pull_count: int, result_embed: discord.Embed, result_file: Optional[discord.File], result_view: Optional[discord.ui.View] = None):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.user_name = user_name
        self.pool = list(pool)
        self.pull_count = pull_count
        self.result_embed = result_embed
        self.result_file = result_file
        self.result_view = result_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These pack results belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        result_view = self.result_view or GachaResultView(self.user_id, self.user_name, self.pool, self.pull_count)
        await safe_edit_interaction_message(
            interaction,
            embed=self.result_embed,
            attachments=[self.result_file] if self.result_file else [],
            view=result_view,
        )


class PremiumSummonModal(discord.ui.Modal, title="Pack Opening"):
    monster_name = discord.ui.TextInput(
        label="Monster name",
        required=True,
        max_length=100,
        placeholder="Type the exact monster name",
    )
    attribute = discord.ui.TextInput(
        label="Attribute",
        required=True,
        max_length=20,
        placeholder="FIRE, WATER, EARTH, WIND, LIGHT, DARK, or DIVINE",
    )
    monster_type = discord.ui.TextInput(
        label="Monster type",
        required=True,
        max_length=40,
        placeholder="Example: Dragon",
    )

    def __init__(self, banner_view: "GachaBannerView"):
        super().__init__()
        self.banner_view = banner_view

    async def on_submit(self, interaction: discord.Interaction):
        user = db.get_user(self.banner_view.user_id)
        tutorial_free_summon = bool(getattr(self.banner_view, "tutorial_free_premium_summon", False))
        if not tutorial_free_summon and int(user["premium_tickets"] or 0) < 1:
            await interaction.response.send_message(
                f"You need **1 Random Pack** for a Pack Opening. You currently have **{user['premium_tickets']}**.",
                ephemeral=True,
            )
            return

        attribute_name = str(self.attribute.value or "").strip().upper()
        if attribute_name not in ATTRIBUTES:
            await interaction.response.send_message(
                f"That attribute is invalid. Choose one of: {', '.join(ATTRIBUTES)}.",
                ephemeral=True,
            )
            return

        resolved_type = resolve_monster_type_name(str(self.monster_type.value or ""))
        if resolved_type is None:
            await interaction.response.send_message(
                "That monster type is invalid. Enter one of the game's monster types exactly.",
                ephemeral=True,
            )
            return

        monster_name = str(self.monster_name.value or "").strip()
        if not monster_name:
            await interaction.response.send_message(
                "Enter a monster name for the Pack Opening.",
                ephemeral=True,
            )
            return

        monster_row = find_monster_by_name_and_attribute(monster_name, attribute_name)
        if monster_row is None:
            await interaction.response.send_message(
                "I couldn't find a monster with that exact name and attribute.",
                ephemeral=True,
            )
            return
        category = monster_category_for_row(monster_row)
        if category in {"boss", "signature"}:
            await interaction.response.send_message(
                "Extra Deck Monsters cannot be opened with Random Packs.",
                ephemeral=True,
            )
            return

        if not tutorial_free_summon:
            db.add_premium_tickets(self.banner_view.user_id, -1)
        summon_result = db.add_monster_to_inventory(self.banner_view.user_id, str(monster_row["monster_id"]), [resolved_type], monster_attribute=str(monster_row["attribute"]))
        duplicate_ticket_count = int(summon_result.get("premium_tickets_awarded", 0) or 0)
        current = db.get_user(self.banner_view.user_id)
        resolved_hp, resolved_atk, resolved_def, resolved_spd = apply_stat_bonuses(
            int(monster_row["hp"]),
            int(monster_row["atk"]),
            int(monster_row["def"]),
            int(monster_row["spd"]),
            (resolved_type,),
            str(monster_row["attribute"]),
            category,
        )
        pull = {
            "monster_id": str(monster_row["monster_id"]),
            "name": str(monster_row["name"]),
            "attribute": str(monster_row["attribute"]),
            "monster_types": (resolved_type,),
            "hp": resolved_hp,
            "atk": resolved_atk,
            "def": resolved_def,
            "spd": resolved_spd,
            "image_path": str(monster_row["image_path"]),
            "monster_category": category,
        }
        embed = create_gacha_result_embed(
            self.banner_view.user_name,
            int(current["tickets"]),
            [pull],
            self.banner_view.featured,
            1,
            [],
            0,
            0,
            duplicate_ticket_count,
        )
        embed.title = "✨ Pack Opening Result"
        if tutorial_free_summon:
            embed.description = (
                f"You completed a **Tutorial Pack Opening** and opened **{pull['name']}**!\n"
                f"Chosen Attribute: **{pull['attribute']}**\n"
                f"Chosen Monster Type: **{resolved_type}**"
            )
        else:
            embed.description = (
                f"You used **1 Random Pack** and opened **{pull['name']}**!\n"
                f"Chosen Attribute: **{pull['attribute']}**\n"
                f"Chosen Monster Type: **{resolved_type}**"
            )
        embed.set_footer(text=f"{self.banner_view.user_name} • Packs: {current['tickets']} • Random Packs: {current['premium_tickets']}")
        file = None
        if Path(str(pull["image_path"])).exists():
            file = discord.File(str(pull["image_path"]), filename="pull.png")
            embed.set_image(url="attachment://pull.png")
        await interaction.response.send_message(embed=embed, file=file if file else discord.utils.MISSING)
        after_premium_summon = getattr(self.banner_view, "after_premium_summon", None)
        if callable(after_premium_summon):
            await after_premium_summon(interaction, pull, current)


class SummonAllResultsView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(
        self,
        user_id: int,
        user_name: str,
        pool: Sequence[sqlite3.Row],
        pulls: Sequence[Dict[str, object]],
        featured: Sequence[sqlite3.Row],
        support_rewards: Sequence[SupportCardDef],
        duplicate_exp: int = 0,
        total_stage_ups: int = 0,
        duplicate_ticket_count: int = 0,
        tickets_left: int = 0,
        page: int = 0,
    ):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.user_name = user_name
        self.pool = list(pool)
        self.pulls = list(pulls)
        self.featured = list(featured)
        self.support_rewards = list(support_rewards)
        self.duplicate_exp = int(duplicate_exp or 0)
        self.total_stage_ups = int(total_stage_ups or 0)
        self.duplicate_ticket_count = int(duplicate_ticket_count or 0)
        self.tickets_left = int(tickets_left or 0)
        self.page = max(0, int(page or 0))
        self._sync_button_state()

    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.pulls) / self.PAGE_SIZE))

    def _sync_button_state(self):
        total_pages = self.total_pages()
        self.page = max(0, min(self.page, total_pages - 1))
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "summon_all_prev":
                    item.disabled = self.page <= 0
                elif item.custom_id == "summon_all_next":
                    item.disabled = self.page >= total_pages - 1

    def build_embed(self) -> discord.Embed:
        return create_summon_all_result_embed(
            self.user_name,
            self.tickets_left,
            self.pulls,
            self.featured,
            self.support_rewards,
            self.duplicate_exp,
            self.total_stage_ups,
            self.duplicate_ticket_count,
            page=self.page,
            page_size=self.PAGE_SIZE,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These pack results belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="summon_all_prev")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_button_state()
        await safe_edit_interaction_message(interaction, embed=self.build_embed(), attachments=[], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, custom_id="summon_all_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_button_state()
        await safe_edit_interaction_message(interaction, embed=self.build_embed(), attachments=[], view=self)

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary)
    async def banner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db.get_user(self.user_id)
        theme_name = db.get_ui_theme(self.user_id)
        featured = get_featured_monsters(self.pool, user_id=self.user_id)
        embed = create_gacha_banner_embed(self.user_name, user["tickets"], user["premium_tickets"], user["boss_monster_tickets"], self.pool, featured, theme_name)
        file = make_gacha_banner_file(featured, self.user_name, theme_name)
        await safe_edit_interaction_message(
            interaction,
            embed=embed,
            attachments=[file] if file else [],
            view=GachaBannerView(self.user_id, self.user_name, self.pool),
        )


class GachaBannerView(discord.ui.View):
    def __init__(self, user_id: int, user_name: str, pool: Sequence[sqlite3.Row]):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.user_name = user_name
        self.pool = list(pool)
        self.featured = get_featured_monsters(self.pool, user_id=self.user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This summon banner belongs to someone else.", ephemeral=True)
            return False
        return True

    async def do_summon(self, interaction: discord.Interaction, pull_count: int):
        cost = GACHA_COST if pull_count == 1 else GACHA_X10_COST
        user = db.get_user(self.user_id)
        if user["tickets"] < cost:
            await interaction.response.send_message(
                f"You need **{cost}** tickets for that pack opening. You currently have **{user['tickets']}**.",
                ephemeral=True,
            )
            return

        db.add_tickets(self.user_id, -cost)
        pulls, featured = perform_gacha_pulls(self.pool, pull_count, user_id=self.user_id, featured_pool=self.featured)
        duplicate_ticket_count = 0
        for pulled in pulls:
            summon_result = db.add_monster_to_inventory(self.user_id, pulled["monster_id"], pulled["monster_types"], monster_attribute=str(pulled["attribute"]))
            duplicate_ticket_count += int(summon_result.get("premium_tickets_awarded", 0) or 0)
        support_rewards, duplicate_exp, stage_ups = grant_support_cards_for_pulls(self.user_id, pull_count, pulls)
        current = db.get_user(self.user_id)
        theme_name = db.get_ui_theme(self.user_id)
        embed = create_gacha_result_embed(self.user_name, current["tickets"], pulls, featured, pull_count, support_rewards, duplicate_exp, stage_ups, duplicate_ticket_count)
        if pull_count == 1:
            file = make_single_summon_portrait_file(pulls)
            if file is not None:
                embed.set_image(url="attachment://pull.png")
        else:
            file = make_gacha_results_file(pulls, self.user_name, theme_name)
            if file is not None:
                embed.set_image(url="attachment://pulls.png")
        if has_boss_pull(pulls):
            cutscene_embed = create_boss_cutscene_embed(self.user_name, pulls, pull_count)
            cutscene_file = make_boss_cutscene_file(pulls)
            if cutscene_file is not None:
                cutscene_embed.set_image(url="attachment://boss_cutscene.png")
            await safe_edit_interaction_message(interaction, 
                embed=cutscene_embed,
                attachments=[cutscene_file] if cutscene_file else [],
                view=BossCutsceneView(self.user_id, self.user_name, self.pool, pull_count, embed, file),
            )
        else:
            await safe_edit_interaction_message(interaction, 
                embed=embed,
                attachments=[file] if file else [],
                view=GachaResultView(self.user_id, self.user_name, self.pool, pull_count),
            )

    async def do_summon_all(self, interaction: discord.Interaction):
        user = db.get_user(self.user_id)
        pull_count = int(user["tickets"] or 0)
        if pull_count < 1:
            await interaction.response.send_message(
                "You need at least **1** ticket to use Summon All.",
                ephemeral=True,
            )
            return

        # Summon All can take longer than Discord's initial interaction window when
        # a user has many tickets. Acknowledge the button first, then edit the
        # original/banner message after the pulls are finished.
        await safe_acknowledge_interaction(interaction, thinking=False)

        db.add_tickets(self.user_id, -pull_count)
        pulls, featured = perform_gacha_pulls(self.pool, pull_count, user_id=self.user_id, featured_pool=self.featured)
        duplicate_ticket_count = 0
        for pulled in pulls:
            summon_result = db.add_monster_to_inventory(self.user_id, pulled["monster_id"], pulled["monster_types"], monster_attribute=str(pulled["attribute"]))
            duplicate_ticket_count += int(summon_result.get("premium_tickets_awarded", 0) or 0)
        support_rewards, duplicate_exp, stage_ups = grant_support_cards_for_pulls(self.user_id, pull_count, pulls)
        current = db.get_user(self.user_id)
        view = SummonAllResultsView(
            self.user_id,
            self.user_name,
            self.pool,
            pulls,
            featured,
            support_rewards,
            duplicate_exp,
            stage_ups,
            duplicate_ticket_count,
            int(current["tickets"] or 0),
            page=0,
        )
        embed = view.build_embed()
        if has_boss_pull(pulls):
            cutscene_embed = create_boss_cutscene_embed(self.user_name, pulls, pull_count)
            cutscene_file = make_boss_cutscene_file(pulls)
            if cutscene_file is not None:
                cutscene_embed.set_image(url="attachment://boss_cutscene.png")
            await safe_edit_interaction_message(
                interaction,
                embed=cutscene_embed,
                attachments=[cutscene_file] if cutscene_file else [],
                view=BossCutsceneView(
                    self.user_id,
                    self.user_name,
                    self.pool,
                    pull_count,
                    embed,
                    None,
                    result_view=view,
                ),
            )
        else:
            await safe_edit_interaction_message(interaction, embed=embed, attachments=[], view=view)

    async def do_boss_summon(self, interaction: discord.Interaction):
        user = db.get_user(self.user_id)
        if int(user["boss_monster_tickets"] or 0) < 1:
            await interaction.response.send_message(
                f"You need **1 Extra Deck Monster Pack** for Extra Deck Summon. You currently have **{user['boss_monster_tickets']}**.",
                ephemeral=True,
            )
            return

        try:
            pull = perform_boss_monster_ticket_pull(self.pool)
        except RuntimeError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        db.add_boss_monster_tickets(self.user_id, -1)
        summon_result = db.add_monster_to_inventory(
            self.user_id,
            pull["monster_id"],
            pull["monster_types"],
            monster_attribute=str(pull["attribute"]),
        )
        duplicate_ticket_count = int(summon_result.get("premium_tickets_awarded", 0) or 0)
        current = db.get_user(self.user_id)
        embed = create_boss_summon_result_embed(
            self.user_name, int(current["boss_monster_tickets"] or 0), pull, duplicate_ticket_count,
        )
        file = make_single_summon_portrait_file([pull])
        if file is not None:
            embed.set_image(url="attachment://pull.png")

        cutscene_embed = create_boss_cutscene_embed(self.user_name, [pull], 1)
        cutscene_file = make_boss_cutscene_file([pull])
        if cutscene_file is not None:
            cutscene_embed.set_image(url="attachment://boss_cutscene.png")
        await safe_edit_interaction_message(
            interaction,
            embed=cutscene_embed,
            attachments=[cutscene_file] if cutscene_file else [],
            view=BossCutsceneView(
                self.user_id,
                self.user_name,
                self.pool,
                1,
                embed,
                file,
                result_view=BossSummonResultView(self.user_id, self.user_name, self.pool),
            ),
        )

    @discord.ui.button(label="Summon", style=discord.ButtonStyle.primary, row=0)
    async def summon_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_summon(interaction, 1)

    @discord.ui.button(label="Summon x10", style=discord.ButtonStyle.primary, row=0)
    async def summon_ten_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_summon(interaction, 10)

    @discord.ui.button(label="Summon All", style=discord.ButtonStyle.primary, row=0)
    async def summon_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_summon_all(interaction)

    @discord.ui.button(label="Extra Deck Summon", style=discord.ButtonStyle.danger, row=1)
    async def boss_summon_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_boss_summon(interaction)

    @discord.ui.button(label="Pack Opening", style=discord.ButtonStyle.success, row=1)
    async def premium_summon_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PremiumSummonModal(self))


class BossSummonResultView(discord.ui.View):
    def __init__(self, user_id: int, user_name: str, pool: Sequence[sqlite3.Row]):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.user_name = user_name
        self.pool = list(pool)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These boss pack results belong to someone else.", ephemeral=True)
            return False
        return True

    async def show_banner(self, interaction: discord.Interaction):
        user = db.get_user(self.user_id)
        theme_name = db.get_ui_theme(self.user_id)
        featured = get_featured_monsters(self.pool, user_id=self.user_id)
        embed = create_gacha_banner_embed(
            self.user_name,
            user["tickets"],
            user["premium_tickets"],
            user["boss_monster_tickets"],
            self.pool,
            featured,
            theme_name,
        )
        file = make_gacha_banner_file(featured, self.user_name, theme_name)
        await safe_edit_interaction_message(
            interaction,
            embed=embed,
            attachments=[file] if file else [],
            view=GachaBannerView(self.user_id, self.user_name, self.pool),
        )

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary)
    async def banner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_banner(interaction)

    @discord.ui.button(label="Extra Deck Summon Again", style=discord.ButtonStyle.success)
    async def boss_summon_again_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await GachaBannerView(self.user_id, self.user_name, self.pool).do_boss_summon(interaction)


class GachaResultView(discord.ui.View):
    def __init__(self, user_id: int, user_name: str, pool: Sequence[sqlite3.Row], pull_count: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.user_name = user_name
        self.pool = list(pool)
        self.pull_count = pull_count

    async def show_banner(self, interaction: discord.Interaction):
        user = db.get_user(self.user_id)
        theme_name = db.get_ui_theme(self.user_id)
        featured = get_featured_monsters(self.pool, user_id=self.user_id)
        embed = create_gacha_banner_embed(self.user_name, user["tickets"], user["premium_tickets"], user["boss_monster_tickets"], self.pool, featured, theme_name)
        file = make_gacha_banner_file(featured, self.user_name, theme_name)
        await safe_edit_interaction_message(interaction, 
            embed=embed,
            attachments=[file] if file else [],
            view=GachaBannerView(self.user_id, self.user_name, self.pool),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These pack results belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary)
    async def banner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_banner(interaction)

    @discord.ui.button(label="Summon Again", style=discord.ButtonStyle.success)
    async def summon_again_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cost = GACHA_COST if self.pull_count == 1 else GACHA_X10_COST
        user = db.get_user(self.user_id)
        if user["tickets"] < cost:
            await interaction.response.send_message(
                f"You need **{cost}** tickets for that pack opening. You currently have **{user['tickets']}**.",
                ephemeral=True,
            )
            return

        db.add_tickets(self.user_id, -cost)
        current_featured = get_featured_monsters(self.pool, user_id=self.user_id)
        pulls, featured = perform_gacha_pulls(self.pool, self.pull_count, user_id=self.user_id, featured_pool=current_featured)
        duplicate_ticket_count = 0
        for pulled in pulls:
            summon_result = db.add_monster_to_inventory(self.user_id, pulled["monster_id"], pulled["monster_types"], monster_attribute=str(pulled["attribute"]))
            duplicate_ticket_count += int(summon_result.get("premium_tickets_awarded", 0) or 0)
        support_rewards, duplicate_exp, stage_ups = grant_support_cards_for_pulls(self.user_id, self.pull_count, pulls)
        current = db.get_user(self.user_id)
        theme_name = db.get_ui_theme(self.user_id)
        embed = create_gacha_result_embed(self.user_name, current["tickets"], pulls, featured, self.pull_count, support_rewards, duplicate_exp, stage_ups, duplicate_ticket_count)
        if self.pull_count == 1:
            file = make_single_summon_portrait_file(pulls)
            if file is not None:
                embed.set_image(url="attachment://pull.png")
        else:
            file = make_gacha_results_file(pulls, self.user_name, theme_name)
            if file is not None:
                embed.set_image(url="attachment://pulls.png")
        if has_boss_pull(pulls):
            cutscene_embed = create_boss_cutscene_embed(self.user_name, pulls, self.pull_count)
            cutscene_file = make_boss_cutscene_file(pulls)
            if cutscene_file is not None:
                cutscene_embed.set_image(url="attachment://boss_cutscene.png")
            await safe_edit_interaction_message(interaction, 
                embed=cutscene_embed,
                attachments=[cutscene_file] if cutscene_file else [],
                view=BossCutsceneView(self.user_id, self.user_name, self.pool, self.pull_count, embed, file),
            )
        else:
            await safe_edit_interaction_message(interaction, 
                embed=embed,
                attachments=[file] if file else [],
                view=GachaResultView(self.user_id, self.user_name, self.pool, self.pull_count),
            )
