"""Ranked ELO, opponents and rewards."""

from __future__ import annotations

import random
import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.config import RANKED_STARTING_ELO, RNG, TEAM_SIZE
from kuribot.models import Fighter, SupportDeckRuntime
from kuribot.core import bot
from kuribot.cards.equip_spells import EQUIP_SPELL_DEFS, init_equip_spells, random_equip_spell
from kuribot.database import db
from kuribot.monsters import resolve_row_combat_stats, stable_seed_for_name
from kuribot.cards.support_cards import add_random_support_card_to_user
from kuribot.cards.support_decks import build_support_runtime_from_cards
from kuribot.battle.fighters import make_fighter
from kuribot.battle.stats import scale_fighter_base_stats_exact
from kuribot.battle.setup import _active_team_fighters_for_user
from kuribot.duelists import slugify_duelist_key
from kuribot.opponents import (
    assign_unique_generated_equip_spells, build_aligned_support_cards_for_team,
    format_duelist_display_name,
)
from kuribot.dueling_grounds import (
    _append_duelist_archetype_rows, _append_duelist_name_prefix_rows, _append_duelist_pool_rows,
    _append_duelist_theme_rows, _append_zexal_utopia_rows, _is_zexal_utopia_duelist,
    _sample_enemy_rows_for_pool, get_dueling_grounds_duelist,
    get_dueling_grounds_duelist_portrait_path,
)
from kuribot.campaign import RANKED_DUELIST_STAT_MULTIPLIER


RANKED_K_FACTOR = 32
RANKED_MATCH_ELO_RANGE = 400
RANKED_REWARD_ELO_THRESHOLDS = tuple(range(1200, 3001, 200))
RANKED_REWARD_GOLD = 10000
RANKED_REWARD_TICKETS = 10
RANKED_REWARD_PREMIUM_TICKETS = 1
RANKED_REWARD_EQUIP_SPELLS = 3
RANKED_REWARD_SUPPORT_CARDS = 15
RANKED_DUELIST_SUPPORT_BONUSES = [25]


def ranked_gxe_from_row(user_row: sqlite3.Row) -> float:
    wins = float(user_row["ranked_wins"] or 0)
    losses = float(user_row["ranked_losses"] or 0)
    draws = float(user_row["ranked_draws"] or 0)
    total = wins + losses + draws
    if total <= 0:
        return 50.0
    adjusted_wins = wins + (0.5 * draws)
    return round(((adjusted_wins + 1.0) / (total + 2.0)) * 100.0, 1)


def ranked_expected_score(player_elo: int, opponent_elo: int) -> float:
    return 1.0 / (1.0 + 10 ** ((float(opponent_elo) - float(player_elo)) / 400.0))


def ranked_elo_delta(player_elo: int, opponent_elo: int, score: float, k_factor: int = RANKED_K_FACTOR) -> int:
    expected = ranked_expected_score(player_elo, opponent_elo)
    delta = int(round(float(k_factor) * (float(score) - expected)))
    if score in {0.0, 1.0} and delta == 0:
        delta = 1 if score > expected else -1
    return delta


def ranked_outcome_score(player_won: bool, enemy_won: bool) -> float:
    if player_won:
        return 1.0
    if enemy_won:
        return 0.0
    return 0.5


def ranked_duelist_support_mode(elo: int) -> str:
    return 'aligned'


def support_runtime_for_enemy_mode(label_name: str, enemy_team: Sequence[Fighter], mode: str) -> Tuple[Optional[SupportDeckRuntime], str]:
    normalized_mode = str(mode or 'aligned').strip().lower()
    if normalized_mode == 'none' or not enemy_team:
        return None, 'No Support Deck'
    seed = stable_seed_for_name(f"enemy_mode_support:{label_name}:{normalized_mode}:{int(time.time() // 60)}")
    cards = build_aligned_support_cards_for_team(enemy_team, RANKED_DUELIST_SUPPORT_BONUSES, rng=random.Random(seed), deck_name=f"{label_name} Aligned Support Deck")
    runtime = build_support_runtime_from_cards(f"{label_name} Aligned Support Deck", cards, special_first=True)
    return runtime, ('Aligned Support Deck' if runtime else 'No Support Deck')


def row_total_power(row: sqlite3.Row) -> int:
    hp, atk, defense, spd = resolve_row_combat_stats(row)
    return int(hp) + int(atk) + int(defense) + int(spd)


def strongest_enemy_rows_for_pool(pool: Sequence[sqlite3.Row], team_size: int) -> List[sqlite3.Row]:
    ranked_rows = sorted(list(pool), key=lambda row: (row_total_power(row), str(row['monster_id'])), reverse=True)
    if len(ranked_rows) >= team_size:
        return ranked_rows[:team_size]
    picks = list(ranked_rows)
    while len(picks) < team_size and ranked_rows:
        picks.append(ranked_rows[len(picks) % len(ranked_rows)])
    return picks[:team_size]


