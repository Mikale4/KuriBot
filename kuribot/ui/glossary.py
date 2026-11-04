"""The ;glossary menus."""

from __future__ import annotations

import math
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.config import SINGLE_TYPE_CHANCE
from kuribot.cards.equip_spells import EQUIP_SPELL_DEFS, init_equip_spells
from kuribot.database import db
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.monsters import (
    _normalized_monster_name_key, ATTRIBUTE_STAT_BONUSES, is_boss_monster_name,
    is_signature_monster_name, TYPE_STAT_BONUSES,
)
from kuribot.cards.support_cards import init_support_cards, SUPPORT_CARD_DEFS
from kuribot.ui.common import safe_send_interaction_message


def glossary_status_entries() -> List[Tuple[str, str]]:
    entries = [
        ("BURN", "Loses 5% max HP at the end of each turn. Reapplying BURN refreshes it instead of stacking and also removes FREEZE."),
        ("POISON", "Loses end-of-turn damage that ramps from 1% max HP up to 10%. Reapplying POISON refreshes it instead of stacking."),
        ("STUN", "Cannot act on its next turn."),
        ("FREEZE", "Cannot act on its next turn. Removed immediately by BURN."),
        ("PETRIFY", "Cannot act on its next turn and cannot receive other status effects while petrified."),
        ("TAUNT", "Must target the unit that applied TAUNT while that unit is alive."),
        ("FEAR", "Has a 1 in 3 chance to fail its action each turn."),
        ("SLEEP", "Cannot act on its next turn. Ends early if hit."),
        ("CHARM", "Has a 1 in 3 chance to attack an ally each turn."),
        ("SILENCE", "Equip spell effects are disabled while this debuff is active."),
        ("BOMB", "Explodes after its countdown ends, ignores DEF, deals damage, and STUNS the target if it survives."),
        ("HEAL BLOCK", "Cannot be healed while this debuff is active."),
        ("MARK", "Takes increased damage while this debuff is active."),
        ("IMMUNITY", "Cannot receive new debuffs while this buff is active."),
        ("PROTECTION", "Negates all incoming damage while this buff is active."),
        ("SHIELD", "Absorbs incoming damage until the shield value is depleted."),
        ("TOKEN", "Creates a copy of affected monster with 50% of the monster's stats. TOKENs act as SHIELDs and can also attack enemies on their own. (Note: TOKENs are considered Buffs, therefore can be removed by Buff-removing effects)"),
        ("RECOVERY", "Heals at the end of turns while this buff is active."),
        ("PRECOG", "Strikes its attacker before being attacked."),
        ("LIFESTEAL", "Heals for a portion of the damage it deals."),
        ("REFLECT", "Returns a portion of damage taken back to the attacker."),
        ("COUNTER", "Counterattacks after being hit while this buff is active."),
    ]
    return sorted(entries, key=lambda entry: entry[0].casefold())


def glossary_monster_generation_text(page: int) -> str:
    page = max(1, min(int(page), 3))
    single_type_chance = int(round(SINGLE_TYPE_CHANCE * 100))
    dual_type_chance = max(0, 100 - single_type_chance)
    if page == 1:
        return (
            "**Monster Generation**\n"
            "• A monster's **Attribute** and **Type(s)** are separate systems, so they can be different on purpose.\n"
            "• **Attributes** are generated after the monster is generated, giving that monster a random **Attribute**.\n"
            "• **Type(s)** are rolled separately when a monster is generated. Each copy of a monster gets 1 random type most of the time, or 2 random types occasionally.\n"
            f"• Current roll rates: about **{single_type_chance}% single-type** and **{dual_type_chance}% dual-type**.\n\n"
            "Page **1/3**"
        )
    if page == 2:
        return (
            "**Monster Generation**\n"
            "• Because types are rolled per copy, two copies of the same monster can share the same Attribute but end up with different Type combinations.\n"
            "• Dual-type monsters gain both of their type stat bonuses, then also gain their Attribute bonus.\n"
            "• Type effectiveness in battle checks the monster's Type(s), not its Attribute bonus package.\n"
            "• This means the same monster can fill very different roles depending on the copy you pulled.\n\n"
            "Page **2/3**"
        )
    return (
        "**Monster Generation**\n"
        "• Types are chosen from the full Monster Type pool, so the number of possible combinations is massive.\n"
        "• With the current Attribute pool and Type rules, there are over **53 million** possible monster combinations.\n"
        "• That huge spread is why duplicate monster names can still feel different in battle, team-building, and the market.\n\n"
        "Page **3/3**"
    )


