"""Monster and support card collection browsers."""

from __future__ import annotations

import io
import math
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import discord
from PIL import Image, ImageOps

from kuribot.config import ATTRIBUTES, SUPPORT_DECK_SIZE
from kuribot.timeutil import reset_schedule_text
from kuribot.cards.equip_spells import get_equip_spell_def
from kuribot.database import db
from kuribot.render.images import _PanelUserContext
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.monsters import (
    decode_monster_types, extra_deck_effect_description, extra_deck_summon_requirements_text,
    format_monster_types, is_boss_monster_row, is_extra_deck_monster_row, is_signature_monster_row,
    resolve_row_combat_stats, resolve_secondary_stat_bonuses,
)
from kuribot.cards.support_cards import (
    get_support_card_def, support_card_effect_label, support_card_exp_to_next_stage,
    support_card_family_key, support_card_max_stage, support_card_stage_number,
)
from kuribot.opponents import find_equip_spells_by_partial_name
from kuribot.render.battle import format_crit_damage_percent, format_ratio_percent
from kuribot.render.collection import render_monster_binder_page, render_support_card_page
from kuribot.ui.common import safe_acknowledge_interaction, safe_edit_interaction_message


def make_monster_detail_file(row: sqlite3.Row) -> Optional[discord.File]:
    image_path = Path(str(row["image_path"] or "")).expanduser()
    if not image_path.exists() or not image_path.is_file():
        return None
    try:
        with Image.open(image_path) as image:
            preview = ImageOps.contain(image.convert("RGBA"), (128, 128))
            buffer = io.BytesIO()
            preview.save(buffer, format="PNG", compress_level=3)
            buffer.seek(0)
            return discord.File(buffer, filename="monster_detail.png")
    except Exception:
        return discord.File(str(image_path), filename="monster_detail.png")


def build_monster_detail_embed(row: sqlite3.Row) -> discord.Embed:
    monster_types = decode_monster_types(row["monster_types"])
    resolved_hp, resolved_atk, resolved_defense, resolved_spd = resolve_row_combat_stats(row, monster_types)
    resolved_crit_rate, resolved_crit_damage, resolved_accuracy, resolved_dodge = resolve_secondary_stat_bonuses(row["attribute"])
    is_extra_deck = is_extra_deck_monster_row(row)
    description_lines = [f"Type: **{format_monster_types(monster_types)}**"]
    if is_extra_deck:
        description_lines.append("Category: **Extra Deck Monster**")
    embed = discord.Embed(title=f"{row['monster_name']} ({row['attribute']})", description="\n".join(description_lines), color=discord.Color.blurple())
    embed.add_field(name="HP", value=str(resolved_hp), inline=True)
    embed.add_field(name="ATK", value=str(resolved_atk), inline=True)
    embed.add_field(name="DEF", value=str(resolved_defense), inline=True)
    embed.add_field(name="SPD", value=str(resolved_spd), inline=True)
    embed.add_field(name="Crit Rate", value=format_ratio_percent(resolved_crit_rate), inline=True)
    embed.add_field(name="Crit Damage", value=format_crit_damage_percent(resolved_crit_damage), inline=True)
    embed.add_field(name="Accuracy", value=format_ratio_percent(resolved_accuracy), inline=True)
    embed.add_field(name="Dodge", value=format_ratio_percent(resolved_dodge), inline=True)
    if is_extra_deck:
        monster_name = str(row["monster_name"])
        embed.add_field(name="Summon Requirements", value=extra_deck_summon_requirements_text(monster_name), inline=False)
        embed.add_field(name="Special Effect", value=extra_deck_effect_description(monster_name), inline=False)
    obtained = str(row["obtained_at"])[:19].replace("T", " ") if row["obtained_at"] else "Unknown"
    embed.add_field(name="Obtained", value=obtained, inline=False)
    if is_extra_deck:
        embed.add_field(name="Equip Spell", value="Extra Deck Monsters cannot equip spells.", inline=False)
    else:
        equipped_row = db.get_equipped_spell_for_monster(str(row["instance_id"]), int(row["user_id"]) if "user_id" in row.keys() else None)
        equipped_spell = get_equip_spell_def(equipped_row["equip_spell_id"]) if equipped_row else None
        embed.add_field(name="Equip Spell", value=(f"**{equipped_spell.name}**\n{equipped_spell.description}" if equipped_spell else "None"), inline=False)
    if make_monster_detail_file(row) is not None:
        embed.set_thumbnail(url="attachment://monster_detail.png")
    return embed


