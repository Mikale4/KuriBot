"""Pack menus."""

from __future__ import annotations

import asyncio
import io
import re
import sqlite3
from typing import Dict, List, Optional, Sequence

import discord
from PIL import ImageDraw

from kuribot.config import PACK_SELL_GUILD_COINS, SUPPORT_CARD_IMAGE_PATH
from kuribot.packs import normalize_pack_key, pack_display_name
from kuribot.database import db
from kuribot.render.fonts import FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS
from kuribot.render.themes import element_color, get_ui_theme_palette
from kuribot.render.images import (
    open_binder_canvas_background, open_card_image_contain_tight, paste_binder_panel,
)
from kuribot.render.text import wrap_text
from kuribot.pack_opening import build_pack_results_embed, open_pack_for_user
from kuribot.render.gacha import render_pack_opening_results_file
from kuribot.ui.common import (
    safe_acknowledge_interaction, safe_edit_interaction_message, safe_send_interaction_message,
)


def render_pack_inventory_page(user_name: str, owner_id: int, packs: Sequence[sqlite3.Row], theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 780
    palette = get_ui_theme_palette(theme_name)
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    total_packs = sum(int(row["quantity"] or 0) for row in packs)
    draw.text((34, 28), f"{user_name}'s Packs", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), f"Owned packs: {total_packs:,} • Open packs to receive 3 monsters and 3 support cards.", font=FONT_S, fill=palette["subtitle"])
    outer = (24, 116, 936, 744)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)
    if not packs:
        draw.text((480, 420), "No packs owned. Buy themed packs from the Pack Shop.", font=FONT_M_BOLD, fill=palette["empty_text"], anchor="mm")
    box_w, box_h = 270, 150
    x_positions = [60, 345, 630]
    y_positions = [154, 324, 494]
    for idx, row in enumerate(list(packs)[:9]):
        x = x_positions[idx % 3]
        y = y_positions[idx // 3]
        pack_name = str(row["pack_name"])
        qty = int(row["quantity"] or 0)
        kind = str(row["theme_kind"] or "random")
        value = str(row["theme_value"] or "")
        accent = element_color(value) if kind == "attribute" else ((190, 132, 255) if kind == "boss" else palette["accent_cards"])
        box = (x, y, x + box_w, y + box_h)
        draw.rounded_rectangle(box, 20, fill=palette["panel_fill"], outline=accent, width=3)
        icon_size = (70, 104)
        icon = open_card_image_contain_tight(str(SUPPORT_CARD_IMAGE_PATH), icon_size, radius=8)
        icon_x = x + 18 + (icon_size[0] - icon.width) // 2
        icon_y = y + 24 + (icon_size[1] - icon.height) // 2
        img.paste(icon, (icon_x, icon_y), icon)
        draw.rounded_rectangle((icon_x, icon_y, icon_x + icon.width, icon_y + icon.height), 8, outline=accent, width=2)
        name_lines = wrap_text(draw, pack_name, FONT_S_BOLD, box_w - 116)[:2]
        for line_idx, line in enumerate(name_lines):
            draw.text((x + 108, y + 28 + line_idx * 22), line, font=FONT_S_BOLD, fill=palette["title"])
        draw.text((x + 108, y + 83), f"Qty: {qty:,}", font=FONT_M_BOLD, fill=palette["accent_dust"])
        theme = "Random" if kind == "random" else ("Boss" if kind == "boss" else value)
        draw.text((x + 108, y + 118), f"Theme: {theme}", font=FONT_XS, fill=palette["info_text"])
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_pack_inventory_file(user_name: str, owner_id: int) -> discord.File:
    packs = db.user_packs(owner_id)
    return discord.File(render_pack_inventory_page(user_name, owner_id, packs, db.get_ui_theme(owner_id)), filename="packs.png")


async def resolve_pack_gift_target(guild: Optional[discord.Guild], query: str) -> Optional[discord.Member]:
    raw = str(query or "").strip()
    if guild is None or not raw:
        return None
    mention_match = re.search(r"(\d{15,25})", raw)
    if mention_match:
        member = guild.get_member(int(mention_match.group(1)))
        if member is not None:
            return member
    lowered = raw.casefold().lstrip("@")
    for member in guild.members:
        names = {member.name.casefold(), member.display_name.casefold(), str(member).casefold()}
        if getattr(member, "global_name", None):
            names.add(str(member.global_name).casefold())
        if lowered in names:
            return member
    return None


class PackSingleResultsView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, pack_key: str, pack_view: Optional["PacksView"] = None):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = str(user_name or "")
        self.pack_key = normalize_pack_key(pack_key) or "random_themed"
        self.pack_view = pack_view
        self._sync_buttons()

    def _sync_buttons(self):
        self.again_button.label = f"Again ({pack_display_name(self.pack_key)})"
        self.again_button.disabled = db.pack_quantity(self.owner_id, self.pack_key) <= 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These pack results belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Again", style=discord.ButtonStyle.success)
    async def again_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_acknowledge_interaction(interaction, ephemeral=False, thinking=False)
        try:
            result = open_pack_for_user(self.owner_id, self.pack_key)
        except ValueError as exc:
            self._sync_buttons()
            await safe_send_interaction_message(interaction, content=str(exc), ephemeral=True)
            return

        if self.pack_view is not None:
            await self.pack_view.refresh_pack_message()

        self.pack_key = normalize_pack_key(str(result.get("pack_key") or self.pack_key)) or self.pack_key
        self._sync_buttons()
        embed = build_pack_results_embed(self.user_name, [result])
        theme_name = db.get_ui_theme(self.owner_id)

        def _make_pack_result_attachment():
            rendered = render_pack_opening_results_file(result, self.user_name, theme_name)
            return [discord.File(rendered, filename="pack_opening.png")]

        await safe_edit_interaction_message(
            interaction,
            embed=embed,
            attachments_factory=_make_pack_result_attachment,
            view=self,
        )


