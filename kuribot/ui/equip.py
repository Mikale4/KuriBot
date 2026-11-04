"""Equip spell menus."""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.cards.equip_spells import get_equip_spell_def
from kuribot.database import db
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.monsters import decode_monster_types, format_monster_types
from kuribot.ui.common import (
    safe_acknowledge_interaction, safe_edit_interaction_message, safe_send_interaction_message,
    safe_send_interaction_modal,
)
from kuribot.ui.collection import (
    build_monster_detail_embed, InventoryCategoryView, make_monster_detail_file, MonsterDetailView,
)


class EquipSpellMonsterSearchModal(discord.ui.Modal, title="Find Monster"):
    monster_name = discord.ui.TextInput(label="Monster name", min_length=1, max_length=80, placeholder="Type part of a monster name")

    def __init__(self, owner_id: int, spell_instance_id: str, parent_view: Optional[discord.ui.View] = None):
        super().__init__()
        self.owner_id = owner_id
        self.spell_instance_id = spell_instance_id
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        query = str(self.monster_name).strip().lower()
        candidates = [row for row in db.user_inventory(self.owner_id) if query in str(row["monster_name"]).lower()]
        if not candidates:
            await interaction.response.send_message("No monsters matched that search.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Search results for **{query}**:",
            ephemeral=True,
            view=EquipSpellMonsterTargetView(self.owner_id, self.spell_instance_id, candidates),
        )


