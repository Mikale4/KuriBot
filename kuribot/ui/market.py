"""Player market menus."""

from __future__ import annotations

import math
import sqlite3
from typing import List

import discord

from kuribot.cards.equip_spells import get_equip_spell_def
from kuribot.database import db
from kuribot.render.text import ellipsize_plain_text
from kuribot.monsters import decode_monster_types, encode_monster_types, normalize_monster_name
from kuribot.cards.support_cards import get_support_card_def, support_card_family_key
from kuribot.render.shop import make_market_hub_file
from kuribot.ui.common import safe_edit_interaction_message


# Tickets and Random Pack tickets were converted into packs (pack_ticket_conversion_v1), so
# every player's ticket balance is 0 and those market tabs could never be used. Old ticket
# listings still display through MARKET_LEGACY_CATEGORY_LABELS.
MARKET_LEGACY_CATEGORY_LABELS = {'tickets': 'Tickets', 'premium_tickets': 'Random Packs'}
MARKET_CATEGORY_LABELS = {
    'monsters': 'Monsters',
    'support_cards': 'Support Cards',
    'equip_spells': 'Equip Spells',
}
MARKET_CATEGORY_ORDER = list(MARKET_CATEGORY_LABELS.keys())
MARKET_OWNER_CATEGORY_LABELS = {'all': 'All Listings', **MARKET_CATEGORY_LABELS}
MARKET_PAGE_SIZE = 10


def market_category_label(category: str) -> str:
    key = str(category or '').strip().lower()
    return MARKET_CATEGORY_LABELS.get(key) or MARKET_LEGACY_CATEGORY_LABELS.get(key, 'Unknown')


def market_listing_title(row: sqlite3.Row) -> str:
    category = str(row['category'] or '')
    item_name = str(row['item_name'] or 'Unknown Item')
    price = int(row['price'] or 0)
    qty = max(1, int(row['quantity'] or 1))
    details: List[str] = []
    if category == 'monsters':
        if row['monster_attribute']:
            details.append(str(row['monster_attribute']))
        monster_types = decode_monster_types(row['monster_types']) if row['monster_types'] else []
        if monster_types:
            details.append('/'.join(monster_types))
    elif qty > 1:
        details.append(f'x{qty}')
    detail_text = f" ({' • '.join(details)})" if details else ''
    return f"{item_name}{detail_text} — {price:,} gold"