def _format_glossary_bonus_value(stat_key: str, value: float) -> str:
    stat_labels = {
        "hp": "HP",
        "atk": "ATK",
        "defense": "DEF",
        "spd": "SPD",
        "crit_rate": "Crit Rate",
        "crit_damage": "Crit Damage",
        "accuracy": "Accuracy",
        "dodge": "Dodge",
    }
    label = stat_labels.get(str(stat_key), str(stat_key).replace("_", " ").title())
    amount = int(round(float(value) * 100))
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount}% {label}"


def _format_glossary_bonus_line(name: str, bonuses: Dict[str, float]) -> str:
    return f"- {name} → `" + ", ".join(_format_glossary_bonus_value(stat_key, value) for stat_key, value in bonuses.items()) + "`"


def glossary_monster_stat_bonuses_text(page: int) -> str:
    page = max(1, min(int(page), 2))
    attribute_lines = [
        _format_glossary_bonus_line(name, ATTRIBUTE_STAT_BONUSES[name])
        for name in sorted(ATTRIBUTE_STAT_BONUSES, key=str.casefold)
    ]
    type_lines = [
        _format_glossary_bonus_line(name, TYPE_STAT_BONUSES[name])
        for name in sorted(TYPE_STAT_BONUSES, key=str.casefold)
    ]
    split_index = math.ceil(len(type_lines) / 2)
    selected_type_lines = type_lines[:split_index] if page == 1 else type_lines[split_index:]

    base_stats_text = ""
    attribute_text = ""
    if page == 1:
        base_stats_text = (
            "\nThese are the **percentage-based stat bonuses** applied to monsters from their **Attribute** and **Monster Type(s)**.\n\n"
            "> Core stats use the monster's Base Stats, then apply Type Bonus(s) and Attribute Bonus.\n"
            "> Dual-type monsters gain both type bonuses.\n"
            "> Secondary stats start from their base values, then apply any Attribute bonus.\n\n"
            "**Base Secondary Stats**\n"
            "- Crit Rate → `15%`\n"
            "- Crit Damage → `150%`\n"
            "- Accuracy → `90%`\n"
            "- Dodge → `0%`\n\n"
            "----------------------------------------\n"
            "**Attribute Bonuses**\n"
            "----------------------------------------\n"
            + "\n".join(attribute_lines)
            + "\n"
        )
        attribute_text = "\n"

    return (
        "**Monster Stat Bonuses**\n"
        f"{base_stats_text}"
        f"{attribute_text}"
        "----------------------------------------\n"
        "**Monster Type Bonuses**\n"
        "----------------------------------------\n"
        + "\n".join(selected_type_lines)
        + f"\n\nPage **{page}/2**"
    )


def glossary_equip_spell_pages() -> List[str]:
    init_equip_spells()
    page_size = 10
    pages: List[str] = []
    ordered_spells = sorted(EQUIP_SPELL_DEFS, key=lambda spell: (str(spell.name).casefold(), str(spell.equip_spell_id)))
    total_pages = max(1, math.ceil(len(ordered_spells) / page_size))
    for page_number in range(total_pages):
        start = page_number * page_size
        selected = ordered_spells[start:start + page_size]
        lines = [
            "**Equip Spells**",
            "• Equip Spells are equipped by monsters.",
            "• A monster can hold **1 equip spell** at a time.",
            "",
            "",
        ]
        for spell in selected:
            lines.append(f"• **{spell.name}** — {spell.description}")
        lines.append("")
        lines.append(f"Page **{page_number + 1}/{total_pages}**")
        pages.append("\n".join(lines))
    return pages


def glossary_equip_spells_text(page: int) -> str:
    pages = glossary_equip_spell_pages()
    page = max(1, min(int(page), len(pages)))
    return pages[page - 1]