class EquipSpellMonsterSelect(discord.ui.Select):
    def __init__(self, parent: "EquipSpellMonsterTargetView"):
        self.parent_view = parent
        rows = parent.page_rows()
        options = []
        for row in rows:
            current = db.get_equipped_spell_for_monster(row["instance_id"], parent.owner_id)
            current_spell = get_equip_spell_def(current["equip_spell_id"]) if current else None
            desc = f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))}"
            if current_spell:
                desc = f"{desc} • Equipped: {current_spell.name}"
            options.append(discord.SelectOption(label=str(row["monster_name"])[:100], value=str(row["instance_id"]), description=desc[:100]))
        if not options:
            options = [discord.SelectOption(label="No monsters", value="0")]
        super().__init__(placeholder="Choose a monster to equip", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        monster_instance_id = self.values[0]
        existing = db.get_equipped_spell_for_monster(monster_instance_id, self.parent_view.owner_id)
        spell_row = db.get_equip_spell_instance(self.parent_view.spell_instance_id, self.parent_view.owner_id)
        monster_row = db.get_inventory_instance(monster_instance_id, self.parent_view.owner_id)
        spell = get_equip_spell_def(spell_row["equip_spell_id"]) if spell_row else None
        swapped = bool(existing and str(existing["equip_spell_instance_id"]) != str(self.parent_view.spell_instance_id))
        ok, msg = db.equip_spell_to_monster(self.parent_view.owner_id, self.parent_view.spell_instance_id, monster_instance_id, replace=True)
        if ok and spell and monster_row:
            action_text = "Swapped equip spell on" if swapped else "Equipped"
            msg = f"{action_text} **{monster_row['monster_name']}** with **{spell.name}**."
        await interaction.response.send_message(msg, ephemeral=True)


class EquipSpellMonsterTargetView(discord.ui.View):
    def __init__(self, owner_id: int, spell_instance_id: str, monster_rows: Optional[Sequence[sqlite3.Row]] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.spell_instance_id = spell_instance_id
        self.monster_rows = list(monster_rows) if monster_rows is not None else list(db.user_inventory(owner_id))
        self.page = 0
        self.rebuild()

    def page_rows(self) -> List[sqlite3.Row]:
        start = self.page * 10
        return self.monster_rows[start:start + 10]

    def rebuild(self):
        self.clear_items()
        self.add_item(EquipSpellMonsterSelect(self))
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.search_btn)
        max_page = max(0, math.ceil(max(1, len(self.monster_rows)) / 10) - 1)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= max_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content="Choose a monster to equip this spell to.", view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = max(0, math.ceil(max(1, len(self.monster_rows)) / 10) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content="Choose a monster to equip this spell to.", view=self)

    @discord.ui.button(label="Search Monster", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EquipSpellMonsterSearchModal(self.owner_id, self.spell_instance_id, self))


class EquipOverviewMonsterVersionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, spell_name: str, rows: Sequence[sqlite3.Row]):
        self.owner_id = owner_id
        self.spell_name = spell_name
        self.monster_rows = list(rows)
        options: List[discord.SelectOption] = []
        for row in self.monster_rows[:25]:
            current = db.get_equipped_spell_for_monster(str(row["instance_id"]), owner_id)
            current_spell = get_equip_spell_def(current["equip_spell_id"]) if current else None
            monster_types = format_monster_types(decode_monster_types(row["monster_types"]))
            desc = f"{row['attribute']} • {monster_types}"
            if current_spell is not None:
                desc = f"{desc} • Equipped: {current_spell.name}"
            options.append(discord.SelectOption(label=str(row["monster_name"])[:100], value=str(row["instance_id"]), description=desc[:100]))
        if not options:
            options = [discord.SelectOption(label="No monsters found", value="0")]
        super().__init__(placeholder="Choose a monster version", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        row = db.get_inventory_instance(self.values[0], self.owner_id)
        if row is None:
            await interaction.response.send_message("That monster could not be found.", ephemeral=True)
            return
        embed = build_monster_detail_embed(row)
        detail_file = make_monster_detail_file(row)
        content = f"Viewing **{row['monster_name']}**. Use **Equip Spell** below to manage its spell"
        if self.spell_name:
            content += f" for **{self.spell_name}**"
        content += "."
        kwargs = {"content": content, "embed": embed, "view": MonsterDetailView(self.owner_id, row), "ephemeral": True}
        if detail_file is not None:
            kwargs["file"] = detail_file
        await interaction.response.send_message(**kwargs)


class EquipOverviewMonsterVersionView(discord.ui.View):
    def __init__(self, owner_id: int, spell_name: str, rows: Sequence[sqlite3.Row]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(EquipOverviewMonsterVersionSelect(owner_id, spell_name, rows))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True


class EquipOverviewMonsterSearchModal(discord.ui.Modal, title="Equip Spell Target"):
    monster_name = discord.ui.TextInput(label="Monster name", min_length=1, max_length=80, placeholder="Type the monster's name")

    def __init__(self, parent_view: "PlayerEquipOverviewView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        query = str(self.monster_name).strip().lower()
        selected_spell = self.parent_view.selected_row()
        spell_name = str(selected_spell.get("name") or "") if selected_spell else ""
        candidates = [row for row in db.user_inventory(self.parent_view.owner_id) if query in str(row["monster_name"]).lower()]
        if not candidates:
            await interaction.response.send_message("No monsters matched that search.", ephemeral=True)
            return
        candidates.sort(key=lambda row: (str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"])))
        await interaction.response.send_message(
            f"Choose which copy of **{str(candidates[0]['monster_name'])}** you want to open.",
            ephemeral=True,
            view=EquipOverviewMonsterVersionView(self.parent_view.owner_id, spell_name, candidates),
        )


def build_equipped_monster_rows(user_id: int) -> List[dict]:
    rows: List[dict] = []
    for monster_row in db.user_inventory(user_id):
        equipped = db.get_equipped_spell_for_monster(str(monster_row["instance_id"]), user_id)
        if not equipped:
            continue
        spell = get_equip_spell_def(str(equipped["equip_spell_id"]))
        rows.append({
            "instance_id": str(monster_row["instance_id"]),
            "monster_name": str(monster_row["monster_name"]),
            "attribute": str(monster_row["attribute"]),
            "monster_types": str(monster_row["monster_types"]),
            "spell_name": spell.name if spell else "Unknown Equip Spell",
        })
    rows.sort(key=lambda row: (str(row["monster_name"]).lower(), str(row["spell_name"]).lower(), str(row["instance_id"])))
    return rows


class PlayerEquipSpellSelect(discord.ui.Select):
    def __init__(self, parent: "PlayerEquipOverviewView"):
        self.parent_view = parent
        options: List[discord.SelectOption] = []
        for row in parent.spell_page_rows():
            options.append(build_equip_spell_select_option(row, selected=(str(row["equip_spell_id"]) == str(parent.selected_spell_id))))
        if not options:
            options = [discord.SelectOption(label="No equip spells", value="0")]
        super().__init__(placeholder="Choose an equip spell", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        self.parent_view.selected_spell_id = self.values[0]
        await self.parent_view.refresh(interaction)


class PlayerEquipOverviewView(discord.ui.View):
    SPELLS_PER_PAGE = 10
    MONSTERS_PER_PAGE = 10

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.spell_page = 0
        self.monster_page = 0
        spell_rows = self.spell_rows()
        self.selected_spell_id = str(spell_rows[0]["equip_spell_id"]) if spell_rows else None
        self.rebuild()

    def spell_rows(self) -> List[dict]:
        return build_grouped_user_equip_spell_rows(self.owner_id, "az")

    def equipped_monster_rows(self) -> List[dict]:
        return build_equipped_monster_rows(self.owner_id)

    def spell_total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.spell_rows())) / self.SPELLS_PER_PAGE))

    def monster_total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.equipped_monster_rows())) / self.MONSTERS_PER_PAGE))

    def spell_page_rows(self) -> List[dict]:
        start = self.spell_page * self.SPELLS_PER_PAGE
        return self.spell_rows()[start:start + self.SPELLS_PER_PAGE]

    def monster_page_rows(self) -> List[dict]:
        start = self.monster_page * self.MONSTERS_PER_PAGE
        return self.equipped_monster_rows()[start:start + self.MONSTERS_PER_PAGE]

    def selected_row(self) -> Optional[dict]:
        for row in self.spell_rows():
            if str(row["equip_spell_id"]) == str(self.selected_spell_id):
                return row
        return None

    def summary_text(self) -> str:
        spell_rows = self.spell_rows()
        equipped_rows = self.equipped_monster_rows()
        lines = [f"**{self.user_name}'s Equip Spells**"]
        selected = self.selected_row()
        if selected is not None:
            lines.append(f"Selected: **{selected['name']}** — {selected['description']}")
            lines.append(f"Owned: **{selected['copy_count']}** • Free: **{selected['available_count']}** • Equipped: **{selected['equipped_count']}**")
        else:
            lines.append("You do not own any equip spells yet.")
        lines.append("")
        lines.append(f"**Equip Spell List** — Page **{self.spell_page + 1}/{self.spell_total_pages()}**")
        if spell_rows:
            for row in self.spell_page_rows():
                marker = "➤ " if str(row["equip_spell_id"]) == str(self.selected_spell_id) else "• "
                lines.append(f"{marker}**{row['name']}** — Owned {row['copy_count']} • Free {row['available_count']} • Equipped {row['equipped_count']}")
        else:
            lines.append("• No equip spells owned.")
        lines.append("")
        lines.append(f"**Monsters With Equipped Spells** — Page **{self.monster_page + 1}/{self.monster_total_pages()}**")
        if equipped_rows:
            for row in self.monster_page_rows():
                lines.append(f"• **{row['monster_name']}** — {row['spell_name']}")
        else:
            lines.append("• No monsters currently have equip spells equipped.")
        lines.append("")
        lines.append("Use **Equip** to search for a monster and open its binder-style stat page.")
        return fit_discord_message_content("\n".join(lines))

    def rebuild(self):
        spell_max = max(0, self.spell_total_pages() - 1)
        monster_max = max(0, self.monster_total_pages() - 1)
        self.spell_page = max(0, min(self.spell_page, spell_max))
        self.monster_page = max(0, min(self.monster_page, monster_max))
        if self.selected_spell_id and self.selected_row() is None:
            page_rows = self.spell_page_rows()
            self.selected_spell_id = str(page_rows[0]["equip_spell_id"]) if page_rows else None
        self.clear_items()
        self.add_item(PlayerEquipSpellSelect(self))
        self.add_item(self.spell_prev_btn)
        self.add_item(self.spell_next_btn)
        self.add_item(self.monster_prev_btn)
        self.add_item(self.monster_next_btn)
        self.add_item(self.equip_btn)
        self.add_item(self.refresh_btn)
        self.spell_prev_btn.disabled = self.spell_page <= 0
        self.spell_next_btn.disabled = self.spell_page >= spell_max
        self.monster_prev_btn.disabled = self.monster_page <= 0
        self.monster_next_btn.disabled = self.monster_page >= monster_max
        self.equip_btn.disabled = self.selected_row() is None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Spell ◀", style=discord.ButtonStyle.secondary, row=1)
    async def spell_prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.spell_page = max(0, self.spell_page - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Spell ▶", style=discord.ButtonStyle.secondary, row=1)
    async def spell_next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.spell_page = min(max(0, self.spell_total_pages() - 1), self.spell_page + 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Monsters ◀", style=discord.ButtonStyle.secondary, row=2)
    async def monster_prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.monster_page = max(0, self.monster_page - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Monsters ▶", style=discord.ButtonStyle.secondary, row=2)
    async def monster_next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.monster_page = min(max(0, self.monster_total_pages() - 1), self.monster_page + 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Equip", style=discord.ButtonStyle.success, row=3)
    async def equip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EquipOverviewMonsterSearchModal(self))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=3)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.refresh(interaction)


class EquipSpellSearchModal(discord.ui.Modal, title="Find Equip Spell"):
    spell_name = discord.ui.TextInput(
        label="Equip spell name",
        min_length=1,
        max_length=80,
        placeholder="Type part of an equip spell name (use clear/reset/all to clear search)",
    )

    def __init__(self, parent_view: "EquipSpellPickerMixin"):
        super().__init__()
        self.parent_view = parent_view
        if getattr(parent_view, "search_query", ""):
            self.spell_name.default = str(parent_view.search_query)

    async def on_submit(self, interaction: discord.Interaction):
        raw_value = str(self.spell_name).strip()
        normalized = raw_value.casefold()
        if normalized in {"all", "clear", "reset"}:
            setattr(self.parent_view, "search_query", "")
            self.parent_view.page = 0
            self.parent_view.apply_filtered_rows(None)
            await safe_edit_interaction_message(
                interaction,
                content=self.parent_view.header_text_with_extra("Equip spell search cleared."),
                view=self.parent_view,
            )
            return

        source_rows = build_grouped_user_equip_spell_rows(self.parent_view.owner_id, getattr(self.parent_view, "sort_mode", "az"))
        tokens = [token for token in re.split(r"\s+", normalized) if token]
        matches = []
        for row in source_rows:
            haystack = f"{row.get('name', '')} {row.get('description', '')}".casefold()
            if normalized in haystack or (tokens and all(token in haystack for token in tokens)):
                matches.append(row)

        if not matches:
            await interaction.response.send_message("No equip spells matched that search.", ephemeral=True)
            return

        setattr(self.parent_view, "search_query", raw_value)
        self.parent_view.page = 0
        if len(matches) == 1:
            self.parent_view.selected_spell_id = str(matches[0]["equip_spell_id"])
        self.parent_view.apply_filtered_rows(matches)
        match_word = "match" if len(matches) == 1 else "matches"
        selected_text = f" Auto-selected **{matches[0]['name']}**." if len(matches) == 1 else ""
        await safe_edit_interaction_message(
            interaction,
            content=self.parent_view.header_text_with_extra(f"Search: **{raw_value}** • {len(matches)} {match_word}.{selected_text}"),
            view=self.parent_view,
        )


class EquipSpellPickerMixin:
    owner_id: int
    page: int
    sort_mode: str

    def header_text_with_extra(self, extra: str = "") -> str:
        base = self.header_text()
        return f"{base}\n{extra}" if extra else base

    def sort_label(self) -> str:
        return "A-Z" if str(getattr(self, "sort_mode", "az")).lower() == "az" else "Z-A"

    def all_rows(self) -> List[dict]:
        raise NotImplementedError

    def apply_filtered_rows(self, rows: Optional[Sequence[dict]] = None):
        raise NotImplementedError

    def header_text(self) -> str:
        return "Choose an equip spell."


def build_grouped_user_equip_spell_rows(user_id: int, sort_mode: str = "az") -> List[dict]:
    grouped: Dict[str, dict] = {}
    for row in db.user_equip_spells(user_id):
        spell_id = str(row["equip_spell_id"])
        spell = get_equip_spell_def(spell_id)
        if spell is None:
            continue
        entry = grouped.setdefault(
            spell_id,
            {
                "equip_spell_id": spell_id,
                "instance_id": str(row["instance_id"]),
                "name": spell.name,
                "description": spell.description,
                "copy_count": 0,
                "available_count": 0,
                "equipped_count": 0,
                "equipped_monster_names": [],
                "equipped_monster_instance_id": None,
            },
        )
        entry["copy_count"] += 1
        equipped_row = db.get_monster_for_equip_spell(str(row["instance_id"]), user_id)
        if equipped_row:
            entry["equipped_count"] += 1
            monster_row = db.get_inventory_instance(str(equipped_row["monster_instance_id"]), user_id)
            if monster_row:
                monster_name = str(monster_row["monster_name"])
                if monster_name not in entry["equipped_monster_names"]:
                    entry["equipped_monster_names"].append(monster_name)
                if entry["equipped_monster_instance_id"] is None:
                    entry["equipped_monster_instance_id"] = str(monster_row["instance_id"])
        else:
            entry["available_count"] += 1
            if not entry.get("instance_id"):
                entry["instance_id"] = str(row["instance_id"])

    rows = list(grouped.values())
    for row in rows:
        row["equipped_monster_names"] = sorted(row["equipped_monster_names"], key=lambda name: name.lower())
    reverse = str(sort_mode or "az").lower() == "za"
    rows.sort(key=lambda row: (str(row.get("name") or "").lower(), str(row.get("equip_spell_id") or "")), reverse=reverse)
    return rows


def resolve_equip_spell_instance_for_selection(
    user_id: int,
    equip_spell_id: str,
    *,
    preferred_instance_id: Optional[str] = None,
    target_monster_instance_id: Optional[str] = None,
) -> Optional[str]:
    equip_spell_id = str(equip_spell_id)
    target_monster_instance_id = str(target_monster_instance_id) if target_monster_instance_id else None

    def _matches(instance_id: Optional[str]) -> Optional[str]:
        if not instance_id:
            return None
        row = db.get_equip_spell_instance(str(instance_id), user_id)
        if row is None or str(row["equip_spell_id"]) != equip_spell_id:
            return None
        equipped_to = db.get_monster_for_equip_spell(str(instance_id), user_id)
        if equipped_to is None:
            return str(instance_id)
        if target_monster_instance_id and str(equipped_to["monster_instance_id"]) == target_monster_instance_id:
            return str(instance_id)
        return None

    matched = _matches(preferred_instance_id)
    if matched:
        return matched

    for row in db.user_equip_spells(user_id):
        if str(row["equip_spell_id"]) != equip_spell_id:
            continue
        matched = _matches(str(row["instance_id"]))
        if matched:
            return matched
    return None


def build_equip_spell_select_option(row: dict, *, selected: bool = False) -> discord.SelectOption:
    name = str(row.get("name") or "Unknown Equip Spell").strip()
    full_description = re.sub(r"\s+", " ", str(row.get("description") or "").strip())
    owned_bits = [f"Owned {int(row.get('copy_count', 0))}"]
    if row.get("available_count"):
        owned_bits.append(f"Free {int(row.get('available_count', 0))}")
    if row.get("equipped_count"):
        equipped_names = ", ".join(str(name) for name in (row.get("equipped_monster_names") or []) if str(name).strip())
        owned_bits.append(f"On {equipped_names}" if equipped_names else f"Equipped {int(row.get('equipped_count', 0))}")
    status_text = " • ".join(owned_bits)

    if full_description:
        label_text = f"{name} — {full_description}"
    else:
        label_text = name
    label = label_text[:100]

    overflow = label_text[100:].strip(" —")
    if overflow:
        description = overflow[:100]
    elif full_description:
        description = full_description[:100]
    else:
        description = status_text[:100]

    if status_text and description == full_description[:100] and len(full_description) <= 100:
        combined = f"{description} • {status_text}"
        if len(combined) <= 100:
            description = combined

    return discord.SelectOption(
        label=label or name[:100],
        value=str(row["equip_spell_id"]),
        description=(description or status_text or None),
        default=selected,
    )


class EquipSpellSelect(discord.ui.Select):
    def __init__(self, browser: "EquipSpellBrowserView"):
        self.browser = browser
        rows = browser.page_rows()
        options = []
        for row in rows:
            options.append(build_equip_spell_select_option(
                row,
                selected=(str(row["equip_spell_id"]) == str(browser.selected_spell_id)),
            ))
        if not options:
            options = [discord.SelectOption(label="No equip spells", value="0")]
        super().__init__(placeholder="Choose an equip spell", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        self.browser.selected_spell_id = self.values[0]
        self.browser.rebuild()
        await safe_edit_interaction_message(interaction, content=self.browser.summary_text(), view=self.browser)


class EquipSpellBrowserView(EquipSpellPickerMixin, discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = 0
        self.sort_mode = "az"
        self.filtered_rows: Optional[List[dict]] = None
        self.search_query = ""
        rows = self.all_rows()
        self.selected_spell_id = str(rows[0]["equip_spell_id"]) if rows else None
        self.rebuild()

    def all_rows(self) -> List[dict]:
        if self.filtered_rows is not None:
            return list(self.filtered_rows)
        return build_grouped_user_equip_spell_rows(self.owner_id, self.sort_mode)

    def apply_filtered_rows(self, rows: Optional[Sequence[dict]] = None):
        self.filtered_rows = list(rows) if rows is not None else None
        self.rebuild()

    def page_rows(self) -> List[dict]:
        rows = self.all_rows()
        start = self.page * 10
        return rows[start:start + 10]

    def selected_row(self) -> Optional[dict]:
        for row in self.all_rows():
            if str(row["equip_spell_id"]) == str(self.selected_spell_id):
                return row
        return None

    def summary_text(self) -> str:
        rows = self.all_rows()
        row = self.selected_row()
        owned = sum(int(item.get("copy_count", 0)) for item in rows)
        unique_count = len(rows)
        if row is None:
            return f"You own **{owned}** equip spell copy/copies across **{unique_count}** unique spell(s)."
        equipped_names = row.get("equipped_monster_names") or []
        if equipped_names:
            equipped_text = f"Equipped on **{', '.join(equipped_names)}**"
        else:
            equipped_text = "Not equipped"
        return (
            f"**{row['name']}**\n{row['description']}\n{equipped_text}\n"
            f"Owned copies: **{row['copy_count']}** • Free copies: **{row['available_count']}** • Sort: **{self.sort_label()}**"
        )

    def header_text(self) -> str:
        return self.summary_text()

    def rebuild(self):
        rows = self.all_rows()
        max_page = max(0, math.ceil(max(1, len(rows)) / 10) - 1)
        self.page = max(0, min(self.page, max_page))
        if self.selected_spell_id and self.selected_row() is None:
            page_rows = self.page_rows()
            self.selected_spell_id = str(page_rows[0]["equip_spell_id"]) if page_rows else None
        self.clear_items()
        self.add_item(EquipSpellSelect(self))
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.sort_btn)
        self.add_item(self.search_btn)
        self.add_item(self.clear_btn)
        self.add_item(self.equip_btn)
        self.add_item(self.unequip_btn)
        self.add_item(self.back_btn)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= max_page
        self.clear_btn.disabled = self.filtered_rows is None
        self.equip_btn.disabled = self.selected_row() is None
        self.unequip_btn.disabled = self.selected_row() is None or not self.selected_row().get("equipped_monster_instance_id")
        self.sort_btn.label = f"Sort: {self.sort_label()}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This inventory belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = max(0, math.ceil(max(1, len(self.all_rows())) / 10) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Sort: A-Z", style=discord.ButtonStyle.secondary)
    async def sort_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sort_mode = "za" if self.sort_mode == "az" else "az"
        self.filtered_rows = None
        self.search_query = ""
        self.page = 0
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Search Spell", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EquipSpellSearchModal(self))

    @discord.ui.button(label="Clear Search", style=discord.ButtonStyle.secondary)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filtered_rows = None
        self.search_query = ""
        self.page = 0
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Equip", style=discord.ButtonStyle.success)
    async def equip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.selected_row()
        if row is None:
            await interaction.response.send_message("Select an equip spell first.", ephemeral=True)
            return
        spell_instance_id = resolve_equip_spell_instance_for_selection(self.owner_id, row["equip_spell_id"], preferred_instance_id=row["instance_id"])
        if not spell_instance_id:
            await interaction.response.send_message("No copy of that equip spell is available.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Choose a monster to equip **{row['name']}** to.",
            ephemeral=True,
            view=EquipSpellMonsterTargetView(self.owner_id, spell_instance_id),
        )

    @discord.ui.button(label="Unequip", style=discord.ButtonStyle.danger)
    async def unequip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.selected_row()
        if row is None:
            await interaction.response.send_message("Select an equip spell first.", ephemeral=True)
            return
        target_monster_id = str(row.get("equipped_monster_instance_id") or "")
        if not target_monster_id:
            await interaction.response.send_message("That equip spell is not currently equipped.", ephemeral=True)
            return
        if db.unequip_spell_from_monster(self.owner_id, target_monster_id):
            self.rebuild()
            await safe_edit_interaction_message(interaction, content=f"Removed **{row['name']}** from its monster.", view=self)
        else:
            await interaction.response.send_message("That equip spell is not currently equipped.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content=f"Choose a category to browse for **{self.user_name}**.", view=InventoryCategoryView(self.owner_id, self.user_name))


class MonsterEquipSpellSelect(discord.ui.Select):
    def __init__(self, parent: "MonsterEquipSpellSelectView"):
        self.parent_view = parent
        rows = parent.page_rows()
        options = []
        for row in rows:
            options.append(build_equip_spell_select_option(
                row,
                selected=(str(row["equip_spell_id"]) == str(parent.selected_spell_id)),
            ))
        if not options:
            options = [discord.SelectOption(label="No equip spells", value="0")]
        super().__init__(placeholder="Choose an equip spell", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        spell_id = self.values[0]
        self.parent_view.selected_spell_id = spell_id
        row = self.parent_view.selected_row()
        if row is None:
            await interaction.response.send_message("That equip spell could not be found.", ephemeral=True)
            return
        spell_instance_id = resolve_equip_spell_instance_for_selection(
            self.parent_view.owner_id,
            spell_id,
            preferred_instance_id=row["instance_id"],
            target_monster_instance_id=self.parent_view.monster_instance_id,
        )
        if not spell_instance_id:
            await interaction.response.send_message("No copy of that equip spell is available.", ephemeral=True)
            return
        existing = db.get_equipped_spell_for_monster(self.parent_view.monster_instance_id, self.parent_view.owner_id)
        monster_row = db.get_inventory_instance(self.parent_view.monster_instance_id, self.parent_view.owner_id)
        spell_row = db.get_equip_spell_instance(spell_instance_id, self.parent_view.owner_id)
        spell = get_equip_spell_def(spell_row["equip_spell_id"]) if spell_row else None
        swapped = bool(existing and str(existing["equip_spell_instance_id"]) != str(spell_instance_id))
        ok, msg = db.equip_spell_to_monster(self.parent_view.owner_id, spell_instance_id, self.parent_view.monster_instance_id, replace=True)
        if ok and spell and monster_row:
            action_text = "Swapped equip spell on" if swapped else "Equipped"
            msg = f"{action_text} **{monster_row['monster_name']}** with **{spell.name}**."
        await interaction.response.send_message(msg, ephemeral=True)


class MonsterEquipSpellSelectView(EquipSpellPickerMixin, discord.ui.View):
    def __init__(self, owner_id: int, monster_instance_id: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.monster_instance_id = monster_instance_id
        self.page = 0
        self.sort_mode = "az"
        self.filtered_rows: Optional[List[dict]] = None
        self.search_query = ""
        existing = db.get_equipped_spell_for_monster(monster_instance_id, owner_id)
        self.selected_spell_id = str(existing["equip_spell_id"]) if existing else None
        self.rebuild()

    def all_rows(self) -> List[dict]:
        if self.filtered_rows is not None:
            return list(self.filtered_rows)
        return build_grouped_user_equip_spell_rows(self.owner_id, self.sort_mode)

    def apply_filtered_rows(self, rows: Optional[Sequence[dict]] = None):
        self.filtered_rows = list(rows) if rows is not None else None
        self.rebuild()

    def page_rows(self) -> List[dict]:
        start = self.page * 10
        return self.all_rows()[start:start + 10]

    def selected_row(self) -> Optional[dict]:
        for row in self.all_rows():
            if str(row["equip_spell_id"]) == str(self.selected_spell_id):
                return row
        return None

    def header_text(self) -> str:
        monster_row = db.get_inventory_instance(self.monster_instance_id, self.owner_id)
        monster_name = str(monster_row["monster_name"]) if monster_row else "this monster"
        search_text = str(getattr(self, "search_query", "")).strip() or "None"
        return f"Choose an equip spell for **{monster_name}**. Search: **{search_text}** • Sort: **{self.sort_label()}**"

    def rebuild(self):
        rows = self.all_rows()
        max_page = max(0, math.ceil(max(1, len(rows)) / 10) - 1)
        self.page = max(0, min(self.page, max_page))
        if self.selected_spell_id and self.selected_row() is None:
            page_rows = self.page_rows()
            self.selected_spell_id = str(page_rows[0]["equip_spell_id"]) if page_rows else None
        self.clear_items()
        self.add_item(MonsterEquipSpellSelect(self))
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.sort_btn)
        self.add_item(self.search_btn)
        self.add_item(self.clear_btn)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= max_page
        self.clear_btn.disabled = self.filtered_rows is None
        self.sort_btn.label = f"Sort: {self.sort_label()}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.header_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = max(0, math.ceil(max(1, len(self.all_rows())) / 10) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.header_text(), view=self)

    @discord.ui.button(label="Sort: A-Z", style=discord.ButtonStyle.secondary)
    async def sort_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sort_mode = "za" if self.sort_mode == "az" else "az"
        self.filtered_rows = None
        self.search_query = ""
        self.page = 0
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.header_text(), view=self)

    @discord.ui.button(label="Search Spell", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EquipSpellSearchModal(self))

    @discord.ui.button(label="Clear Search", style=discord.ButtonStyle.secondary)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filtered_rows = None
        self.search_query = ""
        self.page = 0
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.header_text(), view=self)


def _normalize_equip_lookup(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def find_owned_equip_spell_row_by_name(user_id: int, query: str) -> Optional[dict]:
    normalized_query = _normalize_equip_lookup(query)
    if not normalized_query:
        return None
    rows = build_grouped_user_equip_spell_rows(user_id, "az")
    exact_matches = [row for row in rows if _normalize_equip_lookup(row.get("name") or "") == normalized_query]
    if exact_matches:
        return exact_matches[0]
    partial_matches = [row for row in rows if normalized_query in _normalize_equip_lookup(row.get("name") or "")]
    return partial_matches[0] if partial_matches else None


def find_user_inventory_row_by_name(user_id: int, query: str) -> Optional[sqlite3.Row]:
    normalized_query = _normalize_equip_lookup(query)
    if not normalized_query:
        return None
    rows = list(db.user_inventory(user_id))
    exact_matches = [row for row in rows if _normalize_equip_lookup(row["monster_name"]) == normalized_query]
    if exact_matches:
        return exact_matches[0]
    partial_matches = [row for row in rows if normalized_query in _normalize_equip_lookup(row["monster_name"])]
    return partial_matches[0] if partial_matches else None


def get_owned_equip_spell_row_by_id(user_id: int, equip_spell_id: str) -> Optional[dict]:
    for row in build_grouped_user_equip_spell_rows(user_id, "az"):
        if str(row["equip_spell_id"]) == str(equip_spell_id):
            return row
    return None


def build_equip_spell_detail_embed(row: dict) -> discord.Embed:
    equipped_names = list(row.get("equipped_monster_names") or [])
    equipped_value = "\n".join(f"• {name}" for name in equipped_names[:20]) if equipped_names else "None"
    if equipped_names and len(equipped_names) > 20:
        equipped_value += f"\n• +{len(equipped_names) - 20} more"
    embed = discord.Embed(
        title=str(row.get("name") or "Unknown Equip Spell"),
        description=str(row.get("description") or "No description."),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Owned", value=str(row.get("copy_count") or 0), inline=True)
    embed.add_field(name="Free", value=str(row.get("available_count") or 0), inline=True)
    embed.add_field(name="Equipped", value=str(row.get("equipped_count") or 0), inline=True)
    embed.add_field(name="Equipped By", value=equipped_value, inline=False)
    return embed


def equip_spell_to_named_monster(user_id: int, monster_name: str, equip_spell_row: dict) -> Tuple[bool, str]:
    monster_row = find_user_inventory_row_by_name(user_id, monster_name)
    if monster_row is None:
        return False, f"No monster named **{monster_name.strip()}** was found in your inventory."
    spell_instance_id = resolve_equip_spell_instance_for_selection(
        user_id,
        str(equip_spell_row["equip_spell_id"]),
        preferred_instance_id=equip_spell_row.get("instance_id"),
        target_monster_instance_id=str(monster_row["instance_id"]),
    )
    if not spell_instance_id:
        return False, f"No available copy of **{equip_spell_row['name']}** was found."
    existing = db.get_equipped_spell_for_monster(str(monster_row["instance_id"]), user_id)
    swapped = bool(existing and str(existing["equip_spell_instance_id"]) != str(spell_instance_id))
    ok, msg = db.equip_spell_to_monster(user_id, spell_instance_id, str(monster_row["instance_id"]), replace=True)
    if ok:
        action = "Swapped equip spell on" if swapped else "Equipped"
        msg = f"{action} **{monster_row['monster_name']}** with **{equip_spell_row['name']}**."
    return ok, msg


class QuickEquipSpellModal(discord.ui.Modal, title="Quick Equip"):
    monster_name = discord.ui.TextInput(label="Monster name", min_length=1, max_length=80, placeholder="Type the monster's name")

    def __init__(self, owner_id: int, equip_spell_row: dict):
        super().__init__()
        self.owner_id = owner_id
        self.equip_spell_row = dict(equip_spell_row)

    async def on_submit(self, interaction: discord.Interaction):
        ok, msg = equip_spell_to_named_monster(self.owner_id, str(self.monster_name), self.equip_spell_row)
        await interaction.response.send_message(msg, ephemeral=True)


class EquipMonsterDirectModal(discord.ui.Modal, title="Equip Monster"):
    monster_name = discord.ui.TextInput(label="Monster name", min_length=1, max_length=80, placeholder="Type the monster's name")
    equip_spell_name = discord.ui.TextInput(label="Equip spell name", min_length=1, max_length=80, placeholder="Type the equip spell's name")

    def __init__(self, owner_id: int):
        super().__init__()
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        spell_row = find_owned_equip_spell_row_by_name(self.owner_id, str(self.equip_spell_name))
        if spell_row is None:
            await interaction.response.send_message(
                f"No owned equip spell named **{str(self.equip_spell_name).strip()}** was found.",
                ephemeral=True,
            )
            return
        ok, msg = equip_spell_to_named_monster(self.owner_id, str(self.monster_name), spell_row)
        await interaction.response.send_message(msg, ephemeral=True)


class EquipSpellListSelect(discord.ui.Select):
    def __init__(self, parent: "EquipSpellListView"):
        self.parent_view = parent
        options: List[discord.SelectOption] = []
        for row in parent.page_rows():
            label = str(row.get("name") or "Unknown Equip Spell")[:100]
            description = f"Owned {row['copy_count']} • Free {row['available_count']} • Equipped {row['equipped_count']}"[:100]
            options.append(discord.SelectOption(label=label, value=str(row["equip_spell_id"]), description=description))
        if not options:
            options = [discord.SelectOption(label="No equip spells", value="0")]
        super().__init__(placeholder="Choose an equip spell", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        row = self.parent_view.get_row(self.values[0])
        if row is None:
            await interaction.response.send_message("That equip spell could not be found.", ephemeral=True)
            return
        await safe_edit_interaction_message(
            interaction,
            content="Viewing equip spell details.",
            embed=build_equip_spell_detail_embed(row),
            view=EquipSpellDetailView(self.parent_view.owner_id, self.parent_view.user_name, self.values[0], self.parent_view.page),
        )


class EquipSpellListView(discord.ui.View):
    PAGE_SIZE = 10

    def __init__(self, owner_id: int, user_name: str, page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = page
        self.rebuild()

    def rows(self) -> List[dict]:
        return build_grouped_user_equip_spell_rows(self.owner_id, "az")

    def total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.rows())) / self.PAGE_SIZE))

    def page_rows(self) -> List[dict]:
        start = self.page * self.PAGE_SIZE
        return self.rows()[start:start + self.PAGE_SIZE]

    def get_row(self, equip_spell_id: str) -> Optional[dict]:
        return get_owned_equip_spell_row_by_id(self.owner_id, equip_spell_id)

    def summary_text(self) -> str:
        rows = self.rows()
        if not rows:
            return fit_discord_message_content(
                f"**{self.user_name}'s Equip Spells**\n\nYou do not own any equip spells yet."
            )
        return fit_discord_message_content(
            f"**{self.user_name}'s Equip Spells**\n"
            f"Use the dropdown to inspect an equip spell.\n"
            f"Page **{self.page + 1}/{self.total_pages()}** • Total owned spell types: **{len(rows)}**"
        )

    def rebuild(self):
        max_page = max(0, self.total_pages() - 1)
        self.page = max(0, min(self.page, max_page))
        self.clear_items()
        self.add_item(EquipSpellListSelect(self))
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.back_btn)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= max_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), embed=None, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(max(0, self.total_pages() - 1), self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), embed=None, view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        hub_view = EquipCommandHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=hub_view.summary_text(), embed=None, view=hub_view)


