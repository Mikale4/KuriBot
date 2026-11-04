"""Dueling Grounds NPC pools, enemy teams and rewards."""

from __future__ import annotations

import io
import random
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import discord
from PIL import Image, ImageOps

from kuribot.config import (
    ATTRIBUTES, DUELING_GROUNDS_GOLD_MAX, DUELING_GROUNDS_GOLD_MIN,
    DUELING_GROUNDS_TICKET_DROP_CHANCE, RNG, SUPPORT_DECK_SIZE, SUPPORT_DRAW_COUNT, TEAM_SIZE,
)
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.database import db
from kuribot.monsters import stable_seed_for_name
from kuribot.cards.support_decks import build_support_runtime_from_cards
from kuribot.battle.fighters import make_fighter
from kuribot.battle.stats import scale_fighter_stats
from kuribot.duelists import (
    DUELIST_DATA, DUELIST_THEME_POOLS, resolve_dueling_grounds_source_key, slugify_duelist_key,
)
from kuribot.portraits import get_duelist_files, normalize_portrait_lookup_name
from kuribot.opponents import (
    build_aligned_support_cards_for_team, build_generated_support_cards_for_targets,
    format_duelist_display_name,
)


_DUELIST_POOL_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_duelist_pool_text(value: str) -> str:
    return _DUELIST_POOL_NORMALIZE_RE.sub("", str(value or "").lower())


@lru_cache(maxsize=1)
def _get_duelist_pool_monster_rows() -> Tuple[sqlite3.Row, ...]:
    return tuple(sorted(db.all_monsters(), key=lambda entry: (str(entry["name"]).lower(), str(entry["monster_id"]).lower())))


@lru_cache(maxsize=1)
def _get_duelist_pool_monster_name_index() -> Tuple[Tuple[sqlite3.Row, str, str], ...]:
    indexed_rows: List[Tuple[sqlite3.Row, str, str]] = []
    seen_names: set[str] = set()
    for row in _get_duelist_pool_monster_rows():
        name = str(row["name"]).strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen_names:
            continue
        seen_names.add(lowered)
        indexed_rows.append((row, name, _normalize_duelist_pool_text(name)))
    return tuple(indexed_rows)


def _monster_name_contains_phrase(name: str, phrase: str, normalized_name: Optional[str] = None) -> bool:
    normalized_phrase = _normalize_duelist_pool_text(phrase)
    if not normalized_phrase:
        return False
    return normalized_phrase in (normalized_name if normalized_name is not None else _normalize_duelist_pool_text(name))


def _monster_name_contains_any_phrase(name: str, phrases: Sequence[str], normalized_name: Optional[str] = None) -> bool:
    candidate = normalized_name if normalized_name is not None else _normalize_duelist_pool_text(name)
    return any((_normalize_duelist_pool_text(phrase) in candidate) for phrase in phrases if str(phrase or "").strip())


def _monster_name_has_standalone_word(name: str, word: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(str(word or '').lower())}(?![a-z0-9])", str(name or "").lower()))


def _monster_rows_matching(predicate) -> List[sqlite3.Row]:
    matched: List[sqlite3.Row] = []
    for row, name, normalized_name in _get_duelist_pool_monster_name_index():
        if predicate(name, normalized_name):
            matched.append(row)
    return matched


def _monster_rows_for_exact_names(target_names: Sequence[str]) -> List[sqlite3.Row]:
    wanted = {_normalize_duelist_pool_text(name) for name in target_names if str(name or "").strip()}
    return [row for row, _name, normalized_name in _get_duelist_pool_monster_name_index() if normalized_name in wanted]


def _monster_names_from_rows(rows: Sequence[sqlite3.Row]) -> List[str]:
    names: List[str] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row["name"]).strip()
        lowered = name.lower()
        if not name or lowered in seen:
            continue
        seen.add(lowered)
        names.append(name)
    return names


