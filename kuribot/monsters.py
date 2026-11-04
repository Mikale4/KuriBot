"""Monster generation from card art, categories (boss, signature, Extra Deck) and stats."""

from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from kuribot.config import (
    ATTRIBUTES, BASE_ACCURACY, BASE_CRIT_DAMAGE, BASE_CRIT_RATE, BASE_DODGE,
    BOSS_MONSTER_EXACT_NAMES, BOSS_MONSTER_NAME_PATTERNS, BOSS_MONSTER_STAT_BOOST, CARD_EXTENSIONS,
    EXTRA_DECK_SIZE, FEATURED_MONSTER_DUAL_TYPE_CHANCE, MONSTER_TYPES, RNG,
    SIGNATURE_MONSTER_EXACT_NAMES, SIGNATURE_MONSTER_STAT_BOOST, SINGLE_TYPE_CHANCE,
    SPECIAL_MONSTER_COMPENSATION_PREMIUM_TICKETS, SPECIAL_MONSTER_COMPENSATION_TICKETS,
    SPECIAL_MONSTER_REMOVAL_BODY_MARKER,
)
from kuribot.models import MonsterDef
from kuribot.core import bot
from kuribot.database import db


def stable_seed_for_name(name: str) -> int:
    total = 0
    for i, ch in enumerate(name.lower(), 1):
        total += ord(ch) * i
    return total


def derive_element(name: str) -> str:
    return ATTRIBUTES[stable_seed_for_name(name) % len(ATTRIBUTES)]


def extract_name_and_attribute_from_stem(stem: str) -> Tuple[str, str]:
    cleaned = stem.replace("_", " ").strip()
    upper = cleaned.upper()

    for attribute in ATTRIBUTES:
        bracket_suffixes = [f"[{attribute}]", f"({attribute})", f"- {attribute}", f"| {attribute}", f": {attribute}"]
        for suffix in bracket_suffixes:
            if upper.endswith(suffix):
                base_name = cleaned[: -len(suffix)].strip(" -_|:()[]")
                if base_name:
                    return base_name, attribute

    parts = cleaned.split()
    if parts and parts[-1].upper() in ATTRIBUTES:
        base_name = " ".join(parts[:-1]).strip(" -_|:()[]")
        if base_name:
            return base_name, parts[-1].upper()

    return cleaned, derive_element(cleaned)


def make_monster_variant_id(base_name: str, attribute: str) -> str:
    return f"{base_name} [{attribute}]"


TYPE_STAT_BONUSES: Dict[str, Dict[str, float]] = {
    # HP-focused / frontline
    "Aqua": {"hp": 0.22, "atk": -0.06},
    "Beast": {"hp": 0.20, "spd": 0.08},
    "Dinosaur": {"hp": 0.18, "atk": 0.10, "spd": -0.06},
    "Plant": {"hp": 0.18, "defense": 0.12, "spd": -0.06},
    "Reptile": {"hp": 0.16, "spd": 0.12},
    "Sea Beast": {"hp": 0.22, "defense": 0.08, "spd": -0.08},
    "Sea Serpent": {"hp": 0.18, "spd": 0.10},
    "Wyrm": {"hp": 0.16, "atk": 0.12},
    "Immortal": {"hp": 0.24, "defense": 0.08, "spd": -0.10},
    "Divine-Beast": {"hp": 0.18, "atk": 0.12, "defense": 0.12, "spd": -0.10},

    # ATK-focused / damage dealers
    "Beast-Warrior": {"atk": 0.22, "spd": 0.08, "defense": -0.08},
    "Black Magic": {"atk": 0.24, "spd": 0.06, "hp": -0.08},
    "Celestial Warrior": {"atk": 0.20, "defense": 0.10},
    "Dragon": {"atk": 0.24, "hp": 0.08, "spd": -0.08},
    "Dragon Magic": {"atk": 0.20, "spd": 0.12, "defense": -0.08},
    "Fiend": {"atk": 0.22, "spd": 0.10, "hp": -0.06},
    "High Dragon": {"atk": 0.18, "hp": 0.12, "defense": 0.08},
    "Magical Knight": {"atk": 0.18, "defense": 0.14},
    "Pyro": {"atk": 0.26, "hp": -0.10},
    "Warrior": {"atk": 0.18, "spd": 0.14},

    # DEF-focused / tanks
    "Creator God": {"defense": 0.26, "hp": 0.14, "spd": -0.12},
    "Cyborg": {"defense": 0.22, "spd": 0.08},
    "Fish": {"defense": 0.20, "hp": 0.12},
    "Galaxy": {"defense": 0.18, "atk": 0.14},
    "Machine": {"defense": 0.24, "hp": 0.10, "spd": -0.10},
    "Rock": {"defense": 0.28, "hp": 0.16, "spd": -0.14},
    "Thunder": {"defense": 0.18, "spd": 0.14},
    "White Magic": {"defense": 0.20, "hp": 0.12, "spd": 0.06},
    "Yokai": {"defense": 0.18, "atk": 0.14, "hp": -0.06},

    # SPD-focused / tempo units
    "Charisma": {"spd": 0.24, "atk": 0.10, "hp": -0.10},
    "Cyberse": {"spd": 0.28, "atk": 0.12, "hp": -0.12},
    "Fairy": {"spd": 0.20, "defense": 0.10},
    "Human": {"spd": 0.18, "atk": 0.12},
    "Illusion": {"spd": 0.24, "atk": 0.08, "defense": -0.08},
    "Illusion Magic": {"spd": 0.22, "atk": 0.12, "hp": -0.08},
    "Insect": {"spd": 0.22, "atk": 0.10, "hp": -0.08},
    "Omega Psychic": {"spd": 0.20, "atk": 0.14},
    "Psychic": {"spd": 0.24, "defense": 0.08, "hp": -0.08},
    "Spellcaster": {"spd": 0.18, "atk": 0.16},
    "Winged Beast": {"spd": 0.26, "atk": 0.10, "defense": -0.10},
}