class MonsterDetailView(discord.ui.View):
    def __init__(self, owner_id: int, monster_row: sqlite3.Row):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.monster_instance_id = str(monster_row["instance_id"])
        self._sync_remove_button_state()

    def _sync_remove_button_state(self):
        row = db.get_inventory_instance(self.monster_instance_id, self.owner_id)
        is_extra_deck = row is not None and is_extra_deck_monster_row(row)
        equipped_row = db.get_equipped_spell_for_monster(self.monster_instance_id, self.owner_id)
        self.equip_spell_btn.disabled = is_extra_deck
        self.remove_equip_btn.disabled = is_extra_deck or equipped_row is None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This monster belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Equip Spell", style=discord.ButtonStyle.primary)
    async def equip_spell_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.equip import MonsterEquipSpellSelectView
        row = db.get_inventory_instance(self.monster_instance_id, self.owner_id)
        if row is not None and is_extra_deck_monster_row(row):
            self._sync_remove_button_state()
            await safe_edit_interaction_message(interaction, content="Extra Deck Monsters cannot equip spells.", view=self)
            return
        await interaction.response.send_message(
            "Choose an equip spell for this monster.",
            ephemeral=True,
            view=MonsterEquipSpellSelectView(self.owner_id, self.monster_instance_id),
        )

    @discord.ui.button(label="Remove Equip", style=discord.ButtonStyle.danger)
    async def remove_equip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = db.get_inventory_instance(self.monster_instance_id, self.owner_id)
        if row is None:
            await safe_edit_interaction_message(interaction, content="That monster is no longer available.", embed=None, attachments=[], view=None)
            return
        equipped_row = db.get_equipped_spell_for_monster(self.monster_instance_id, self.owner_id)
        if equipped_row is None:
            self._sync_remove_button_state()
            await safe_edit_interaction_message(interaction, content="That monster does not have an equip spell.", view=self)
            return
        spell = get_equip_spell_def(equipped_row["equip_spell_id"])
        db.unequip_spell_from_monster(self.owner_id, self.monster_instance_id)
        updated_row = db.get_inventory_instance(self.monster_instance_id, self.owner_id)
        if updated_row is None:
            await safe_edit_interaction_message(interaction, content="That monster is no longer available.", embed=None, attachments=[], view=None)
            return
        embed = build_monster_detail_embed(updated_row)
        detail_file = make_monster_detail_file(updated_row)
        refreshed_view = MonsterDetailView(self.owner_id, updated_row)
        kwargs = {
            "content": f"Removed **{spell.name if spell else 'that equip spell'}** from **{updated_row['monster_name']}**.",
            "embed": embed,
            "view": refreshed_view,
        }
        if detail_file is not None:
            kwargs["attachments"] = [detail_file]
        else:
            kwargs["attachments"] = []
        await safe_edit_interaction_message(interaction, **kwargs)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = db.get_inventory_instance(self.monster_instance_id, self.owner_id)
        if row is None:
            await safe_edit_interaction_message(interaction, content="That monster is no longer available.", embed=None, attachments=[], view=None)
            return
        embed = build_monster_detail_embed(row)
        detail_file = make_monster_detail_file(row)
        kwargs = {"embed": embed, "view": MonsterDetailView(self.owner_id, row)}
        if detail_file is not None:
            kwargs["attachments"] = [detail_file]
        else:
            kwargs["attachments"] = []
        await safe_edit_interaction_message(interaction, **kwargs)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, view=None)


class UseEquipSpellTicketModal(discord.ui.Modal):
    spell_name = discord.ui.TextInput(
        label="Equip Spell Name",
        required=True,
        min_length=2,
        max_length=80,
        placeholder="Enter full or partial equip spell name",
    )

    def __init__(self, owner_id: int):
        super().__init__(title="Use Equip Spell Ticket")
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This ticket belongs to someone else.", ephemeral=True)
            return
        matches = find_equip_spells_by_partial_name(str(self.spell_name), limit=5)
        if not matches:
            await interaction.response.send_message("No equip spells matched that search. Your ticket was not used.", ephemeral=True)
            return
        exact = [spell for spell in matches if spell.name.casefold() == str(self.spell_name).strip().casefold()]
        if exact:
            # "Typhoon" / "Trap Hole" are also part of longer names; an exact name wins.
            matches = exact[:1]
        if len(matches) > 1:
            preview = "\n".join(f"• {spell.name}" for spell in matches[:5])
            await interaction.response.send_message(f"Multiple equip spells matched. Please search a more specific name:\n{preview}", ephemeral=True)
            return
        spell = matches[0]
        if not db.spend_equip_spell_ticket(self.owner_id, 1):
            await interaction.response.send_message("You do not have any Equip Spell Tickets.", ephemeral=True)
            return
        db.add_equip_spell_to_inventory(self.owner_id, spell.equip_spell_id)
        user_row = db.get_user(self.owner_id)
        await interaction.response.send_message(
            f"Used **1 Equip Spell Ticket** and obtained **{spell.name}**. Tickets left: **{int(user_row['equip_spell_tickets'] or 0)}**.",
            ephemeral=True,
        )


class InventoryCategoryView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    def make_file(self) -> discord.File:
        from kuribot.ui.binder import BinderHubView
        return BinderHubView(self.owner_id, self.user_name).make_file()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This inventory belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Monsters", style=discord.ButtonStyle.primary)
    async def monsters_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CollectionBrowserView(self.owner_id, self.user_name, "monsters")
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Equip Spells", style=discord.ButtonStyle.primary)
    async def equip_spells_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.equip import EquipSpellBrowserView
        view = EquipSpellBrowserView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), view=view, attachments=[])

    @discord.ui.button(label="Use Equip Ticket", style=discord.ButtonStyle.success)
    async def use_equip_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_row = db.get_user(self.owner_id)
        if int(user_row["equip_spell_tickets"] or 0) <= 0:
            await interaction.response.send_message("You do not have any Equip Spell Tickets.", ephemeral=True)
            return
        await interaction.response.send_modal(UseEquipSpellTicketModal(self.owner_id))

    @discord.ui.button(label="Decks", style=discord.ButtonStyle.secondary)
    async def deck_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.decks import DeckBrowserView
        view = DeckBrowserView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Teams", style=discord.ButtonStyle.secondary)
    async def teams_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.teams import TeamBrowserView
        view = TeamBrowserView(self.owner_id, self.user_name)
        view.message = interaction.message
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)