def build_market_embed(owner_id: int, user_name: str, category: str, page: int, search: str = '') -> discord.Embed:
    user = db.get_user(owner_id)
    rows = db.list_market_listings(category, search)
    total_pages = max(1, math.ceil(len(rows) / MARKET_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * MARKET_PAGE_SIZE
    visible = rows[start:start + MARKET_PAGE_SIZE]
    category_name = market_category_label(category)
    title = f"{user_name}'s Market • Buy"
    description = f"Category: **{category_name}** • Page **{page + 1}/{total_pages}**"
    if search:
        description += f"\nSearch: `{search}`"
    embed = discord.Embed(title=title, description=description, color=discord.Color.gold())
    embed.add_field(name='Gold', value=f"{int(user['gold'] or 0):,}", inline=True)
    embed.add_field(name='Listings', value=str(len(rows)), inline=True)
    embed.add_field(name='Trade', value='Disabled', inline=True)
    if visible:
        lines = [f"`#{int(row['id'])}` {market_listing_title(row)}" for row in visible]
        embed.add_field(name='For Sale', value='\n'.join(lines), inline=False)
    else:
        empty_reason = 'No active listings in this category.' if not search else 'No listings matched that search.'
        embed.add_field(name='For Sale', value=empty_reason, inline=False)
    embed.set_footer(text='Choose a category, then pick a listing below. Search filters by item name.')
    return embed


class MarketSearchModal(discord.ui.Modal, title='Search Market Item'):
    item_name = discord.ui.TextInput(label='Item name', min_length=1, max_length=100, placeholder='Search by exact or partial name')

    def __init__(self, market_view: 'MarketBuyView'):
        super().__init__()
        self.market_view = market_view

    async def on_submit(self, interaction: discord.Interaction):
        self.market_view.search_query = str(self.item_name.value or '').strip()
        self.market_view.page = 0
        await self.market_view.refresh(interaction)


class MarketCategorySelect(discord.ui.Select):
    def __init__(self, market_view: 'MarketBuyView'):
        self.market_view = market_view
        options = [
            discord.SelectOption(label=label, value=value, default=(value == market_view.category))
            for value, label in MARKET_CATEGORY_LABELS.items()
        ]
        super().__init__(placeholder='Choose a category', min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.market_view.category = self.values[0]
        self.market_view.page = 0
        self.market_view.search_query = ''
        await self.market_view.refresh(interaction)


class MarketListingSelect(discord.ui.Select):
    def __init__(self, market_view: 'MarketBuyView'):
        self.market_view = market_view
        rows = market_view.current_rows()
        if rows:
            options = []
            for row in rows:
                desc = ellipsize_plain_text(f"{int(row['price'] or 0):,} gold", 100)
                options.append(discord.SelectOption(label=ellipsize_plain_text(str(row['item_name'] or 'Unknown Item'), 100), description=desc, value=str(int(row['id']))))
            super().__init__(placeholder='Choose an item to buy', min_values=1, max_values=1, options=options, row=1)
            self.disabled = False
        else:
            super().__init__(placeholder='No items on this page', min_values=1, max_values=1, options=[discord.SelectOption(label='No items available', value='none')], row=1)
            self.disabled = True

    async def callback(self, interaction: discord.Interaction):
        if not self.values or self.values[0] == 'none':
            await interaction.response.send_message('There are no items to buy on this page.', ephemeral=True)
            return
        listing_id = int(self.values[0])
        row = db.get_market_listing(listing_id)
        if row is None or str(row['status']) != 'active':
            await interaction.response.send_message('That listing is no longer available.', ephemeral=True)
            return
        await safe_edit_interaction_message(interaction, embed=build_market_confirm_embed(self.market_view.owner_id, row), view=MarketConfirmBuyView(self.market_view, listing_id))


class MarketBuyView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, *, category: str = 'monsters', page: int = 0, search_query: str = ''):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.category = category if category in MARKET_CATEGORY_ORDER else 'monsters'
        self.page = max(0, int(page))
        self.search_query = str(search_query or '').strip()
        self._rebuild_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    def rows(self) -> List[sqlite3.Row]:
        return db.list_market_listings(self.category, self.search_query)

    def current_rows(self) -> List[sqlite3.Row]:
        rows = self.rows()
        total_pages = max(1, math.ceil(len(rows) / MARKET_PAGE_SIZE))
        self.page = max(0, min(self.page, total_pages - 1))
        start = self.page * MARKET_PAGE_SIZE
        return rows[start:start + MARKET_PAGE_SIZE]

    def _rebuild_components(self):
        self.clear_items()
        self.add_item(MarketCategorySelect(self))
        self.add_item(MarketListingSelect(self))
        prev_disabled = self.page <= 0
        next_disabled = (self.page + 1) >= max(1, math.ceil(len(self.rows()) / MARKET_PAGE_SIZE))
        prev_btn = discord.ui.Button(label='Prev', style=discord.ButtonStyle.secondary, row=2, disabled=prev_disabled)
        next_btn = discord.ui.Button(label='Next', style=discord.ButtonStyle.secondary, row=2, disabled=next_disabled)
        search_btn = discord.ui.Button(label='Search', style=discord.ButtonStyle.primary, row=2)
        sell_btn = discord.ui.Button(label='Sell', style=discord.ButtonStyle.success, row=3)
        trade_btn = discord.ui.Button(label='Trade', style=discord.ButtonStyle.secondary, row=3, disabled=True)
        back_btn = discord.ui.Button(label='Back', style=discord.ButtonStyle.secondary, row=3)

        async def prev_callback(interaction: discord.Interaction):
            self.page = max(0, self.page - 1)
            await self.refresh(interaction)
        async def next_callback(interaction: discord.Interaction):
            self.page += 1
            await self.refresh(interaction)
        async def search_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(MarketSearchModal(self))
        async def sell_callback(interaction: discord.Interaction):
            await safe_edit_interaction_message(interaction, content='Choose a category to sell from.', embed=None, view=MarketSellCategoryView(self.owner_id, self.user_name))
        async def back_callback(interaction: discord.Interaction):
            user = db.get_user(self.owner_id)
            await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[make_market_hub_file(self.user_name, user)], view=MarketHubView(self.owner_id, self.user_name))

        prev_btn.callback = prev_callback
        next_btn.callback = next_callback
        search_btn.callback = search_callback
        sell_btn.callback = sell_callback
        back_btn.callback = back_callback

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(search_btn)
        self.add_item(sell_btn)
        self.add_item(trade_btn)
        self.add_item(back_btn)

    async def refresh(self, interaction: discord.Interaction):
        self._rebuild_components()
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_embed(self.owner_id, self.user_name, self.category, self.page, self.search_query), view=self)


