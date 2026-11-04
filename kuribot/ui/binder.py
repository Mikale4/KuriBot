"""The binder hub menu."""

from __future__ import annotations

import time
from typing import Optional

import discord
from discord.ext import commands

from kuribot.core import _recent_binder_invocations, BINDER_COMMAND_DEBOUNCE_SECONDS
from kuribot.database import db
from kuribot.render.hubs import render_binder_hub_page
from kuribot.ui.common import safe_edit_interaction_message
from kuribot.ui.collection import CollectionBrowserView, SupportCardBrowserView
from kuribot.ui.teams import TeamBrowserView
from kuribot.ui.decks import DeckBrowserView
from kuribot.ui.extra_deck import ExtraDeckBrowserView


def binder_hub_summary_text(owner_id: int) -> str:
    monster_count = len(db.user_inventory(owner_id))
    card_count = len(db.user_support_cards(owner_id))
    deck_count = len(db.list_support_decks(owner_id))
    extra_deck_count = len(db.list_extra_decks(owner_id)) if hasattr(db, "list_extra_decks") else 0
    spell_count = len(db.user_equip_spells(owner_id))
    team_count = len(db.list_teams(owner_id))
    return (
        f"Choose a collection to browse. You own **{monster_count}** monsters, **{card_count}** support cards, "
        f"**{spell_count}** equip spells, **{deck_count}** support decks, **{extra_deck_count}** Extra Decks, "
        f"and **{team_count}** teams. "
        "You can also jump straight in with **;cards**, **;deck**, or **;ed**."
    )


class BinderHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    def make_file(self) -> discord.File:
        buffer = render_binder_hub_page(self.user_name, self.owner_id)
        return discord.File(buffer, filename="binder.png")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This binder belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Monsters", style=discord.ButtonStyle.primary, row=0)
    async def monsters_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CollectionBrowserView(self.owner_id, self.user_name, "monsters")
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Cards", style=discord.ButtonStyle.primary, row=0)
    async def cards_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SupportCardBrowserView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=view.summary_text(),
            embed=view.selected_embed(),
            attachments=[],
            view=view,
        )

    @discord.ui.button(label="Decks", style=discord.ButtonStyle.primary, row=0)
    async def decks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DeckBrowserView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Extra Decks", style=discord.ButtonStyle.primary, row=1)
    async def extra_decks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ExtraDeckBrowserView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Teams", style=discord.ButtonStyle.primary, row=1)
    async def teams_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TeamBrowserView(self.owner_id, self.user_name)
        view.message = interaction.message
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], view=view)


def make_binder_section_view(owner_id: int, user_name: str, section: Optional[str] = None) -> tuple[str, discord.File, discord.ui.View]:
    normalized = str(section or "").strip().lower()
    if normalized in {"monsters", "monster"}:
        view = CollectionBrowserView(owner_id, user_name, "monsters")
        return view.summary_text(), view.make_file(), view
    if normalized in {"cards", "card"}:
        view = CollectionBrowserView(owner_id, user_name, "cards")
        return view.summary_text(), view.make_file(), view
    if normalized in {"decks", "deck"}:
        view = DeckBrowserView(owner_id, user_name)
        return view.summary_text(), view.make_file(), view
    if normalized in {"extra_decks", "extra_deck", "extradecks", "extradeck", "ed", "extra"}:
        view = ExtraDeckBrowserView(owner_id, user_name)
        return view.summary_text(), view.make_file(), view
    if normalized in {"teams", "team"}:
        view = TeamBrowserView(owner_id, user_name)
        return view.summary_text(), view.make_file(), view
    view = BinderHubView(owner_id, user_name)
    return binder_hub_summary_text(owner_id), view.make_file(), view


async def send_binder_section_message(ctx: commands.Context, section: Optional[str] = None):
    channel_id = int(getattr(getattr(ctx, "channel", None), "id", 0) or 0)
    invocation_key = (int(ctx.author.id), channel_id)
    now = time.monotonic()
    last_invoked_at = _recent_binder_invocations.get(invocation_key)
    if last_invoked_at is not None and (now - last_invoked_at) < BINDER_COMMAND_DEBOUNCE_SECONDS:
        return
    _recent_binder_invocations[invocation_key] = now
    stale_before = now - (BINDER_COMMAND_DEBOUNCE_SECONDS * 4)
    for key, stamped_at in list(_recent_binder_invocations.items()):
        if stamped_at < stale_before:
            _recent_binder_invocations.pop(key, None)

    message = await ctx.send("Opening binder...")
    if message is None:
        # The global debug-channel send guard can intentionally block sends by
        # returning None. Do not try to edit a message that was never created.
        return

    try:
        content, binder_file, view = make_binder_section_view(ctx.author.id, ctx.author.display_name, section)
        await message.edit(content=content, attachments=[binder_file], view=view)
        if hasattr(view, "message"):
            view.message = message
    except Exception:
        try:
            await message.edit(content="The binder hit an error while opening. Please try again.", attachments=[], view=None)
        except Exception:
            # Preserve the original binder error without replacing it with a
            # secondary edit failure.
            pass
        raise
