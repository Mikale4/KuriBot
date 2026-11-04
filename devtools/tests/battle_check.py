"""Run real battles in every mode (normal, campaign, ranked, guild boss, arena, duelist vs
duelist) on a sandbox copy of the database.

    python devtools/tests/battle_check.py            (5 battles per mode)
    python devtools/tests/battle_check.py 20         (20 per mode)

Each battle uses a real player's team (or two NPC duelists) and a simulated Discord message, so the
full battle loop, effects, rewards and battle images all run. Exits with code 1 if any battle
crashes, hangs, or sends something Discord would reject.
"""
from __future__ import annotations

import asyncio
import collections
import random
import sys
import time
import traceback

import harness
from harness import FakeInteraction, k

PER_MODE = int(sys.argv[1]) if len(sys.argv) > 1 else 5
SPEEDS = ["instant", "very_fast", "fast", "normal"]
k.db.get_battle_speed = lambda uid: random.choice(SPEEDS)
FAILURES = collections.defaultdict(list)


def players_with_team(mode: str):
    found = []
    for row in k.db.conn.execute("SELECT user_id FROM users ORDER BY user_id").fetchall():
        uid = int(row["user_id"])
        try:
            if len(k._active_team_fighters_for_user(uid, mode)) >= k.TEAM_SIZE:
                found.append(uid)
        except Exception:
            FAILURES[f"loading {mode} team"].append(traceback.format_exc())
    return found


async def run_one(label: str, start_battle) -> tuple:
    started = time.time()
    try:
        await asyncio.wait_for(start_battle(), timeout=180)
        return True, time.time() - started
    except asyncio.TimeoutError:
        FAILURES[label].append("TIMEOUT: the battle ran for more than 180 seconds (possible endless loop)")
    except Exception:
        FAILURES[label].append(traceback.format_exc())
    return False, time.time() - started


async def main() -> int:
    random.seed(1234)
    jobs = []
    for uid in players_with_team("lobby")[:PER_MODE]:
        jobs.append(("normal", lambda uid=uid: k.start_dueling_grounds_normal_battle(FakeInteraction(uid), uid, f"user{uid}")))
    for uid in players_with_team("campaign")[:PER_MODE]:
        area = random.choice(k.CAMPAIGN_AREAS)["key"]
        keys = k.get_campaign_duelist_keys(area)
        if keys:
            duelist = random.choice(keys)
            hard = random.random() < 0.4
            jobs.append(("campaign", lambda uid=uid, area=area, duelist=duelist, hard=hard: k.start_campaign_stage_battle(FakeInteraction(uid), uid, f"user{uid}", area, duelist, hard_mode=hard)))
    for uid in players_with_team("ranked")[:PER_MODE]:
        jobs.append(("ranked", lambda uid=uid: k.start_dueling_grounds_ranked_battle(FakeInteraction(uid), uid, f"user{uid}")))
    for uid in players_with_team("guild_boss")[:PER_MODE]:
        jobs.append(("guild_boss", lambda uid=uid: k.start_guild_boss_battle(FakeInteraction(uid), uid, f"user{uid}")))
    for uid in players_with_team("arena_offense")[:PER_MODE]:
        async def arena_battle(uid=uid):
            pool = await k.build_arena_pool_entries(uid)
            opponents = k.select_arena_opponents(pool, k.get_user_arena_rank_position(uid), k.ARENA_MATCH_COUNT)
            if opponents:
                await k.start_arena_battle(FakeInteraction(uid), uid, f"user{uid}", random.choice(opponents), roster_opponents=opponents)
        jobs.append(("arena", arena_battle))
    duelists = list(k.get_dueling_grounds_duelist_data().keys())
    viewer = next(iter(players_with_team("lobby")), 1)
    if len(duelists) >= 2:
        for _ in range(PER_MODE):
            a, b = random.sample(duelists, 2)
            jobs.append(("duelist_vs_duelist", lambda a=a, b=b: k.start_duelist_vs_duelist_battle(FakeInteraction(viewer), viewer, f"user{viewer}", a, b)))

    print(f"Running {len(jobs)} battles...")
    results = collections.Counter()
    times = collections.defaultdict(list)
    for label, start_battle in jobs:
        ok, seconds = await run_one(label, start_battle)
        results[(label, ok)] += 1
        times[label].append(seconds)
    for (label, ok), count in sorted(results.items()):
        print(f"  {label:20s} {'ok' if ok else 'FAILED'} x{count}   (avg {sum(times[label]) / len(times[label]):.1f}s)")
    failures = harness.summarize_failures(FAILURES)
    for where, problem in collections.Counter(harness.LIMIT_PROBLEMS).most_common(20):
        print(f"  Discord would reject [{where}]: {problem}")
    bad = failures + len(harness.LIMIT_PROBLEMS)
    print("\nAll battles passed." if not bad and jobs else f"\n{bad} problem(s) found." if bad else "\nNo battles could run (no players with full teams?).")
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