class PackThemeSelect(discord.ui.Select):
    def __init__(self, pack_view: "PacksView", pack_rows: Sequence[sqlite3.Row], *, chunk_index: int = 0, row: int = 0):
        self.pack_view = pack_view
        options: List[discord.SelectOption] = []
        for pack_row in pack_rows:
            pack_key = str(pack_row["pack_key"])
            quantity = int(pack_row["quantity"] or 0)
            options.append(
                discord.SelectOption(
                    label=pack_display_name(pack_key)[:100],
                    value=pack_key[:100],
                    description=f"{quantity:,} owned"[:100],
                )
            )
        placeholder = "Choose a pack theme to open" if chunk_index <= 0 else f"Choose a pack theme to open ({chunk_index + 1})"
        super().__init__(placeholder=placeholder[:100], min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        pack_key = normalize_pack_key(self.values[0]) or "random_themed"
        await open_pack_and_send_result(interaction, self.pack_view.owner_id, self.pack_view.user_name, pack_key, self.pack_view)


class PackOpenSelectView(discord.ui.View):
    def __init__(self, pack_view: "PacksView"):
        super().__init__(timeout=120)
        self.pack_view = pack_view
        packs = list(db.user_packs(pack_view.owner_id))
        for chunk_index, start in enumerate(range(0, len(packs), 25)):
            if chunk_index >= 5:
                break
            chunk = packs[start:start + 25]
            if chunk:
                self.add_item(PackThemeSelect(pack_view, chunk, chunk_index=chunk_index, row=chunk_index))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.pack_view.owner_id:
            await interaction.response.send_message("These packs belong to someone else.", ephemeral=True)
            return False
        return True


async def open_pack_and_send_result(interaction: discord.Interaction, owner_id: int, user_name: str, pack_key: str, pack_view: Optional["PacksView"] = None) -> Optional[discord.Message]:
    await safe_acknowledge_interaction(interaction, ephemeral=False, thinking=True)
    pack_key = normalize_pack_key(pack_key) or "random_themed"
    try:
        result = open_pack_for_user(owner_id, pack_key)
    except ValueError as exc:
        await safe_send_interaction_message(interaction, content=str(exc), ephemeral=True)
        return None

    if pack_view is not None:
        await pack_view.refresh_pack_message()

    embed = build_pack_results_embed(user_name, [result])
    file = await asyncio.to_thread(render_pack_opening_results_file, result, user_name, db.get_ui_theme(owner_id))
    view = PackSingleResultsView(owner_id, user_name, str(result.get("pack_key") or pack_key), pack_view)
    return await safe_send_interaction_message(
        interaction,
        embed=embed,
        file=discord.File(file, filename="pack_opening.png"),
        view=view,
        ephemeral=False,
    )


class PackGiftModal(discord.ui.Modal, title="Gift Pack"):
    username = discord.ui.TextInput(label="Discord username", required=True, max_length=80, placeholder="Username, display name, or mention")
    pack_name = discord.ui.TextInput(label="Pack name", required=True, max_length=80, placeholder="Example: Dinosaur Pack")
    quantity = discord.ui.TextInput(label="Quantity", required=True, max_length=8, default="1")

    def __init__(self, view: "PacksView"):
        super().__init__()
        self.pack_view = view

    async def on_submit(self, interaction: discord.Interaction):
        target = await resolve_pack_gift_target(interaction.guild, str(self.username.value))
        if target is None:
            await interaction.response.send_message("I couldn't find that user in this server.", ephemeral=True)
            return
        pack_key = normalize_pack_key(str(self.pack_name.value))
        if pack_key is None:
            await interaction.response.send_message("That pack name was not recognized.", ephemeral=True)
            return
        try:
            qty = max(1, int(str(self.quantity.value).strip()))
        except Exception:
            await interaction.response.send_message("Quantity must be a whole number.", ephemeral=True)
            return
        if not db.transfer_pack(self.pack_view.owner_id, target.id, pack_key, qty):
            await interaction.response.send_message(f"You do not have {qty:,} {pack_display_name(pack_key)}(s) to gift.", ephemeral=True)
            return
        await self.pack_view.refresh_pack_message()
        await interaction.response.send_message(f"Gifted **{qty:,} {pack_display_name(pack_key)}** to {target.mention}.", ephemeral=True)


class PackSellModal(discord.ui.Modal, title="Sell Pack"):
    pack_name = discord.ui.TextInput(label="Pack name", required=True, max_length=80, placeholder="Example: Light Pack")
    quantity = discord.ui.TextInput(label="Quantity", required=True, max_length=8, default="1")

    def __init__(self, view: "PacksView"):
        super().__init__()
        self.pack_view = view

    async def on_submit(self, interaction: discord.Interaction):
        pack_key = normalize_pack_key(str(self.pack_name.value))
        if pack_key is None:
            await interaction.response.send_message("That pack name was not recognized.", ephemeral=True)
            return
        try:
            qty = max(1, int(str(self.quantity.value).strip()))
        except Exception:
            await interaction.response.send_message("Quantity must be a whole number.", ephemeral=True)
            return
        if not db.spend_pack(self.pack_view.owner_id, pack_key, qty):
            await interaction.response.send_message(f"You do not have {qty:,} {pack_display_name(pack_key)}(s) to sell.", ephemeral=True)
            return
        coins = qty * PACK_SELL_GUILD_COINS
        db.add_guild_coins(self.pack_view.owner_id, coins)
        await self.pack_view.refresh_pack_message()
        await interaction.response.send_message(f"Sold **{qty:,} {pack_display_name(pack_key)}** for **{coins:,} Guild Coins**.", ephemeral=True)


class PackBulkResultsView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, results: Sequence[Dict[str, object]], page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.results = list(results)
        self.page = int(page or 0)
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These pack results belong to someone else.", ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        max_page = max(0, len(self.results) - 1)
        self.page = max(0, min(self.page, max_page))
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= max_page

    def _current_page_result(self) -> Optional[Dict[str, object]]:
        if not self.results:
            return None
        self.page = max(0, min(self.page, len(self.results) - 1))
        return self.results[self.page]

    def _current_attachment(self) -> List[discord.File]:
        result = self._current_page_result()
        if result is None:
            return []
        rendered = render_pack_opening_results_file(result, self.user_name, db.get_ui_theme(self.owner_id))
        return [discord.File(rendered, filename=f"pack_opening_{self.page + 1}.png")]

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_buttons()
        await safe_edit_interaction_message(
            interaction,
            embed=build_pack_results_embed(self.user_name, self.results, self.page, page_size=1),
            attachments_factory=self._current_attachment,
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_buttons()
        await safe_edit_interaction_message(
            interaction,
            embed=build_pack_results_embed(self.user_name, self.results, self.page, page_size=1),
            attachments_factory=self._current_attachment,
            view=self,
        )


class PacksView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.message: Optional[discord.Message] = None

    async def refresh_pack_message(self):
        message = self.message
        if message is None:
            return
        try:
            file = make_pack_inventory_file(self.user_name, self.owner_id)
            await message.edit(attachments=[file], view=self)
        except (discord.NotFound, discord.HTTPException, TypeError, AttributeError):
            return

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These packs belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Open Pack", style=discord.ButtonStyle.success)
    async def open_pack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        packs = db.user_packs(self.owner_id)
        if not packs:
            await interaction.response.send_message("You do not have any packs to open.", ephemeral=True)
            return
        await interaction.response.send_message("Choose a pack theme to open:", view=PackOpenSelectView(self), ephemeral=True)

    @discord.ui.button(label="Open x5", style=discord.ButtonStyle.primary)
    async def open_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_acknowledge_interaction(interaction, ephemeral=False, thinking=True)
        packs = db.user_packs(self.owner_id)
        total = sum(int(row["quantity"] or 0) for row in packs)
        if total <= 0:
            await safe_send_interaction_message(interaction, content="You do not have any packs to open.", ephemeral=True)
            return
        results: List[Dict[str, object]] = []
        remaining_to_open = min(5, total)
        for row in list(packs):
            if remaining_to_open <= 0:
                break
            pack_key = str(row["pack_key"])
            qty = int(row["quantity"] or 0)
            for _ in range(min(qty, remaining_to_open)):
                results.append(open_pack_for_user(self.owner_id, pack_key))
                remaining_to_open -= 1
                if remaining_to_open <= 0:
                    break
        await self.refresh_pack_message()
        view = PackBulkResultsView(self.owner_id, self.user_name, results)
        await safe_send_interaction_message(
            interaction,
            embed=build_pack_results_embed(self.user_name, results, 0, page_size=1),
            files=view._current_attachment(),
            view=view,
            ephemeral=False,
        )

    @discord.ui.button(label="Gift Pack", style=discord.ButtonStyle.secondary)
    async def gift_pack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PackGiftModal(self))

    @discord.ui.button(label="Sell Pack", style=discord.ButtonStyle.danger)
    async def sell_pack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PackSellModal(self))
