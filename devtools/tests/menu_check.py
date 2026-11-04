"""Run every player command for a few real players and press the buttons/dropdowns in the
menus that come back, on a sandbox copy of the database.

    python devtools/tests/menu_check.py              (6 players, 2 menu levels deep)
    python devtools/tests/menu_check.py 10 3         (10 players, 3 levels)

Destructive buttons (delete, reset, sell all, disband, leave, kick) are never pressed. Exits with
code 1 if anything crashes or would be rejected by Discord.
"""
from __future__ import annotations

import asyncio
import collections
import random
import sys
import time
import traceback

import discord

import harness
from harness import FakeContext, FakeInteraction, k

PLAYERS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
DEPTH = int(sys.argv[2]) if len(sys.argv) > 2 else 2
PLAYER_COMMANDS = [
    "help", "daily", "quest", "settings", "leaderboard", "rank", "profile", "inventory", "shop", "tutorial",
    "market", "packs", "equip", "binder", "team", "monster", "cards", "deck", "ed", "guild", "glossary",
    "pve", "pvp", "inbox", "ranked", "arena", "lobby", "normal", "tournament", "campaign", "extradeck", "start",
]
NEVER_PRESS = ("delete", "reset", "sell all", "disband", "leave guild", "kick")
k.db.get_battle_speed = lambda uid: "instant"
FAILURES = collections.defaultdict(list)


async def guarded(label: str, coro, timeout: float = 90) -> None:
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        FAILURES[label].append("TIMEOUT: no answer within the time limit")
    except Exception:
        FAILURES[label].append(traceback.format_exc())


def item_label(item) -> str:
    if isinstance(item, discord.ui.Button):
        return f"button:{item.label or item.emoji or item.custom_id}"
    if isinstance(item, discord.ui.Select):
        return f"dropdown:{item.placeholder or item.custom_id}"
    return type(item).__name__


async def press_everything(origin: str, owner_id: int, view, message, depth: int) -> None:
    for item in list(view.children):
        if getattr(item, "disabled", False) or (isinstance(item, discord.ui.Button) and item.url):
            continue
        label = item_label(item)
        if any(word in label.lower() for word in NEVER_PRESS):
            continue
        interaction = FakeInteraction(owner_id, message)
        if isinstance(item, discord.ui.Select):
            if not item.options:
                continue
            item._values = [item.options[0].value]
            try:
                from discord.ui.select import selected_values
                selected_values.set({item.custom_id: item._values})
            except Exception:
                pass
        before = len(harness.SENT_VIEWS)
        where = f"{origin} > {type(view).__name__}.{label}"
        await guarded(where, item.callback(interaction), timeout=60)
        if depth > 1:
            for (_, oid, next_view, next_message) in harness.SENT_VIEWS[before:before + 3]:
                if next_view is not view:
                    await press_everything(where, oid, next_view, next_message, depth - 1)


async def main() -> int:
    random.seed(7)
    rows = k.db.conn.execute(
        "SELECT u.user_id, (SELECT count(*) FROM inventory i WHERE i.user_id = u.user_id) AS monsters FROM users u ORDER BY monsters DESC"
    ).fetchall()
    ids = [int(r["user_id"]) for r in rows]
    if not ids:
        print("No players in the database - nothing to check.")
        return 0
    half = max(1, PLAYERS // 2)
    sample = ids[:half] + random.sample(ids[half:], min(len(ids) - half, max(0, PLAYERS - half)))
    started = time.time()
    for uid in sample:
        for name in PLAYER_COMMANDS:
            command = k.bot.get_command(name)
            if command is None:
                FAILURES[f";{name}"].append("command is not registered")
                continue
            before = len(harness.SENT_VIEWS)
            await guarded(f";{name}", command.callback(FakeContext(uid, command)))
            for (_, oid, view, message) in harness.SENT_VIEWS[before:before + 2]:
                await press_everything(f";{name}", oid, view, message, DEPTH)
    print(f"{len(sample)} players x {len(PLAYER_COMMANDS)} commands: {len(harness.SENT_VIEWS)} menu screens checked in {time.time() - started:.0f}s")
    for where, problem in collections.Counter(harness.LIMIT_PROBLEMS).most_common(25):
        print(f"  Discord would reject [{where}]: {problem}")
    failures = harness.summarize_failures(FAILURES)
    bad = failures + len(harness.LIMIT_PROBLEMS)
    print("\nAll menus passed." if not bad else f"\n{bad} problem(s) found.")
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