def glossary_support_card_pages(include_special: bool = False) -> List[str]:
    init_support_cards()
    page_size = 10
    pages: List[str] = []
    ordered_cards = sorted(
        [
            card for card in SUPPORT_CARD_DEFS
            if (str(card.attribute).upper() == "SPECIAL") == bool(include_special)
        ],
        key=lambda card: (str(card.attribute), str(card.name).lower(), int(card.bonus_pct), str(card.support_card_id)),
    )
    total_pages = max(1, math.ceil(len(ordered_cards) / page_size))
    title = "**Special Cards**" if include_special else "**Support Cards**"
    subtitle = (
        "• Special Cards are Support Cards with archetype-specific effects."
        if include_special
        else "• Support Cards activate automatically from the active Support Deck."
    )
    detail = (
        "• This glossary lists every Special Support Card effect."
        if include_special
        else "• This glossary lists every non-Special support card definition and description."
    )
    for page_number in range(total_pages):
        start = page_number * page_size
        selected = ordered_cards[start:start + page_size]
        lines = [title, subtitle, detail, ""]
        for card in selected:
            lines.append(f"• **{card.name}** — {card.description}")
        lines.append("")
        lines.append(f"Page **{page_number + 1}/{total_pages}**")
        pages.append("\n".join(lines))
    return pages


def glossary_support_cards_text(page: int) -> str:
    pages = glossary_support_card_pages(include_special=False)
    page = max(1, min(int(page), len(pages)))
    return pages[page - 1]


def glossary_special_support_cards_text(page: int) -> str:
    pages = glossary_support_card_pages(include_special=True)
    page = max(1, min(int(page), len(pages)))
    return pages[page - 1]


def glossary_monster_base_stat_pages() -> List[str]:
    ranges = get_monster_base_stat_ranges()
    lines = [
        '**Monster Base Stats**',
        '',
        '• Monster base stats are the stats a monster starts with before **Attribute** and **Monster Type** generation.',
        '• Attribute and Monster Type bonuses are applied after these base stats.',
        '',
        '**Base Stat Ranges**',
        f"• **HP:** {ranges['hp_min']}-{ranges['hp_max']}",
        f"• **ATK:** {ranges['atk_min']}-{ranges['atk_max']}",
        f"• **DEF:** {ranges['def_min']}-{ranges['def_max']}",
        f"• **SPD:** {ranges['spd_min']}-{ranges['spd_max']}",
        '',
        "Use the **Search** button below to look up a specific monster's base stats.",
        '',
        'Page **1/1**',
    ]
    return ['\n'.join(lines)]


def glossary_monster_base_stats_text(page: int) -> str:
    return glossary_monster_base_stat_pages()[0]


MONSTER_BASE_STATS_SEARCH_PAGE_SIZE = 10


def dedupe_monster_base_stat_rows(rows: Sequence[sqlite3.Row]) -> List[dict]:
    deduped_rows: List[dict] = []
    seen_keys: set[tuple[str, int, int, int, int]] = set()
    for row in rows:
        name = str(row['name'] or '').strip()
        hp = int(row['hp'])
        atk = int(row['atk'])
        defense = int(row['def'])
        spd = int(row['spd'])
        dedupe_key = (name.casefold(), hp, atk, defense, spd)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped_rows.append({
            'name': name,
            'hp': hp,
            'atk': atk,
            'def': defense,
            'spd': spd,
        })
    return deduped_rows


def glossary_special_monster_base_stat_rows(search_value: str) -> List[sqlite3.Row]:
    normalized = _normalized_monster_name_key(search_value)
    if normalized == 'bossmonster':
        matcher = is_boss_monster_name
    elif normalized == 'signaturemonster':
        matcher = is_signature_monster_name
    else:
        return []
    grouped_rows = db.conn.execute(
        """
        SELECT name, MIN(hp) AS hp, MIN(atk) AS atk, MIN(def) AS def, MIN(spd) AS spd
        FROM monster_defs
        GROUP BY name
        ORDER BY name COLLATE NOCASE
        """
    ).fetchall()
    return [row for row in grouped_rows if matcher(str(row['name'] or ''))]