ATTRIBUTE_STAT_BONUSES: Dict[str, Dict[str, float]] = {
    "FIRE": {"atk": 0.16, "spd": 0.05, "crit_damage": 0.25, "defense": -0.08},
    "WATER": {"hp": 0.10, "defense": 0.10, "dodge": 0.06},
    "EARTH": {"hp": 0.18, "defense": 0.12, "spd": -0.10},
    "WIND": {"spd": 0.18, "atk": 0.06, "dodge": 0.06, "hp": -0.08},
    "LIGHT": {"hp": 0.14, "defense": 0.08, "spd": 0.06, "crit_rate": 0.05},
    "DARK": {"atk": 0.12, "spd": 0.08, "crit_rate": 0.10, "hp": -0.08},
    "DIVINE": {"hp": 0.10, "atk": 0.10, "defense": 0.10, "spd": 0.10},
}


def apply_special_monster_raw_stat_bonus(hp: int, atk: int, defense: int, spd: int, category: Optional[str]) -> Tuple[int, int, int, int]:
    bonus_pct = 0.0
    if category == "boss":
        bonus_pct = BOSS_MONSTER_STAT_BOOST
    elif category == "signature":
        bonus_pct = SIGNATURE_MONSTER_STAT_BOOST
    if bonus_pct <= 0:
        return max(1, int(hp)), max(1, int(atk)), max(1, int(defense)), max(1, int(spd))
    multiplier = 1.0 + float(bonus_pct)
    return (
        max(1, int(round(hp * multiplier))),
        max(1, int(round(atk * multiplier))),
        max(1, int(round(defense * multiplier))),
        max(1, int(round(spd * multiplier))),
    )


def generate_stats(name: str) -> Tuple[int, int, int, int]:
    rng = random.Random(stable_seed_for_name(name))
    base = 100 + rng.randint(0, 40)
    hp = 200 + base + rng.randint(0, 140)
    atk = 55 + rng.randint(0, 55)
    defense = 35 + rng.randint(0, 55)
    spd = 35 + rng.randint(0, 55)
    return apply_special_monster_raw_stat_bonus(hp, atk, defense, spd, monster_category_for_name(name))


def _normalized_monster_name_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def is_signature_monster_name_key(normalized_name: str) -> bool:
    return str(normalized_name or "") in SIGNATURE_MONSTER_EXACT_NAMES


def is_boss_monster_name_key(normalized_name: str) -> bool:
    normalized = str(normalized_name or "")
    return normalized in BOSS_MONSTER_EXACT_NAMES or any(pattern.search(normalized) for pattern in BOSS_MONSTER_NAME_PATTERNS)