class EquipSpellDetailView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, equip_spell_id: str, list_page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.equip_spell_id = str(equip_spell_id)
        self.list_page = list_page
        self.refresh_button_state()

    def spell_row(self) -> Optional[dict]:
        return get_owned_equip_spell_row_by_id(self.owner_id, self.equip_spell_id)

    def refresh_button_state(self):
        row = self.spell_row()
        self.quick_equip_btn.disabled = row is None or int(row.get("copy_count") or 0) <= 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Quick Equip", style=discord.ButtonStyle.success)
    async def quick_equip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.spell_row()
        if row is None:
            await interaction.response.send_message("That equip spell could not be found.", ephemeral=True)
            return
        if not await safe_send_interaction_modal(interaction, QuickEquipSpellModal(self.owner_id, row)):
            await safe_send_interaction_message(interaction, content="That equip menu expired. Open it again and retry.", ephemeral=True)

    @discord.ui.button(label="Back to List", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        list_view = EquipSpellListView(self.owner_id, self.user_name, self.list_page)
        await safe_edit_interaction_message(interaction, content=list_view.summary_text(), embed=None, view=list_view)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        hub_view = EquipCommandHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=hub_view.summary_text(), embed=None, view=hub_view)


class EquipCommandHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    def summary_text(self) -> str:
        owned_rows = build_grouped_user_equip_spell_rows(self.owner_id, "az")
        equipped_rows = build_equipped_monster_rows(self.owner_id)
        return fit_discord_message_content(
            f"**{self.user_name}'s Equip Menu**\n"
            f"Owned equip spell types: **{len(owned_rows)}**\n"
            f"Monsters with an equip spell: **{len(equipped_rows)}**\n\n"
            "Choose **List** to browse your equip spells, or **Equip Monster** to directly equip by typing a monster name and equip spell name."
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This equip menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="List", style=discord.ButtonStyle.primary)
    async def list_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        list_view = EquipSpellListView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=list_view.summary_text(), embed=None, view=list_view)

    @discord.ui.button(label="Equip Monster", style=discord.ButtonStyle.success)
    async def equip_monster_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EquipMonsterDirectModal(self.owner_id))