def build_monster_base_stats_search_embeds(search_value: str, rows: Sequence[sqlite3.Row]) -> List[discord.Embed]:
    unique_rows = dedupe_monster_base_stat_rows(rows)
    total_matches = len(unique_rows)
    if total_matches == 0:
        return []

    embeds: List[discord.Embed] = []
    total_pages = max(1, math.ceil(total_matches / MONSTER_BASE_STATS_SEARCH_PAGE_SIZE))
    for page_number in range(total_pages):
        start = page_number * MONSTER_BASE_STATS_SEARCH_PAGE_SIZE
        visible_rows = unique_rows[start:start + MONSTER_BASE_STATS_SEARCH_PAGE_SIZE]
        lines = [
            (
                f"**{row['name']}**\n"
                f"HP: {int(row['hp'])} • ATK: {int(row['atk'])} • DEF: {int(row['def'])} • SPD: {int(row['spd'])}"
            )
            for row in visible_rows
        ]
        embed = discord.Embed(
            title="Monster Base Stats",
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Search: {search_value} • {total_matches} match(es) • Page {page_number + 1}/{total_pages}")
        embeds.append(embed)
    return embeds


class GlossaryMonsterBaseStatsSearchResultsView(discord.ui.View):
    def __init__(self, owner_id: int, search_value: str, rows: Sequence[sqlite3.Row]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.search_value = search_value
        self.rows = list(rows)
        self.embeds = build_monster_base_stats_search_embeds(search_value, self.rows)
        self.page = 0
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('These search results belong to someone else.', ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        max_page = max(0, len(self.embeds) - 1)
        self.page = max(0, min(self.page, max_page))
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= max_page

    @discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(self.embeds) - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)


class GlossaryMonsterBaseStatsSearchModal(discord.ui.Modal, title="Monster Base Stats Search"):

    monster_name = discord.ui.TextInput(
        label="Monster name",
        min_length=1,
        max_length=100,
        placeholder="Enter a monster name",
    )

    def __init__(self, glossary_view: "GlossaryView"):
        super().__init__()
        self.glossary_view = glossary_view

    async def on_submit(self, interaction: discord.Interaction):
        search_value = str(self.monster_name).strip()
        special_rows = glossary_special_monster_base_stat_rows(search_value)
        rows = special_rows or find_monster_base_stats_by_name(search_value)
        if not rows:
            await interaction.response.send_message(
                f"I couldn't find any monsters with **{search_value}** in the name.",
                ephemeral=True,
            )
            return

        view = GlossaryMonsterBaseStatsSearchResultsView(interaction.user.id, search_value, rows)
        await safe_send_interaction_message(interaction, embed=view.embeds[0], view=view, ephemeral=True)


def get_monster_base_stat_ranges() -> dict:
    return {
        "hp_min": 300,
        "hp_max": 480,
        "atk_min": 55,
        "atk_max": 110,
        "def_min": 35,
        "def_max": 90,
        "spd_min": 35,
        "spd_max": 90,
    }


def find_monster_base_stats_by_name(monster_name: str) -> List[dict]:
    if not monster_name:
        return []
    normalized = str(monster_name).strip()
    if not normalized:
        return []
    partial_rows = db.conn.execute(
        """
        SELECT name, hp, atk, def, spd
        FROM monster_defs
        WHERE lower(name) LIKE lower(?)
        ORDER BY
            CASE WHEN lower(name) = lower(?) THEN 0 ELSE 1 END,
            CASE WHEN lower(name) LIKE lower(?) THEN 0 ELSE 1 END,
            length(name),
            name ASC
        """,
        (f"%{normalized}%", normalized, f"{normalized}%"),
    ).fetchall()
    return dedupe_monster_base_stat_rows(partial_rows)


class GlossaryView(discord.ui.View):
    SECTION_LABELS = {
        "statuses": "Statuses",
        "generation": "Monster Generation",
        "stat_bonuses": "Stat Bonuses",
        "monster_base_stats": "Monster Base Stats",
        "equip_spells": "Equip Spells",
        "support_cards": "Support Cards",
        "special_cards": "Special Cards",
    }
    SECTION_PAGES = {
        "statuses": 2,
        "generation": 3,
        "stat_bonuses": 2,
    }

    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None
        self.section = "statuses"
        self.page = 1
        self._sync_buttons()

    def total_pages(self) -> int:
        if self.section == "monster_base_stats":
            return max(1, len(glossary_monster_base_stat_pages()))
        if self.section == "equip_spells":
            return max(1, len(glossary_equip_spell_pages()))
        if self.section == "support_cards":
            return max(1, len(glossary_support_card_pages(include_special=False)))
        if self.section == "special_cards":
            return max(1, len(glossary_support_card_pages(include_special=True)))
        return max(1, int(self.SECTION_PAGES.get(self.section, 1)))

    def set_section(self, section: str):
        self.section = section
        self.page = 1

    def _sync_buttons(self):
        total_pages = self.total_pages()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                custom_id = str(getattr(item, 'custom_id', '') or '')
                if custom_id.startswith('glossary_section:'):
                    section_name = custom_id.split(':', 1)[1]
                    item.disabled = (section_name == self.section)
                elif custom_id == 'glossary_search_monster_base_stats':
                    item.disabled = (self.section != 'monster_base_stats')
                elif custom_id == 'glossary_prev_page':
                    item.disabled = self.page <= 1
                elif custom_id == 'glossary_next_page':
                    item.disabled = self.page >= total_pages

    def content(self) -> str:
        if self.section == 'generation':
            return glossary_monster_generation_text(self.page)
        if self.section == 'stat_bonuses':
            return glossary_monster_stat_bonuses_text(self.page)
        if self.section == 'monster_base_stats':
            return glossary_monster_base_stats_text(self.page)
        if self.section == 'equip_spells':
            return glossary_equip_spells_text(self.page)
        if self.section == 'support_cards':
            return glossary_support_cards_text(self.page)
        if self.section == 'special_cards':
            return glossary_special_support_cards_text(self.page)
        entries = glossary_status_entries()
        page = max(1, min(self.page, 2))
        page_size = math.ceil(len(entries) / 2)
        start = (page - 1) * page_size
        end = start + page_size
        lines = ['**Statuses**', '']
        for name, description in entries[start:end]:
            lines.append(f"• **{name}** — {description}")
        lines.append('')
        lines.append(f'Page **{page}/2**')
        return '\n'.join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This glossary belongs to someone else.', ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        self._sync_buttons()
        content = fit_discord_message_content(self.content())
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=self)
        else:
            await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label='Statuses', style=discord.ButtonStyle.blurple, row=0, custom_id='glossary_section:statuses')
    async def show_statuses(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('statuses')
        await self.refresh(interaction)

    @discord.ui.button(label='Monster Gen', style=discord.ButtonStyle.blurple, row=0, custom_id='glossary_section:generation')
    async def show_generation(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('generation')
        await self.refresh(interaction)

    @discord.ui.button(label='Stat Bonuses', style=discord.ButtonStyle.blurple, row=0, custom_id='glossary_section:stat_bonuses')
    async def show_stat_bonuses(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('stat_bonuses')
        await self.refresh(interaction)

    @discord.ui.button(label='Equip Spells', style=discord.ButtonStyle.blurple, row=0, custom_id='glossary_section:equip_spells')
    async def show_equip_spells(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('equip_spells')
        await self.refresh(interaction)

    @discord.ui.button(label='Monster Base Stats', style=discord.ButtonStyle.blurple, row=1, custom_id='glossary_section:monster_base_stats')
    async def show_monster_base_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('monster_base_stats')
        await self.refresh(interaction)

    @discord.ui.button(label='Support Cards', style=discord.ButtonStyle.blurple, row=1, custom_id='glossary_section:support_cards')
    async def show_support_cards(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('support_cards')
        await self.refresh(interaction)

    @discord.ui.button(label='Special Cards', style=discord.ButtonStyle.blurple, row=1, custom_id='glossary_section:special_cards')
    async def show_special_cards(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.set_section('special_cards')
        await self.refresh(interaction)

    @discord.ui.button(label='Search', style=discord.ButtonStyle.success, row=2, custom_id='glossary_search_monster_base_stats')
    async def search_monster_base_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GlossaryMonsterBaseStatsSearchModal(self))

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary, row=2, custom_id='glossary_prev_page')
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 1:
            self.page -= 1
        await self.refresh(interaction)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary, row=2, custom_id='glossary_next_page')
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages():
            self.page += 1
        await self.refresh(interaction)