def monster_category_for_name(name: str) -> Optional[str]:
    normalized = _normalized_monster_name_key(name)
    if not normalized:
        return None
    # Signature monsters are now treated as Extra Deck Monsters everywhere that cares
    # about combat/category rules, including the +50% base stat bonus.
    if is_boss_monster_name_key(normalized) or is_signature_monster_name_key(normalized):
        return "boss"
    return None


def monster_category_for_row(row: sqlite3.Row) -> Optional[str]:
    try:
        keys = set(row.keys())
    except Exception:
        keys = set()
    name_value = None
    if "name" in keys:
        name_value = row["name"]
    elif "monster_name" in keys:
        name_value = row["monster_name"]
    elif "monster_id" in keys:
        name_value = row["monster_id"]
    return monster_category_for_name(str(name_value or ""))


def is_boss_monster_name(name: str) -> bool:
    return monster_category_for_name(name) == "boss"


def is_signature_monster_name(name: str) -> bool:
    return is_signature_monster_name_key(_normalized_monster_name_key(name))


def is_boss_monster_row(row: sqlite3.Row) -> bool:
    return monster_category_for_row(row) == "boss"


def is_signature_monster_row(row: sqlite3.Row) -> bool:
    try:
        keys = set(row.keys())
    except Exception:
        keys = set()
    name_value = None
    if "name" in keys:
        name_value = row["name"]
    elif "monster_name" in keys:
        name_value = row["monster_name"]
    elif "monster_id" in keys:
        name_value = row["monster_id"]
    return is_signature_monster_name(str(name_value or ""))


def random_monster_attribute_for_row(monster_row: sqlite3.Row) -> str:
    base_attribute = str(monster_row["attribute"] or "").strip().upper()
    if is_boss_monster_row(monster_row):
        return RNG.choice(ATTRIBUTES)
    if base_attribute in ATTRIBUTES:
        return base_attribute
    return RNG.choice(ATTRIBUTES)