class MarketConfirmBuyView(discord.ui.View):
    def __init__(self, parent_view: MarketBuyView, listing_id: int):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.owner_id = parent_view.owner_id
        self.user_name = parent_view.user_name
        self.listing_id = int(listing_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Confirm Purchase', style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        listing = db.get_market_listing(self.listing_id)
        if listing is None or str(listing['status']) != 'active':
            await interaction.response.send_message('That listing is no longer available.', ephemeral=True)
            return
        if int(listing['seller_user_id']) == self.owner_id:
            await interaction.response.send_message('You cannot buy your own listing.', ephemeral=True)
            return
        user = db.get_user(self.owner_id)
        price = int(listing['price'] or 0)
        if int(user['gold'] or 0) < price:
            await interaction.response.send_message(f'You need **{price:,} gold** to buy that item.', ephemeral=True)
            return
        category = str(listing['category'])
        qty = max(1, int(listing['quantity'] or 1))
        if category == 'monsters' and db.owns_monster_variant(
            self.owner_id,
            str(listing['item_ref'] or ''),
            str(listing['monster_attribute'] or ''),
            decode_monster_types(str(listing['monster_types'] or '')),
        ):
            await interaction.response.send_message('You already own this exact monster (same attribute and types), so buying it would give you nothing.', ephemeral=True)
            return
        if category == 'support_cards' and db.get_support_card_instance_by_family(self.owner_id, support_card_family_key(str(listing['item_ref'] or ''))) is not None:
            await interaction.response.send_message('You already own this Support Card. Duplicates only convert into Card Dust, so the purchase was stopped.', ephemeral=True)
            return
        if not db.mark_market_listing_sold(self.listing_id, self.owner_id):
            await interaction.response.send_message('That listing was already taken.', ephemeral=True)
            return
        db.add_gold(self.owner_id, -price)
        db.add_gold(int(listing['seller_user_id']), price)
        if category == 'tickets':
            db.add_tickets(self.owner_id, qty)
        elif category == 'premium_tickets':
            db.add_premium_tickets(self.owner_id, qty)
        elif category == 'monsters':
            monster_id = str(listing['item_ref'] or '')
            monster_types = decode_monster_types(str(listing['monster_types'] or ''))
            if monster_id:
                db.add_monster_to_inventory(self.owner_id, monster_id, monster_types, monster_attribute=str(listing['monster_attribute'] or ''))
        elif category == 'support_cards':
            support_card_id = str(listing['item_ref'] or '')
            for _ in range(qty):
                db.add_support_card_to_inventory(self.owner_id, support_card_id)
        elif category == 'equip_spells':
            equip_spell_id = str(listing['item_ref'] or '')
            for _ in range(qty):
                db.add_equip_spell_to_inventory(self.owner_id, equip_spell_id)
        self.parent_view._rebuild_components()
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_embed(self.owner_id, self.user_name, self.parent_view.category, self.parent_view.page, self.parent_view.search_query), view=self.parent_view)
        await interaction.followup.send(f"Purchased **{listing['item_name']}** for **{price:,} gold**.", ephemeral=True)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.parent_view._rebuild_components()
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_embed(self.owner_id, self.user_name, self.parent_view.category, self.parent_view.page, self.parent_view.search_query), view=self.parent_view)


class MarketSellCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=value) for value, label in MARKET_CATEGORY_LABELS.items()]
        super().__init__(placeholder='Choose a category to sell', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        if parent is None:
            return
        await safe_edit_interaction_message(interaction, content=f"Selling in **{market_category_label(self.values[0])}**. Open the modal below.", embed=None, view=MarketSellView(parent.owner_id, parent.user_name, self.values[0]))


class MarketSellCategoryView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.add_item(MarketSellCategorySelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db.get_user(self.owner_id)
        await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[make_market_hub_file(self.user_name, user)], view=MarketHubView(self.owner_id, self.user_name))


class MarketSellView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, category: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.category = category

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Open Sell Modal', style=discord.ButtonStyle.success)
    async def open_modal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MarketSellModal(self.owner_id, self.user_name, self.category))

    @discord.ui.button(label='Change Category', style=discord.ButtonStyle.primary)
    async def change_category_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content='Choose a category to sell from.', embed=None, view=MarketSellCategoryView(self.owner_id, self.user_name))

    @discord.ui.button(label='Back to Market', style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        buy_view = MarketBuyView(self.owner_id, self.user_name, category=self.category if self.category in MARKET_CATEGORY_ORDER else 'monsters')
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_embed(self.owner_id, self.user_name, buy_view.category, buy_view.page, buy_view.search_query), view=buy_view)


