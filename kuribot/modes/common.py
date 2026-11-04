"""Shared battle-mode guards."""

from __future__ import annotations

from typing import Tuple

import discord

from kuribot.ui.common import safe_send_interaction_message


# One battle per player per mode at a time. Several fixes (guild boss attempts,
# ranked rating, tournament results) were exploitable by starting the same battle twice -
# double-clicking "Battle Again" or opening two panels.
_BATTLES_IN_PROGRESS: set[Tuple[int, str]] = set()


def single_battle_per_player(mode: str):
    """Decorator for battle start functions: refuse a second battle in `mode` while the
    player's first one is still running."""
    def decorate(runner):
        async def guarded(interaction: discord.Interaction, owner_id: int, *args, **kwargs):
            key = (int(owner_id), mode)
            if key in _BATTLES_IN_PROGRESS:
                try:
                    await safe_send_interaction_message(interaction, content="You already have a battle in this mode in progress - finish it first.", ephemeral=True)
                except Exception:
                    pass
                return None
            _BATTLES_IN_PROGRESS.add(key)
            try:
                return await runner(interaction, owner_id, *args, **kwargs)
            finally:
                _BATTLES_IN_PROGRESS.discard(key)

        guarded.__name__ = getattr(runner, "__name__", "battle_runner")
        guarded.__doc__ = getattr(runner, "__doc__", None)
        return guarded
    return decorate