def roll_monster_types_for_row(monster_row: sqlite3.Row, *, featured_monster_ids: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    first_type = RNG.choice(MONSTER_TYPES)
    second_candidates = [monster_type for monster_type in MONSTER_TYPES if monster_type != first_type]
    second_type = RNG.choice(second_candidates) if second_candidates else first_type
    ordered = sorted((first_type, second_type), key=MONSTER_TYPES.index)

    if is_signature_monster_row(monster_row):
        return tuple(ordered)

    featured_id_set = {str(monster_id) for monster_id in (featured_monster_ids or ())}
    if featured_id_set and str(monster_row["monster_id"]) in featured_id_set:
        if RNG.random() < FEATURED_MONSTER_DUAL_TYPE_CHANCE:
            return tuple(ordered)
        return (first_type,)

    if RNG.random() < SINGLE_TYPE_CHANCE:
        return (first_type,)
    return tuple(ordered)


def ensure_dual_monster_types(monster_types: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    """Return exactly two distinct valid monster types, preserving any supplied type first."""
    cleaned: List[str] = []
    for monster_type in monster_types or ():
        value = str(monster_type or "").strip()
        if value in MONSTER_TYPES and value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= 2:
            break
    while len(cleaned) < 2:
        candidates = [monster_type for monster_type in MONSTER_TYPES if monster_type not in cleaned]
        if not candidates:
            break
        cleaned.append(RNG.choice(candidates))
    return tuple(cleaned[:2])


def build_special_monster_compensation_mail_body(user_name: str, removed_count: int) -> str:
    monster_label = "monster" if removed_count == 1 else "monsters"
    return (
        f"Hello {user_name},\n\n"
        f"As part of the Extra Deck Monster update, {removed_count} {monster_label} that matched the new protected categories were removed from your collection.\n\n"
        f"To compensate you, this message includes {SPECIAL_MONSTER_COMPENSATION_TICKETS} Tickets and {SPECIAL_MONSTER_COMPENSATION_PREMIUM_TICKETS} Random Packs as an attachment.\n\n"
        f"{SPECIAL_MONSTER_REMOVAL_BODY_MARKER}"
    )


async def backfill_special_monster_compensation_mail_usernames() -> int:
    rows = db.conn.execute(
        """
        SELECT id, user_id, body
        FROM mailbox_messages
        WHERE instr(body, ?) > 0
        ORDER BY id ASC
        """,
        (SPECIAL_MONSTER_REMOVAL_BODY_MARKER,),
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for row in rows:
        user_id = int(row["user_id"])
        body = str(row["body"] or "")

        removed_count = 1
        match = re.search(r"(?:\*\*)?(\d+)\s+monster(?:s)?(?:\*\*)?", body, flags=re.IGNORECASE)
        if match:
            try:
                removed_count = max(1, int(match.group(1)))
            except Exception:
                removed_count = 1

        username = str(user_id)
        discord_user = bot.get_user(user_id)
        if discord_user is None:
            try:
                discord_user = await bot.fetch_user(user_id)
            except Exception:
                discord_user = None
        if discord_user is not None:
            username = str(getattr(discord_user, "name", "") or getattr(discord_user, "display_name", "") or user_id)

        new_body = build_special_monster_compensation_mail_body(username, removed_count)
        if new_body == body:
            continue

        db.conn.execute(
            "UPDATE mailbox_messages SET body = ? WHERE id = ?",
            (new_body, int(row["id"])),
        )
        updated += 1

    if updated:
        db.conn.commit()
    return updated


def apply_stat_bonuses(hp: int, atk: int, defense: int, spd: int, monster_types: Sequence[str] = (), attribute: Optional[str] = None, monster_category: Optional[str] = None) -> Tuple[int, int, int, int]:
    hp_mult = 1.0
    atk_mult = 1.0
    def_mult = 1.0
    spd_mult = 1.0

    for monster_type in monster_types:
        bonus = TYPE_STAT_BONUSES.get(monster_type, {})
        hp_mult += float(bonus.get("hp", 0.0))
        atk_mult += float(bonus.get("atk", 0.0))
        def_mult += float(bonus.get("defense", 0.0))
        spd_mult += float(bonus.get("spd", 0.0))

    if attribute:
        bonus = ATTRIBUTE_STAT_BONUSES.get(str(attribute).upper(), {})
        hp_mult += float(bonus.get("hp", 0.0))
        atk_mult += float(bonus.get("atk", 0.0))
        def_mult += float(bonus.get("defense", 0.0))
        spd_mult += float(bonus.get("spd", 0.0))

    total_hp = max(1, int(round(hp * hp_mult)))
    total_atk = max(1, int(round(atk * atk_mult)))
    total_defense = max(1, int(round(defense * def_mult)))
    total_spd = max(1, int(round(spd * spd_mult)))
    return total_hp, total_atk, total_defense, total_spd


def resolve_secondary_stat_bonuses(attribute: Optional[str] = None) -> Tuple[float, float, float, float]:
    crit_rate = BASE_CRIT_RATE
    crit_damage = BASE_CRIT_DAMAGE
    accuracy = BASE_ACCURACY
    dodge = BASE_DODGE
    if attribute:
        bonus = ATTRIBUTE_STAT_BONUSES.get(str(attribute).upper(), {})
        crit_rate += float(bonus.get("crit_rate", 0.0))
        crit_damage += float(bonus.get("crit_damage", 0.0))
        accuracy += float(bonus.get("accuracy", 0.0))
        dodge += float(bonus.get("dodge", 0.0))
    return (
        max(0.0, crit_rate),
        max(0.0, crit_damage),
        max(0.0, accuracy),
        max(0.0, dodge),
    )


def resolve_row_combat_stats(row: sqlite3.Row, monster_types: Optional[Sequence[str]] = None) -> Tuple[int, int, int, int]:
    resolved_types = tuple(monster_types) if monster_types is not None else decode_monster_types(row["monster_types"]) if "monster_types" in row.keys() else tuple()
    hp = int(row["hp"])
    atk = int(row["atk"])
    defense = int(row["def"])
    spd = int(row["spd"])
    return apply_stat_bonuses(hp, atk, defense, spd, resolved_types, row["attribute"], monster_category_for_row(row))


def scan_cards_directory(cards_dir: Path) -> int:
    if not cards_dir.exists():
        print(f"[WARN] CARDS_DIR does not exist: {cards_dir}")
        return 0

    # Build every row first, then write them in ONE transaction. The old version committed
    # once per card (9,000+ commits), which froze the bot for a long time on every
    # reconnect - long enough on a phone for Discord to drop the connection.
    rows = []
    for path in cards_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in CARD_EXTENSIONS:
            continue
        name, attribute = extract_name_and_attribute_from_stem(path.stem)
        hp, atk, defense, spd = generate_stats(name)
        monster_id = make_monster_variant_id(name, attribute)
        rows.append((monster_id, name, attribute, hp, atk, defense, spd, str(path)))
    if not rows:
        return 0
    with db.lock:
        db.conn.executemany(
            """
            INSERT INTO monster_defs(monster_id, name, attribute, hp, atk, def, spd, image_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(monster_id) DO UPDATE SET
                name = excluded.name,
                attribute = excluded.attribute,
                hp = excluded.hp,
                atk = excluded.atk,
                def = excluded.def,
                spd = excluded.spd,
                image_path = excluded.image_path
            WHERE monster_defs.name IS NOT excluded.name
               OR monster_defs.attribute IS NOT excluded.attribute
               OR monster_defs.hp IS NOT excluded.hp
               OR monster_defs.atk IS NOT excluded.atk
               OR monster_defs.def IS NOT excluded.def
               OR monster_defs.spd IS NOT excluded.spd
               OR monster_defs.image_path IS NOT excluded.image_path
            """,
            rows,
        )
        db.conn.commit()
    return len(rows)


def sync_boss_monster_base_stats() -> int:
    """Refresh stored base stats for all Extra Deck Monsters, including former Extra Deck Monsters.

    Inventory rows reference monster_defs for base stats, so updating monster_defs also
    updates monsters that already exist in user collections.
    """
    rows = db.conn.execute(
        "SELECT monster_id, name, hp, atk, def, spd FROM monster_defs"
    ).fetchall()
    updated = 0
    for row in rows:
        if monster_category_for_name(str(row["name"] or "")) != "boss":
            continue
        hp, atk, defense, spd = generate_stats(str(row["name"] or ""))
        if (int(row["hp"]), int(row["atk"]), int(row["def"]), int(row["spd"])) == (hp, atk, defense, spd):
            continue
        db.conn.execute(
            "UPDATE monster_defs SET hp = ?, atk = ?, def = ?, spd = ? WHERE monster_id = ?",
            (hp, atk, defense, spd, str(row["monster_id"])),
        )
        updated += 1
    if updated:
        db.conn.commit()
    return updated


def team_slots_from_csv(csv: str) -> List[str]:
    return [x for x in (part.strip() for part in csv.split(",")) if x]


def encode_monster_types(monster_types: Sequence[str]) -> str:
    return "|".join(monster_type for monster_type in monster_types if monster_type in MONSTER_TYPES)


def decode_monster_types(raw: Optional[str]) -> Tuple[str, ...]:
    if not raw:
        return tuple()
    return tuple(part for part in (chunk.strip() for chunk in raw.split("|")) if part)


def format_monster_types(monster_types: Sequence[str]) -> str:
    return " / ".join(monster_types) if monster_types else "Unassigned"


def roll_monster_types() -> Tuple[str, ...]:
    first_type = RNG.choice(MONSTER_TYPES)
    if RNG.random() < SINGLE_TYPE_CHANCE:
        return (first_type,)
    second_candidates = [monster_type for monster_type in MONSTER_TYPES if monster_type != first_type]
    second_type = RNG.choice(second_candidates) if second_candidates else first_type
    ordered = sorted((first_type, second_type), key=MONSTER_TYPES.index)
    return tuple(ordered)


def display_monster_label_from_id(instance_id: str) -> str:
    row = db.get_inventory_instance(instance_id)
    if row:
        return f"{row['monster_name']} ({row['attribute']}) [{format_monster_types(decode_monster_types(row['monster_types']))}]"
    row = db.get_monster_by_id(instance_id)
    if row:
        return f"{row['name']} ({row['attribute']})"
    return instance_id


def normalize_monster_name(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").replace("_", " ").strip())
    return normalized.casefold()


def find_monster_by_name_and_attribute(monster_name: str, attribute: str, pool: Optional[Sequence[sqlite3.Row]] = None) -> Optional[sqlite3.Row]:
    raw_name = str(monster_name or "").replace("_", " ").strip()
    target_name = normalize_monster_name(raw_name)
    target_attribute = str(attribute or "").strip().upper()
    if not target_name or not target_attribute:
        return None

    search_pool = list(pool) if pool is not None else db.all_monsters()
    exact_name_match: Optional[sqlite3.Row] = None
    for row in search_pool:
        row_name = str(row["name"] or "").strip()
        if normalize_monster_name(row_name) != target_name:
            continue
        if exact_name_match is None:
            exact_name_match = row
        if str(row["attribute"] or "").strip().upper() == target_attribute:
            return row

    if exact_name_match is None:
        return None
    if pool is not None:
        return exact_name_match

    display_name = str(exact_name_match["name"] or raw_name).strip() or raw_name
    monster_id = make_monster_variant_id(display_name, target_attribute)
    existing_variant = db.get_monster_by_id(monster_id)
    if existing_variant is not None:
        return existing_variant

    hp, atk, defense, spd = generate_stats(display_name)
    db.upsert_monster_def(
        MonsterDef(
            monster_id=monster_id,
            name=display_name,
            attribute=target_attribute,
            hp=hp,
            atk=atk,
            defense=defense,
            spd=spd,
            image_path=str(exact_name_match["image_path"] or ""),
        )
    )
    return db.get_monster_by_id(monster_id)


def resolve_monster_type_name(monster_type: str) -> Optional[str]:
    wanted = str(monster_type or "").strip().lower()
    if not wanted:
        return None
    for entry in MONSTER_TYPES:
        if entry.lower() == wanted:
            return entry
    return None


def is_extra_deck_monster_name(name: str) -> bool:
    return monster_category_for_name(name) == "boss" or is_signature_monster_name(name)


def is_extra_deck_monster_row(row: sqlite3.Row) -> bool:
    return monster_category_for_row(row) == "boss" or is_signature_monster_row(row)


def extra_deck_effect_key(name: str) -> str:
    key = _normalized_monster_name_key(name)
    replacements = {
        "the winged dragon of ra": "ra",
        "slifer the sky dragon": "slifer",
        "obelisk the tormentor": "obelisk",
        "blue-eyes white dragon": "blue_eyes",
        "blue-eyes ultimate dragon": "blue_eyes_ultimate",
        "red-eyes black dragon": "red_eyes",
        "red-eyes darkness dragon": "red_eyes",
        "dark magician": "dark_magician",
        "exodia the forbidden one": "exodia",
        "stardust dragon": "stardust",
        "black rose dragon": "black_rose",
        "red dragon archfiend": "red_dragon_archfiend",
        "scarlight red dragon archfiend": "red_dragon_archfiend",
        "ancient fairy dragon": "ancient_fairy",
        "black-winged dragon": "black_winged",
        "power tool dragon": "power_tool",
        "clear wing synchro dragon": "clear_wing",
        "dark rebellion xyz dragon": "dark_rebellion",
        "odd-eyes pendulum dragon": "odd_eyes",
        "starving venom fusion dragon": "starving_venom",
        "firewall dragon": "firewall",
        "decode talker": "decode",
        "borreload dragon": "borreload",
        "topologic gumblar dragon": "topologic",
        "the arrival cyberse @ignister": "arrival",
        "number 39 utopia": "utopia",
        "number 32 shark drake": "shark_drake",
        "galaxy-eyes photon dragon": "galaxy_eyes",
        "number 100 numeron dragon": "numeron",
        "number 9 dyson sphere": "dyson",
        "number 103 ragnazero": "ragnazero",
        "chronomaly crystal chrononaut": "chrononaut",
        "superdreadnought rail cannon gustav max": "gustav",
        "heroic champion - excalibur": "excalibur",
        "lunalight leo dancer": "leo_dancer",
        "lunalight cat dancer": "leo_dancer",
        "frightfur tiger": "frightfur_tiger",
        "raidraptor - rise falcon": "rise_falcon",
        "bloom diva the melodious choir": "bloom_diva",
        "ddd doom king armageddon": "doom_armageddon",
        "sun dragon inti": "sun_dragon",
        "moon dragon quilla": "moon_dragon",
        "rainbow dragon": "rainbow_dragon",
        "armed dragon lv10": "armed_dragon",
        "vwxyz-dragon catapult cannon": "vwxyz",
        "jinzo": "jinzo",
        "relinquished": "relinquished",
        "dark necrofear": "dark_necrofear",
        "destiny hero plasma": "plasma",
        "hamon, lord of striking thunder": "hamon",
        "uria, lord of searing flames": "uria",
        "holactie the creator of light": "holactie",
        "holactie the creator of": "holactie",
        "the wicked avatar": "wicked_avatar",
        "the wicked eraser": "wicked_eraser",
        "the wicked dreadroot": "wicked_dreadroot",
    }
    if key in replacements:
        return replacements[key]
    if "raviel" in key:
        return "raviel"
    if "armityle" in key:
        return "armityle"
    if "z-arc" in key:
        return "zarc"
    if "numerounius" in key:
        return "numeron"
    if "cyber end dragon" in key:
        return "cyber_end"
    if "cyber dragon" in key:
        return "cyber_end"
    return "generic"


def extra_deck_effect_description(name: str) -> str:
    effect = extra_deck_effect_key(name)
    descriptions = {
        "dark_magician": "Dark Magic Circle: SILENCE one enemy on summon and punish buffed enemies.",
        "blue_eyes": "Burst Stream: first attack splashes all enemies and can pierce protection.",
        "red_eyes": "Inferno Legacy: grows when allies fall and can BURN targets.",
        "exodia": "Obliterate: late-battle finisher that deals massive damage to all enemies.",
        "blue_eyes_ultimate": "Ultimate Burst: first turn becomes a triple-hit assault.",
        "cyber_end": "Power Bond Overload: huge first attack, then recoil.",
        "stardust": "Shooting Star Protection: shields the team when entering battle.",
        "red_dragon_archfiend": "Absolute Powerforce: bonus damage to disabled enemies.",
        "black_rose": "Black Rose Gale: damages all monsters when summoned.",
        "ancient_fairy": "Sacred Garden: heals allies and grants protection.",
        "black_winged": "Black Feather Counter: converts damage into ATK.",
        "power_tool": "Toolbox Repair: grants temporary ATK/DEF/SPD to allies.",
        "clear_wing": "Clear Wing Guard: reduces enemy speed and counters fast enemies.",
        "dark_rebellion": "Rebellion Drain: steals ATK from the strongest enemy.",
        "odd_eyes": "Pendulum Swing: enters quickly and restores ally momentum.",
        "starving_venom": "Predator Copy: copies enemy stats and poisons the killer.",
        "firewall": "Firewall Bounce: cancels one incoming attack after summon.",
        "decode": "Decode Boost: gains ATK for each living ally.",
        "borreload": "Load Control: strips enemy buffs and lowers ATK/SPD.",
        "topologic": "Network Collapse: AoE damage plus SILENCE/FEAR.",
        "arrival": "Unaffected Arrival: immune to status and support/equip effects.",
        "utopia": "Utopia Guard: can negate a lethal hit once.",
        "shark_drake": "Shark Pursuit: hunts the weakest enemy and can chain attacks.",
        "galaxy_eyes": "Photon Vanish: dodges the first incoming attack and returns damage.",
        "numeron": "Numeron Finish: gains ATK from defeated monsters.",
        "dyson": "Orbital Wall: damage reduction and reflection.",
        "ragnazero": "Zero Judgment: punishes and strips buffed enemies.",
        "chrononaut": "Chronomaly Reflection: reflects part of the next hit.",
        "gustav": "Rail Cannon: defense-piercing fixed damage.",
        "excalibur": "Heroic Blade: doubles ATK for one attack.",
        "leo_dancer": "Moonlit Combo: multi-hit assault that ignores dodge once.",
        "frightfur_tiger": "Toybox Maul: random multi-hit bonus damage.",
        "rise_falcon": "Rebellion Raid: stronger while outnumbered.",
        "bloom_diva": "Melodious Barrier: damage reduction and reflection.",
        "doom_armageddon": "Contract with Doom: revives an ally at 1 HP, then bleeds HP.",
        "sun_dragon": "Solar Rebirth: punishes its destroyer.",
        "moon_dragon": "Lunar Recovery: heals allies when enemies take damage.",
        "rainbow_dragon": "Rainbow Overdrive: cleanses and gains stats from allies.",
        "armed_dragon": "Level Cannon: AoE cannon damage.",
        "vwxyz": "Catapult Banish: defense-piercing damage and speed control.",
        "jinzo": "Trap Shutdown: disables enemy support cards while active.",
        "relinquished": "Absorb: drains HP/ATK from a target.",
        "dark_necrofear": "Spirit Possession: CHARM/FEAR on summon.",
        "plasma": "Plasma Lock: suppresses an enemy special effect briefly.",
        "ra": "Phoenix Tribute: sacrifices HP for massive damage.",
        "slifer": "Thunderforce: damages and weakens enemies as they act.",
        "obelisk": "Fist of Fate: cannot be stunned and hits all enemies first.",
        "hamon": "Thunder Fortress: gains DEF and retaliates.",
        "uria": "Searing Flames: stacks BURN and grows from damage over time.",
        "raviel": "Phantasm Token: gains ATK whenever a monster is defeated.",
        "armityle": "Chaos Phantasm: huge attack every other turn.",
        "holactie": "Creator's Victory: PvE divine finisher / full-field reset.",
        "wicked_avatar": "Shadow Above All: ATK rises above the highest enemy ATK.",
        "wicked_eraser": "Erase the Field: heavy damage on defeat.",
        "wicked_dreadroot": "Dread Aura: reduces enemy ATK/SPD.",
        "zarc": "Supreme King Collapse: AoE damage and Extra Deck lockout.",
        "generic": "Extra Deck Surge: enters through an unused zone with a personal stat burst.",
    }
    return descriptions.get(effect, descriptions["generic"])


def extra_deck_summon_requirements_text(name: str) -> str:
    effect = extra_deck_effect_key(name)
    shared = "Equip an Extra Deck to the team. Only one Extra Deck Monster can be active at a time."
    defeated_slot = "Usually replaces a defeated ally."
    pressure_slot = "Can replace the lowest-HP living ally if no defeated ally is available."
    specific_requirements = {
        "utopia": "Can also trigger when any living ally is at 30% HP or lower.",
        "stardust": "Can also trigger when any living ally is at 30% HP or lower.",
        "firewall": "Can also trigger when any living ally is at 30% HP or lower.",
        "black_rose": "Can also trigger when 5 or more total monsters are still alive.",
        "topologic": "Can also trigger when 5 or more total monsters are still alive.",
        "numeron": "Can also trigger once 4 or more total monsters have been defeated.",
        "holactie": "Can also trigger once 4 or more total monsters have been defeated.",
        "armityle": "Can also trigger once 4 or more total monsters have been defeated.",
        "wicked_avatar": "Can also trigger when an enemy is buffed or has higher ATK than your strongest living ally.",
        "dark_rebellion": "Can also trigger when an enemy is buffed or has higher ATK than your strongest living ally.",
        "ragnazero": "Can also trigger when an enemy is buffed or has higher ATK than your strongest living ally.",
    }
    pressure_slot_effects = {"black_rose", "topologic", "holactie", "ra", "obelisk", "slifer", "wicked_avatar", "wicked_dreadroot"}
    lines = [
        f"• {shared}",
        "• Summons after an ally is defeated.",
        "• Can also become ready after your team takes damage equal to 50% of its total max HP.",
    ]
    extra = specific_requirements.get(effect)
    if extra:
        lines.append(f"• {extra}")
    lines.append(f"• {pressure_slot if effect in pressure_slot_effects else defeated_slot}")
    return "\n".join(lines)


def _extra_deck_monster_ids_from_instance_ids(user_id: int, instance_ids: Sequence[str]) -> List[str]:
    picked: List[str] = []
    seen: set[str] = set()
    for raw_id in instance_ids:
        instance_id = str(raw_id or "").strip()
        if not instance_id or instance_id in seen:
            continue
        row = db.get_inventory_instance(instance_id, int(user_id))
        if row is None or not is_extra_deck_monster_row(row):
            continue
        seen.add(instance_id)
        picked.append(instance_id)
        if len(picked) >= EXTRA_DECK_SIZE:
            break
    return picked


def decode_extra_deck_monsters(value: str) -> List[str]:
    try:
        loaded = json.loads(str(value or "[]"))
    except Exception:
        loaded = []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item or "").strip()]


# Extra Deck Monsters cannot be normal team members.
def boss_or_signature_team_conflict(user_id: int, member_ids: Sequence[str]) -> Optional[str]:
    for member_id in member_ids:
        row = db.get_inventory_instance(str(member_id), int(user_id))
        if row is not None and is_extra_deck_monster_row(row):
            return "Extra Deck Monsters cannot be placed in normal teams. Put them in an Extra Deck instead."
    return None


def category_title_for_monster(name: str) -> Optional[str]:
    return "Extra Deck Monster" if is_extra_deck_monster_name(name) else None