class MarketSellModal(discord.ui.Modal):
    def __init__(self, owner_id: int, user_name: str, category: str):
        super().__init__(title=f"Sell {market_category_label(category)}")
        self.owner_id = owner_id
        self.user_name = user_name
        self.category = category
        self.item_name = discord.ui.TextInput(label='Item name', min_length=1, max_length=100, placeholder='Enter the item name')
        self.quantity = discord.ui.TextInput(label='Quantity', min_length=1, max_length=5, default='1', placeholder='How many do you want to sell?')
        self.price = discord.ui.TextInput(label='Gold price', min_length=1, max_length=12, placeholder='Set your price in gold')
        self.add_item(self.item_name)
        self.add_item(self.quantity)
        if category == 'monsters':
            self.monster_attribute = discord.ui.TextInput(label='Monster attribute', min_length=1, max_length=20, placeholder='FIRE, WATER, EARTH...')
            self.monster_type = discord.ui.TextInput(label='Monster type', min_length=1, max_length=40, placeholder='Dragon, Warrior, Spellcaster...')
            self.add_item(self.monster_attribute)
            self.add_item(self.monster_type)
        else:
            self.monster_attribute = None
            self.monster_type = None
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction):
        raw_name = str(self.item_name.value or '').strip()
        try:
            quantity = max(1, int(str(self.quantity.value or '1').strip()))
            price = int(str(self.price.value or '0').strip())
        except Exception:
            await interaction.response.send_message('Quantity and price must be valid numbers.', ephemeral=True)
            return
        if price <= 0:
            await interaction.response.send_message('Gold price must be greater than 0.', ephemeral=True)
            return

        category = self.category
        item_ref = ''
        monster_attribute = ''
        monster_types: List[str] = []
        display_name = raw_name

        if category == 'tickets':
            user = db.get_user(self.owner_id)
            if int(user['tickets'] or 0) < quantity:
                await interaction.response.send_message('You do not have enough Tickets to sell that many.', ephemeral=True)
                return
            db.add_tickets(self.owner_id, -quantity)
            display_name = 'Ticket'
        elif category == 'premium_tickets':
            user = db.get_user(self.owner_id)
            if int(user['premium_tickets'] or 0) < quantity:
                await interaction.response.send_message('You do not have enough Random Packs to sell that many.', ephemeral=True)
                return
            db.add_premium_tickets(self.owner_id, -quantity)
            display_name = 'Random Pack'
        elif category == 'monsters':
            monster_attribute = str(self.monster_attribute.value or '').strip().upper() if self.monster_attribute is not None else ''
            chosen_type = str(self.monster_type.value or '').strip() if self.monster_type is not None else ''
            if quantity != 1:
                await interaction.response.send_message('Monster listings can only sell one monster at a time.', ephemeral=True)
                return
            inventory_rows = db.user_inventory(self.owner_id)
            normalized_name = normalize_monster_name(raw_name)
            matched = None
            for row in inventory_rows:
                if normalize_monster_name(str(row['monster_name'] or '')) != normalized_name:
                    continue
                if monster_attribute and str(row['attribute'] or '').upper() != monster_attribute:
                    continue
                row_types = decode_monster_types(str(row['monster_types'] or ''))
                if chosen_type and chosen_type.casefold() not in {value.casefold() for value in row_types}:
                    continue
                if db.user_has_monster_in_team(self.owner_id, str(row['instance_id'])):
                    continue
                if db.monster_locked_outside_teams(self.owner_id, str(row['instance_id'])):
                    continue
                matched = row
                break
            if matched is None:
                await interaction.response.send_message('No matching monster was found that is outside your teams, Arena defense team and Extra Decks.', ephemeral=True)
                return
            # Return the equip spell to the player first; it used to stay "equipped" to a
            # monster that no longer existed, locking the spell forever.
            db.unequip_spell_from_monster(self.owner_id, str(matched['instance_id']))
            if not db.delete_inventory_instance(self.owner_id, str(matched['instance_id'])):
                await interaction.response.send_message('That monster could not be listed right now. Try again.', ephemeral=True)
                return
            item_ref = str(matched['monster_id'])
            display_name = str(matched['monster_name'])
            monster_attribute = str(matched['attribute'])
            monster_types = decode_monster_types(str(matched['monster_types'] or ''))
        elif category == 'support_cards':
            rows = db.user_support_cards(self.owner_id)
            normalized_name = raw_name.casefold()
            matches = []
            for row in rows:
                card = get_support_card_def(str(row['support_card_id']))
                if card is not None and card.name.casefold() == normalized_name:
                    matches.append((row, card))
            if len(matches) < quantity:
                await interaction.response.send_message('You do not own enough copies of that Support Card.', ephemeral=True)
                return
            card = matches[0][1]
            for row, _ in matches[:quantity]:
                db.delete_support_card_instance(self.owner_id, str(row['instance_id']))
            item_ref = str(card.support_card_id)
            display_name = card.name
        elif category == 'equip_spells':
            rows = db.user_equip_spells(self.owner_id)
            normalized_name = raw_name.casefold()
            matches = []
            for row in rows:
                spell = get_equip_spell_def(str(row['equip_spell_id']))
                if spell is not None and spell.name.casefold() == normalized_name and not db.user_has_equip_spell_equipped(self.owner_id, str(row['instance_id'])):
                    matches.append((row, spell))
            if len(matches) < quantity:
                await interaction.response.send_message('You do not own enough unequipped copies of that Equip Spell.', ephemeral=True)
                return
            spell = matches[0][1]
            for row, _ in matches[:quantity]:
                db.delete_equip_spell_instance(self.owner_id, str(row['instance_id']))
            item_ref = str(spell.equip_spell_id)
            display_name = spell.name
        else:
            await interaction.response.send_message('That market category is not available.', ephemeral=True)
            return

        listing_id = db.create_market_listing(
            self.owner_id,
            category,
            display_name,
            item_ref=item_ref,
            monster_attribute=monster_attribute,
            monster_types=encode_monster_types(monster_types) if monster_types else '',
            quantity=quantity,
            price=price,
        )
        await interaction.response.send_message(f"Listing **#{listing_id}** created: **{display_name}** for **{price:,} gold**.", ephemeral=True)