class InventoryView(InventoryCategoryView):
    def summary_text(self, user_row: Optional[sqlite3.Row] = None) -> str:
        from kuribot.ui.binder import binder_hub_summary_text
        return binder_hub_summary_text(self.owner_id)


def build_support_deck_detail_embed(row: dict) -> discord.Embed:
    description = f"Support Deck #{row['deck_id']}"
    if row.get("is_active"):
        description += " • ACTIVE"
    embed = discord.Embed(title=row["name"], description=description, color=discord.Color.gold() if row.get("is_active") else discord.Color.blurple())
    embed.add_field(name="Cards", value=f"{row['card_count']}/{SUPPORT_DECK_SIZE}", inline=True)
    embed.add_field(name="Status", value="Saved", inline=True)
    preview_cards = row.get("cards") or []
    if preview_cards:
        lines = [f"• {card.name} ({support_card_effect_label(card)})" for card in preview_cards[:15]]
        embed.add_field(name="Deck List", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Deck List", value="No cards saved in this deck.", inline=False)
    return embed


def build_support_card_detail_embed(row: dict) -> discord.Embed:
    card = row["card"]
    embed = discord.Embed(title=card.name, description=card.description, color=discord.Color.dark_purple())
    embed.add_field(name="Attribute", value=card.attribute, inline=True)
    embed.add_field(name="Effect", value=support_card_effect_label(card), inline=True)
    obtained = str(row.get("obtained_at") or "Unknown")[:19].replace("T", " ")
    embed.add_field(name="Obtained", value=obtained, inline=False)
    return embed


def build_support_card_browser_rows(user_id: int) -> List[dict]:
    rows: List[dict] = []
    counts: Counter[str] = Counter()
    for row in db.user_support_cards(user_id):
        card = get_support_card_def(str(row["support_card_id"]))
        if card is None:
            continue
        family_key = support_card_family_key(card.support_card_id)
        counts[family_key] += 1
        obtained_at = str(row["obtained_at"] or "")
        obtained_label = "Unknown"
        if obtained_at:
            try:
                obtained_label = datetime.fromisoformat(obtained_at).strftime("%Y-%m-%d")
            except Exception:
                obtained_label = obtained_at[:10]
        rows.append({
            "instance_id": str(row["instance_id"]),
            "support_card_id": card.support_card_id,
            "exp": int(row["exp"] or 0),
            "exp_to_next": support_card_exp_to_next_stage(card.support_card_id),
            "stage": support_card_stage_number(card.support_card_id),
            "max_stage": support_card_max_stage(card.support_card_id),
            "obtained_at": obtained_at,
            "obtained_label": obtained_label,
            "copy_no": counts[family_key],
            "name": card.name,
            "attribute": card.attribute,
            "bonus_pct": int(card.bonus_pct or 0),
            "stat_type": card.stat_type,
            "card": card,
            "is_special": str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL",
        })
    return rows


class SupportCardBrowserSortSelect(discord.ui.Select):
    def __init__(self, browser: "SupportCardBrowserView"):
        self.browser = browser
        options = [
            discord.SelectOption(label="Name A-Z", value="name_asc", default=browser.sort_key == "name_asc"),
            discord.SelectOption(label="Name Z-A", value="name_desc", default=browser.sort_key == "name_desc"),
            discord.SelectOption(label="Highest Bonus", value="bonus_desc", default=browser.sort_key == "bonus_desc"),
            discord.SelectOption(label="Newest First", value="newest", default=browser.sort_key == "newest"),
            discord.SelectOption(label="Oldest First", value="oldest", default=browser.sort_key == "oldest"),
        ]
        super().__init__(placeholder="Sort support cards", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.browser.sort_key = self.values[0]
        self.browser.page = 0
        self.browser.selected_instance_id = None
        self.browser.rebuild()
        await self.browser.refresh_message(interaction, note=f"Sorting by **{self.browser.sort_label()}**.")


class SupportCardBrowserAttributeFilterSelect(discord.ui.Select):
    def __init__(self, browser: "SupportCardBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label="All Attributes", value="__all__", default=browser.attribute_filter == "__all__")]
        options.extend(
            discord.SelectOption(label=attribute.title(), value=attribute, default=browser.attribute_filter == attribute)
            for attribute in ATTRIBUTES
        )
        options.append(discord.SelectOption(label="Special", value="SPECIAL", default=browser.attribute_filter == "SPECIAL"))
        super().__init__(placeholder="Filter by attribute", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.browser.attribute_filter = self.values[0]
        self.browser.page = 0
        self.browser.selected_instance_id = None
        self.browser.rebuild()
        await self.browser.refresh_message(interaction, note=f"Attribute filter: **{self.browser.attribute_filter_label()}**.")


class SupportCardBrowserTypeFilterSelect(discord.ui.Select):
    def __init__(self, browser: "SupportCardBrowserView"):
        self.browser = browser
        options = [
            discord.SelectOption(label="All Effects", value="__all__", default=browser.type_filter == "__all__"),
            discord.SelectOption(label="ATK", value="ATK", default=browser.type_filter == "ATK"),
            discord.SelectOption(label="DEF", value="DEF", default=browser.type_filter == "DEF"),
            discord.SelectOption(label="SPD", value="SPD", default=browser.type_filter == "SPD"),
            discord.SelectOption(label="Special", value="SPECIAL", default=browser.type_filter == "SPECIAL"),
        ]
        super().__init__(placeholder="Filter by effect type", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.browser.type_filter = self.values[0]
        self.browser.page = 0
        self.browser.selected_instance_id = None
        self.browser.rebuild()
        await self.browser.refresh_message(interaction, note=f"Effect filter: **{self.browser.type_filter_label()}**.")


class SupportCardBrowserCardSelect(discord.ui.Select):
    def __init__(self, browser: "SupportCardBrowserView"):
        self.browser = browser
        page_rows = browser.page_rows()
        options: List[discord.SelectOption] = []
        for row in page_rows:
            card = row["card"]
            options.append(discord.SelectOption(
                label=str(card.name)[:100],
                value=str(row["instance_id"]),
                description=support_card_effect_label(card)[:100],
                default=browser.selected_instance_id == str(row["instance_id"]),
            ))
        if not options:
            options = [discord.SelectOption(label="No support cards found", value="__none__")]
        super().__init__(placeholder="Choose a support card to inspect", min_values=1, max_values=1, options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.browser.selected_instance_id = self.values[0]
        self.browser.rebuild()
        selected_row = self.browser.selected_row()
        selected_name = selected_row["card"].name if selected_row else "that card"
        await self.browser.refresh_message(interaction, note=f"Viewing **{selected_name}**.")


class SupportCardBrowserPageButton(discord.ui.Button):
    def __init__(self, browser: "SupportCardBrowserView", direction: int):
        self.browser = browser
        self.direction = direction
        label = "◀" if direction < 0 else "▶"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=4)
        if direction < 0:
            self.disabled = browser.page <= 0
        else:
            self.disabled = browser.page >= browser.max_page_index()

    async def callback(self, interaction: discord.Interaction):
        next_page = self.browser.page + self.direction
        self.browser.page = max(0, min(self.browser.max_page_index(), next_page))
        self.browser.selected_instance_id = None
        self.browser.rebuild()
        await self.browser.refresh_message(interaction)


class SupportCardBrowserBackButton(discord.ui.Button):
    def __init__(self, browser: "SupportCardBrowserView"):
        self.browser = browser
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.ui.binder import binder_hub_summary_text, BinderHubView
        view = BinderHubView(self.browser.owner_id, self.browser.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=binder_hub_summary_text(self.browser.owner_id),
            embed=None,
            attachments_factory=lambda: [view.make_file()],
            view=view,
        )


class SupportCardBrowserView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.sort_key = "name_asc"
        self.attribute_filter = "__all__"
        self.type_filter = "__all__"
        self.page = 0
        self.selected_instance_id: Optional[str] = None
        self.inventory_rows: List[dict] = []
        self.reload_rows()
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This support card browser belongs to someone else.", ephemeral=True)
            return False
        return True

    def reload_rows(self):
        self.inventory_rows = build_support_card_browser_rows(self.owner_id)
        self.page = max(0, min(self.page, self.max_page_index()))
        if self.selected_instance_id and not any(str(row["instance_id"]) == self.selected_instance_id for row in self.inventory_rows):
            self.selected_instance_id = None

    def sort_label(self) -> str:
        return {
            "name_asc": "Name A-Z",
            "name_desc": "Name Z-A",
            "bonus_desc": "Highest Bonus",
            "newest": "Newest First",
            "oldest": "Oldest First",
        }.get(self.sort_key, "Name A-Z")

    def attribute_filter_label(self) -> str:
        if self.attribute_filter == "__all__":
            return "All Attributes"
        if self.attribute_filter == "SPECIAL":
            return "Special"
        return str(self.attribute_filter).title()

    def type_filter_label(self) -> str:
        if self.type_filter == "__all__":
            return "All Effects"
        return str(self.type_filter)

    def filtered_rows(self) -> List[dict]:
        rows: List[dict] = []
        for row in self.inventory_rows:
            card = row["card"]
            if self.attribute_filter != "__all__":
                if self.attribute_filter == "SPECIAL":
                    if not row["is_special"]:
                        continue
                elif str(card.attribute).upper() != str(self.attribute_filter).upper():
                    continue
            if self.type_filter != "__all__" and str(card.stat_type).upper() != str(self.type_filter).upper():
                continue
            rows.append(row)
        if self.sort_key == "name_desc":
            rows.sort(key=lambda row: (str(row["name"]).lower(), str(row["instance_id"])), reverse=True)
        elif self.sort_key == "stage_desc":
            rows.sort(key=lambda row: (-int(row["stage"]), -int(row["bonus_pct"]), str(row["name"]).lower(), str(row["instance_id"])))
        elif self.sort_key == "bonus_desc":
            rows.sort(key=lambda row: (-int(row["bonus_pct"]), -int(row["stage"]), str(row["name"]).lower(), str(row["instance_id"])))
        elif self.sort_key == "newest":
            rows.sort(key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])), reverse=True)
        elif self.sort_key == "oldest":
            rows.sort(key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])))
        else:
            rows.sort(key=lambda row: (str(row["name"]).lower(), str(row["instance_id"])))
        return rows

    def max_page_index(self) -> int:
        filtered_count = len(self.filtered_rows())
        if filtered_count <= 0:
            return 0
        return max(0, math.ceil(filtered_count / self.PER_PAGE) - 1)

    def page_rows(self) -> List[dict]:
        rows = self.filtered_rows()
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        return rows[start:end]

    def selected_row(self) -> Optional[dict]:
        if not self.selected_instance_id:
            return None
        for row in self.filtered_rows():
            if str(row["instance_id"]) == self.selected_instance_id:
                return row
        return None

    def summary_text(self, note: str = "") -> str:
        filtered = self.filtered_rows()
        total_pages = self.max_page_index() + 1
        lines = [
            f"**{self.user_name}'s Support Cards**",
            f"Showing **{len(filtered)}** card(s) • Page **{self.page + 1}/{total_pages}**",
            f"Sort: **{self.sort_label()}** • Attribute: **{self.attribute_filter_label()}** • Effect: **{self.type_filter_label()}**",
        ]
        page_rows = self.page_rows()
        if page_rows:
            lines.append("")
            for idx, row in enumerate(page_rows, start=1 + (self.page * self.PER_PAGE)):
                card = row["card"]
                lines.append(
                    f"`{idx:02d}.` **{card.name}** • {support_card_effect_label(card)}"
                )
        else:
            lines.append("")
            lines.append("No support cards match the current filters.")
        if note:
            lines.append("")
            lines.append(note)
        return fit_discord_message_content("\n".join(lines))

    def selected_embed(self) -> Optional[discord.Embed]:
        row = self.selected_row()
        if row is None:
            return None
        embed = build_support_card_detail_embed(row)
        embed.set_footer(text=f"Copy #{row['copy_no']} • {row['obtained_label']}")
        return embed

    def rebuild(self):
        self.clear_items()
        self.page = max(0, min(self.page, self.max_page_index()))
        self.add_item(SupportCardBrowserSortSelect(self))
        self.add_item(SupportCardBrowserAttributeFilterSelect(self))
        self.add_item(SupportCardBrowserTypeFilterSelect(self))
        self.add_item(SupportCardBrowserCardSelect(self))
        self.add_item(SupportCardBrowserPageButton(self, -1))
        self.add_item(SupportCardBrowserPageButton(self, 1))
        self.add_item(SupportCardBrowserBackButton(self))

    async def refresh_message(self, interaction: discord.Interaction, note: str = ""):
        await safe_edit_interaction_message(
            interaction,
            content=self.summary_text(note),
            embed=self.selected_embed(),
            attachments=[],
            view=self,
        )


