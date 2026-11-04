"""Shop menus."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import List, Optional, Sequence, Tuple

import discord

from kuribot.config import SHOP_PAID_REFRESH_COST
from kuribot.models import EquipSpellDef, SupportCardDef
from kuribot.database import db
from kuribot.render.images import _PanelUserContext
from kuribot.render.text import ellipsize_plain_text
from kuribot.shops import (
    card_shop_offer_price, card_shop_offer_to_card, equip_shop_offer_price,
    equip_shop_offer_to_spell, get_card_shop_offers, get_equip_shop_offers,
    get_monster_shop_offers, monster_shop_offer_price, refresh_shop_offers,
    shop_refresh_button_label,
)
from kuribot.render.shop import (
    make_card_shop_file, make_customization_shop_file, make_duel_partners_shop_file,
    make_equip_shop_file, make_monster_shop_file, render_shop_hub_page,
)
from kuribot.ui.common import safe_edit_interaction_message, safe_send_interaction_message


class ShopOfferSelect(discord.ui.Select):
    def __init__(self, shop_kind: str, offers: Sequence[sqlite3.Row]):
        self.shop_kind = shop_kind
        options: List[discord.SelectOption] = []
        for row in offers:
            if bool(row["purchased"]):
                continue
            slot_index = int(row["slot_index"])
            if shop_kind == "monster":
                raw_name = str(row["pack_name"])
                price_text = f"{monster_shop_offer_price(row)} gold"
            elif shop_kind == "card":
                card = card_shop_offer_to_card(row)
                if card is None:
                    continue
                raw_name = card.name
                price_text = f"{card_shop_offer_price(row)} dust"
            else:
                spell = equip_shop_offer_to_spell(row)
                if spell is None:
                    continue
                raw_name = spell.name
                price_text = f"{equip_shop_offer_price(row)} gold"
            label = ellipsize_plain_text(f"Buy {raw_name}", 100)
            description = ellipsize_plain_text(f"Slot #{slot_index} • {price_text}", 100)
            options.append(discord.SelectOption(label=label, description=description, value=str(slot_index)))

        if options:
            placeholder = {"monster": "Choose a pack to buy", "card": "Choose a support card to buy", "equip": "Choose an equip spell to buy"}.get(shop_kind, "Choose an offer to buy")
            super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=1)
            self.disabled = False
        else:
            placeholder = {"monster": "All pack offers purchased", "card": "All support cards purchased", "equip": "All equip spells purchased"}.get(shop_kind, "All offers purchased")
            super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=[discord.SelectOption(label="No offers available", value="none")], row=1)
            self.disabled = True

    async def callback(self, interaction: discord.Interaction):
        if not self.values or self.values[0] == "none":
            await interaction.response.send_message("There are no offers left to buy.", ephemeral=True)
            return
        parent = self.view
        if parent is None:
            return
        await parent.handle_offer_purchase(interaction, self.shop_kind, int(self.values[0]))


SHOP_CATEGORY_CONFIG = {
    "monster": {"label": "Pack Shop", "description": "Browse today's 6 themed packs."},
    "card": {"label": "Support Card Shop", "description": "Browse today's 6 support cards."},
    "equip": {"label": "Equip Spell Shop", "description": "Browse today's 6 equip spells."},
    "customization": {"label": "Customization Shop", "description": "Unlock profile customization upgrades."},
}
SHOP_CATEGORY_ORDER = ("monster", "card", "equip", "customization")


class ShopCategorySelect(discord.ui.Select):
    def __init__(self, current_shop_kind: str):
        options = [
            discord.SelectOption(
                label=SHOP_CATEGORY_CONFIG[shop_kind]["label"],
                description=SHOP_CATEGORY_CONFIG[shop_kind]["description"],
                value=shop_kind,
                default=(shop_kind == current_shop_kind),
            )
            for shop_kind in SHOP_CATEGORY_ORDER
        ]
        super().__init__(placeholder="Switch shop category", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if not self.values:
            return
        parent = self.view
        if parent is None:
            return
        await parent.switch_shop_category(interaction, str(self.values[0]))


def make_combined_shop_view(owner_id: int, user_name: str, shop_kind: str = "monster") -> discord.ui.View:
    shop_kind = str(shop_kind or "monster").strip().lower()
    if shop_kind == "card":
        return CardShopView(owner_id, user_name)
    if shop_kind == "equip":
        return EquipShopView(owner_id, user_name)
    if shop_kind == "customization":
        return CustomizationShopView(owner_id, user_name)
    return MonsterShopView(owner_id, user_name)


class ShopHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This shop belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Pack Shop", style=discord.ButtonStyle.primary)
    async def monster_shop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await MonsterShopView(self.owner_id, self.user_name).show(interaction)

    @discord.ui.button(label="Card Shop", style=discord.ButtonStyle.primary)
    async def card_shop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await CardShopView(self.owner_id, self.user_name).show(interaction)

    @discord.ui.button(label="Equip Shop", style=discord.ButtonStyle.primary)
    async def equip_shop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await EquipShopView(self.owner_id, self.user_name).show(interaction)

    @discord.ui.button(label="Customization", style=discord.ButtonStyle.primary)
    async def customization_shop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await CustomizationShopView(self.owner_id, self.user_name).show(interaction)

    @discord.ui.button(label="Duel Partners", style=discord.ButtonStyle.secondary)
    async def duel_partners_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await DuelPartnersShopView(self.owner_id, self.user_name).show(interaction)


class BaseShopView(discord.ui.View):
    shop_kind = "base"

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.shop_category_select = ShopCategorySelect(self.shop_kind)
        self.add_item(self.shop_category_select)
        # Shop switching now happens through the category dropdown, so the old
        # hub/back navigation button is removed from the unified shop view.
        back_button = getattr(self, "back_button", None)
        if back_button is not None:
            self.remove_item(back_button)
        self.offer_select: Optional[ShopOfferSelect] = None
        self._sync_refresh_button_state()
        self._sync_buy_all_button_state()
        self.refresh_offer_select()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This shop belongs to someone else.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item, /):
        print(f"[SHOP VIEW ERROR] {self.__class__.__name__} item={getattr(item, 'custom_id', None) or getattr(item, 'label', None) or item.__class__.__name__}: {error!r}")
        import traceback
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            await safe_send_interaction_message(
                interaction,
                content="That shop action failed. Please try again.",
                ephemeral=True,
            )
        except Exception:
            pass

    async def switch_shop_category(self, interaction: discord.Interaction, shop_kind: str):
        view = make_combined_shop_view(self.owner_id, self.user_name, shop_kind)
        if hasattr(view, "show"):
            await view.show(interaction)
        else:
            await interaction.response.send_message("That shop category is not available yet.", ephemeral=True)

    def _sync_refresh_button_state(self):
        refresh_button = getattr(self, 'refresh_button', None)
        if refresh_button is None:
            return
        refresh_button.disabled = False
        refresh_button.label = shop_refresh_button_label(self.owner_id, self.shop_kind)

    def _can_buy_all(self) -> bool:
        return False

    def _sync_buy_all_button_state(self):
        buy_all_button = getattr(self, 'buy_all_button', None)
        if buy_all_button is None:
            return
        buy_all_button.disabled = not self._can_buy_all()

    def refresh_offer_select(self):
        if self.offer_select is not None:
            self.remove_item(self.offer_select)
        self.offer_select = ShopOfferSelect(self.shop_kind, self.get_offers())
        self.add_item(self.offer_select)
        self._sync_refresh_button_state()
        self._sync_buy_all_button_state()

    async def show(self, interaction: discord.Interaction):
        self.refresh_offer_select()
        await self.refresh_shop_message(interaction)

    async def refresh_shop_message(self, interaction: discord.Interaction):
        embed, file = self.build_message()
        if file is None:
            await safe_edit_interaction_message(
                interaction,
                content=None,
                embed=embed,
                attachments=[],
                view=self,
            )
        else:
            await safe_edit_interaction_message(
                interaction,
                content=None,
                embed=embed,
                attachments=[file],
                view=self,
            )

    def build_message(self) -> Tuple[Optional[discord.Embed], Optional[discord.File]]:
        raise NotImplementedError

    def get_offers(self) -> Sequence[sqlite3.Row]:
        raise NotImplementedError

    async def handle_offer_purchase(self, interaction: discord.Interaction, shop_kind: str, slot_index: int):
        raise NotImplementedError

    async def handle_buy_all(self, interaction: discord.Interaction):
        await interaction.response.send_message("Buy All is not available for this shop.", ephemeral=True)

    @discord.ui.button(label="Buy All", style=discord.ButtonStyle.success, row=2)
    async def buy_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_buy_all(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=3)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        used, _remaining = db.try_use_shop_refresh(self.owner_id, self.shop_kind)
        if not used:
            user_row = db.get_user(self.owner_id)
            await interaction.response.send_message(
                f"You already used this shop's free refresh. Refresh again for **{SHOP_PAID_REFRESH_COST:,} coins**?\n"
                f"Your coins: **{int(user_row['gold'] or 0):,}**",
                ephemeral=True,
                view=ShopRefreshConfirmView(self, interaction.message),
            )
            return

        refresh_shop_offers(self.owner_id, self.shop_kind)
        self.refresh_offer_select()
        await self.refresh_shop_message(interaction)
        await safe_send_interaction_message(
            interaction,
            content=f"This shop was refreshed for free. Additional refreshes cost **{SHOP_PAID_REFRESH_COST:,} coins** each until the next 12-hour reset.",
            ephemeral=True,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db.get_user(self.owner_id)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=None,
            attachments_factory=lambda: [make_shop_hub_file(self.user_name, user)],
            view=ShopHubView(self.owner_id, self.user_name),
        )


class ShopRefreshConfirmView(discord.ui.View):
    def __init__(self, shop_view: "BaseShopView", source_message: Optional[discord.Message] = None):
        super().__init__(timeout=60)
        self.shop_view = shop_view
        self.source_message = source_message
        self.owner_id = int(shop_view.owner_id)
        self.shop_kind = str(shop_view.shop_kind)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This refresh prompt belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label=f"Refresh for {SHOP_PAID_REFRESH_COST:,} coins", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not db.try_use_paid_shop_refresh(self.owner_id, self.shop_kind, SHOP_PAID_REFRESH_COST):
            user_row = db.get_user(self.owner_id)
            await interaction.response.edit_message(
                content=f"You need **{SHOP_PAID_REFRESH_COST:,} coins** to refresh again. You have **{int(user_row['gold'] or 0):,}**.",
                view=None,
            )
            return
        refresh_shop_offers(self.owner_id, self.shop_kind)
        self.shop_view.refresh_offer_select()
        embed, file = self.shop_view.build_message()
        if self.source_message is not None:
            try:
                if file is None:
                    await self.source_message.edit(content=None, embed=embed, attachments=[], view=self.shop_view)
                else:
                    await self.source_message.edit(content=None, embed=embed, attachments=[file], view=self.shop_view)
            except (discord.NotFound, discord.HTTPException, TypeError):
                pass
        await interaction.response.edit_message(
            content=f"This shop was refreshed for **{SHOP_PAID_REFRESH_COST:,} coins**. You can refresh again as long as you have enough coins.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Refresh cancelled.", view=None)


class MonsterShopView(BaseShopView):
    shop_kind = "monster"

    def get_offers(self) -> Sequence[sqlite3.Row]:
        return get_monster_shop_offers(self.owner_id)

    def build_message(self) -> Tuple[Optional[discord.Embed], Optional[discord.File]]:
        user = db.get_user(self.owner_id)
        offers = self.get_offers()
        return None, make_monster_shop_file(self.user_name, int(user["gold"]), offers)

    def _can_buy_all(self) -> bool:
        offers = [row for row in self.get_offers() if not bool(row["purchased"])]
        if not offers:
            return False
        total_cost = sum(monster_shop_offer_price(row) for row in offers)
        user = db.get_user(self.owner_id)
        return int(user["gold"]) >= total_cost

    async def handle_offer_purchase(self, interaction: discord.Interaction, shop_kind: str, slot_index: int):
        offers = {int(row["slot_index"]): row for row in self.get_offers()}
        offer = offers.get(slot_index)
        if offer is None or bool(offer["purchased"]):
            await interaction.response.send_message("That pack offer is no longer available.", ephemeral=True)
            return
        user = db.get_user(self.owner_id)
        price = monster_shop_offer_price(offer)
        if int(user["gold"]) < price:
            await interaction.response.send_message(f"You need **{price:,}** gold for that pack.", ephemeral=True)
            return

        db.add_gold(self.owner_id, -price)
        db.add_pack(self.owner_id, str(offer["pack_key"]), 1)
        db.mark_monster_shop_offer_purchased(self.owner_id, slot_index)

        self.refresh_offer_select()
        await self.refresh_shop_message(interaction)
        await safe_send_interaction_message(interaction, content=f"Purchased **{offer['pack_name']}** for **{price:,} gold**.", ephemeral=True)

    async def handle_buy_all(self, interaction: discord.Interaction):
        offers = [row for row in self.get_offers() if not bool(row["purchased"])]
        if not offers:
            await interaction.response.send_message("There are no pack offers left to buy.", ephemeral=True)
            return
        total_cost = sum(monster_shop_offer_price(row) for row in offers)
        user = db.get_user(self.owner_id)
        if int(user["gold"]) < total_cost:
            await interaction.response.send_message(f"You need **{total_cost:,}** gold to buy all remaining packs.", ephemeral=True)
            return

        purchased_names: List[str] = []
        for offer in offers:
            slot_index = int(offer["slot_index"])
            db.add_gold(self.owner_id, -monster_shop_offer_price(offer))
            db.add_pack(self.owner_id, str(offer["pack_key"]), 1)
            db.mark_monster_shop_offer_purchased(self.owner_id, slot_index)
            purchased_names.append(str(offer["pack_name"]))

        self.refresh_offer_select()
        await self.refresh_shop_message(interaction)
        summary = ", ".join(f"**{name}**" for name in purchased_names)
        await safe_send_interaction_message(interaction, content=f"Purchased all remaining packs for **{total_cost:,} gold**: {summary}", ephemeral=True)


class CardShopView(BaseShopView):
    shop_kind = "card"

    def get_offers(self) -> Sequence[sqlite3.Row]:
        return get_card_shop_offers(self.owner_id)

    def build_message(self) -> Tuple[Optional[discord.Embed], discord.File]:
        user = db.get_user(self.owner_id)
        offers = self.get_offers()
        return None, make_card_shop_file(self.user_name, int(user["card_dust"]), offers)

    def _can_buy_all(self) -> bool:
        offers = [row for row in self.get_offers() if not bool(row["purchased"])]
        if not offers:
            return False
        total_cost = sum(card_shop_offer_price(row) for row in offers)
        user = db.get_user(self.owner_id)
        return int(user["card_dust"]) >= total_cost

    async def complete_offer_purchase(self, interaction: discord.Interaction, shop_kind: str, slot_index: int):
        offers = {int(row["slot_index"]): row for row in self.get_offers()}
        offer = offers.get(slot_index)
        if offer is None or bool(offer["purchased"]):
            await interaction.response.send_message("That card offer is no longer available.", ephemeral=True)
            return
        user = db.get_user(self.owner_id)
        price = card_shop_offer_price(offer)
        if int(user["card_dust"]) < price:
            await interaction.response.send_message(f"You need **{price}** Card Dust for that support card.", ephemeral=True)
            return

        card = card_shop_offer_to_card(offer)
        if card is None:
            await interaction.response.send_message("That support card could not be loaded.", ephemeral=True)
            return

        db.add_card_dust(self.owner_id, -price)
        db.add_support_card_to_inventory(self.owner_id, card.support_card_id)
        db.mark_card_shop_offer_purchased(self.owner_id, slot_index)

        self.refresh_offer_select()
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=None,
            attachments_factory=lambda: [self.build_message()[1]],
            view=self,
        )
        await safe_send_interaction_message(interaction, content=f"Purchased **{card.name}** for **{price} Card Dust**.", ephemeral=True)

    async def handle_buy_all(self, interaction: discord.Interaction):
        offers = [row for row in self.get_offers() if not bool(row["purchased"])]
        if not offers:
            await interaction.response.send_message("There are no support cards left to buy.", ephemeral=True)
            return
        total_cost = sum(card_shop_offer_price(row) for row in offers)
        user = db.get_user(self.owner_id)
        if int(user["card_dust"]) < total_cost:
            await interaction.response.send_message(f"You need **{total_cost}** Card Dust to buy all remaining support cards.", ephemeral=True)
            return

        purchased_names: List[str] = []
        for offer in offers:
            card = card_shop_offer_to_card(offer)
            if card is None:
                continue
            slot_index = int(offer["slot_index"])
            db.add_card_dust(self.owner_id, -card_shop_offer_price(offer))
            db.add_support_card_to_inventory(self.owner_id, card.support_card_id)
            db.mark_card_shop_offer_purchased(self.owner_id, slot_index)
            purchased_names.append(card.name)

        if not purchased_names:
            await interaction.response.send_message("The remaining support cards could not be loaded.", ephemeral=True)
            return

        self.refresh_offer_select()
        await self.refresh_shop_message(interaction)
        summary = ", ".join(f"**{name}**" for name in purchased_names)
        await safe_send_interaction_message(interaction, content=f"Purchased all remaining support cards for **{total_cost} Card Dust**: {summary}", ephemeral=True)

    async def handle_offer_purchase(self: CardShopView, interaction: discord.Interaction, shop_kind: str, slot_index: int):
        offers = {int(row["slot_index"]): row for row in self.get_offers()}
        offer = offers.get(int(slot_index))
        if offer is None or bool(offer["purchased"]):
            await interaction.response.send_message("That card offer is no longer available.", ephemeral=True)
            return
        user = db.get_user(self.owner_id)
        price = card_shop_offer_price(offer)
        if int(user["card_dust"]) < price:
            await interaction.response.send_message(f"You need **{price}** Card Dust for that support card.", ephemeral=True)
            return
        card = card_shop_offer_to_card(offer)
        if card is None:
            await interaction.response.send_message("That support card could not be loaded.", ephemeral=True)
            return
        await safe_edit_interaction_message(
            interaction,
            content=None,
            attachments=[],
            embed=build_support_card_purchase_confirm_embed(self.owner_id, offer, card, price),
            view=ShopPurchaseConfirmView(self, "card", int(slot_index)),
        )


class EquipShopView(BaseShopView):
    shop_kind = "equip"

    def get_offers(self) -> Sequence[sqlite3.Row]:
        return get_equip_shop_offers(self.owner_id)

    def build_message(self) -> Tuple[Optional[discord.Embed], discord.File]:
        user = db.get_user(self.owner_id)
        offers = self.get_offers()
        return None, make_equip_shop_file(self.user_name, int(user["gold"]), offers)

    def _can_buy_all(self) -> bool:
        offers = [row for row in self.get_offers() if not bool(row["purchased"])]
        if not offers:
            return False
        total_cost = sum(equip_shop_offer_price(row) for row in offers)
        user = db.get_user(self.owner_id)
        return int(user["gold"]) >= total_cost

    async def complete_offer_purchase(self, interaction: discord.Interaction, shop_kind: str, slot_index: int):
        offers = {int(row["slot_index"]): row for row in self.get_offers()}
        offer = offers.get(slot_index)
        if offer is None or bool(offer["purchased"]):
            await interaction.response.send_message("That equip spell offer is no longer available.", ephemeral=True)
            return
        user = db.get_user(self.owner_id)
        price = equip_shop_offer_price(offer)
        if int(user["gold"]) < price:
            await interaction.response.send_message(f"You need **{price}** gold for that equip spell.", ephemeral=True)
            return

        spell = equip_shop_offer_to_spell(offer)
        if spell is None:
            await interaction.response.send_message("That equip spell could not be loaded.", ephemeral=True)
            return

        db.add_gold(self.owner_id, -price)
        db.add_equip_spell_to_inventory(self.owner_id, spell.equip_spell_id)
        db.mark_equip_shop_offer_purchased(self.owner_id, slot_index)

        self.refresh_offer_select()
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=None,
            attachments_factory=lambda: [self.build_message()[1]],
            view=self,
        )
        await safe_send_interaction_message(interaction, content=f"Purchased **{spell.name}** for **{price} gold**.", ephemeral=True)

    async def handle_buy_all(self, interaction: discord.Interaction):
        offers = [row for row in self.get_offers() if not bool(row["purchased"])]
        if not offers:
            await interaction.response.send_message("There are no equip spell offers left to buy.", ephemeral=True)
            return
        total_cost = sum(equip_shop_offer_price(row) for row in offers)
        user = db.get_user(self.owner_id)
        if int(user["gold"]) < total_cost:
            await interaction.response.send_message(f"You need **{total_cost}** gold to buy all remaining equip spells.", ephemeral=True)
            return

        purchased_names: List[str] = []
        for offer in offers:
            spell = equip_shop_offer_to_spell(offer)
            if spell is None:
                continue
            slot_index = int(offer["slot_index"])
            db.add_gold(self.owner_id, -equip_shop_offer_price(offer))
            db.add_equip_spell_to_inventory(self.owner_id, spell.equip_spell_id)
            db.mark_equip_shop_offer_purchased(self.owner_id, slot_index)
            purchased_names.append(spell.name)

        if not purchased_names:
            await interaction.response.send_message("The remaining equip spells could not be loaded.", ephemeral=True)
            return

        self.refresh_offer_select()
        await self.refresh_shop_message(interaction)
        summary = ", ".join(f"**{name}**" for name in purchased_names)
        await safe_send_interaction_message(interaction, content=f"Purchased all remaining equip spells for **{total_cost} gold**: {summary}", ephemeral=True)

    async def handle_offer_purchase(self: EquipShopView, interaction: discord.Interaction, shop_kind: str, slot_index: int):
        offers = {int(row["slot_index"]): row for row in self.get_offers()}
        offer = offers.get(int(slot_index))
        if offer is None or bool(offer["purchased"]):
            await interaction.response.send_message("That equip spell offer is no longer available.", ephemeral=True)
            return
        user = db.get_user(self.owner_id)
        price = equip_shop_offer_price(offer)
        if int(user["gold"]) < price:
            await interaction.response.send_message(f"You need **{price}** gold for that equip spell.", ephemeral=True)
            return
        spell = equip_shop_offer_to_spell(offer)
        if spell is None:
            await interaction.response.send_message("That equip spell could not be loaded.", ephemeral=True)
            return
        await safe_edit_interaction_message(
            interaction,
            content=None,
            attachments=[],
            embed=build_equip_spell_purchase_confirm_embed(self.owner_id, offer, spell, price),
            view=ShopPurchaseConfirmView(self, "equip", int(slot_index)),
        )


class CustomizationShopView(discord.ui.View):
    SHOWCASE_COST = 5000
    UI_THEME_COST = 5000
    BACKGROUND_COST = 5000

    def __init__(self: CustomizationShopView, owner_id: int, user_name: str):
        discord.ui.View.__init__(self, timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.shop_kind = "customization"
        for attr_name in ("buy_showcase_button", "buy_ui_theme_button", "back_button"):
            item = getattr(self, attr_name, None)
            if item is not None and item in self.children:
                self.remove_item(item)
        buy_all_button = getattr(self, "buy_all_button", None)
        if buy_all_button is not None:
            async def buy_all_callback(interaction: discord.Interaction):
                await _dropdown_customization_buy_all_button(self, interaction, buy_all_button)
            buy_all_button.callback = buy_all_callback
        self.shop_category_select = ShopCategorySelect(self.shop_kind)
        self.add_item(self.shop_category_select)
        self.upgrade_select = CustomizationUpgradeSelect(self)
        self.add_item(self.upgrade_select)
        self._sync_buttons()

    def _sync_buttons(self: CustomizationShopView):
        if hasattr(self, "upgrade_select") and self.upgrade_select in self.children:
            self.remove_item(self.upgrade_select)
        self.upgrade_select = CustomizationUpgradeSelect(self)
        self.add_item(self.upgrade_select)
        user = db.get_user(self.owner_id)
        total_cost = 0
        for upgrade in CUSTOMIZATION_UPGRADES:
            feature_name = str(upgrade["key"])
            if not customization_upgrade_owned(self.owner_id, feature_name):
                total_cost += int(getattr(self, str(upgrade["cost_attr"]), 5000))
        buy_all_button = getattr(self, "buy_all_button", None)
        if buy_all_button is not None:
            buy_all_button.disabled = total_cost <= 0 or int(user["gold"]) < total_cost

    async def switch_shop_category(self, interaction: discord.Interaction, shop_kind: str):
        view = make_combined_shop_view(self.owner_id, self.user_name, shop_kind)
        if hasattr(view, "show"):
            await view.show(interaction)
        else:
            await interaction.response.send_message("That shop category is not available yet.", ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This shop belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return make_customization_shop_file(self.user_name, db.get_user(self.owner_id))

    async def show(self, interaction: discord.Interaction):
        self._sync_buttons()
        await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[await asyncio.to_thread(self.make_file)], view=self)

    async def _purchase_upgrade(self: CustomizationShopView, interaction: discord.Interaction, feature_name: str, cost: int, label: str):
        user = db.get_user(self.owner_id)
        if customization_upgrade_owned(self.owner_id, feature_name):
            await interaction.response.send_message(f"You already unlocked **{label}**.", ephemeral=True)
            return
        if int(user["gold"]) < cost:
            await interaction.response.send_message(f"You need **{cost}** gold to unlock **{label}**.", ephemeral=True)
            return
        db.add_gold(self.owner_id, -cost)
        db.unlock_profile_customization(self.owner_id, feature_name)
        self._sync_buttons()
        await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[await asyncio.to_thread(self.make_file)], view=self)
        await interaction.followup.send(f"Unlocked **{label}** for **{cost} gold**.", ephemeral=True)

    @discord.ui.button(label="Buy Showcase", style=discord.ButtonStyle.success, row=1)
    async def buy_showcase_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._purchase_upgrade(interaction, "showcase", self.SHOWCASE_COST, "Showcase")

    @discord.ui.button(label="Buy UI Theme", style=discord.ButtonStyle.success, row=1)
    async def buy_ui_theme_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._purchase_upgrade(interaction, "ui_theme", self.UI_THEME_COST, "UI Theme")

    @discord.ui.button(label="Buy All", style=discord.ButtonStyle.success, row=2)
    async def buy_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db.get_user(self.owner_id)
        unlocks: List[Tuple[str, int, str]] = []
        if not db.has_showcase_unlocked(self.owner_id):
            unlocks.append(("showcase", self.SHOWCASE_COST, "Showcase"))
        if not db.has_ui_theme_unlocked(self.owner_id):
            unlocks.append(("ui_theme", self.UI_THEME_COST, "UI Theme"))
        if not unlocks:
            await interaction.response.send_message("You already unlocked every customization upgrade.", ephemeral=True)
            return
        total_cost = sum(cost for _, cost, _ in unlocks)
        if int(user["gold"]) < total_cost:
            await interaction.response.send_message(f"You need **{total_cost}** gold to buy all remaining customization upgrades.", ephemeral=True)
            return

        purchased_labels: List[str] = []
        for feature_name, cost, label in unlocks:
            db.add_gold(self.owner_id, -cost)
            db.unlock_profile_customization(self.owner_id, feature_name)
            purchased_labels.append(label)

        self._sync_buttons()
        await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[await asyncio.to_thread(self.make_file)], view=self)
        summary = ", ".join(f"**{label}**" for label in purchased_labels)
        await interaction.followup.send(f"Unlocked all remaining customization upgrades for **{total_cost} gold**: {summary}", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db.get_user(self.owner_id)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=None,
            attachments_factory=lambda: [make_shop_hub_file(self.user_name, user)],
            view=ShopHubView(self.owner_id, self.user_name),
        )


class DuelPartnersShopView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This shop belongs to someone else.", ephemeral=True)
            return False
        return True

    async def show(self, interaction: discord.Interaction):
        user = db.get_user(self.owner_id)
        file = make_duel_partners_shop_file(self.user_name, user)
        await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[file], view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db.get_user(self.owner_id)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=None,
            attachments_factory=lambda: [make_shop_hub_file(self.user_name, user)],
            view=ShopHubView(self.owner_id, self.user_name),
        )


def make_shop_hub_file(user_name: str, user_row: sqlite3.Row) -> discord.File:
    with _PanelUserContext(int(user_row["user_id"])):
        return discord.File(render_shop_hub_page(user_name, int(user_row["gold"]), int(user_row["card_dust"]), db.get_ui_theme(int(user_row["user_id"]))), filename="shop_hub.png")


def build_support_card_purchase_confirm_embed(owner_id: int, offer: sqlite3.Row, card: SupportCardDef, price: int) -> discord.Embed:
    user = db.get_user(owner_id)
    embed = discord.Embed(
        title=f"Confirm Support Card Purchase — {card.name}",
        description=str(card.description or "No description available."),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Slot", value=f"#{int(offer['slot_index'])}", inline=True)
    embed.add_field(name="Cost", value=f"{price:,} Card Dust", inline=True)
    embed.add_field(name="Your Card Dust", value=f"{int(user['card_dust']):,}", inline=True)
    embed.add_field(name="Card Details", value=f"**Attribute:** {card.attribute}\n**Bonus:** {card.bonus_pct}% {card.stat_type}", inline=False)
    embed.set_footer(text="Confirm to purchase, or cancel to return to the Support Card Shop.")
    return embed


def build_equip_spell_purchase_confirm_embed(owner_id: int, offer: sqlite3.Row, spell: EquipSpellDef, price: int) -> discord.Embed:
    user = db.get_user(owner_id)
    embed = discord.Embed(
        title=f"Confirm Equip Spell Purchase — {spell.name}",
        description=str(spell.description or "No description available."),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Slot", value=f"#{int(offer['slot_index'])}", inline=True)
    embed.add_field(name="Cost", value=f"{price:,} gold", inline=True)
    embed.add_field(name="Your Gold", value=f"{int(user['gold']):,}", inline=True)
    embed.set_footer(text="Confirm to purchase, or cancel to return to the Equipment Shop.")
    return embed


class ShopPurchaseConfirmView(discord.ui.View):
    def __init__(self, parent_view: BaseShopView, shop_kind: str, slot_index: int):
        super().__init__(timeout=120)
        self.parent_view = parent_view
        self.owner_id = parent_view.owner_id
        self.user_name = parent_view.user_name
        self.shop_kind = shop_kind
        self.slot_index = int(slot_index)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This confirmation belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.shop_kind == "card":
            await self.parent_view.complete_offer_purchase(interaction, self.shop_kind, self.slot_index)
            return
        if self.shop_kind == "equip":
            await self.parent_view.complete_offer_purchase(interaction, self.shop_kind, self.slot_index)
            return
        await interaction.response.send_message("That shop confirmation is no longer valid.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.parent_view.refresh_offer_select()
        await self.parent_view.refresh_shop_message(interaction)


CUSTOMIZATION_UPGRADES = (
    {"key": "showcase", "label": "Showcase", "cost_attr": "SHOWCASE_COST", "description": "Unlock the Showcase upload button in Customize."},
    {"key": "background", "label": "Background", "cost_attr": "BACKGROUND_COST", "description": "Unlock the Background upload button in Customize."},
    {"key": "ui_theme", "label": "UI Theme", "cost_attr": "UI_THEME_COST", "description": "Unlock the UI Theme selector in Customize."},
)


def customization_upgrade_owned(owner_id: int, feature_name: str) -> bool:
    if feature_name == "showcase":
        return db.has_showcase_unlocked(owner_id)
    if feature_name == "background":
        return db.has_background_unlocked(owner_id)
    if feature_name == "ui_theme":
        return db.has_ui_theme_unlocked(owner_id)
    return False


class CustomizationUpgradeSelect(discord.ui.Select):
    def __init__(self, parent_view: CustomizationShopView):
        self.parent_view = parent_view
        options: List[discord.SelectOption] = []
        for upgrade in CUSTOMIZATION_UPGRADES:
            feature_name = str(upgrade["key"])
            label = str(upgrade["label"])
            cost = int(getattr(parent_view, str(upgrade["cost_attr"]), 5000))
            owned = customization_upgrade_owned(parent_view.owner_id, feature_name)
            description = "Owned" if owned else ellipsize_plain_text(f"{cost:,} gold • {upgrade['description']}", 100)
            options.append(discord.SelectOption(label=label, description=description, value=feature_name))
        all_owned = all(customization_upgrade_owned(parent_view.owner_id, str(upgrade["key"])) for upgrade in CUSTOMIZATION_UPGRADES)
        super().__init__(placeholder="Choose a customization upgrade to buy", min_values=1, max_values=1, options=options, row=1, disabled=all_owned)

    async def callback(self, interaction: discord.Interaction):
        if not self.values:
            return
        feature_name = str(self.values[0])
        upgrade = next((item for item in CUSTOMIZATION_UPGRADES if item["key"] == feature_name), None)
        if upgrade is None:
            await interaction.response.send_message("That customization upgrade is not available.", ephemeral=True)
            return
        cost = int(getattr(self.parent_view, str(upgrade["cost_attr"]), 5000))
        await self.parent_view._purchase_upgrade(interaction, feature_name, cost, str(upgrade["label"]))


async def _dropdown_customization_buy_all_button(self: CustomizationShopView, interaction: discord.Interaction, button: discord.ui.Button):
    user = db.get_user(self.owner_id)
    unlocks: List[Tuple[str, int, str]] = []
    for upgrade in CUSTOMIZATION_UPGRADES:
        feature_name = str(upgrade["key"])
        if not customization_upgrade_owned(self.owner_id, feature_name):
            unlocks.append((feature_name, int(getattr(self, str(upgrade["cost_attr"]), 5000)), str(upgrade["label"])))
    if not unlocks:
        await interaction.response.send_message("You already unlocked every customization upgrade.", ephemeral=True)
        return
    total_cost = sum(cost for _, cost, _ in unlocks)
    if int(user["gold"]) < total_cost:
        await interaction.response.send_message(f"You need **{total_cost}** gold to buy all remaining customization upgrades.", ephemeral=True)
        return
    purchased_labels: List[str] = []
    for feature_name, cost, label in unlocks:
        db.add_gold(self.owner_id, -cost)
        db.unlock_profile_customization(self.owner_id, feature_name)
        purchased_labels.append(label)
    self._sync_buttons()
    await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[await asyncio.to_thread(self.make_file)], view=self)
    summary = ", ".join(f"**{label}**" for label in purchased_labels)
    await interaction.followup.send(f"Unlocked all remaining customization upgrades for **{total_cost} gold**: {summary}", ephemeral=True)