@lru_cache(maxsize=128)
def _build_generated_pool_for_duelist_cached(key: str, display_name: str) -> Tuple[str, ...]:
    name = str(display_name or format_duelist_display_name(key)).strip() or "Unknown Duelist"
    lookup_values = [name, key, str(display_name or "")]
    normalized_values = {_normalize_duelist_pool_text(value) for value in lookup_values if str(value or "").strip()}

    exact_name_builders: Dict[str, callable] = {
        "fiveheadeddragon": lambda: _monster_rows_for_exact_names([
            "Five-Headed Dragon",
            "Five-Headed Link Dragon",
            "Meteor Dragon",
            "Luster Dragon #2",
            "Tyrant Dragon",
            "Aqua Dragon",
            "Serpent Night Dragon",
        ]),
        "guardianbaou": lambda: _monster_rows_for_exact_names([
            "Guardian Eatos",
            "Guardian Dreadscythe",
            "Guardian Baou",
            "Guardian Kay'est",
            "Guardian Kayest",
            "Guardian Grarl",
            "Guardian Elma",
            "Guardian Ceal",
            "Guardian Tryce",
            "Arsenal Summoner",
        ]),
        "hamonlordofstrikingthunder": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_any_phrase(monster_name, ["Hamon", "Summoning Beast"], normalized_name)),
        "leviadragondaedalus": lambda: _monster_rows_for_exact_names([
            "Codarus",
            "Ocean Dragon Lord - Kairyu-Shin",
            "Levia-Dragon - Daedalus",
            "Ocean Dragon Lord - Neo-Daedalus",
        ]),
        "oceandragonlordneodaedalus": lambda: _monster_rows_for_exact_names([
            "Codarus",
            "Ocean Dragon Lord - Kairyu-Shin",
            "Levia-Dragon - Daedalus",
            "Ocean Dragon Lord - Neo-Daedalus",
        ]),
        "orbital7": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_any_phrase(monster_name, ["Galaxy", "Photon"], normalized_name)),
        "raviellordofphantasms": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_any_phrase(monster_name, ["Raviel", "Summoning Beast"], normalized_name)),
        "skilleddarkmagician": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Dark Magician", normalized_name)),
        "skilledredmagician": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Red-Eyes", normalized_name)),
        "skilledwhitemagician": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_any_phrase(monster_name, ["Buster Blader", "Destruction Sword"], normalized_name)),
        "skyscout": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Harpie", normalized_name)),
        "thecreator": lambda: _monster_rows_matching(lambda monster_name, normalized_name: (
            _monster_name_contains_any_phrase(monster_name, ["Raviel", "Summoning Beast", "Armityle", "Hamon", "Sacred Beast", "The Creator"], normalized_name)
            or _monster_name_has_standalone_word(monster_name, "Uria")
        )),
        "urialordofsearingflames": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Summoning Beast", normalized_name) or _monster_name_has_standalone_word(monster_name, "Uria")),
        "tourguidefromtheunderworld": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_any_phrase(monster_name, ["Tour Guide", "Tour Bus"], normalized_name) or normalized_name in {"giantorc", "tardyorc"}),
        "whitemagicianpikeru": lambda: _monster_rows_matching(lambda monster_name, normalized_name: _monster_name_contains_any_phrase(monster_name, ["Pikeru", "Curran"], normalized_name)),
    }
    for normalized_name, builder in exact_name_builders.items():
        if normalized_name in normalized_values:
            names = _monster_names_from_rows(builder())
            if names:
                return tuple(names)

    contains_rules: List[Tuple[str, object]] = [
        ("alien", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Alien", normalized_name)),
        ("darkworld", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Dark World", normalized_name)),
        ("earthbound", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Earthbound", normalized_name)),
        ("vampire", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Vampire", normalized_name)),
        ("evilhero", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Evil HERO", normalized_name)),
        ("gladiatorbeast", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Gladiator Beast", normalized_name)),
        ("skullservant", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Skull Servant", normalized_name) or _monster_name_contains_any_phrase(monster_name, ["Wight"], normalized_name)),
        ("meklord", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Meklord", normalized_name)),
        ("mokey", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Mokey", normalized_name)),
        ("volcanic", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Volcanic", normalized_name)),
        ("sixsamurai", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Six Samurai", normalized_name)),
        ("ojama", lambda monster_name, normalized_name: _monster_name_contains_phrase(monster_name, "Ojama", normalized_name)),
        ("dd", lambda monster_name, normalized_name: "d.d." in str(monster_name or "").lower()),
    ]
    for marker, predicate in contains_rules:
        if any(marker in value for value in normalized_values):
            names = _monster_names_from_rows(_monster_rows_matching(predicate))
            if names:
                return tuple(names)

    monsters = sorted(_get_duelist_pool_monster_rows(), key=lambda row: str(row["monster_id"]))
    if monsters:
        seed = stable_seed_for_name(f"dueling_grounds:{key}")
        rng = random.Random(seed)
        sample_size = min(max(TEAM_SIZE + 3, 6), len(monsters))
        selected = rng.sample(monsters, k=sample_size)
        return tuple(_monster_names_from_rows(selected))
    return tuple()


def _build_generated_pool_for_duelist(duelist_key: str, display_name: Optional[str] = None) -> List[str]:
    key = slugify_duelist_key(duelist_key)
    resolved_display_name = str(display_name or format_duelist_display_name(key)).strip()
    return list(_build_generated_pool_for_duelist_cached(key, resolved_display_name))


def build_generated_duelist_data(duelist_key: str, display_name: Optional[str] = None) -> dict:
    key = slugify_duelist_key(duelist_key)
    resolved_key = resolve_dueling_grounds_source_key(key, display_name)
    if resolved_key and resolved_key in DUELIST_DATA:
        return dict(DUELIST_DATA[resolved_key])

    name = str(display_name or format_duelist_display_name(key)).strip() or "Unknown Duelist"
    pool = _build_generated_pool_for_duelist(key, display_name)
    return {
        "name": name,
        "pool": pool,
        "pre": f"{name} steps into the Dueling Grounds!",
        "win": f"{name} claims the Dueling Grounds win.",
        "loss": f"{name} falters, but vows to duel again.",
    }


@lru_cache(maxsize=8)
def _build_dueling_grounds_duelist_data_cached(duelist_names: Tuple[str, ...]) -> Dict[str, dict]:
    duelists: Dict[str, dict] = {}
    for duelist_name in duelist_names:
        duelist_key = slugify_duelist_key(duelist_name)
        known = DUELIST_DATA.get(duelist_key)
        duelists[duelist_key] = dict(known) if known is not None else build_generated_duelist_data(duelist_key, duelist_name)
    return dict(sorted(duelists.items(), key=lambda item: str(item[1].get("name", item[0])).lower()))


def get_dueling_grounds_duelist_data() -> Dict[str, dict]:
    duelist_names = tuple(path.stem for path in get_duelist_files())
    cached = _build_dueling_grounds_duelist_data_cached(duelist_names)
    return {key: dict(value) for key, value in cached.items()}


def get_dueling_grounds_duelist(duelist_key: str) -> Optional[dict]:
    duelists = get_dueling_grounds_duelist_data()
    normalized_key = slugify_duelist_key(duelist_key)
    duelist = duelists.get(normalized_key)
    if duelist is not None:
        return dict(duelist)
    resolved_key = resolve_dueling_grounds_source_key(duelist_key)
    if resolved_key:
        for folder_key, folder_duelist in duelists.items():
            folder_name = str(folder_duelist.get("name", folder_key))
            if resolve_dueling_grounds_source_key(folder_key, folder_name) == resolved_key:
                return dict(folder_duelist)
    return None


def choose_random_dueling_grounds_duelist() -> Tuple[str, dict]:
    duelists = get_dueling_grounds_duelist_data()
    if not duelists:
        raise RuntimeError("No duelists are available for Dueling Grounds.")
    duelist_key = RNG.choice(list(duelists.keys()))
    return duelist_key, duelists[duelist_key]


def choose_specific_dueling_grounds_duelist(duelist_key: str) -> Tuple[str, dict]:
    duelists = get_dueling_grounds_duelist_data()
    if not duelists:
        raise RuntimeError("No duelists are available for Dueling Grounds.")
    normalized_key = slugify_duelist_key(duelist_key)
    duelist = duelists.get(normalized_key)
    if duelist is None:
        raise ValueError(f"Unknown Dueling Grounds duelist: {duelist_key}")
    return normalized_key, duelist


def _normalize_dueling_grounds_name(value: str) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", raw)


def _find_dueling_grounds_monster_rows(base_name: str) -> List[sqlite3.Row]:
    target_name = str(base_name).strip().lower()
    target_normalized = _normalize_dueling_grounds_name(base_name)
    matches = []
    for row in db.all_monsters():
        row_name = str(row["name"] or "").strip().lower()
        if row_name == target_name or _normalize_dueling_grounds_name(row_name) == target_normalized:
            matches.append(row)
    return sorted(matches, key=lambda row: str(row["monster_id"]))


def _append_duelist_pool_rows(pool: List[sqlite3.Row], seen_ids: set[str], base_names: Sequence[str]):
    for base_name in base_names:
        for row in _find_dueling_grounds_monster_rows(base_name):
            monster_id = str(row["monster_id"])
            if monster_id in seen_ids:
                continue
            seen_ids.add(monster_id)
            pool.append(row)


def _append_duelist_archetype_rows(pool: List[sqlite3.Row], seen_ids: set[str], archetype_prefixes: Sequence[str]):
    normalized_prefixes = tuple(str(prefix).strip().lower() for prefix in archetype_prefixes if str(prefix).strip())
    compact_prefixes = tuple(_normalize_dueling_grounds_name(prefix) for prefix in normalized_prefixes)
    if not normalized_prefixes:
        return
    for row in sorted(db.all_monsters(), key=lambda row: str(row["monster_id"])):
        name = str(row["name"] or "").strip().lower()
        compact_name = _normalize_dueling_grounds_name(name)
        if not any(name.startswith(prefix) or compact_name.startswith(compact_prefix) for prefix, compact_prefix in zip(normalized_prefixes, compact_prefixes)):
            continue
        monster_id = str(row["monster_id"])
        if monster_id in seen_ids:
            continue
        seen_ids.add(monster_id)
        pool.append(row)


def _append_duelist_theme_rows(pool: List[sqlite3.Row], seen_ids: set[str], theme_keys: Sequence[str]):
    for theme_key in theme_keys:
        for base_name in DUELIST_THEME_POOLS.get(str(theme_key).strip(), []):
            for row in _find_dueling_grounds_monster_rows(base_name):
                monster_id = str(row["monster_id"])
                if monster_id in seen_ids:
                    continue
                seen_ids.add(monster_id)
                pool.append(row)


def _append_duelist_name_prefix_rows(pool: List[sqlite3.Row], seen_ids: set[str], prefixes: Sequence[str]):
    normalized_prefixes = tuple(_normalize_dueling_grounds_name(prefix) for prefix in prefixes if str(prefix).strip())
    if not normalized_prefixes:
        return
    for row in sorted(db.all_monsters(), key=lambda row: str(row["monster_id"])):
        compact_name = _normalize_dueling_grounds_name(str(row["name"] or ""))
        if not any(compact_name.startswith(prefix) for prefix in normalized_prefixes):
            continue
        monster_id = str(row["monster_id"])
        if monster_id in seen_ids:
            continue
        seen_ids.add(monster_id)
        pool.append(row)


def _is_zexal_utopia_duelist(duelist_key: str) -> bool:
    return slugify_duelist_key(duelist_key) in {"yuma_tsukumo", "astral", "eliphas"}


def _is_zexal_utopia_pool_name(name: str) -> bool:
    raw_name = str(name or "").strip().lower()
    compact_name = _normalize_dueling_grounds_name(raw_name)
    if not raw_name:
        return False
    if "utopia" in raw_name or "utopic" in raw_name:
        return True
    return compact_name.startswith("zw") or compact_name.startswith("zs")


def _append_zexal_utopia_rows(pool: List[sqlite3.Row], seen_ids: set[str]):
    for row in sorted(db.all_monsters(), key=lambda row: str(row["monster_id"])):
        if not _is_zexal_utopia_pool_name(str(row["name"] or "")):
            continue
        monster_id = str(row["monster_id"])
        if monster_id in seen_ids:
            continue
        seen_ids.add(monster_id)
        pool.append(row)


def _sample_enemy_rows_for_pool(pool: Sequence[sqlite3.Row], team_size: int, rng: random.Random) -> List[sqlite3.Row]:
    if team_size <= 0:
        return []
    pool_list = list(pool)
    if not pool_list:
        raise RuntimeError("Enemy team pool is empty.")
    if len(pool_list) >= team_size:
        return rng.sample(pool_list, k=team_size)
    picks = list(pool_list)
    while len(picks) < team_size:
        picks.append(rng.choice(pool_list))
    rng.shuffle(picks)
    return picks[:team_size]


def build_dueling_grounds_enemy_team(duelist_key: str) -> Tuple[List[Fighter], dict]:
    duelist = get_dueling_grounds_duelist(duelist_key)
    if duelist is None:
        raise ValueError(f"Unknown Dueling Grounds duelist: {duelist_key}")
    pool: List[sqlite3.Row] = []
    seen_ids = set()
    normalized_duelist_key = slugify_duelist_key(resolve_dueling_grounds_source_key(duelist_key, str(duelist.get("name", ""))) or duelist_key)
    use_global_fallback = True

    if normalized_duelist_key == "tetsu_trudge":
        _append_duelist_theme_rows(pool, seen_ids, ("goyo_police",))
        _append_duelist_name_prefix_rows(pool, seen_ids, ("Goyo", "Stygian", "Worm"))
        use_global_fallback = False
    else:
        if _is_zexal_utopia_duelist(normalized_duelist_key):
            _append_zexal_utopia_rows(pool, seen_ids)
            use_global_fallback = False
        else:
            _append_duelist_pool_rows(pool, seen_ids, duelist.get("pool", []))
        if normalized_duelist_key == "carly_carmine":
            _append_duelist_archetype_rows(pool, seen_ids, ("fortune lady", "fortune fairy"))
        elif normalized_duelist_key == "jaden_yuki":
            _append_duelist_archetype_rows(pool, seen_ids, ("elemental hero",))
        elif normalized_duelist_key == "supreme_king":
            _append_duelist_archetype_rows(pool, seen_ids, ("evil hero",))

    if len(pool) < TEAM_SIZE and use_global_fallback:
        fallback_pool = sorted(db.all_monsters(), key=lambda row: str(row["monster_id"]))
        seed = stable_seed_for_name(f"dueling_grounds:fallback:{duelist_key}:{int(time.time() // 60)}")
        rng = random.Random(seed)
        fallback_pool = list(fallback_pool)
        rng.shuffle(fallback_pool)
        for row in fallback_pool:
            monster_id = str(row["monster_id"])
            if monster_id in seen_ids:
                continue
            seen_ids.add(monster_id)
            pool.append(row)
            if len(pool) >= max(TEAM_SIZE, 8):
                break
    if len(pool) < TEAM_SIZE:
        raise RuntimeError("Not enough monsters in the pool for Dueling Grounds battles.")
    seed = stable_seed_for_name(f"dueling_grounds:{duelist_key}:{int(time.time() // 60)}")
    rng = random.Random(seed)
    enemy_rows = _sample_enemy_rows_for_pool(pool, TEAM_SIZE, rng)
    multiplier = 1.0
    team: List[Fighter] = []
    for row in enemy_rows:
        fighter = make_fighter(row)
        scale_fighter_stats(fighter, multiplier)
        team.append(fighter)
    return team, duelist


def build_dueling_grounds_enemy_support_runtime(duelist_name: str, enemy_team: Sequence[Fighter]) -> Tuple[Optional[SupportDeckRuntime], str]:
    attrs = [fighter.attribute for fighter in enemy_team]
    if not attrs:
        return None, "No Support Deck"
    seed = stable_seed_for_name(f"dueling_grounds_support:{duelist_name}:{int(time.time() // 60)}")
    rng = random.Random(seed)
    mode = rng.choices(["none", "random", "aligned"], weights=[1, 2, 5], k=1)[0]
    if mode == "none":
        return None, "No Support Deck"
    bonuses = [10, 15, 20] if mode == "random" else [15, 20, 25]
    if mode == "aligned":
        cards = build_aligned_support_cards_for_team(enemy_team, bonuses, rng=rng, deck_name=f"{duelist_name} Aligned Support Deck")
    else:
        targets = list(dict.fromkeys(list(attrs) + [monster_type for fighter in enemy_team for monster_type in fighter.monster_types]))
        cards = build_aligned_support_cards_for_team(enemy_team, bonuses, rng=rng, deck_name=f"{duelist_name} Adaptive Support Deck")
        cards = cards[: max(SUPPORT_DRAW_COUNT + 2, min(SUPPORT_DECK_SIZE, len(cards)))] + [
            card for card in build_generated_support_cards_for_targets(targets or ATTRIBUTES, bonuses, rng=rng)
            if card.support_card_id not in {existing.support_card_id for existing in cards}
        ]
        cards = cards[:SUPPORT_DECK_SIZE]
    label = f"{duelist_name} {'Aligned' if mode == 'aligned' else 'Adaptive'} Support Deck"
    descriptor = "Aligned Support Deck" if mode == "aligned" else "Adaptive Support Deck"
    return build_support_runtime_from_cards(label, cards, special_first=True), descriptor


def dueling_grounds_reward() -> Tuple[int, int]:
    gold = RNG.randint(DUELING_GROUNDS_GOLD_MIN, DUELING_GROUNDS_GOLD_MAX)
    tickets = 1 if RNG.random() < DUELING_GROUNDS_TICKET_DROP_CHANCE else 0
    return gold, tickets


def get_dueling_grounds_duelist_portrait_path(duelist_key: str, duelist_name: str) -> Optional[Path]:
    candidates = [
        str(duelist_name or "").strip(),
        str(duelist_key or "").strip(),
        str(duelist_key or "").replace("_", " ").strip(),
    ]
    normalized_candidates = {normalize_portrait_lookup_name(candidate) for candidate in candidates if str(candidate or "").strip()}
    if not normalized_candidates:
        return None
    for duelist_path in get_duelist_files():
        if normalize_portrait_lookup_name(duelist_path.stem) in normalized_candidates:
            return duelist_path
    return None


def make_dueling_grounds_duelist_portrait_file(duelist_key: str, duelist_name: str, *, filename: str = "dueling_grounds_duelist.png") -> Optional[discord.File]:
    portrait_path = get_dueling_grounds_duelist_portrait_path(duelist_key, duelist_name)
    if portrait_path is None or not portrait_path.exists() or not portrait_path.is_file():
        return None
    try:
        with Image.open(portrait_path) as image:
            preview = ImageOps.contain(image.convert("RGBA"), (256, 256), method=Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            preview.save(buffer, format="PNG", compress_level=3)
            buffer.seek(0)
            return discord.File(buffer, filename=filename)
    except Exception:
        try:
            return discord.File(str(portrait_path), filename=filename)
        except Exception:
            return None