class CollectionItemSelect(discord.ui.Select):
    def __init__(self, browser: "CollectionBrowserView"):
        self.browser = browser
        rows = browser.page_rows()
        options = []
        for row in rows:
            if browser.category == "monsters":
                label = str(row["monster_name"])[:100]
                description = f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))}"[:100]
            else:
                label = str(row["name"])[:100]
                description = ("Special Effect" if str(row["stat_type"]).upper() == "SPECIAL" else f"{row['attribute']} • +{row['bonus_pct']}% {row['stat_type']}")[:100]
            options.append(discord.SelectOption(label=label, value=str(row["instance_id"]), description=description))
        if not options:
            options = [discord.SelectOption(label="No items on this page", value="__none__")]
        super().__init__(placeholder="Open a monster/card from this page", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.browser.selected_instance_id = self.values[0]
        row = self.browser.get_selected_row()
        if row is None:
            await interaction.response.send_message("That monster/card is no longer available.", ephemeral=True)
            return
        if self.browser.category == "monsters":
            embed = build_monster_detail_embed(row)
            detail_file = make_monster_detail_file(row)
            kwargs = {"embed": embed, "ephemeral": True, "view": MonsterDetailView(self.browser.owner_id, row)}
            if detail_file is not None:
                kwargs["file"] = detail_file
            await interaction.response.send_message(**kwargs)
        else:
            embed = build_support_card_detail_embed(row)
            await interaction.response.send_message(embed=embed, ephemeral=True)


class CollectionFilterSelect(discord.ui.Select):
    def __init__(self, browser: "CollectionBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.filter_key)) for key, label in browser.filter_options()]
        super().__init__(placeholder="Filter", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.browser.filter_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class CollectionSortSelect(discord.ui.Select):
    def __init__(self, browser: "CollectionBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.sort_key)) for key, label in browser.sort_options()]
        super().__init__(placeholder="Order", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.browser.sort_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class CollectionMonsterSearchSelect(discord.ui.Select):
    def __init__(self, browser: "CollectionBrowserView", rows: Sequence[sqlite3.Row]):
        self.browser = browser
        self.rows = list(rows)
        options: List[discord.SelectOption] = []
        for row in self.rows[:25]:
            description = f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))}"[:100]
            options.append(discord.SelectOption(label=str(row["monster_name"])[:100], value=str(row["instance_id"]), description=description))
        if not options:
            options = [discord.SelectOption(label="No monsters found", value="__none__")]
        super().__init__(placeholder="Choose a monster", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        selected_row = None
        for row in self.rows:
            if str(row["instance_id"]) == str(value):
                selected_row = row
                break
        if selected_row is None:
            await interaction.response.send_message("That monster is no longer available.", ephemeral=True)
            return
        self.browser.selected_instance_id = str(value)
        embed = build_monster_detail_embed(selected_row)
        detail_file = make_monster_detail_file(selected_row)
        kwargs = {"embed": embed, "ephemeral": True, "view": MonsterDetailView(self.browser.owner_id, selected_row)}
        if detail_file is not None:
            kwargs["file"] = detail_file
        await interaction.response.send_message(**kwargs)


class CollectionMonsterSearchResultsView(discord.ui.View):
    def __init__(self, browser: "CollectionBrowserView", rows: Sequence[sqlite3.Row]):
        super().__init__(timeout=180)
        self.browser = browser
        self.owner_id = browser.owner_id
        self.rows = list(rows)
        self.add_item(CollectionMonsterSearchSelect(browser, self.rows))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This monster search belongs to someone else.", ephemeral=True)
            return False
        return True


class CollectionMonsterSearchModal(discord.ui.Modal, title="Search Monsters"):
    monster_name = discord.ui.TextInput(label="Monster name", min_length=1, max_length=80, placeholder="Type part of a monster name")

    def __init__(self, browser: "CollectionBrowserView"):
        super().__init__()
        self.browser = browser

    async def on_submit(self, interaction: discord.Interaction):
        from kuribot.ui.equip import _normalize_equip_lookup
        query = _normalize_equip_lookup(str(self.monster_name))
        if not query:
            await interaction.response.send_message("Type at least part of a monster name.", ephemeral=True)
            return
        candidates = self.browser.search_monster_rows(query)
        if not candidates:
            await interaction.response.send_message("No owned monsters matched that search.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Found **{len(candidates)}** owned monster(s) for **{query}**. Pick one from the dropdown below.",
            ephemeral=True,
            view=CollectionMonsterSearchResultsView(self.browser, candidates),
        )


class CollectionBrowserView(discord.ui.View):
    MONSTER_PER_PAGE = 9
    CARD_PER_PAGE = 6

    def __init__(self, owner_id: int, user_name: str, category: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.category = category
        self.page = 1
        self.filter_key = "all"
        self.sort_key = "name_asc"
        self.monster_type_filter_page = 0
        self.filtered_rows: List[object] = []
        self.selected_instance_id: Optional[str] = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This binder belongs to someone else.", ephemeral=True)
            return False
        return True

    def all_rows(self):
        if self.category == "monsters":
            return list(db.user_inventory(self.owner_id))
        rows = []
        counts = Counter()
        for row in db.user_support_cards(self.owner_id):
            card = get_support_card_def(row["support_card_id"])
            if card is None:
                continue
            family_key = support_card_family_key(card.support_card_id)
            counts[family_key] += 1
            obtained_label = "Unknown"
            if row["obtained_at"]:
                try:
                    obtained_label = datetime.fromisoformat(row["obtained_at"]).strftime("%Y-%m-%d")
                except Exception:
                    obtained_label = str(row["obtained_at"])[:10]
            rows.append({
                "instance_id": row["instance_id"],
                "support_card_id": row["support_card_id"],
                "exp": int(row["exp"] or 0),
                "exp_to_next": support_card_exp_to_next_stage(card.support_card_id),
                "stage": support_card_stage_number(card.support_card_id),
                "max_stage": support_card_max_stage(card.support_card_id),
                "obtained_at": row["obtained_at"],
                "obtained_label": obtained_label,
                "copy_no": counts[family_key],
                "name": card.name,
                "attribute": card.attribute,
                "bonus_pct": card.bonus_pct,
                "stat_type": card.stat_type,
                "card": card,
            })
        return rows

    def per_page(self) -> int:
        return self.MONSTER_PER_PAGE if self.category == "monsters" else self.CARD_PER_PAGE

    def filter_options(self) -> List[Tuple[str, str]]:
        if self.category == "monsters":
            return [("all", "All Monsters")] + [(f"attr:{attribute}", attribute.title()) for attribute in ATTRIBUTES] + [("single", "Single-Type"), ("dual", "Dual-Type"), ("boss", "Extra Deck Monster"), ("equips", "Equips")]
        return [("all", "All Cards")] + [(f"attr:{attribute}", attribute.title()) for attribute in ATTRIBUTES] + [("special", "Special")] + [("bonus:25", "+25% Any Stat")] + [("stat:ATK", "ATK Cards"), ("stat:DEF", "DEF Cards"), ("stat:SPD", "SPD Cards")]

    def sort_options(self) -> List[Tuple[str, str]]:
        options = [("name_asc", "Name A-Z"), ("name_desc", "Name Z-A"), ("newest", "Newest First"), ("oldest", "Oldest First")]
        if self.category == "monsters":
            options += [
                ("atk_desc", "Highest ATK"),
                ("def_desc", "Highest DEF"),
                ("hp_desc", "Highest HP"),
                ("spd_desc", "Highest SPD"),
            ]
        if self.category == "cards":
            options += [("bonus_desc", "Bonus High-Low"), ("bonus_asc", "Bonus Low-High")]
        return options

    def apply_filter(self, rows):
        if self.filter_key == "all":
            return list(rows)
        if self.filter_key.startswith("attr:"):
            attr = self.filter_key.split(":", 1)[1]
            return [row for row in rows if row["attribute"] == attr]
        if self.category == "monsters" and self.filter_key == "single":
            return [row for row in rows if len(decode_monster_types(row["monster_types"])) == 1]
        if self.category == "monsters" and self.filter_key == "dual":
            return [row for row in rows if len(decode_monster_types(row["monster_types"])) > 1]
        if self.category == "monsters" and self.filter_key == "boss":
            return [row for row in rows if is_boss_monster_row(row)]
        if self.category == "monsters" and self.filter_key == "signature":
            return [row for row in rows if is_signature_monster_row(row)]
        if self.category == "monsters" and self.filter_key == "equips":
            return [row for row in rows if db.get_equipped_spell_for_monster(str(row["instance_id"]), self.owner_id) is not None]
        if self.category == "cards" and self.filter_key == "special":
            return [row for row in rows if str(row["stat_type"]).upper() == "SPECIAL"]
        if self.category == "cards" and self.filter_key.startswith("bonus:"):
            bonus = int(self.filter_key.split(":", 1)[1])
            return [row for row in rows if int(row["bonus_pct"]) == bonus]
        if self.category == "cards" and self.filter_key.startswith("stat:"):
            stat_type = self.filter_key.split(":", 1)[1].upper()
            return [row for row in rows if str(row["stat_type"]).upper() == stat_type]
        return list(rows)

    def apply_sort(self, rows):
        if self.category == "monsters":
            if self.sort_key == "name_desc":
                return sorted(rows, key=lambda row: (str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])), reverse=True)
            if self.sort_key == "newest":
                return sorted(rows, key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])), reverse=True)
            if self.sort_key == "oldest":
                return sorted(rows, key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])))
            if self.sort_key == "atk_desc":
                return sorted(rows, key=lambda row: (-resolve_row_combat_stats(row)[1], str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])))
            if self.sort_key == "def_desc":
                return sorted(rows, key=lambda row: (-resolve_row_combat_stats(row)[2], str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])))
            if self.sort_key == "hp_desc":
                return sorted(rows, key=lambda row: (-resolve_row_combat_stats(row)[0], str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])))
            if self.sort_key == "spd_desc":
                return sorted(rows, key=lambda row: (-resolve_row_combat_stats(row)[3], str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])))
            return sorted(rows, key=lambda row: (str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])))
        if self.sort_key == "name_desc":
            return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["bonus_pct"]), str(row["instance_id"])), reverse=True)
        if self.sort_key == "newest":
            return sorted(rows, key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])), reverse=True)
        if self.sort_key == "oldest":
            return sorted(rows, key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])))
        if self.sort_key == "bonus_desc":
            return sorted(rows, key=lambda row: (int(row["bonus_pct"]), str(row["name"]).lower(), str(row["instance_id"])), reverse=True)
        if self.sort_key == "bonus_asc":
            return sorted(rows, key=lambda row: (int(row["bonus_pct"]), str(row["name"]).lower(), str(row["instance_id"])))
        return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["bonus_pct"]), str(row["instance_id"])))

    def total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.filtered_rows)) / self.per_page()))

    def page_rows(self):
        start = (self.page - 1) * self.per_page()
        return self.filtered_rows[start:start + self.per_page()]

    def get_selected_row(self):
        if not self.selected_instance_id:
            return None
        for row in self.filtered_rows:
            if str(row["instance_id"]) == str(self.selected_instance_id):
                return row
        return None

    def current_filter_label(self) -> str:
        return dict(self.filter_options()).get(self.filter_key, "All")

    def current_sort_label(self) -> str:
        return dict(self.sort_options()).get(self.sort_key, "Name A-Z")

    def search_monster_rows(self, query: str) -> List[sqlite3.Row]:
        from kuribot.ui.equip import _normalize_equip_lookup
        if self.category != "monsters":
            return []
        normalized_query = _normalize_equip_lookup(query)
        if not normalized_query:
            return []
        rows = list(db.user_inventory(self.owner_id))
        token_parts = [part for part in normalized_query.split(" ") if part]

        def normalized_monster_name(row: sqlite3.Row) -> str:
            return _normalize_equip_lookup(row["monster_name"])

        def row_key(row: sqlite3.Row):
            normalized_name = normalized_monster_name(row)
            exact_priority = 0 if normalized_name == normalized_query else 1
            return (exact_priority, normalized_name, str(row["attribute"]).lower(), str(row["instance_id"]))

        # Search by the full phrase first. This makes a search like "cyber dragon"
        # return every owned monster whose name contains that phrase, including
        # "Cyber Dragon" itself and names such as "Cyber Dragon Core".
        phrase_matches = [row for row in rows if normalized_query in normalized_monster_name(row)]
        if phrase_matches:
            return sorted(phrase_matches, key=row_key)

        def matches_all_words(row: sqlite3.Row) -> bool:
            normalized_name = normalized_monster_name(row)
            return all(part in normalized_name for part in token_parts)

        return sorted([row for row in rows if matches_all_words(row)], key=row_key)

    def jump_to_instance_id(self, instance_id: str):
        rows = self.apply_sort(self.apply_filter(self.all_rows()))
        for idx, row in enumerate(rows):
            if str(row["instance_id"]) != str(instance_id):
                continue
            self.selected_instance_id = str(instance_id)
            self.filtered_rows = rows
            self.page = (idx // self.per_page()) + 1
            self.rebuild()
            return row
        return None

    def rebuild(self, *, reload: bool = True):
        # Page flips pass reload=False: the rows are already loaded, filtered and sorted, so
        # only the page changes. Filters, sorting, searches and edits reload everything.
        if reload or not getattr(self, "_rows_loaded", False):
            self.filtered_rows = self.apply_sort(self.apply_filter(self.all_rows()))
            self._rows_loaded = True
            self._summary_counts = None
        self.page = max(1, min(self.page, self.total_pages()))
        self.clear_items()
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages()
        self.add_item(CollectionItemSelect(self))
        self.add_item(CollectionFilterSelect(self))
        self.add_item(CollectionSortSelect(self))
        if self.category == "monsters":
            self.add_item(self.search_btn)
        self.add_item(self.back_btn)
        if self.category != "monsters":
            self.add_item(self.view_details_btn)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        if self.selected_instance_id and self.get_selected_row() is None:
            page_rows = self.page_rows()
            self.selected_instance_id = str(page_rows[0]["instance_id"]) if page_rows else None
        elif not self.selected_instance_id:
            page_rows = self.page_rows()
            self.selected_instance_id = str(page_rows[0]["instance_id"]) if page_rows else None
        self.view_details_btn.disabled = self.get_selected_row() is None
        if self.category == "monsters":
            self.view_details_btn.disabled = True

    def summary_text(self) -> str:
        if getattr(self, "_summary_counts", None) is None:
            self._summary_counts = len(db.user_inventory(self.owner_id)) if self.category == "monsters" else len(db.user_support_cards(self.owner_id))
        total_owned = self._summary_counts
        kind = "monsters" if self.category == "monsters" else "cards"
        visible_start = 0 if not self.filtered_rows else (self.page - 1) * self.per_page() + 1
        visible_end = min(len(self.filtered_rows), self.page * self.per_page())
        return f"Showing {visible_start}-{visible_end} of {len(self.filtered_rows)} filtered {kind} • {total_owned} total owned"

    def make_file(self) -> discord.File:
        with _PanelUserContext(self.owner_id):
            if self.category == "monsters":
                buffer = render_monster_binder_page(self.user_name, self.filtered_rows, self.page, self.MONSTER_PER_PAGE, self.current_filter_label(), self.current_sort_label(), db.get_ui_theme(self.owner_id))
                return discord.File(buffer, filename="monsters.png")
            buffer = render_support_card_page(self.user_name, self.filtered_rows, self.page, self.CARD_PER_PAGE, self.current_filter_label(), self.current_sort_label(), db.get_ui_theme(self.owner_id))
            return discord.File(buffer, filename="cards.png")

    async def refresh(self, interaction: discord.Interaction):
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, row=4)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.category != "monsters":
            await safe_acknowledge_interaction(interaction)
            return
        await interaction.response.send_modal(CollectionMonsterSearchModal(self))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary, row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.binder import binder_hub_summary_text, BinderHubView
        view = BinderHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=binder_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="View Selected", style=discord.ButtonStyle.success, row=4)
    async def view_details_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.get_selected_row()
        if row is None:
            await interaction.response.send_message("Select a monster or card from the dropdown first.", ephemeral=True)
            return
        if self.category == "monsters":
            embed = build_monster_detail_embed(row)
            detail_file = make_monster_detail_file(row)
            kwargs = {"embed": embed, "ephemeral": True, "view": MonsterDetailView(self.owner_id, row)}
            if detail_file is not None:
                kwargs["file"] = detail_file
            await interaction.response.send_message(**kwargs)
        else:
            embed = build_support_card_detail_embed(row)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=4)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        self.rebuild(reload=False)
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=4)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages(), self.page + 1)
        self.rebuild(reload=False)
        await self.refresh(interaction)


def build_inventory_embed(user_name: str, user_row: sqlite3.Row, *, monster_count: int, support_card_count: int, equip_spell_count: int, pack_count: int = 0) -> discord.Embed:
    embed = discord.Embed(
        title=f"{user_name}'s Inventory",
        color=discord.Color.blurple(),
    )
    equip_spell_tickets = int(user_row["equip_spell_tickets"] or 0)
    embed.add_field(name="Gold", value=f"{int(user_row['gold'] or 0):,}", inline=True)
    embed.add_field(name="Packs", value=f"{int(pack_count):,}", inline=True)
    embed.add_field(name="Monsters", value=str(int(monster_count)), inline=True)
    embed.add_field(name="Support Cards", value=str(int(support_card_count)), inline=True)
    embed.add_field(name="Equip Spells", value=str(int(equip_spell_count)), inline=True)
    embed.add_field(name="Equip Spell Tickets", value=str(equip_spell_tickets), inline=True)
    embed.add_field(name="Daily Refresh", value=reset_schedule_text(), inline=False)
    return embed