def build_market_listings_embed(owner_id: int, user_name: str, category: str, page: int) -> discord.Embed:
    user = db.get_user(owner_id)
    active_rows = db.list_user_market_listings(owner_id, None if category == 'all' else category, status='active')
    total_pages = max(1, math.ceil(len(active_rows) / MARKET_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * MARKET_PAGE_SIZE
    visible = active_rows[start:start + MARKET_PAGE_SIZE]
    category_name = MARKET_OWNER_CATEGORY_LABELS.get(category, 'All Listings')
    embed = discord.Embed(
        title=f"{user_name}'s Market • Listings",
        description=f"Category: **{category_name}** • Page **{page + 1}/{total_pages}**",
        color=discord.Color.green(),
    )
    embed.add_field(name='Gold', value=f"{int(user['gold'] or 0):,}", inline=True)
    embed.add_field(name='Active Listings', value=str(len(active_rows)), inline=True)
    embed.add_field(name='Trade', value='Disabled', inline=True)
    if visible:
        lines = [f"`#{int(row['id'])}` {market_listing_title(row)}" for row in visible]
        embed.add_field(name='Your Listings', value='\n'.join(lines), inline=False)
    else:
        embed.add_field(name='Your Listings', value='You have no active listings in this category.', inline=False)
    embed.set_footer(text='Choose one of your listings below to remove it from the market and receive it back.')
    return embed


def restore_market_listing_to_seller(listing: sqlite3.Row) -> bool:
    owner_id = int(listing['seller_user_id'])
    category = str(listing['category'] or '').strip().lower()
    qty = max(1, int(listing['quantity'] or 1))
    if category == 'tickets':
        db.add_tickets(owner_id, qty)
        return True
    if category == 'premium_tickets':
        db.add_premium_tickets(owner_id, qty)
        return True
    if category == 'monsters':
        monster_id = str(listing['item_ref'] or '').strip()
        if not monster_id:
            return False
        monster_types = decode_monster_types(str(listing['monster_types'] or ''))
        db.add_monster_to_inventory(owner_id, monster_id, monster_types, monster_attribute=str(listing['monster_attribute'] or ''))
        return True
    if category == 'support_cards':
        support_card_id = str(listing['item_ref'] or '').strip()
        if not support_card_id:
            return False
        for _ in range(qty):
            db.add_support_card_to_inventory(owner_id, support_card_id)
        return True
    if category == 'equip_spells':
        equip_spell_id = str(listing['item_ref'] or '').strip()
        if not equip_spell_id:
            return False
        for _ in range(qty):
            db.add_equip_spell_to_inventory(owner_id, equip_spell_id)
        return True
    return False


class MarketOwnerCategorySelect(discord.ui.Select):
    def __init__(self, listings_view: 'MarketListingsView'):
        self.listings_view = listings_view
        options = [
            discord.SelectOption(label=label, value=value, default=(value == listings_view.category))
            for value, label in MARKET_OWNER_CATEGORY_LABELS.items()
        ]
        super().__init__(placeholder='Choose one of your listing categories', min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.listings_view.category = self.values[0]
        self.listings_view.page = 0
        await self.listings_view.refresh(interaction)


class MarketOwnerListingSelect(discord.ui.Select):
    def __init__(self, listings_view: 'MarketListingsView'):
        self.listings_view = listings_view
        rows = listings_view.current_rows()
        if rows:
            options = []
            for row in rows:
                desc = ellipsize_plain_text(f"{market_category_label(str(row['category']))} • {int(row['price'] or 0):,} gold", 100)
                options.append(discord.SelectOption(label=ellipsize_plain_text(str(row['item_name'] or 'Unknown Item'), 100), description=desc, value=str(int(row['id']))))
            super().__init__(placeholder='Choose one of your listings', min_values=1, max_values=1, options=options, row=1)
            self.disabled = False
        else:
            super().__init__(placeholder='No active listings on this page', min_values=1, max_values=1, options=[discord.SelectOption(label='No listings available', value='none')], row=1)
            self.disabled = True

    async def callback(self, interaction: discord.Interaction):
        if not self.values or self.values[0] == 'none':
            await interaction.response.send_message('You have no listings to remove on this page.', ephemeral=True)
            return
        listing_id = int(self.values[0])
        row = db.get_market_listing(listing_id)
        if row is None or str(row['status']) != 'active' or int(row['seller_user_id']) != self.listings_view.owner_id:
            await interaction.response.send_message('That listing is no longer available.', ephemeral=True)
            return
        await safe_edit_interaction_message(interaction, embed=build_market_remove_listing_embed(self.listings_view.owner_id, row), view=MarketConfirmRemoveListingView(self.listings_view, listing_id))


class MarketListingsView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, *, category: str = 'all', page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.category = category if category in MARKET_OWNER_CATEGORY_LABELS else 'all'
        self.page = max(0, int(page))
        self._rebuild_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    def rows(self) -> List[sqlite3.Row]:
        return db.list_user_market_listings(self.owner_id, None if self.category == 'all' else self.category, status='active')

    def current_rows(self) -> List[sqlite3.Row]:
        rows = self.rows()
        total_pages = max(1, math.ceil(len(rows) / MARKET_PAGE_SIZE))
        self.page = max(0, min(self.page, total_pages - 1))
        start = self.page * MARKET_PAGE_SIZE
        return rows[start:start + MARKET_PAGE_SIZE]

    def _rebuild_components(self):
        self.clear_items()
        self.add_item(MarketOwnerCategorySelect(self))
        self.add_item(MarketOwnerListingSelect(self))
        prev_disabled = self.page <= 0
        next_disabled = (self.page + 1) >= max(1, math.ceil(len(self.rows()) / MARKET_PAGE_SIZE))
        prev_btn = discord.ui.Button(label='Prev', style=discord.ButtonStyle.secondary, row=2, disabled=prev_disabled)
        next_btn = discord.ui.Button(label='Next', style=discord.ButtonStyle.secondary, row=2, disabled=next_disabled)
        back_btn = discord.ui.Button(label='Back', style=discord.ButtonStyle.secondary, row=2)

        async def prev_callback(interaction: discord.Interaction):
            self.page = max(0, self.page - 1)
            await self.refresh(interaction)

        async def next_callback(interaction: discord.Interaction):
            self.page += 1
            await self.refresh(interaction)

        async def back_callback(interaction: discord.Interaction):
            user = db.get_user(self.owner_id)
            await safe_edit_interaction_message(interaction, content=None, embed=None, attachments=[make_market_hub_file(self.user_name, user)], view=MarketHubView(self.owner_id, self.user_name))

        prev_btn.callback = prev_callback
        next_btn.callback = next_callback
        back_btn.callback = back_callback
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(back_btn)

    async def refresh(self, interaction: discord.Interaction):
        self._rebuild_components()
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_listings_embed(self.owner_id, self.user_name, self.category, self.page), view=self)


def build_market_remove_listing_embed(owner_id: int, row: sqlite3.Row) -> discord.Embed:
    user = db.get_user(owner_id)
    embed = discord.Embed(title='Remove Listing', description='Remove this listing from the market and return the item to your inventory?', color=discord.Color.orange())
    embed.add_field(name='Item', value=str(row['item_name']), inline=False)
    embed.add_field(name='Category', value=market_category_label(str(row['category'])), inline=True)
    embed.add_field(name='Price', value=f"{int(row['price'] or 0):,} gold", inline=True)
    embed.add_field(name='Your Gold', value=f"{int(user['gold'] or 0):,}", inline=True)
    if str(row['category']) == 'monsters':
        monster_types = decode_monster_types(str(row['monster_types'] or ''))
        extra = []
        if row['monster_attribute']:
            extra.append(str(row['monster_attribute']))
        if monster_types:
            extra.append('/'.join(monster_types))
        if extra:
            embed.add_field(name='Details', value=' • '.join(extra), inline=False)
    else:
        embed.add_field(name='Quantity', value=str(max(1, int(row['quantity'] or 1))), inline=False)
    return embed


class MarketConfirmRemoveListingView(discord.ui.View):
    def __init__(self, parent_view: MarketListingsView, listing_id: int):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.owner_id = parent_view.owner_id
        self.user_name = parent_view.user_name
        self.listing_id = int(listing_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Remove Listing', style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        listing = db.get_market_listing(self.listing_id)
        if listing is None or str(listing['status']) != 'active' or int(listing['seller_user_id']) != self.owner_id:
            await interaction.response.send_message('That listing is no longer available.', ephemeral=True)
            return
        if str(listing['category'] or '') == 'monsters' and db.owns_monster_variant(
            self.owner_id,
            str(listing['item_ref'] or ''),
            str(listing['monster_attribute'] or ''),
            decode_monster_types(str(listing['monster_types'] or '')),
        ):
            await interaction.response.send_message('You own this exact monster again, so it could not be returned - the listing stays up so it is not lost.', ephemeral=True)
            return
        if not db.cancel_market_listing(self.listing_id, self.owner_id):
            await interaction.response.send_message('That listing could not be removed.', ephemeral=True)
            return
        if not restore_market_listing_to_seller(listing):
            await interaction.response.send_message('The listing was removed, but the item could not be restored automatically.', ephemeral=True)
            return
        self.parent_view._rebuild_components()
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_listings_embed(self.owner_id, self.user_name, self.parent_view.category, self.parent_view.page), view=self.parent_view)
        await interaction.followup.send(f"Removed listing **#{self.listing_id}** and returned **{listing['item_name']}** to you.", ephemeral=True)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.parent_view._rebuild_components()
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_listings_embed(self.owner_id, self.user_name, self.parent_view.category, self.parent_view.page), view=self.parent_view)


def build_market_confirm_embed(owner_id: int, row: sqlite3.Row) -> discord.Embed:
    user = db.get_user(owner_id)
    embed = discord.Embed(title='Confirm Purchase', description='Are you sure you want to purchase this item?', color=discord.Color.orange())
    embed.add_field(name='Item', value=str(row['item_name']), inline=False)
    embed.add_field(name='Category', value=market_category_label(str(row['category'])), inline=True)
    embed.add_field(name='Price', value=f"{int(row['price'] or 0):,} gold", inline=True)
    embed.add_field(name='Your Gold', value=f"{int(user['gold'] or 0):,}", inline=True)
    if str(row['category']) == 'monsters':
        monster_types = decode_monster_types(str(row['monster_types'] or ''))
        extra = []
        if row['monster_attribute']:
            extra.append(str(row['monster_attribute']))
        if monster_types:
            extra.append('/'.join(monster_types))
        if extra:
            embed.add_field(name='Details', value=' • '.join(extra), inline=False)
    else:
        embed.add_field(name='Quantity', value=str(max(1, int(row['quantity'] or 1))), inline=False)
    return embed


class MarketHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This market belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Buy', style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MarketBuyView(self.owner_id, self.user_name, category='monsters')
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_embed(self.owner_id, self.user_name, view.category, view.page, view.search_query), attachments=[], view=view)

    @discord.ui.button(label='Sell', style=discord.ButtonStyle.primary)
    async def sell_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content='Choose a category to sell from.', embed=None, attachments=[], view=MarketSellCategoryView(self.owner_id, self.user_name))

    @discord.ui.button(label='Listings', style=discord.ButtonStyle.primary)
    async def listings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MarketListingsView(self.owner_id, self.user_name, category='all')
        await safe_edit_interaction_message(interaction, content=None, embed=build_market_listings_embed(self.owner_id, self.user_name, view.category, view.page), attachments=[], view=view)

    @discord.ui.button(label='Trade', style=discord.ButtonStyle.secondary, disabled=True)
    async def trade_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass
