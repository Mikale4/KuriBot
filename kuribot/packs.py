"""Pack definitions and pack lookup helpers."""

from __future__ import annotations

import re
from typing import Dict, Optional, TYPE_CHECKING, Tuple

from kuribot.config import ATTRIBUTES, MONSTER_TYPES

if TYPE_CHECKING:  # only for type hints; these modules import this one
    from kuribot.database import Database


PACK_ATTRIBUTE_KEYS = {attr.lower(): (f"{attr.title()} Pack", "attribute", attr) for attr in ATTRIBUTES}
PACK_TYPE_KEYS = {re.sub(r"[^a-z0-9]+", "_", monster_type.lower()).strip("_"): (f"{monster_type} Pack", "type", monster_type) for monster_type in MONSTER_TYPES}
PACK_DEFS: Dict[str, Tuple[str, str, str]] = {
    "random_themed": ("Random Pack", "random", ""),
    "boss_themed": ("Extra Deck Themed Pack", "boss", "boss"),
    **PACK_ATTRIBUTE_KEYS,
    **PACK_TYPE_KEYS,
}
PACK_NAME_TO_KEY = {name.casefold(): key for key, (name, _kind, _value) in PACK_DEFS.items()}
PACK_NAME_TO_KEY.update({
    "random themed pack": "random_themed",
    "random themed": "random_themed",
})


def normalize_pack_key(pack_name_or_key: str) -> Optional[str]:
    raw = str(pack_name_or_key or "").strip()
    if not raw:
        return None
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if key in PACK_DEFS:
        return key
    lowered = raw.casefold()
    if lowered in PACK_NAME_TO_KEY:
        return PACK_NAME_TO_KEY[lowered]
    if not lowered.endswith(" pack"):
        with_pack = f"{lowered} pack"
        if with_pack in PACK_NAME_TO_KEY:
            return PACK_NAME_TO_KEY[with_pack]
    return None


def pack_display_name(pack_key: str) -> str:
    return PACK_DEFS.get(str(pack_key), PACK_DEFS["random_themed"])[0]


def pack_theme_kind_value(pack_key: str) -> Tuple[str, str]:
    _name, kind, value = PACK_DEFS.get(str(pack_key), PACK_DEFS["random_themed"])
    return kind, value


def init_pack_system_tables(database: Database):
    database.conn.execute("""
        CREATE TABLE IF NOT EXISTS pack_inventory (
            user_id INTEGER NOT NULL,
            pack_key TEXT NOT NULL,
            pack_name TEXT NOT NULL,
            theme_kind TEXT NOT NULL DEFAULT 'random',
            theme_value TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, pack_key)
        )
    """)
    database.conn.execute("""
        CREATE TABLE IF NOT EXISTS pack_shop_offers (
            user_id INTEGER NOT NULL,
            slot_index INTEGER NOT NULL,
            pack_key TEXT NOT NULL,
            pack_name TEXT NOT NULL,
            theme_kind TEXT NOT NULL DEFAULT 'random',
            theme_value TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL,
            purchased INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, slot_index)
        )
    """)
    database.conn.execute("""
        CREATE TABLE IF NOT EXISTS user_notice_flags (
            user_id INTEGER NOT NULL,
            notice_key TEXT NOT NULL,
            shown_at TEXT NOT NULL,
            PRIMARY KEY (user_id, notice_key)
        )
    """)
    database.conn.execute("UPDATE pack_inventory SET pack_name = ? WHERE pack_key = ?", (PACK_DEFS["random_themed"][0], "random_themed"))
    database.conn.execute("UPDATE pack_shop_offers SET pack_name = ? WHERE pack_key = ?", (PACK_DEFS["random_themed"][0], "random_themed"))
    database.conn.commit()