def assign_enemy_team_equip_spells(team: Sequence[Fighter], elo: int, *, rng: Optional[random.Random] = None, support_runtime: Optional[SupportDeckRuntime] = None, target_team: Optional[Sequence[Fighter]] = None, adaptation_memory: Optional[Dict[str, object]] = None) -> List[str]:
    init_equip_spells()
    if not team or not EQUIP_SPELL_DEFS:
        return []
    rng = rng or RNG
    return assign_unique_generated_equip_spells(
        team,
        rng,
        support_cards=(support_runtime.shuffled_cards if support_runtime else None),
        label_name=f"elo:{elo}",
        target_team=target_team,
        adaptation_memory=adaptation_memory,
    )


def resolve_ranked_dueling_grounds_user_label(user_id: int) -> str:
    discord_user = bot.get_user(int(user_id))
    if discord_user is not None:
        for attr in ('global_name', 'name', 'display_name'):
            value = str(getattr(discord_user, attr, '') or '').strip()
            if value:
                return value
    return f'User {int(user_id)}'


async def resolve_ranked_dueling_grounds_user_label_for_battle(interaction: discord.Interaction, user_id: int) -> str:
    user_id = int(user_id)
    if interaction.guild is not None:
        member = interaction.guild.get_member(user_id)
        if member is not None:
            value = str(getattr(member, 'global_name', '') or getattr(member, 'name', '') or getattr(member, 'display_name', '') or '').strip()
            if value:
                return value
        try:
            member = await interaction.guild.fetch_member(user_id)
        except Exception:
            member = None
        if member is not None:
            value = str(getattr(member, 'global_name', '') or getattr(member, 'name', '') or getattr(member, 'display_name', '') or '').strip()
            if value:
                return value
    cached_user = bot.get_user(user_id)
    if cached_user is not None:
        value = str(getattr(cached_user, 'global_name', '') or getattr(cached_user, 'name', '') or getattr(cached_user, 'display_name', '') or '').strip()
        if value:
            return value
    try:
        fetched_user = await bot.fetch_user(user_id)
    except Exception:
        fetched_user = None
    if fetched_user is not None:
        value = str(getattr(fetched_user, 'global_name', '') or getattr(fetched_user, 'name', '') or '').strip()
        if value:
            return value
    return resolve_ranked_dueling_grounds_user_label(user_id)


def choose_ranked_user_opponent(owner_id: int) -> Optional[int]:
    # Filter by rating first and build teams only for the closest candidates. This used to
    # build a full battle team for EVERY ranked player on each match start.
    owner_row = db.get_user(owner_id)
    owner_elo = int(owner_row['ranked_elo'] or RANKED_STARTING_ELO)
    scored: List[Tuple[int, int]] = []
    for user_id in db.users_with_active_teams(exclude_user_id=owner_id):
        candidate_row = db.get_user(user_id)
        candidate_elo = int((candidate_row['ranked_elo'] if candidate_row is not None else 0) or RANKED_STARTING_ELO)
        distance = abs(candidate_elo - owner_elo)
        if distance <= RANKED_MATCH_ELO_RANGE:
            scored.append((distance, int(user_id)))
    scored.sort()
    closest: List[int] = []
    for _distance, user_id in scored:
        if len(_active_team_fighters_for_user(user_id, "ranked")) >= TEAM_SIZE:
            closest.append(user_id)
            if len(closest) >= 6:
                break
    if not closest:
        return None
    return RNG.choice(closest)


def random_ranked_duelist_elo(player_elo: int) -> int:
    lower_bound = max(0, int(player_elo) - RANKED_MATCH_ELO_RANGE)
    upper_bound = max(lower_bound, int(player_elo) + RANKED_MATCH_ELO_RANGE)
    return RNG.randint(lower_bound, upper_bound)


def build_ranked_dueling_grounds_enemy_team(duelist_key: str, player_elo: int) -> Tuple[List[Fighter], dict]:
    duelist = get_dueling_grounds_duelist(duelist_key)
    if duelist is None:
        raise ValueError(f'Unknown Dueling Grounds duelist: {duelist_key}')
    pool: List[sqlite3.Row] = []
    seen_ids = set()
    normalized_duelist_key = slugify_duelist_key(duelist_key)
    use_global_fallback = True

    if normalized_duelist_key == 'tetsu_trudge':
        _append_duelist_theme_rows(pool, seen_ids, ('goyo_police',))
        _append_duelist_name_prefix_rows(pool, seen_ids, ('Goyo', 'Stygian', 'Worm'))
        use_global_fallback = False
    else:
        if _is_zexal_utopia_duelist(normalized_duelist_key):
            _append_zexal_utopia_rows(pool, seen_ids)
            use_global_fallback = False
        else:
            _append_duelist_pool_rows(pool, seen_ids, duelist.get('pool', []))
        if normalized_duelist_key == 'carly_carmine':
            _append_duelist_archetype_rows(pool, seen_ids, ('fortune lady', 'fortune fairy'))
        elif normalized_duelist_key == 'jaden_yuki':
            _append_duelist_archetype_rows(pool, seen_ids, ('elemental hero',))
        elif normalized_duelist_key == 'supreme_king':
            _append_duelist_archetype_rows(pool, seen_ids, ('evil hero',))

    if len(pool) < TEAM_SIZE and use_global_fallback:
        fallback_pool = sorted(db.all_monsters(), key=lambda row: str(row['monster_id']))
        seed = stable_seed_for_name(f'ranked_dueling_grounds:fallback:{duelist_key}:{int(time.time() // 60)}')
        rng = random.Random(seed)
        fallback_pool = list(fallback_pool)
        rng.shuffle(fallback_pool)
        for row in fallback_pool:
            monster_id = str(row['monster_id'])
            if monster_id in seen_ids:
                continue
            seen_ids.add(monster_id)
            pool.append(row)
            if len(pool) >= max(TEAM_SIZE, 10):
                break
    if len(pool) < TEAM_SIZE:
        raise RuntimeError('Not enough monsters in the pool for Ranked Dueling Grounds battles.')

    seed = stable_seed_for_name(f'ranked_dueling_grounds:{duelist_key}:{int(time.time() // 60)}:{player_elo}')
    rng = random.Random(seed)
    if player_elo >= 1800:
        enemy_rows = strongest_enemy_rows_for_pool(pool, TEAM_SIZE)
    else:
        enemy_rows = _sample_enemy_rows_for_pool(pool, TEAM_SIZE, rng)
    team: List[Fighter] = []
    for row in enemy_rows:
        fighter = make_fighter(row)
        scale_fighter_base_stats_exact(fighter, RANKED_DUELIST_STAT_MULTIPLIER)
        team.append(fighter)
    support_runtime, _support_label = support_runtime_for_enemy_mode(duelist.get('name', format_duelist_display_name(duelist_key)), team, ranked_duelist_support_mode(player_elo))
    assign_enemy_team_equip_spells(team, player_elo, rng=rng, support_runtime=support_runtime)
    return team, duelist


def make_dueling_grounds_result_embed_mode(duelist_key: str, duelist_name: str, speech: str, player_won: bool, progress_text: str = '', mode_label: str = 'Dueling Grounds • Normal', thumbnail_allowed: bool = True) -> discord.Embed:
    outcome_label = 'Victory' if player_won else ('Defeat' if speech and not speech.startswith('Both') else 'Draw')
    embed = discord.Embed(
        title=f'{duelist_name}',
        description=speech or ('Excellent duel.' if player_won else 'Victory is mine.'),
        color=discord.Color.green() if player_won else (discord.Color.orange() if outcome_label == 'Draw' else discord.Color.red()),
    )
    embed.add_field(name='Mode', value=mode_label, inline=True)
    embed.add_field(name='Outcome', value=outcome_label, inline=True)
    if progress_text:
        embed.add_field(name='Rewards', value=progress_text, inline=False)
    if thumbnail_allowed and get_dueling_grounds_duelist_portrait_path(duelist_key, duelist_name) is not None:
        embed.set_thumbnail(url='attachment://dueling_grounds_duelist.png')
    return embed


def award_ranked_elo_threshold_rewards(user_id: int, previous_elo: int, new_elo: int) -> List[int]:
    claimed = db.ranked_reward_thresholds_claimed(user_id)
    # Pay every milestone at or below the current rating that was never claimed - this also
    # back-pays milestones that were crossed while defending (those used to be skipped).
    newly_claimed = [threshold for threshold in RANKED_REWARD_ELO_THRESHOLDS if threshold <= new_elo and threshold not in claimed]
    if not newly_claimed:
        return []
    count = len(newly_claimed)
    db.add_gold(user_id, RANKED_REWARD_GOLD * count)
    db.add_tickets(user_id, RANKED_REWARD_TICKETS * count)
    db.add_premium_tickets(user_id, RANKED_REWARD_PREMIUM_TICKETS * count)
    for _ in range(RANKED_REWARD_EQUIP_SPELLS * count):
        db.add_equip_spell_instance(user_id, random_equip_spell().equip_spell_id)
    for _ in range(RANKED_REWARD_SUPPORT_CARDS * count):
        add_random_support_card_to_user(user_id)
    db.mark_ranked_reward_thresholds_claimed(user_id, newly_claimed)
    return newly_claimed


def make_dueling_grounds_result_embed(duelist_key: str, duelist_name: str, speech: str, player_won: bool, progress_text: str = "") -> discord.Embed:
    return make_dueling_grounds_result_embed_mode(duelist_key, duelist_name, speech, player_won, progress_text, mode_label="Dueling Grounds • Normal")
