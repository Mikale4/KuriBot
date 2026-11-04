"""Support deck encoding, drawing and support card runtime."""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional, Sequence

from kuribot.config import RNG, SUPPORT_DRAW_COUNT
from kuribot.models import SupportCardDef, SupportDeckRuntime
from kuribot.database import db
from kuribot.cards.support_cards import (
    get_current_support_card_def_for_user, get_support_card_def, support_card_effect_label,
    support_card_family_key,
)


def encode_support_cards(card_ids: Sequence[str]) -> str:
    return json.dumps(list(card_ids))


def decode_support_cards(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except Exception:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]


def build_support_deck_runtime(deck_row: Optional[sqlite3.Row], user_id: Optional[int] = None) -> Optional[SupportDeckRuntime]:
    if not deck_row:
        return None
    if user_id is None:
        cards = [get_support_card_def(card_id) for card_id in decode_support_cards(deck_row["cards_json"])]
    else:
        cards = [get_current_support_card_def_for_user(user_id, card_id) for card_id in decode_support_cards(deck_row["cards_json"])]
    cards = [card for card in cards if card is not None]
    shuffled = list(cards)
    RNG.shuffle(shuffled)
    soul_servants = [card for card in shuffled if card.support_card_id == "SOUL_SERVANT"]
    if soul_servants:
        chosen_soul_servant = soul_servants[0]
        shuffled.remove(chosen_soul_servant)
        insert_at = min(len(shuffled), RNG.randrange(SUPPORT_DRAW_COUNT))
        shuffled.insert(insert_at, chosen_soul_servant)
    runtime = SupportDeckRuntime(deck_name=deck_row["name"], shuffled_cards=shuffled)
    runtime.flags.setdefault("battle_round", 1)
    draw_support_cards(runtime)
    return runtime


def draw_support_cards(runtime: SupportDeckRuntime) -> List[SupportCardDef]:
    if not runtime.has_cards_remaining():
        runtime.active_cards = []
        runtime.rounds_remaining = 0
        return []
    start = runtime.draw_index
    end = min(len(runtime.shuffled_cards), start + SUPPORT_DRAW_COUNT)
    runtime.active_cards = runtime.shuffled_cards[start:end]
    runtime.draw_index = end
    runtime.rounds_remaining = 2 if runtime.active_cards else 0
    return list(runtime.active_cards)


def discard_and_redraw_support_cards(runtime: Optional[SupportDeckRuntime]) -> List[SupportCardDef]:
    if runtime is None:
        return []
    runtime.active_cards = []
    runtime.rounds_remaining = 0
    return draw_support_cards(runtime)


def active_support_text(runtime: Optional[SupportDeckRuntime]) -> str:
    if runtime is None or not runtime.active_cards:
        return "No active support cards"
    return "; ".join(f"{card.name} ({support_card_effect_label(card)})" for card in runtime.active_cards)


def build_support_runtime_from_cards(deck_name: str, cards: Sequence[SupportCardDef], *, special_first: bool = False) -> Optional[SupportDeckRuntime]:
    filtered = [card for card in cards if card is not None]
    if not filtered:
        return None
    if special_first:
        special_cards = [card for card in filtered if str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL"]
        special_ids = {id(card) for card in special_cards}
        regular_cards = [card for card in filtered if id(card) not in special_ids]
        RNG.shuffle(special_cards)
        RNG.shuffle(regular_cards)
        shuffled = [*special_cards, *regular_cards]
    else:
        shuffled = list(filtered)
        RNG.shuffle(shuffled)
    runtime = SupportDeckRuntime(deck_name=deck_name, shuffled_cards=shuffled)
    runtime.flags.setdefault("battle_round", 1)
    draw_support_cards(runtime)
    return runtime


def unique_support_card_ids_from_instance_ids(user_id: int, card_instance_ids: Sequence[str]) -> List[str]:
    unique_card_ids: List[str] = []
    seen_families = set()
    for instance_id in card_instance_ids:
        row = db.get_support_card_instance(instance_id, user_id)
        if row is None:
            continue
        support_card_id = str(row["support_card_id"])
        family_key = support_card_family_key(support_card_id)
        if family_key in seen_families:
            continue
        seen_families.add(family_key)
        unique_card_ids.append(support_card_id)
    return unique_card_ids


def user_unique_support_card_count(user_id: int) -> int:
    return len({support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(user_id)})
