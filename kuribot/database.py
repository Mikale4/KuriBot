"""SQLite storage: the Database class and the shared `db` instance."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from kuribot.config import (
    ATTRIBUTES, DAILY_ARENA_TICKETS, DB_PATH, EXTRA_DECK_MIN_SIZE, EXTRA_DECK_SIZE,
    GUILD_ACCEPTANCE_APPLY, GUILD_ACCEPTANCE_FREE_JOIN, GUILD_ACCEPTANCE_LABELS,
    GUILD_APPLICATION_STATUS_ACCEPTED, GUILD_APPLICATION_STATUS_DENIED,
    GUILD_APPLICATION_STATUS_PENDING, GUILD_BOSS_ATK_MULTIPLIER,
    GUILD_BOSS_ATTEMPT_GUILD_COIN_REWARD, GUILD_BOSS_DEFEAT_GOLD_REWARD,
    GUILD_BOSS_DEFEAT_GUILD_COIN_REWARD, GUILD_BOSS_HP_MULTIPLIER, GUILD_CREATE_COST,
    GUILD_DAILY_CLAIM_COINS, GUILD_DONATION_OPTION_BY_KEY, GUILD_MAX_MEMBERS, GUILD_MAX_OFFICERS,
    GUILD_NAME_MAX_LENGTH, GUILD_NAME_MIN_LENGTH, GUILD_ROLE_LEADER, GUILD_ROLE_MEMBER,
    GUILD_ROLE_OFFICER, GUILD_SHOP_ARENA_TICKET_AMOUNT, GUILD_SHOP_ITEM_BY_KEY, LEVELING_MAX_LEVEL,
    LEVELING_MESSAGE_COOLDOWN_SECONDS, LEVELING_REWARD_GOLD, LEVELING_REWARD_PACKS, MONSTER_TYPES,
    PACKS_PER_DAILY, RANKED_STARTING_ELO, RNG, SHOP_PAID_REFRESH_COST, SINGLE_TYPE_CHANCE,
    SPECIAL_MONSTER_REMOVAL_BODY_MARKER, SUPPORT_CARD_DUPLICATE_DUST, SUPPORT_DECK_MIN_SIZE,
    SUPPORT_DECK_SIZE, TEAM_ACTIVE_MODES, TEAM_SIZE,
)
from kuribot.models import MonsterDef
from kuribot.timeutil import (
    eastern_midnight_countdown_text, is_current_reset_window, is_same_eastern_calendar_day,
    time_until_next_reset, utc_now,
)
from kuribot.settings import BATTLE_SPEED_LABELS
from kuribot.packs import normalize_pack_key, PACK_DEFS


class LockedCursor:
    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock):
        self._cursor = cursor
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            self._cursor.execute(*args, **kwargs)
        return self

    def executemany(self, *args, **kwargs):
        with self._lock:
            self._cursor.executemany(*args, **kwargs)
        return self

    def executescript(self, *args, **kwargs):
        with self._lock:
            self._cursor.executescript(*args, **kwargs)
        return self

    def fetchone(self):
        with self._lock:
            return self._cursor.fetchone()

    def fetchall(self):
        with self._lock:
            return self._cursor.fetchall()

    def fetchmany(self, *args, **kwargs):
        with self._lock:
            return self._cursor.fetchmany(*args, **kwargs)

    def close(self):
        with self._lock:
            return self._cursor.close()

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class LockedConnection:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def cursor(self, *args, **kwargs) -> LockedCursor:
        with self._lock:
            cursor = self._conn.cursor(*args, **kwargs)
        return LockedCursor(cursor, self._lock)

    def execute(self, *args, **kwargs) -> LockedCursor:
        with self._lock:
            cursor = self._conn.execute(*args, **kwargs)
        return LockedCursor(cursor, self._lock)

    def executemany(self, *args, **kwargs) -> LockedCursor:
        with self._lock:
            cursor = self._conn.executemany(*args, **kwargs)
        return LockedCursor(cursor, self._lock)

    def executescript(self, *args, **kwargs) -> LockedCursor:
        with self._lock:
            cursor = self._conn.executescript(*args, **kwargs)
        return LockedCursor(cursor, self._lock)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def rollback(self):
        with self._lock:
            return self._conn.rollback()

    def close(self):
        with self._lock:
            return self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


class Database:
    def __init__(self, path: str):
        self.lock = threading.RLock()
        raw_conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        raw_conn.row_factory = sqlite3.Row
        raw_conn.execute("PRAGMA busy_timeout = 30000")
        try:
            raw_conn.execute("PRAGMA journal_mode = WAL")
            # Safe with WAL (no corruption risk) and far fewer disk flushes per commit -
            # a big win on phone storage, where every commit used to wait on a full sync.
            raw_conn.execute("PRAGMA synchronous = NORMAL")
            raw_conn.execute("PRAGMA temp_store = MEMORY")
        except Exception:
            pass
        self.conn = LockedConnection(raw_conn, self.lock)
        # Tables are created and upgraded by setup(), which startup.prepare_database() calls once
        # every module is loaded (the upgrades use card and monster helpers from other modules).

    def setup(self):
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                gold INTEGER NOT NULL DEFAULT 0,
                tickets INTEGER NOT NULL DEFAULT 0,
                premium_tickets INTEGER NOT NULL DEFAULT 0,
                boss_monster_tickets INTEGER NOT NULL DEFAULT 0,
                guild_coins INTEGER NOT NULL DEFAULT 0,
                arena_tickets INTEGER NOT NULL DEFAULT 0,
                equip_spell_tickets INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                ranked_wins INTEGER NOT NULL DEFAULT 0,
                ranked_losses INTEGER NOT NULL DEFAULT 0,
                ranked_draws INTEGER NOT NULL DEFAULT 0,
                ranked_elo INTEGER NOT NULL DEFAULT 1000,
                ranked_peak_elo INTEGER NOT NULL DEFAULT 1000,
                ranked_rewards_claimed TEXT NOT NULL DEFAULT '',
                card_dust INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                last_quest_refresh TEXT,
                last_monster_shop_refresh TEXT,
                last_card_shop_refresh TEXT,
                last_equip_shop_refresh TEXT,
                signature_text TEXT NOT NULL DEFAULT '',
                ui_theme TEXT NOT NULL DEFAULT 'gray',
                battle_speed TEXT NOT NULL DEFAULT 'normal',
                notifications_enabled INTEGER NOT NULL DEFAULT 1,
                level INTEGER NOT NULL DEFAULT 1,
                level_xp INTEGER NOT NULL DEFAULT 0,
                last_message_xp_at TEXT,
                tag_image_path TEXT NOT NULL DEFAULT '',
                avatar_path TEXT NOT NULL DEFAULT '',
                registration_complete INTEGER NOT NULL DEFAULT 0,
                showcase_unlocked INTEGER NOT NULL DEFAULT 0,
                ui_theme_unlocked INTEGER NOT NULL DEFAULT 0,
                campaign_hard_mode_notified INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS tutorial_progress (
                user_id INTEGER PRIMARY KEY,
                summon_done_at TEXT,
                premium_done_at TEXT,
                reward_claimed_at TEXT,
                team_done_at TEXT,
                team_reward_claimed_at TEXT,
                battle_done_at TEXT,
                hearts_done_at TEXT,
                battle_reward_claimed_at TEXT,
                monster_generation_done_at TEXT,
                monster_generation_reward_claimed_at TEXT,
                support_cards_done_at TEXT,
                support_cards_reward_claimed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS profile_tag_uploads (
                request_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                guild_id INTEGER,
                channel_id INTEGER,
                original_filename TEXT NOT NULL DEFAULT '',
                pending_image_path TEXT NOT NULL,
                approved_image_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                submitted_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER,
                admin_message_ids_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS monster_defs (
                monster_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                attribute TEXT NOT NULL,
                hp INTEGER NOT NULL,
                atk INTEGER NOT NULL,
                def INTEGER NOT NULL,
                spd INTEGER NOT NULL,
                image_path TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory (
                instance_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                monster_id TEXT NOT NULL,
                monster_attribute TEXT NOT NULL DEFAULT '',
                monster_types TEXT NOT NULL,
                obtained_at TEXT NOT NULL,
                FOREIGN KEY (monster_id) REFERENCES monster_defs(monster_id)
            );

            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                slots_csv TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                support_deck_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS team_active_modes (
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                team_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, mode)
            );

            CREATE TABLE IF NOT EXISTS support_card_inventory (
                instance_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                support_card_id TEXT NOT NULL,
                exp INTEGER NOT NULL DEFAULT 0,
                obtained_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS support_decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                cards_json TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS equip_spell_inventory (
                instance_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                equip_spell_id TEXT NOT NULL,
                obtained_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monster_equip_spells (
                monster_instance_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                equip_spell_instance_id TEXT NOT NULL UNIQUE,
                equipped_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monster_shop_offers (
                user_id INTEGER NOT NULL,
                slot_index INTEGER NOT NULL,
                monster_id TEXT NOT NULL,
                monster_attribute TEXT NOT NULL DEFAULT '',
                monster_types TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                purchased INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, slot_index)
            );

            CREATE TABLE IF NOT EXISTS card_shop_offers (
                user_id INTEGER NOT NULL,
                slot_index INTEGER NOT NULL,
                support_card_id TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                purchased INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, slot_index)
            );

            CREATE TABLE IF NOT EXISTS equip_shop_offers (
                user_id INTEGER NOT NULL,
                slot_index INTEGER NOT NULL,
                equip_spell_id TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                purchased INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, slot_index)
            );

            CREATE TABLE IF NOT EXISTS mailbox_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sender_id INTEGER,
                sender_name TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                reward_type TEXT,
                reward_amount INTEGER NOT NULL DEFAULT 0,
                reward_bundle_json TEXT NOT NULL DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                reward_claimed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quest_progress (
                user_id INTEGER PRIMARY KEY,
                quest_mode TEXT NOT NULL,
                target_key TEXT NOT NULL,
                target_label TEXT NOT NULL DEFAULT '',
                generated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS guilds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lower_name TEXT NOT NULL UNIQUE,
                leader_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                acceptance_mode TEXT NOT NULL DEFAULT 'free_join'
            );

            CREATE TABLE IF NOT EXISTS guild_members (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(id) ON DELETE CASCADE
            );
            

            CREATE TABLE IF NOT EXISTS guild_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER,
                UNIQUE(guild_id, user_id, status),
                FOREIGN KEY (guild_id) REFERENCES guilds(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS guild_bosses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                monster_id TEXT NOT NULL,
                monster_attribute TEXT NOT NULL DEFAULT '',
                monster_types TEXT NOT NULL DEFAULT '',
                max_hp INTEGER NOT NULL,
                current_hp INTEGER NOT NULL,
                atk INTEGER NOT NULL,
                def INTEGER NOT NULL,
                spd INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                defeated_at TEXT,
                FOREIGN KEY (guild_id) REFERENCES guilds(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS guild_boss_participants (
                guild_boss_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                first_participated_at TEXT NOT NULL,
                last_participated_at TEXT NOT NULL,
                total_damage INTEGER NOT NULL DEFAULT 0,
                participation_chances INTEGER NOT NULL DEFAULT 0,
                rewarded_at TEXT,
                PRIMARY KEY (guild_boss_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS guild_boss_reward_history (
                user_id INTEGER PRIMARY KEY,
                first_rewarded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS guild_shop_purchases (
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                purchased_at TEXT NOT NULL,
                PRIMARY KEY (user_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS guild_hall_daily_actions (
                user_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                used_at TEXT NOT NULL,
                PRIMARY KEY (user_id, action_key)
            );

            CREATE TABLE IF NOT EXISTS guild_boss_bonus_rewards (
                guild_boss_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                bonus_key TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                PRIMARY KEY (guild_boss_id, user_id, bonus_key)
            );

            CREATE TABLE IF NOT EXISTS market_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_ref TEXT NOT NULL DEFAULT '',
                monster_attribute TEXT NOT NULL DEFAULT '',
                monster_types TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                sold_at TEXT,
                buyer_user_id INTEGER
            );
            

            CREATE TABLE IF NOT EXISTS duelist_adaptation_memory (
                memory_scope TEXT NOT NULL,
                duelist_key TEXT NOT NULL,
                player_user_id INTEGER NOT NULL,
                losses INTEGER NOT NULL DEFAULT 0,
                counter_tags_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (memory_scope, duelist_key, player_user_id)
            );
            """
        )
        self.conn.commit()
        self.migrate_schema()

    def migrate_schema(self):
        from kuribot.cards.support_cards import (
            canonical_support_card_id, get_support_card_def, support_card_family_key,
        )
        from kuribot.cards.support_decks import decode_support_cards, encode_support_cards
        user_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(users)").fetchall()}
        if user_columns and "card_dust" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN card_dust INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "ranked_wins" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ranked_wins INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "ranked_losses" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ranked_losses INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "ranked_draws" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ranked_draws INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "ranked_elo" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ranked_elo INTEGER NOT NULL DEFAULT 1000")
            self.conn.commit()
        if user_columns and "ranked_peak_elo" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ranked_peak_elo INTEGER NOT NULL DEFAULT 1000")
            self.conn.commit()
        if user_columns and "ranked_rewards_claimed" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ranked_rewards_claimed TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        if user_columns and "last_quest_refresh" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN last_quest_refresh TEXT")
            self.conn.commit()
        if user_columns and "last_monster_shop_refresh" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN last_monster_shop_refresh TEXT")
            self.conn.commit()
        if user_columns and "last_card_shop_refresh" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN last_card_shop_refresh TEXT")
            self.conn.commit()
        if user_columns and "last_equip_shop_refresh" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN last_equip_shop_refresh TEXT")
            self.conn.commit()
        if user_columns and "signature_text" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN signature_text TEXT NOT NULL DEFAULT ''")
            self.conn.commit()

        if user_columns and "ui_theme" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ui_theme TEXT NOT NULL DEFAULT 'gray'")
            self.conn.commit()
        if user_columns and "battle_speed" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN battle_speed TEXT NOT NULL DEFAULT 'normal'")
            self.conn.commit()
        if user_columns and "notifications_enabled" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN notifications_enabled INTEGER NOT NULL DEFAULT 1")
            self.conn.commit()
        if user_columns and "level" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
            self.conn.commit()
        if user_columns and "level_xp" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN level_xp INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "last_message_xp_at" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN last_message_xp_at TEXT")
            self.conn.commit()
        if user_columns and "premium_tickets" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN premium_tickets INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "boss_monster_tickets" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN boss_monster_tickets INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "guild_coins" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN guild_coins INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "arena_tickets" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN arena_tickets INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "equip_spell_tickets" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN equip_spell_tickets INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "tag_image_path" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN tag_image_path TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        if user_columns and "avatar_path" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        if user_columns and "registration_complete" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN registration_complete INTEGER NOT NULL DEFAULT 1")
            self.conn.commit()
        if user_columns and "showcase_unlocked" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN showcase_unlocked INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "ui_theme_unlocked" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN ui_theme_unlocked INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        if user_columns and "campaign_hard_mode_notified" not in user_columns:
            self.conn.execute("ALTER TABLE users ADD COLUMN campaign_hard_mode_notified INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()

        guild_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(guilds)").fetchall()}
        if guild_columns and "acceptance_mode" not in guild_columns:
            self.conn.execute("ALTER TABLE guilds ADD COLUMN acceptance_mode TEXT NOT NULL DEFAULT 'free_join'")
            self.conn.commit()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER,
                UNIQUE(guild_id, user_id, status),
                FOREIGN KEY (guild_id) REFERENCES guilds(id) ON DELETE CASCADE
            )
        """)
        self.conn.commit()

        tag_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(profile_tag_uploads)").fetchall()}
        if not tag_columns:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS profile_tag_uploads (
                    request_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    original_filename TEXT NOT NULL DEFAULT '',
                    pending_image_path TEXT NOT NULL,
                    approved_image_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    submitted_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by INTEGER,
                    admin_message_ids_json TEXT NOT NULL DEFAULT '[]'
                );
            """)
            self.conn.commit()
        else:
            if "guild_id" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN guild_id INTEGER")
                self.conn.commit()
            if "channel_id" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN channel_id INTEGER")
                self.conn.commit()
            if "original_filename" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN original_filename TEXT NOT NULL DEFAULT ''")
                self.conn.commit()
            if "approved_image_path" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN approved_image_path TEXT NOT NULL DEFAULT ''")
                self.conn.commit()
            if "decided_at" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN decided_at TEXT")
                self.conn.commit()
            if "decided_by" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN decided_by INTEGER")
                self.conn.commit()
            if "admin_message_ids_json" not in tag_columns:
                self.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN admin_message_ids_json TEXT NOT NULL DEFAULT '[]'")
                self.conn.commit()

        mailbox_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(mailbox_messages)").fetchall()}
        if mailbox_columns:
            if "sender_id" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN sender_id INTEGER")
                self.conn.commit()
            if "sender_name" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN sender_name TEXT NOT NULL DEFAULT ''")
                self.conn.commit()
            if "reward_type" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN reward_type TEXT")
                self.conn.commit()
            if "reward_amount" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN reward_amount INTEGER NOT NULL DEFAULT 0")
                self.conn.commit()
            if "reward_bundle_json" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN reward_bundle_json TEXT NOT NULL DEFAULT ''")
                self.conn.commit()
            if "is_read" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")
                self.conn.commit()
            if "reward_claimed" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN reward_claimed INTEGER NOT NULL DEFAULT 0")
                self.conn.commit()
            if "created_at" not in mailbox_columns:
                self.conn.execute("ALTER TABLE mailbox_messages ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
                self.conn.commit()

        monster_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(monster_defs)").fetchall()}
        if monster_columns and "monster_id" not in monster_columns:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS monster_defs_new (
                    monster_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    attribute TEXT NOT NULL,
                    hp INTEGER NOT NULL,
                    atk INTEGER NOT NULL,
                    def INTEGER NOT NULL,
                    spd INTEGER NOT NULL,
                    image_path TEXT NOT NULL
                );

                INSERT OR REPLACE INTO monster_defs_new(monster_id, name, attribute, hp, atk, def, spd, image_path)
                SELECT name, name, attribute, hp, atk, def, spd, image_path
                FROM monster_defs;

                DROP TABLE monster_defs;
                ALTER TABLE monster_defs_new RENAME TO monster_defs;
            """)
            self.conn.commit()

        inventory_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(inventory)").fetchall()}
        if inventory_columns and "monster_attribute" not in inventory_columns:
            self.conn.execute("ALTER TABLE inventory ADD COLUMN monster_attribute TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
            self.conn.execute("UPDATE inventory SET monster_attribute = COALESCE((SELECT attribute FROM monster_defs WHERE monster_defs.monster_id = inventory.monster_id), '') WHERE monster_attribute = ''")
            self.conn.commit()

        if inventory_columns and "instance_id" not in inventory_columns:
            legacy_rows = self.conn.execute("SELECT * FROM inventory").fetchall()
            self.conn.executescript("""
                ALTER TABLE inventory RENAME TO inventory_legacy;
                CREATE TABLE inventory (
                    instance_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    monster_id TEXT NOT NULL,
                    monster_attribute TEXT NOT NULL DEFAULT '',
                    monster_types TEXT NOT NULL,
                    obtained_at TEXT NOT NULL,
                    FOREIGN KEY (monster_id) REFERENCES monster_defs(monster_id)
                );
            """)

            def _roll_types() -> str:
                first_type = RNG.choice(MONSTER_TYPES)
                if RNG.random() < SINGLE_TYPE_CHANCE:
                    return first_type
                second_candidates = [monster_type for monster_type in MONSTER_TYPES if monster_type != first_type]
                second_type = RNG.choice(second_candidates) if second_candidates else first_type
                ordered = sorted((first_type, second_type), key=MONSTER_TYPES.index)
                return "|".join(ordered)

            now_iso = datetime.now(timezone.utc).isoformat()
            for row in legacy_rows:
                keys = set(row.keys())
                monster_id = row["monster_id"] if "monster_id" in keys else row["monster_name"]
                qty = row["qty"] if "qty" in keys else 1
                for _ in range(max(0, qty)):
                    self.conn.execute(
                        "INSERT INTO inventory(instance_id, user_id, monster_id, monster_attribute, monster_types, obtained_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (uuid.uuid4().hex, row["user_id"], monster_id, str(row["attribute"]) if "attribute" in keys else "", _roll_types(), now_iso),
                    )
            self.conn.execute("DROP TABLE inventory_legacy")
            self.conn.commit()

        team_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(teams)").fetchall()}
        if team_columns and "support_deck_id" not in team_columns:
            self.conn.execute("ALTER TABLE teams ADD COLUMN support_deck_id INTEGER")
            self.conn.commit()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS team_active_modes (
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                team_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, mode)
            )
        """)
        self.conn.commit()

        team_rows = self.conn.execute("SELECT id, user_id, slots_csv FROM teams").fetchall()
        known_instance_ids = {row["instance_id"] for row in self.conn.execute("SELECT instance_id FROM inventory").fetchall()}
        needs_team_migration = False
        for team in team_rows:
            slots = [part.strip() for part in str(team["slots_csv"]).split(",") if part.strip()]
            if any(slot not in known_instance_ids for slot in slots):
                needs_team_migration = True
                break
        if needs_team_migration:
            for team in team_rows:
                new_slots = []
                slots = [part.strip() for part in str(team["slots_csv"]).split(",") if part.strip()]
                for legacy_monster_id in slots:
                    row = self.conn.execute(
                        "SELECT instance_id FROM inventory WHERE user_id = ? AND monster_id = ? ORDER BY obtained_at ASC, instance_id ASC LIMIT 1",
                        (team["user_id"], legacy_monster_id),
                    ).fetchone()
                    if row:
                        new_slots.append(row["instance_id"])
                self.conn.execute("UPDATE teams SET slots_csv = ? WHERE id = ?", (",".join(new_slots), team["id"]))
            self.conn.commit()

        support_card_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(support_card_inventory)").fetchall()}
        if support_card_columns and "exp" not in support_card_columns:
            self.conn.execute("ALTER TABLE support_card_inventory ADD COLUMN exp INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()

        # Support-card economy migration: generic cards are now single maxed 25% cards,
        # duplicate support cards become Card Dust, and old generic stage variants are removed.
        support_rows = self.conn.execute(
            "SELECT instance_id, user_id, support_card_id, obtained_at FROM support_card_inventory ORDER BY user_id ASC, obtained_at ASC, instance_id ASC"
        ).fetchall()
        seen_support_families: set[tuple[int, str]] = set()
        duplicate_dust_by_user: Dict[int, int] = {}
        valid_support_instance_ids: set[str] = set()
        for row in support_rows:
            user_id = int(row["user_id"])
            instance_id = str(row["instance_id"])
            original_card_id = str(row["support_card_id"])
            canonical_card_id = canonical_support_card_id(original_card_id)
            card_def = get_support_card_def(canonical_card_id)
            is_deprecated_generic = bool(re.match(r"^(.+)_(ATK|DEF|SPD)_(5|10|15|20)$", original_card_id))
            if card_def is None and is_deprecated_generic:
                self.conn.execute("DELETE FROM support_card_inventory WHERE instance_id = ?", (instance_id,))
                continue
            if card_def is None:
                valid_support_instance_ids.add(instance_id)
                continue
            family_key = support_card_family_key(canonical_card_id)
            owned_key = (user_id, family_key)
            if owned_key in seen_support_families:
                duplicate_dust_by_user[user_id] = duplicate_dust_by_user.get(user_id, 0) + SUPPORT_CARD_DUPLICATE_DUST
                self.conn.execute("DELETE FROM support_card_inventory WHERE instance_id = ?", (instance_id,))
                continue
            seen_support_families.add(owned_key)
            valid_support_instance_ids.add(instance_id)
            if original_card_id != canonical_card_id or int(row["user_id"] or 0) >= 0:
                self.conn.execute(
                    "UPDATE support_card_inventory SET support_card_id = ?, exp = 0 WHERE instance_id = ?",
                    (canonical_card_id, instance_id),
                )
        for user_id, dust_amount in duplicate_dust_by_user.items():
            self.conn.execute("UPDATE users SET card_dust = card_dust + ? WHERE user_id = ?", (dust_amount, user_id))
        self.conn.commit()

        support_decks = self.conn.execute("SELECT id, cards_json FROM support_decks").fetchall()
        for deck in support_decks:
            raw_cards = decode_support_cards(deck["cards_json"])
            if not raw_cards:
                continue
            converted_cards: List[str] = []
            seen_card_ids = set()
            needs_update = False
            for raw_card in raw_cards:
                support_card_id: Optional[str] = None
                card_def = get_support_card_def(raw_card)
                if card_def is not None:
                    support_card_id = canonical_support_card_id(card_def.support_card_id)
                else:
                    row = self.conn.execute(
                        "SELECT support_card_id FROM support_card_inventory WHERE instance_id = ? LIMIT 1",
                        (raw_card,),
                    ).fetchone()
                    if row:
                        support_card_id = canonical_support_card_id(str(row["support_card_id"]))
                        needs_update = True
                    else:
                        needs_update = True
                if support_card_id is None:
                    continue
                support_card_id = canonical_support_card_id(support_card_id)
                card_def = get_support_card_def(support_card_id)
                if card_def is None:
                    needs_update = True
                    continue
                family_key = support_card_family_key(support_card_id)
                if family_key in seen_card_ids:
                    needs_update = True
                    continue
                seen_card_ids.add(family_key)
                converted_cards.append(card_def.support_card_id)
            if needs_update or converted_cards != raw_cards:
                self.conn.execute(
                    "UPDATE support_decks SET cards_json = ? WHERE id = ?",
                    (encode_support_cards(converted_cards), deck["id"]),
                )
        card_shop_rows = self.conn.execute("SELECT user_id, slot_index, support_card_id FROM card_shop_offers").fetchall()
        for row in card_shop_rows:
            canonical_id = canonical_support_card_id(str(row["support_card_id"]))
            if get_support_card_def(canonical_id) is None:
                self.conn.execute(
                    "DELETE FROM card_shop_offers WHERE user_id = ? AND slot_index = ?",
                    (row["user_id"], row["slot_index"]),
                )
            elif canonical_id != str(row["support_card_id"]):
                self.conn.execute(
                    "UPDATE card_shop_offers SET support_card_id = ? WHERE user_id = ? AND slot_index = ?",
                    (canonical_id, row["user_id"], row["slot_index"]),
                )
        self.conn.commit()

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tutorial_progress (
                user_id INTEGER PRIMARY KEY,
                summon_done_at TEXT,
                premium_done_at TEXT,
                reward_claimed_at TEXT,
                team_done_at TEXT,
                team_reward_claimed_at TEXT,
                battle_done_at TEXT,
                hearts_done_at TEXT,
                battle_reward_claimed_at TEXT,
                monster_generation_done_at TEXT,
                monster_generation_reward_claimed_at TEXT,
                support_cards_done_at TEXT,
                support_cards_reward_claimed_at TEXT
            )
        """)
        tutorial_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(tutorial_progress)").fetchall()}
        if tutorial_columns and "team_done_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN team_done_at TEXT")
            self.conn.commit()
        if tutorial_columns and "team_reward_claimed_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN team_reward_claimed_at TEXT")
            self.conn.commit()
        if tutorial_columns and "battle_done_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN battle_done_at TEXT")
            self.conn.commit()
        if tutorial_columns and "hearts_done_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN hearts_done_at TEXT")
            self.conn.commit()
        if tutorial_columns and "battle_reward_claimed_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN battle_reward_claimed_at TEXT")
            self.conn.commit()
        if tutorial_columns and "monster_generation_done_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN monster_generation_done_at TEXT")
            self.conn.commit()
        if tutorial_columns and "monster_generation_reward_claimed_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN monster_generation_reward_claimed_at TEXT")
            self.conn.commit()
        if tutorial_columns and "support_cards_done_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN support_cards_done_at TEXT")
            self.conn.commit()
        if tutorial_columns and "support_cards_reward_claimed_at" not in tutorial_columns:
            self.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN support_cards_reward_claimed_at TEXT")
            self.conn.commit()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS quest_progress (
                user_id INTEGER PRIMARY KEY,
                quest_mode TEXT NOT NULL,
                target_key TEXT NOT NULL,
                target_label TEXT NOT NULL DEFAULT '',
                generated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS equip_spell_inventory (
                instance_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                equip_spell_id TEXT NOT NULL,
                obtained_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS monster_equip_spells (
                monster_instance_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                equip_spell_instance_id TEXT NOT NULL UNIQUE,
                equipped_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guilds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lower_name TEXT NOT NULL UNIQUE,
                leader_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_members (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(id) ON DELETE CASCADE
            )
        """)
        guild_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(guilds)").fetchall()}
        if guild_columns and "lower_name" not in guild_columns:
            self.conn.execute("ALTER TABLE guilds ADD COLUMN lower_name TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
            rows = self.conn.execute("SELECT id, name FROM guilds").fetchall()
            for row in rows:
                self.conn.execute("UPDATE guilds SET lower_name = ? WHERE id = ?", (str(row["name"] or "").strip().lower(), int(row["id"])))
            self.conn.commit()
            try:
                self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_guilds_lower_name ON guilds(lower_name)")
                self.conn.commit()
            except Exception:
                pass
        else:
            try:
                self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_guilds_lower_name ON guilds(lower_name)")
                self.conn.commit()
            except Exception:
                pass
        guild_member_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(guild_members)").fetchall()}
        if guild_member_columns and "role" not in guild_member_columns:
            self.conn.execute("ALTER TABLE guild_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
            self.conn.commit()
        if guild_member_columns and "joined_at" not in guild_member_columns:
            self.conn.execute("ALTER TABLE guild_members ADD COLUMN joined_at TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
            self.conn.execute("UPDATE guild_members SET joined_at = ? WHERE joined_at = ''", (datetime.now(timezone.utc).isoformat(),))
            self.conn.commit()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_bosses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                monster_id TEXT NOT NULL,
                monster_attribute TEXT NOT NULL DEFAULT '',
                monster_types TEXT NOT NULL DEFAULT '',
                max_hp INTEGER NOT NULL,
                current_hp INTEGER NOT NULL,
                atk INTEGER NOT NULL,
                def INTEGER NOT NULL,
                spd INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                defeated_at TEXT,
                FOREIGN KEY (guild_id) REFERENCES guilds(id) ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_boss_participants (
                guild_boss_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                first_participated_at TEXT NOT NULL,
                last_participated_at TEXT NOT NULL,
                total_damage INTEGER NOT NULL DEFAULT 0,
                participation_chances INTEGER NOT NULL DEFAULT 0,
                rewarded_at TEXT,
                PRIMARY KEY (guild_boss_id, user_id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_boss_reward_history (
                user_id INTEGER PRIMARY KEY,
                first_rewarded_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_reset_state (
                reset_key TEXT PRIMARY KEY,
                reset_value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
        """)
        self.conn.commit()
        guild_boss_participant_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(guild_boss_participants)").fetchall()}
        if guild_boss_participant_columns and "participation_chances" not in guild_boss_participant_columns:
            self.conn.execute("ALTER TABLE guild_boss_participants ADD COLUMN participation_chances INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_shop_purchases (
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                purchased_at TEXT NOT NULL,
                PRIMARY KEY (user_id, item_key)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_hall_daily_actions (
                user_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                used_at TEXT NOT NULL,
                PRIMARY KEY (user_id, action_key)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_boss_bonus_rewards (
                guild_boss_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                bonus_key TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                PRIMARY KEY (guild_boss_id, user_id, bonus_key)
            )
        """)
        self.conn.commit()
        monster_shop_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(monster_shop_offers)").fetchall()}
        if monster_shop_columns and "monster_attribute" not in monster_shop_columns:
            self.conn.execute("ALTER TABLE monster_shop_offers ADD COLUMN monster_attribute TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
            self.conn.execute("UPDATE monster_shop_offers SET monster_attribute = COALESCE((SELECT attribute FROM monster_defs WHERE monster_defs.monster_id = monster_shop_offers.monster_id), '') WHERE monster_attribute = ''")
            self.conn.commit()

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_ref TEXT NOT NULL DEFAULT '',
                monster_attribute TEXT NOT NULL DEFAULT '',
                monster_types TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                sold_at TEXT,
                buyer_user_id INTEGER
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS duelist_adaptation_memory (
                memory_scope TEXT NOT NULL,
                duelist_key TEXT NOT NULL,
                player_user_id INTEGER NOT NULL,
                losses INTEGER NOT NULL DEFAULT 0,
                counter_tags_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (memory_scope, duelist_key, player_user_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS waiting_list_entries (
                user_id INTEGER PRIMARY KEY,
                entered_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """
        )
        self.conn.commit()

    def ensure_user(self, user_id: int):
        # Called before almost every read/write (100+ call sites). A plain SELECT avoids
        # opening a write transaction + commit every time for players who already exist.
        if self.conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO users(user_id, tickets, premium_tickets, registration_complete) VALUES (?, 0, 0, 0)",
            (user_id,),
        )
        self.conn.commit()

    def has_mail_reward(self, user_id: int, reward_type: str, reward_amount: int, *, sender_name: str = "", body_substring: str = "") -> bool:
        self.ensure_user(user_id)
        sql = (
            "SELECT 1 FROM mailbox_messages "
            "WHERE user_id = ? AND reward_type = ? AND reward_amount = ?"
        )
        params: List[object] = [user_id, reward_type, int(reward_amount)]
        if sender_name:
            sql += " AND sender_name = ?"
            params.append(sender_name)
        if body_substring:
            sql += " AND instr(body, ?) > 0"
            params.append(body_substring)
        sql += " LIMIT 1"
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return bool(row)

    def user_exists(self, user_id: int) -> bool:
        row = self.conn.execute("SELECT 1 FROM users WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
        return bool(row)

    def get_user(self, user_id: int) -> sqlite3.Row:
        self.ensure_user(user_id)
        return self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def get_duelist_adaptation_memory(self, memory_scope: str, duelist_key: str, player_user_id: int) -> dict:
        row = self.conn.execute(
            "SELECT losses, counter_tags_json, updated_at FROM duelist_adaptation_memory WHERE memory_scope = ? AND duelist_key = ? AND player_user_id = ?",
            (str(memory_scope), str(duelist_key), int(player_user_id)),
        ).fetchone()
        counters: Dict[str, int] = {}
        losses = 0
        updated_at = ""
        if row is not None:
            losses = max(0, int(row["losses"] or 0))
            updated_at = str(row["updated_at"] or "")
            try:
                raw = json.loads(str(row["counter_tags_json"] or "{}"))
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        normalized_key = str(key or "").strip().lower()
                        if not normalized_key:
                            continue
                        try:
                            counters[normalized_key] = max(0, int(value))
                        except Exception:
                            continue
            except Exception:
                counters = {}
        return {"losses": losses, "counters": counters, "updated_at": updated_at}

    def record_duelist_adaptation_loss(self, memory_scope: str, duelist_key: str, player_user_id: int, counter_updates: Dict[str, int]):
        existing = self.get_duelist_adaptation_memory(memory_scope, duelist_key, player_user_id)
        merged: Counter[str] = Counter({str(key): max(0, int(value)) for key, value in dict(existing.get("counters", {})).items()})
        for key, value in dict(counter_updates or {}).items():
            normalized_key = str(key or "").strip().lower()
            if not normalized_key:
                continue
            try:
                amount = int(value)
            except Exception:
                continue
            if amount <= 0:
                continue
            merged[normalized_key] += amount
        losses = max(1, int(existing.get("losses", 0)) + 1)
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(dict(merged), sort_keys=True)
        row = self.conn.execute(
            "SELECT 1 FROM duelist_adaptation_memory WHERE memory_scope = ? AND duelist_key = ? AND player_user_id = ? LIMIT 1",
            (str(memory_scope), str(duelist_key), int(player_user_id)),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO duelist_adaptation_memory(memory_scope, duelist_key, player_user_id, losses, counter_tags_json, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(memory_scope), str(duelist_key), int(player_user_id), losses, payload, now_iso),
            )
        else:
            self.conn.execute(
                "UPDATE duelist_adaptation_memory SET losses = ?, counter_tags_json = ?, updated_at = ? WHERE memory_scope = ? AND duelist_key = ? AND player_user_id = ?",
                (losses, payload, now_iso, str(memory_scope), str(duelist_key), int(player_user_id)),
            )
        self.conn.commit()

    def add_message_xp(self, user_id: int, xp_amount: int, *, now: Optional[datetime] = None) -> Optional[dict]:
        from kuribot.players import xp_required_for_level
        self.ensure_user(user_id)
        now = now or utc_now()
        row = self.get_user(user_id)
        current_level = max(1, min(LEVELING_MAX_LEVEL, int(row["level"] or 1)))
        current_xp = max(0, int(row["level_xp"] or 0))
        last_awarded_raw = row["last_message_xp_at"]
        if current_level >= LEVELING_MAX_LEVEL:
            self.conn.execute(
                "UPDATE users SET level = ?, level_xp = 0, last_message_xp_at = ? WHERE user_id = ?",
                (LEVELING_MAX_LEVEL, now.isoformat(), user_id),
            )
            self.conn.commit()
            return None
        if last_awarded_raw:
            try:
                last_awarded_at = datetime.fromisoformat(str(last_awarded_raw))
                if last_awarded_at.tzinfo is None:
                    last_awarded_at = last_awarded_at.replace(tzinfo=timezone.utc)
                if (now - last_awarded_at).total_seconds() < LEVELING_MESSAGE_COOLDOWN_SECONDS:
                    return None
            except Exception:
                pass

        gained_xp = max(1, int(xp_amount))
        new_level = current_level
        new_xp = current_xp + gained_xp
        levels_gained = 0

        while new_level < LEVELING_MAX_LEVEL:
            required_xp = xp_required_for_level(new_level)
            if new_xp < required_xp:
                break
            new_xp -= required_xp
            new_level += 1
            levels_gained += 1

        if new_level >= LEVELING_MAX_LEVEL:
            new_level = LEVELING_MAX_LEVEL
            new_xp = 0

        self.conn.execute(
            "UPDATE users SET level = ?, level_xp = ?, last_message_xp_at = ? WHERE user_id = ?",
            (new_level, new_xp, now.isoformat(), user_id),
        )
        if levels_gained > 0:
            self.conn.execute(
                "UPDATE users SET gold = gold + ? WHERE user_id = ?",
                (LEVELING_REWARD_GOLD * levels_gained, user_id),
            )
        self.conn.commit()
        if levels_gained > 0:
            if hasattr(self, "add_pack"):
                self.add_pack(user_id, "random_themed", LEVELING_REWARD_PACKS * levels_gained)
            else:
                # Legacy fallback for very early startup states before the pack system is attached.
                self.add_tickets(user_id, LEVELING_REWARD_PACKS * levels_gained)
        return {
            "leveled_up": levels_gained > 0,
            "levels_gained": levels_gained,
            "old_level": current_level,
            "new_level": new_level,
            "xp": new_xp,
            "xp_gained": gained_xp,
            "next_level_xp": 0 if new_level >= LEVELING_MAX_LEVEL else xp_required_for_level(new_level),
        }

    def ranked_leaderboard(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM users
            WHERE (ranked_wins + ranked_losses + ranked_draws) > 0
            ORDER BY ranked_elo DESC,
                     ((ranked_wins + (0.5 * ranked_draws) + 1.0) / (ranked_wins + ranked_losses + ranked_draws + 2.0)) DESC,
                     ranked_wins DESC,
                     user_id ASC
            """
        ).fetchall()

    def enter_waiting_list(self, user_id: int) -> bool:
        self.ensure_user(user_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO waiting_list_entries(user_id, entered_at) VALUES (?, ?)",
            (int(user_id), now_iso),
        )
        self.conn.commit()
        return int(getattr(cursor, "rowcount", 0) or 0) > 0

    def waiting_list_entries(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT w.user_id, u.ranked_elo
            FROM waiting_list_entries w
            JOIN users u ON u.user_id = w.user_id
            WHERE u.registration_complete = 1
            ORDER BY w.entered_at ASC, w.user_id ASC
            """
        ).fetchall()

    def set_signature_text(self, user_id: int, text_value: str):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET signature_text = ? WHERE user_id = ?", (text_value, user_id))
        self.conn.commit()

    def get_tag_image_path(self, user_id: int) -> str:
        row = self.get_user(user_id)
        return str(row["tag_image_path"] or "").strip()

    def set_tag_image_path(self, user_id: int, image_path: str):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET tag_image_path = ? WHERE user_id = ?", (str(image_path or "").strip(), user_id))
        self.conn.commit()

    def get_avatar_path(self, user_id: int) -> str:
        row = self.get_user(user_id)
        return str(row["avatar_path"] or "").strip()

    def set_avatar_path(self, user_id: int, avatar_path: str):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET avatar_path = ? WHERE user_id = ?", (str(avatar_path or "").strip(), user_id))
        self.conn.commit()

    def has_showcase_unlocked(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(int(row["showcase_unlocked"] or 0))

    def has_ui_theme_unlocked(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(int(row["ui_theme_unlocked"] or 0))

    def campaign_hard_mode_notification_sent(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(int(row["campaign_hard_mode_notified"] or 0))

    def mark_campaign_hard_mode_notified(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET campaign_hard_mode_notified = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def unlock_profile_customization(self, user_id: int, feature_name: str):
        self.ensure_user(user_id)
        normalized = str(feature_name or "").strip().lower()
        column = {"showcase": "showcase_unlocked", "background": "background_unlocked", "ui_theme": "ui_theme_unlocked"}.get(normalized)
        if column is None:
            raise ValueError(f"Unknown profile customization feature: {feature_name}")
        self.conn.execute(f"UPDATE users SET {column} = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def create_profile_tag_upload(self, request_id: str, user_id: int, pending_image_path: str, *, guild_id: Optional[int] = None, channel_id: Optional[int] = None, original_filename: str = ""):
        self.ensure_user(user_id)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO profile_tag_uploads(request_id, user_id, guild_id, channel_id, original_filename, pending_image_path, approved_image_path, status, submitted_at, decided_at, decided_by, admin_message_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, '', 'pending', ?, NULL, NULL, '[]')
            """,
            (request_id, user_id, guild_id, channel_id, str(original_filename or ''), str(pending_image_path), datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_profile_tag_upload(self, request_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM profile_tag_uploads WHERE request_id = ? LIMIT 1", (request_id,)).fetchone()

    def get_pending_profile_tag_upload_for_user(self, user_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM profile_tag_uploads WHERE user_id = ? AND status = 'pending' ORDER BY submitted_at DESC LIMIT 1", (user_id,)).fetchone()

    def list_pending_profile_tag_uploads(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM profile_tag_uploads WHERE status = 'pending' ORDER BY submitted_at ASC").fetchall()

    def set_profile_tag_admin_message_ids(self, request_id: str, message_ids: Sequence[int]):
        self.conn.execute("UPDATE profile_tag_uploads SET admin_message_ids_json = ? WHERE request_id = ?", (json.dumps([int(mid) for mid in message_ids]), request_id))
        self.conn.commit()

    def resolve_profile_tag_upload(self, request_id: str, status: str, *, decided_by: Optional[int] = None, approved_image_path: str = "") -> bool:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"accepted", "denied"}:
            return False
        cur = self.conn.execute(
            """
            UPDATE profile_tag_uploads
            SET status = ?, decided_at = ?, decided_by = ?, approved_image_path = ?
            WHERE request_id = ? AND status = 'pending'
            """,
            (normalized_status, datetime.now(timezone.utc).isoformat(), decided_by, str(approved_image_path or ""), request_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_ui_theme(self, user_id: int) -> str:
        from kuribot.render.themes import UI_THEME_PALETTES
        row = self.get_user(user_id)
        raw = str(row["ui_theme"] or "gray").strip().lower()
        return raw if raw in UI_THEME_PALETTES else "gray"

    def set_ui_theme(self, user_id: int, theme_name: str):
        from kuribot.render.themes import UI_THEME_PALETTES
        self.ensure_user(user_id)
        normalized = str(theme_name or "gray").strip().lower()
        if normalized not in UI_THEME_PALETTES:
            normalized = "gray"
        self.conn.execute("UPDATE users SET ui_theme = ? WHERE user_id = ?", (normalized, user_id))
        self.conn.commit()

    def get_battle_speed(self, user_id: int) -> str:
        row = self.get_user(user_id)
        raw = str(row["battle_speed"] or "normal").strip().lower()
        return raw if raw in BATTLE_SPEED_LABELS else "normal"

    def set_battle_speed(self, user_id: int, speed_name: str):
        self.ensure_user(user_id)
        normalized = str(speed_name or "normal").strip().lower()
        if normalized not in BATTLE_SPEED_LABELS:
            normalized = "normal"
        self.conn.execute("UPDATE users SET battle_speed = ? WHERE user_id = ?", (normalized, user_id))
        self.conn.commit()

    def notifications_enabled(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        return bool(int(row["notifications_enabled"] or 0))

    def set_notifications_enabled(self, user_id: int, enabled: bool):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET notifications_enabled = ? WHERE user_id = ?", (1 if enabled else 0, user_id))
        self.conn.commit()

    def complete_registration(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET registration_complete = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def get_registered_user_ids(self) -> List[int]:
        rows = self.conn.execute("SELECT user_id FROM users WHERE registration_complete = 1 ORDER BY user_id ASC").fetchall()
        return [int(row["user_id"]) for row in rows]

    def is_registered_user(self, user_id: int) -> bool:
        row = self.conn.execute("SELECT 1 FROM users WHERE user_id = ? AND registration_complete = 1 LIMIT 1", (user_id,)).fetchone()
        return bool(row)

    def create_mail(
        self,
        user_id: int,
        body: str,
        *,
        sender_id: Optional[int] = None,
        sender_name: str = "",
        reward_type: Optional[str] = None,
        reward_amount: int = 0,
        reward_bundle: Optional[Sequence[Dict[str, int | str]]] = None,
        created_at: Optional[str] = None,
    ) -> int:
        self.ensure_user(user_id)
        normalized_bundle: List[Dict[str, int | str]] = []
        for reward in list(reward_bundle or []):
            reward_kind = str(reward.get('type') or '').strip().lower()
            amount = int(reward.get('amount') or 0)
            if reward_kind and amount > 0:
                normalized_reward: Dict[str, int | str] = {'type': reward_kind, 'amount': amount}
                for key, value in dict(reward).items():
                    if key in {'type', 'amount'}:
                        continue
                    if isinstance(value, (str, int)):
                        normalized_reward[str(key)] = value
                normalized_bundle.append(normalized_reward)
        reward_bundle_json = json.dumps(normalized_bundle) if normalized_bundle else ''
        db_reward_type = reward_type
        db_reward_amount = int(reward_amount)
        if normalized_bundle:
            db_reward_type = 'bundle'
            db_reward_amount = len(normalized_bundle)
        cur = self.conn.execute(
            """
            INSERT INTO mailbox_messages(user_id, sender_id, sender_name, body, reward_type, reward_amount, reward_bundle_json, is_read, reward_claimed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (user_id, sender_id, sender_name, body, db_reward_type, db_reward_amount, reward_bundle_json, created_at or datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def user_mailbox(self, user_id: int) -> List[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            "SELECT * FROM mailbox_messages WHERE user_id = ? ORDER BY datetime(created_at) DESC, id DESC",
            (user_id,),
        ).fetchall()

    def get_mail(self, mail_id: int, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        sql = "SELECT * FROM mailbox_messages WHERE id = ?"
        params: List[object] = [mail_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        return self.conn.execute(sql, tuple(params)).fetchone()

    def unread_mail_count(self, user_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM mailbox_messages WHERE user_id = ? AND is_read = 0", (user_id,)).fetchone()
        return int(row["c"] if row else 0)

    def mark_mail_read(self, mail_id: int, user_id: int) -> bool:
        cur = self.conn.execute("UPDATE mailbox_messages SET is_read = 1 WHERE id = ? AND user_id = ?", (mail_id, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    def mark_all_mail_read(self, user_id: int):
        self.conn.execute("UPDATE mailbox_messages SET is_read = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def delete_read_mail(self, user_id: int) -> int:
        # Read mail whose reward was never claimed is kept - "Delete Read" used to wipe it.
        cur = self.conn.execute(
            """
            DELETE FROM mailbox_messages
            WHERE user_id = ? AND is_read = 1
              AND NOT (
                ((COALESCE(reward_type, '') != '' AND COALESCE(reward_amount, 0) > 0) OR COALESCE(reward_bundle_json, '') != '')
                AND COALESCE(reward_claimed, 0) = 0
              )
            """,
            (user_id,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def mark_mail_reward_claimed(self, mail_id: int, user_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE mailbox_messages SET reward_claimed = 1, is_read = 1 WHERE id = ? AND user_id = ? AND ((reward_type IS NOT NULL AND reward_amount > 0) OR reward_bundle_json != '') AND reward_claimed = 0",
            (mail_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def spend_gold(self, user_id: int, amount: int) -> bool:
        self.ensure_user(user_id)
        cost = max(0, int(amount))
        row = self.get_user(user_id)
        current_gold = int(row["gold"] or 0)
        if current_gold < cost:
            return False
        self.conn.execute("UPDATE users SET gold = gold - ? WHERE user_id = ?", (cost, user_id))
        self.conn.commit()
        return True

    def get_guild(self, guild_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM guilds WHERE id = ? LIMIT 1", (guild_id,)).fetchone()

    def get_guild_by_name(self, name: str) -> Optional[sqlite3.Row]:
        normalized = str(name or "").strip().lower()
        if not normalized:
            return None
        return self.conn.execute("SELECT * FROM guilds WHERE lower_name = ? LIMIT 1", (normalized,)).fetchone()

    def get_user_guild_membership(self, user_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT gm.guild_id, gm.user_id, gm.role, gm.joined_at, g.name, g.leader_user_id, g.created_at
            FROM guild_members gm
            JOIN guilds g ON g.id = gm.guild_id
            WHERE gm.user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    def guild_member_count(self, guild_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM guild_members WHERE guild_id = ?", (guild_id,)).fetchone()
        return int(row["c"] if row else 0)

    def guild_officer_count(self, guild_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM guild_members WHERE guild_id = ? AND role = ?", (guild_id, GUILD_ROLE_OFFICER)).fetchone()
        return int(row["c"] if row else 0)

    def list_guilds(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT g.*, COUNT(gm.user_id) AS member_count
            FROM guilds g
            LEFT JOIN guild_members gm ON gm.guild_id = g.id
            GROUP BY g.id
            ORDER BY LOWER(g.name) ASC, g.id ASC
            """
        ).fetchall()

    def list_joinable_guilds_for_user(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT g.*, COUNT(gm.user_id) AS member_count
            FROM guilds g
            LEFT JOIN guild_members gm ON gm.guild_id = g.id
            WHERE g.id NOT IN (SELECT guild_id FROM guild_members WHERE user_id = ?)
            GROUP BY g.id
            HAVING COUNT(gm.user_id) < ?
            ORDER BY LOWER(g.name) ASC, g.id ASC
            """,
            (user_id, GUILD_MAX_MEMBERS),
        ).fetchall()

    def guild_members(self, guild_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT gm.guild_id, gm.user_id, gm.role, gm.joined_at, g.name, g.leader_user_id
            FROM guild_members gm
            JOIN guilds g ON g.id = gm.guild_id
            WHERE gm.guild_id = ?
            ORDER BY CASE gm.role
                WHEN 'leader' THEN 0
                WHEN 'officer' THEN 1
                ELSE 2
            END ASC, datetime(gm.joined_at) ASC, gm.user_id ASC
            """,
            (guild_id,),
        ).fetchall()

    def get_guild_member_role(self, guild_id: int, user_id: int) -> Optional[str]:
        row = self.conn.execute("SELECT role FROM guild_members WHERE guild_id = ? AND user_id = ? LIMIT 1", (int(guild_id), int(user_id))).fetchone()
        if row is None:
            return None
        return str(row["role"] or GUILD_ROLE_MEMBER).strip().lower()

    def get_guild_acceptance_mode(self, guild_id: int) -> str:
        row = self.get_guild(int(guild_id))
        if row is None:
            return GUILD_ACCEPTANCE_FREE_JOIN
        mode = str(row["acceptance_mode"] if "acceptance_mode" in row.keys() else GUILD_ACCEPTANCE_FREE_JOIN).strip().lower()
        return mode if mode in {GUILD_ACCEPTANCE_FREE_JOIN, GUILD_ACCEPTANCE_APPLY} else GUILD_ACCEPTANCE_FREE_JOIN

    def set_guild_acceptance_mode(self, actor_user_id: int, guild_id: int, mode: str) -> tuple[bool, str]:
        actor_role = self.get_guild_member_role(int(guild_id), int(actor_user_id))
        if actor_role not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            return False, "Only the guild leader and officers can change guild acceptance settings."
        normalized = str(mode or "").strip().lower()
        if normalized not in {GUILD_ACCEPTANCE_FREE_JOIN, GUILD_ACCEPTANCE_APPLY}:
            return False, "Invalid guild acceptance option."
        self.conn.execute("UPDATE guilds SET acceptance_mode = ? WHERE id = ?", (normalized, int(guild_id)))
        self.conn.commit()
        return True, f"Guild acceptance is now set to **{GUILD_ACCEPTANCE_LABELS.get(normalized, normalized)}**."

    def submit_guild_application(self, user_id: int, guild_id: int) -> tuple[bool, str]:
        self.ensure_user(int(user_id))
        if self.get_user_guild_membership(int(user_id)):
            return False, "You are already in a guild."
        guild_row = self.get_guild(int(guild_id))
        if guild_row is None:
            return False, "That guild no longer exists."
        if self.guild_member_count(int(guild_id)) >= GUILD_MAX_MEMBERS:
            return False, "That guild is already full."
        existing = self.conn.execute(
            "SELECT id FROM guild_applications WHERE guild_id = ? AND user_id = ? AND status = ? LIMIT 1",
            (int(guild_id), int(user_id), GUILD_APPLICATION_STATUS_PENDING),
        ).fetchone()
        if existing is not None:
            return False, f"You already have a pending application for **{guild_row['name']}**."
        try:
            self.conn.execute(
                "INSERT INTO guild_applications(guild_id, user_id, status, applied_at) VALUES (?, ?, ?, ?)",
                (int(guild_id), int(user_id), GUILD_APPLICATION_STATUS_PENDING, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
            return True, f"Your application to **{guild_row['name']}** has been submitted. Please wait for a leader or officer to approve it."
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return False, f"You already have a pending application for **{guild_row['name']}**."

    def pending_guild_applications(self, guild_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT ga.*, g.name AS guild_name
            FROM guild_applications ga
            JOIN guilds g ON g.id = ga.guild_id
            WHERE ga.guild_id = ? AND ga.status = ?
            ORDER BY datetime(ga.applied_at) ASC, ga.id ASC
            """,
            (int(guild_id), GUILD_APPLICATION_STATUS_PENDING),
        ).fetchall()

    def get_guild_application(self, application_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT ga.*, g.name AS guild_name
            FROM guild_applications ga
            JOIN guilds g ON g.id = ga.guild_id
            WHERE ga.id = ?
            LIMIT 1
            """,
            (int(application_id),),
        ).fetchone()

    def resolve_guild_application(self, actor_user_id: int, application_id: int, accepted: bool) -> tuple[bool, str, Optional[int], Optional[str]]:
        application = self.get_guild_application(int(application_id))
        if application is None or str(application["status"] or "").lower() != GUILD_APPLICATION_STATUS_PENDING:
            return False, "That application is no longer pending.", None, None
        guild_id = int(application["guild_id"])
        applicant_id = int(application["user_id"])
        guild_name = str(application["guild_name"] or "your guild")
        actor_role = self.get_guild_member_role(guild_id, int(actor_user_id))
        if actor_role not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            return False, "Only the guild leader and officers can review guild applications.", applicant_id, guild_name
        now_iso = datetime.now(timezone.utc).isoformat()
        if accepted:
            if self.get_user_guild_membership(applicant_id):
                self.conn.execute(
                    "UPDATE guild_applications SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                    (GUILD_APPLICATION_STATUS_DENIED, now_iso, int(actor_user_id), int(application_id)),
                )
                self.conn.commit()
                return False, "That applicant is already in a guild, so the application was closed.", applicant_id, guild_name
            if self.guild_member_count(guild_id) >= GUILD_MAX_MEMBERS:
                return False, "That guild is already full.", applicant_id, guild_name
            try:
                self.conn.execute(
                    "INSERT INTO guild_members(guild_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (guild_id, applicant_id, GUILD_ROLE_MEMBER, now_iso),
                )
                self.conn.execute(
                    "UPDATE guild_applications SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                    (GUILD_APPLICATION_STATUS_ACCEPTED, now_iso, int(actor_user_id), int(application_id)),
                )
                self.conn.commit()
                return True, f"Accepted <@{applicant_id}> into **{guild_name}**.", applicant_id, guild_name
            except sqlite3.IntegrityError:
                self.conn.rollback()
                return False, "That applicant is already in a guild.", applicant_id, guild_name
        self.conn.execute(
            "UPDATE guild_applications SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (GUILD_APPLICATION_STATUS_DENIED, now_iso, int(actor_user_id), int(application_id)),
        )
        self.conn.commit()
        return True, f"Denied <@{applicant_id}>'s application to **{guild_name}**.", applicant_id, guild_name

    def create_guild(self, leader_user_id: int, guild_name: str) -> tuple[bool, str, Optional[int]]:
        self.ensure_user(leader_user_id)
        clean_name = re.sub(r"\s+", " ", str(guild_name or "")).strip()
        if len(clean_name) < GUILD_NAME_MIN_LENGTH or len(clean_name) > GUILD_NAME_MAX_LENGTH:
            return False, f"Guild name must be between {GUILD_NAME_MIN_LENGTH} and {GUILD_NAME_MAX_LENGTH} characters.", None
        if self.get_user_guild_membership(leader_user_id):
            return False, "You are already in a guild.", None
        if self.get_guild_by_name(clean_name):
            return False, "That guild name is already taken.", None
        if not self.spend_gold(leader_user_id, GUILD_CREATE_COST):
            return False, f"You need {GUILD_CREATE_COST:,} gold to create a guild.", None
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            cur = self.conn.execute(
                "INSERT INTO guilds(name, lower_name, leader_user_id, created_at) VALUES (?, ?, ?, ?)",
                (clean_name, clean_name.lower(), leader_user_id, created_at),
            )
            guild_id = int(cur.lastrowid)
            self.conn.execute(
                "INSERT INTO guild_members(guild_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                (guild_id, leader_user_id, GUILD_ROLE_LEADER, created_at),
            )
            self.conn.commit()
            return True, f"Guild **{clean_name}** created successfully.", guild_id
        except sqlite3.IntegrityError:
            self.conn.rollback()
            self.add_gold(leader_user_id, GUILD_CREATE_COST)
            return False, "That guild name is already taken.", None

    def join_guild(self, user_id: int, guild_id: int) -> tuple[bool, str]:
        self.ensure_user(user_id)
        if self.get_user_guild_membership(user_id):
            return False, "You are already in a guild."
        guild_row = self.get_guild(guild_id)
        if guild_row is None:
            return False, "That guild no longer exists."
        if self.guild_member_count(guild_id) >= GUILD_MAX_MEMBERS:
            return False, "That guild is already full."
        if self.get_guild_acceptance_mode(guild_id) == GUILD_ACCEPTANCE_APPLY:
            return self.submit_guild_application(user_id, guild_id)
        joined_at = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                "INSERT INTO guild_members(guild_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                (guild_id, user_id, GUILD_ROLE_MEMBER, joined_at),
            )
            self.conn.commit()
            return True, f"You joined **{guild_row['name']}**."
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return False, "You are already in a guild."

    def set_guild_member_role(self, guild_id: int, user_id: int, role: str) -> tuple[bool, str]:
        normalized = str(role or "").strip().lower()
        if normalized not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER, GUILD_ROLE_MEMBER}:
            return False, "Invalid guild role."
        guild_row = self.get_guild(guild_id)
        if guild_row is None:
            return False, "That guild no longer exists."
        member_row = self.conn.execute("SELECT * FROM guild_members WHERE guild_id = ? AND user_id = ? LIMIT 1", (guild_id, user_id)).fetchone()
        if member_row is None:
            return False, "That player is not in the guild."
        if normalized == GUILD_ROLE_LEADER:
            return False, "Guild leaders cannot be changed here."
        if int(guild_row["leader_user_id"] or 0) == int(user_id):
            return False, "You cannot change the guild leader's role."
        if normalized == GUILD_ROLE_OFFICER and self.guild_officer_count(guild_id) >= GUILD_MAX_OFFICERS and str(member_row["role"] or "") != GUILD_ROLE_OFFICER:
            return False, f"A guild can only have {GUILD_MAX_OFFICERS} officers."
        self.conn.execute("UPDATE guild_members SET role = ? WHERE guild_id = ? AND user_id = ?", (normalized, guild_id, user_id))
        self.conn.commit()
        return True, "Guild role updated."

    def rename_guild(self, leader_user_id: int, guild_id: int, new_name: str) -> tuple[bool, str]:
        self.ensure_user(leader_user_id)
        clean_name = re.sub(r"\s+", " ", str(new_name or "")).strip()
        if len(clean_name) < GUILD_NAME_MIN_LENGTH or len(clean_name) > GUILD_NAME_MAX_LENGTH:
            return False, f"Guild name must be between {GUILD_NAME_MIN_LENGTH} and {GUILD_NAME_MAX_LENGTH} characters."
        guild_row = self.get_guild(guild_id)
        if guild_row is None:
            return False, "That guild no longer exists."
        if int(guild_row["leader_user_id"] or 0) != int(leader_user_id):
            return False, "Only the guild leader can rename the guild."
        existing = self.get_guild_by_name(clean_name)
        if existing is not None and int(existing["id"]) != int(guild_id):
            return False, "That guild name is already taken."
        try:
            self.conn.execute(
                "UPDATE guilds SET name = ?, lower_name = ? WHERE id = ?",
                (clean_name, clean_name.lower(), guild_id),
            )
            self.conn.commit()
            return True, f"Guild renamed to **{clean_name}**."
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return False, "That guild name is already taken."

    def leave_guild(self, user_id: int) -> tuple[bool, str]:
        self.ensure_user(user_id)
        membership = self.get_user_guild_membership(user_id)
        if not membership:
            return False, "You are not in a guild."
        guild_id = int(membership["guild_id"])
        guild_name = str(membership["name"] or "your guild")
        role = str(membership["role"] or GUILD_ROLE_MEMBER)
        member_count = self.guild_member_count(guild_id)
        if role == GUILD_ROLE_LEADER:
            if member_count > 1:
                return False, "Guild leaders cannot leave while other members are still in the guild. Kick members first, then leave to disband it."
            self.conn.execute("DELETE FROM guild_boss_participants WHERE guild_id = ?", (guild_id,))
            self.conn.execute("DELETE FROM guild_bosses WHERE guild_id = ?", (guild_id,))
            self.conn.execute("DELETE FROM guild_applications WHERE guild_id = ?", (guild_id,))
            self.conn.execute("DELETE FROM guild_members WHERE guild_id = ?", (guild_id,))
            self.conn.execute("DELETE FROM guilds WHERE id = ?", (guild_id,))
            self.conn.commit()
            return True, f"You left and disbanded **{guild_name}**."
        self.conn.execute("DELETE FROM guild_members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        self.conn.commit()
        return True, f"You left **{guild_name}**."

    def disband_guild(self, leader_user_id: int) -> tuple[bool, str]:
        self.ensure_user(leader_user_id)
        membership = self.get_user_guild_membership(leader_user_id)
        if not membership:
            return False, "You are not in a guild."
        guild_id = int(membership["guild_id"])
        guild_name = str(membership["name"] or "your guild")
        if str(membership["role"] or GUILD_ROLE_MEMBER) != GUILD_ROLE_LEADER:
            return False, "Only the guild leader can disband the guild."
        self.conn.execute("DELETE FROM guild_boss_participants WHERE guild_id = ?", (guild_id,))
        self.conn.execute("DELETE FROM guild_bosses WHERE guild_id = ?", (guild_id,))
        self.conn.execute("DELETE FROM guild_applications WHERE guild_id = ?", (guild_id,))
        self.conn.execute("DELETE FROM guild_members WHERE guild_id = ?", (guild_id,))
        self.conn.execute("DELETE FROM guilds WHERE id = ?", (guild_id,))
        self.conn.commit()
        return True, f"Disbanded **{guild_name}**."

    def kick_guild_member(self, actor_user_id: int, target_user_id: int) -> tuple[bool, str]:
        self.ensure_user(actor_user_id)
        actor_membership = self.get_user_guild_membership(actor_user_id)
        if not actor_membership:
            return False, "You are not in a guild."
        guild_id = int(actor_membership["guild_id"])
        actor_role = str(actor_membership["role"] or GUILD_ROLE_MEMBER)
        if actor_role not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            return False, "Only the guild leader and officers can kick members."
        if int(actor_user_id) == int(target_user_id):
            return False, "Use Leave Guild if you want to remove yourself."
        target_row = self.conn.execute(
            "SELECT * FROM guild_members WHERE guild_id = ? AND user_id = ? LIMIT 1",
            (guild_id, int(target_user_id)),
        ).fetchone()
        if target_row is None:
            return False, "That player is not in your guild."
        target_role = str(target_row["role"] or GUILD_ROLE_MEMBER)
        if target_role == GUILD_ROLE_LEADER:
            return False, "The guild leader cannot be kicked."
        if actor_role == GUILD_ROLE_OFFICER and target_role != GUILD_ROLE_MEMBER:
            return False, "Officers can only kick regular members."
        self.conn.execute(
            "DELETE FROM guild_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, int(target_user_id)),
        )
        self.conn.commit()
        return True, f"Removed <@{int(target_user_id)}> from the guild."

    def add_boss_monster_tickets(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        amount = int(amount)
        if amount > 0 and hasattr(self, "add_pack"):
            self.add_pack(user_id, "boss_themed", amount)
            return
        self.conn.execute("UPDATE users SET boss_monster_tickets = MAX(0, boss_monster_tickets + ?) WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def add_guild_coins(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET guild_coins = guild_coins + ? WHERE user_id = ?", (int(amount), user_id))
        self.conn.commit()

    def add_arena_tickets(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET arena_tickets = MAX(0, arena_tickets + ?) WHERE user_id = ?", (int(amount), int(user_id)))
        self.conn.commit()

    def spend_arena_ticket(self, user_id: int, amount: int = 1) -> bool:
        self.ensure_user(user_id)
        row = self.get_user(user_id)
        if int(row["arena_tickets"] or 0) < int(amount):
            return False
        self.conn.execute("UPDATE users SET arena_tickets = arena_tickets - ? WHERE user_id = ?", (int(amount), int(user_id)))
        self.conn.commit()
        return True

    def add_equip_spell_tickets(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET equip_spell_tickets = MAX(0, equip_spell_tickets + ?) WHERE user_id = ?", (int(amount), int(user_id)))
        self.conn.commit()

    def spend_equip_spell_ticket(self, user_id: int, amount: int = 1) -> bool:
        self.ensure_user(user_id)
        row = self.get_user(user_id)
        if int(row["equip_spell_tickets"] or 0) < int(amount):
            return False
        self.conn.execute("UPDATE users SET equip_spell_tickets = equip_spell_tickets - ? WHERE user_id = ?", (int(amount), int(user_id)))
        self.conn.commit()
        return True

    def is_guild_shop_item_sold_out(self, user_id: int, item_key: str, now_utc: Optional[datetime] = None) -> bool:
        self.ensure_user(user_id)
        row = self.conn.execute(
            "SELECT purchased_at FROM guild_shop_purchases WHERE user_id = ? AND item_key = ?",
            (int(user_id), str(item_key)),
        ).fetchone()
        return bool(row and is_current_reset_window(str(row["purchased_at"] or ""), now_utc))

    def mark_guild_shop_item_purchased(self, user_id: int, item_key: str):
        self.ensure_user(user_id)
        now_iso = utc_now().isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO guild_shop_purchases(user_id, item_key, purchased_at) VALUES (?, ?, ?)",
            (int(user_id), str(item_key), now_iso),
        )
        self.conn.commit()

    def buy_guild_shop_item(self, user_id: int, item_key: str) -> Tuple[bool, str]:
        self.ensure_user(user_id)
        item = GUILD_SHOP_ITEM_BY_KEY.get(str(item_key))
        if item is None:
            return False, "That guild shop item is not available."
        if self.is_guild_shop_item_sold_out(user_id, str(item_key)):
            return False, f"**{item['name']}** is sold out until the next shop refresh."
        user_row = self.get_user(user_id)
        cost = int(item["cost"])
        if int(user_row["guild_coins"] or 0) < cost:
            return False, f"You need **{cost} guild coins** to buy **{item['name']}**."
        self.conn.execute("UPDATE users SET guild_coins = guild_coins - ? WHERE user_id = ?", (cost, int(user_id)))
        if item_key == "guild_boss_ticket":
            membership = self.get_user_guild_membership(user_id)
            if membership:
                boss = self.get_or_create_guild_boss(int(membership["guild_id"]), allow_respawn=True)
                if boss is not None:
                    now_iso = utc_now().isoformat()
                    self.conn.execute(
                        """
                        INSERT INTO guild_boss_participants(guild_boss_id, guild_id, user_id, first_participated_at, last_participated_at, total_damage, participation_chances)
                        VALUES (?, ?, ?, ?, ?, 0, 0)
                        ON CONFLICT(guild_boss_id, user_id) DO UPDATE SET participation_chances = 0
                        """,
                        (int(boss["id"]), int(membership["guild_id"]), int(user_id), now_iso, now_iso),
                    )
        elif item_key == "guild_boss_challenge":
            membership = self.get_user_guild_membership(user_id)
            if membership:
                boss = self.get_or_create_guild_boss(int(membership["guild_id"]))
                if boss is not None:
                    self.conn.execute(
                        """
                        INSERT INTO guild_boss_participants(guild_boss_id, guild_id, user_id, first_participated_at, last_participated_at, total_damage, participation_chances)
                        VALUES (?, ?, ?, ?, ?, 0, -1)
                        ON CONFLICT(guild_boss_id, user_id) DO UPDATE SET participation_chances = participation_chances - 1
                        """,
                        (int(boss["id"]), int(membership["guild_id"]), int(user_id), utc_now().isoformat(), utc_now().isoformat()),
                    )
        elif item_key == "equip_spell_ticket":
            self.conn.execute("UPDATE users SET equip_spell_tickets = equip_spell_tickets + 1 WHERE user_id = ?", (int(user_id),))
        elif item_key == "arena_tickets":
            self.conn.execute("UPDATE users SET arena_tickets = arena_tickets + ? WHERE user_id = ?", (GUILD_SHOP_ARENA_TICKET_AMOUNT, int(user_id)))
        self.conn.execute(
            "INSERT OR REPLACE INTO guild_shop_purchases(user_id, item_key, purchased_at) VALUES (?, ?, ?)",
            (int(user_id), str(item_key), utc_now().isoformat()),
        )
        self.conn.commit()
        return True, f"Purchased **{item['name']}** for **{cost} guild coins**."

    def has_used_guild_hall_daily_action(self, user_id: int, action_key: str, now_utc: Optional[datetime] = None) -> bool:
        self.ensure_user(user_id)
        row = self.conn.execute(
            "SELECT used_at FROM guild_hall_daily_actions WHERE user_id = ? AND action_key = ? LIMIT 1",
            (int(user_id), str(action_key)),
        ).fetchone()
        return bool(row and is_same_eastern_calendar_day(str(row["used_at"] or ""), now_utc))

    def claim_daily_guild_coins(self, user_id: int) -> Tuple[bool, str]:
        self.ensure_user(user_id)
        if self.has_used_guild_hall_daily_action(user_id, "claim"):
            return False, f"You already claimed today's free guild coins. Try again in **{eastern_midnight_countdown_text()}**."
        self.conn.execute(
            "UPDATE users SET guild_coins = guild_coins + ? WHERE user_id = ?",
            (GUILD_DAILY_CLAIM_COINS, int(user_id)),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO guild_hall_daily_actions(user_id, action_key, used_at) VALUES (?, ?, ?)",
            (int(user_id), "claim", utc_now().isoformat()),
        )
        self.conn.commit()
        return True, f"Claimed **{GUILD_DAILY_CLAIM_COINS} guild coins**. You can claim again after **12:00 AM ET**."

    def donate_gold_for_guild_coins(self, user_id: int, donation_key: str) -> Tuple[bool, str]:
        self.ensure_user(user_id)
        option = GUILD_DONATION_OPTION_BY_KEY.get(str(donation_key))
        if option is None:
            return False, "That donation option is not available."
        if self.has_used_guild_hall_daily_action(user_id, "donate"):
            return False, f"Your next donation is available in **{eastern_midnight_countdown_text()}**."
        user_row = self.get_user(user_id)
        gold_cost = int(option["gold"])
        coin_reward = int(option["guild_coins"])
        if int(user_row["gold"] or 0) < gold_cost:
            return False, f"You need **{gold_cost:,} gold** to donate for **{coin_reward} guild coins**."
        self.conn.execute(
            "UPDATE users SET gold = gold - ?, guild_coins = guild_coins + ? WHERE user_id = ?",
            (gold_cost, coin_reward, int(user_id)),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO guild_hall_daily_actions(user_id, action_key, used_at) VALUES (?, ?, ?)",
            (int(user_id), "donate", utc_now().isoformat()),
        )
        self.conn.commit()
        return True, f"Donated **{gold_cost:,} gold** and received **{coin_reward} guild coins**. You can donate again after **12:00 AM ET**."

    def grant_guild_boss_bonus_reward(self, guild_boss_id: int, user_id: int, bonus_key: str, amount: int) -> bool:
        self.ensure_user(user_id)
        now_iso = utc_now().isoformat()
        try:
            self.conn.execute(
                "INSERT INTO guild_boss_bonus_rewards(guild_boss_id, user_id, bonus_key, awarded_at) VALUES (?, ?, ?, ?)",
                (int(guild_boss_id), int(user_id), str(bonus_key), now_iso),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.execute(
            "UPDATE users SET guild_coins = guild_coins + ? WHERE user_id = ?",
            (int(amount), int(user_id)),
        )
        self.conn.commit()
        return True

    def active_guild_boss(self, guild_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT gb.*, md.name AS monster_name, md.image_path, md.attribute AS base_attribute
            FROM guild_bosses gb
            JOIN monster_defs md ON md.monster_id = gb.monster_id
            WHERE gb.guild_id = ? AND gb.defeated_at IS NULL AND gb.current_hp > 0
            ORDER BY gb.id DESC LIMIT 1
            """,
            (int(guild_id),),
        ).fetchone()

    def latest_guild_boss_defeated_at(self, guild_id: int) -> str:
        row = self.conn.execute(
            """
            SELECT defeated_at
            FROM guild_bosses
            WHERE guild_id = ? AND defeated_at IS NOT NULL
            ORDER BY defeated_at DESC LIMIT 1
            """,
            (int(guild_id),),
        ).fetchone()
        return str(row["defeated_at"] or "") if row else ""

    def guild_boss_defeated_today(self, guild_id: int, now_utc: Optional[datetime] = None) -> bool:
        latest_defeated_at = self.latest_guild_boss_defeated_at(guild_id)
        if not is_same_eastern_calendar_day(latest_defeated_at, now_utc):
            return False

        # A Guild Boss Ticket/admin reset creates a fresh active boss after the
        # previous defeat. Do not keep blocking the guild just because an older
        # defeated boss row still exists for today's reward/history records.
        active = self.active_guild_boss(guild_id)
        if active is not None:
            try:
                defeated_at = datetime.fromisoformat(str(latest_defeated_at))
                active_created_at = datetime.fromisoformat(str(active["created_at"] or ""))
                if defeated_at.tzinfo is None:
                    defeated_at = defeated_at.replace(tzinfo=timezone.utc)
                if active_created_at.tzinfo is None:
                    active_created_at = active_created_at.replace(tzinfo=timezone.utc)
                if active_created_at > defeated_at:
                    return False
            except Exception:
                pass
        return True

    def create_guild_boss(self, guild_id: int, monster_row: sqlite3.Row, monster_types: Sequence[str]) -> Optional[sqlite3.Row]:
        from kuribot.monsters import (
            encode_monster_types, random_monster_attribute_for_row, resolve_row_combat_stats,
        )
        attribute = random_monster_attribute_for_row(monster_row)
        hp, atk, defense, spd = resolve_row_combat_stats(monster_row, monster_types)
        max_hp = max(1, int(hp) * GUILD_BOSS_HP_MULTIPLIER)
        boss_atk = max(1, int(atk) * GUILD_BOSS_ATK_MULTIPLIER)
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO guild_bosses(guild_id, monster_id, monster_attribute, monster_types, max_hp, current_hp, atk, def, spd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(guild_id), str(monster_row["monster_id"]), attribute, encode_monster_types(monster_types), max_hp, max_hp, boss_atk, int(defense), int(spd), now_iso),
        )
        self.conn.commit()
        return self.active_guild_boss(guild_id)

    def get_or_create_guild_boss(self, guild_id: int, allow_respawn: bool = False) -> Optional[sqlite3.Row]:
        from kuribot.monsters import is_boss_monster_row, roll_monster_types_for_row
        active = self.active_guild_boss(guild_id)
        if active is not None:
            return active
        # Once a guild's boss is defeated, the next one only appears after the 12:00 AM ET
        # reset (or when a Guild Boss Ticket is used). Previously simply opening the boss
        # panel spawned a fresh boss right away, so bosses could be farmed all day.
        if not allow_respawn and is_same_eastern_calendar_day(self.latest_guild_boss_defeated_at(guild_id), None):
            latest = self.conn.execute(
                """
                SELECT gb.*, md.name AS monster_name, md.image_path, md.attribute AS base_attribute
                FROM guild_bosses gb
                JOIN monster_defs md ON md.monster_id = gb.monster_id
                WHERE gb.guild_id = ?
                ORDER BY gb.id DESC LIMIT 1
                """,
                (int(guild_id),),
            ).fetchone()
            if latest is not None:
                return latest
        boss_pool = [row for row in self.all_monsters() if is_boss_monster_row(row)]
        if not boss_pool:
            boss_pool = list(self.all_monsters())
        if not boss_pool:
            return None
        monster_row = RNG.choice(boss_pool)
        monster_types = roll_monster_types_for_row(monster_row)
        return self.create_guild_boss(guild_id, monster_row, monster_types)

    def refresh_guild_boss(self, guild_id: int) -> Optional[sqlite3.Row]:
        from kuribot.monsters import is_boss_monster_row, roll_monster_types_for_row
        active = self.active_guild_boss(guild_id)
        previous_monster_id = str(active["monster_id"]) if active is not None else ""
        boss_pool = [row for row in self.all_monsters() if is_boss_monster_row(row)]
        if not boss_pool:
            boss_pool = list(self.all_monsters())
        if not boss_pool:
            return None
        if previous_monster_id and len(boss_pool) > 1:
            alternate_pool = [row for row in boss_pool if str(row["monster_id"]) != previous_monster_id]
            if alternate_pool:
                boss_pool = alternate_pool
        if active is not None:
            active_id = int(active["id"])
            self.conn.execute("DELETE FROM guild_boss_participants WHERE guild_boss_id = ?", (active_id,))
            self.conn.execute("DELETE FROM guild_bosses WHERE id = ?", (active_id,))
            self.conn.commit()
        monster_row = RNG.choice(boss_pool)
        monster_types = roll_monster_types_for_row(monster_row)
        return self.create_guild_boss(guild_id, monster_row, monster_types)

    def get_bot_reset_state(self, reset_key: str) -> str:
        row = self.conn.execute(
            "SELECT reset_value FROM bot_reset_state WHERE reset_key = ? LIMIT 1",
            (str(reset_key),),
        ).fetchone()
        return str(row["reset_value"] or "") if row else ""

    def reset_guild_boss_attempts_for_window(self, reset_window_start_utc: datetime) -> Tuple[int, bool]:
        if reset_window_start_utc.tzinfo is None:
            reset_window_start_utc = reset_window_start_utc.replace(tzinfo=timezone.utc)
        reset_window_start_utc = reset_window_start_utc.astimezone(timezone.utc).replace(microsecond=0)
        window_marker = reset_window_start_utc.isoformat()
        state_key = "guild_boss_attempts_reset_window_start_utc"
        if self.get_bot_reset_state(state_key) == window_marker:
            return 0, False
        cursor = self.conn.execute(
            "UPDATE guild_boss_participants SET participation_chances = 0 WHERE participation_chances != 0"
        )
        changed = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        self.conn.execute(
            """
            INSERT INTO bot_reset_state(reset_key, reset_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(reset_key) DO UPDATE SET
                reset_value = excluded.reset_value,
                updated_at = excluded.updated_at
            """,
            (state_key, window_marker, utc_now().isoformat()),
        )
        self.conn.commit()
        return changed, True

    def reset_guild_boss_attempts_for_guild(self, guild_id: int) -> Dict[str, int]:
        guild_id = int(guild_id)
        member_row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM guild_members WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        member_count = int(member_row["c"] if member_row else 0)
        participant_row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM guild_boss_participants
            WHERE guild_id = ?
              AND user_id IN (SELECT user_id FROM guild_members WHERE guild_id = ?)
            """,
            (guild_id, guild_id),
        ).fetchone()
        participant_rows = int(participant_row["c"] if participant_row else 0)
        cursor = self.conn.execute(
            """
            UPDATE guild_boss_participants
            SET participation_chances = 0
            WHERE guild_id = ?
              AND participation_chances != 0
              AND user_id IN (SELECT user_id FROM guild_members WHERE guild_id = ?)
            """,
            (guild_id, guild_id),
        )
        reset_rows = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        self.conn.commit()
        return {
            "member_count": member_count,
            "participant_rows": participant_rows,
            "reset_rows": reset_rows,
        }

    def get_guild_boss_participation_chances(self, guild_boss_id: int, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT participation_chances FROM guild_boss_participants WHERE guild_boss_id = ? AND user_id = ? LIMIT 1",
            (int(guild_boss_id), int(user_id)),
        ).fetchone()
        return int(row["participation_chances"] or 0) if row else 0

    def record_guild_boss_damage(self, guild_boss_id: int, guild_id: int, user_id: int, damage: int):
        self.ensure_user(user_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO guild_boss_participants(guild_boss_id, guild_id, user_id, first_participated_at, last_participated_at, total_damage, participation_chances)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(guild_boss_id, user_id) DO UPDATE SET
                last_participated_at = excluded.last_participated_at,
                total_damage = total_damage + excluded.total_damage,
                participation_chances = participation_chances + 1
            """,
            (int(guild_boss_id), int(guild_id), int(user_id), now_iso, now_iso, max(0, int(damage))),
        )
        self.conn.execute(
            "UPDATE users SET guild_coins = guild_coins + ? WHERE user_id = ?",
            (GUILD_BOSS_ATTEMPT_GUILD_COIN_REWARD, int(user_id)),
        )
        self.conn.commit()

    def get_guild_boss_leaderboard(self, guild_boss_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT user_id, total_damage, first_participated_at, last_participated_at
            FROM guild_boss_participants
            WHERE guild_boss_id = ?
            ORDER BY total_damage DESC, last_participated_at ASC, user_id ASC
            """,
            (int(guild_boss_id),),
        ).fetchall()


    def top_guild_boss_damage_participant(self, guild_boss_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT user_id, total_damage, first_participated_at, last_participated_at
            FROM guild_boss_participants
            WHERE guild_boss_id = ?
            ORDER BY total_damage DESC, last_participated_at ASC, user_id ASC
            LIMIT 1
            """,
            (int(guild_boss_id),),
        ).fetchone()

    def apply_guild_boss_damage(self, guild_boss_id: int, damage: int) -> Tuple[Optional[sqlite3.Row], bool]:
        """Subtract damage from the boss's CURRENT hp and report whether THIS hit defeated it.

        Writing an absolute HP from a snapshot taken at battle start let two members who
        fought at the same time erase each other's damage (or even heal a dead boss).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.conn.execute(
                "UPDATE guild_bosses SET current_hp = MAX(0, current_hp - ?) WHERE id = ? AND defeated_at IS NULL",
                (max(0, int(damage)), int(guild_boss_id)),
            )
            cur = self.conn.execute(
                "UPDATE guild_bosses SET defeated_at = ? WHERE id = ? AND current_hp <= 0 AND defeated_at IS NULL",
                (now_iso, int(guild_boss_id)),
            )
            killed_now = int(cur.rowcount or 0) == 1
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM guild_bosses WHERE id = ?", (int(guild_boss_id),)).fetchone()
        return row, killed_now

    def update_guild_boss_hp(self, guild_boss_id: int, current_hp: int) -> Optional[sqlite3.Row]:
        hp = max(0, int(current_hp))
        defeated_at = datetime.now(timezone.utc).isoformat() if hp <= 0 else None
        self.conn.execute("UPDATE guild_bosses SET current_hp = ?, defeated_at = COALESCE(defeated_at, ?) WHERE id = ?", (hp, defeated_at, int(guild_boss_id)))
        self.conn.commit()
        return self.conn.execute("SELECT * FROM guild_bosses WHERE id = ?", (int(guild_boss_id),)).fetchone()

    def unrewarded_guild_boss_participants(self, guild_boss_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT p.* FROM guild_boss_participants p
            WHERE p.guild_boss_id = ? AND p.rewarded_at IS NULL AND p.total_damage > 0
              AND EXISTS (SELECT 1 FROM guild_members gm WHERE gm.guild_id = p.guild_id AND gm.user_id = p.user_id)
            ORDER BY p.first_participated_at ASC, p.user_id ASC
            """,
            (int(guild_boss_id),),
        ).fetchall()

    def grant_guild_boss_defeat_reward(self, guild_boss_id: int, user_id: int) -> str:
        self.ensure_user(user_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        claimed = self.conn.execute(
            "UPDATE guild_boss_participants SET rewarded_at = ? WHERE guild_boss_id = ? AND user_id = ? AND rewarded_at IS NULL",
            (now_iso, int(guild_boss_id), int(user_id)),
        )
        if int(claimed.rowcount or 0) != 1:
            self.conn.commit()
            return "already rewarded"
        already_first = self.conn.execute("SELECT 1 FROM guild_boss_reward_history WHERE user_id = ? LIMIT 1", (int(user_id),)).fetchone()
        if already_first is None:
            self.conn.execute("INSERT OR IGNORE INTO guild_boss_reward_history(user_id, first_rewarded_at) VALUES (?, ?)", (int(user_id), now_iso))
            self.conn.execute(
                "UPDATE users SET guild_coins = guild_coins + ? WHERE user_id = ?",
                (GUILD_BOSS_DEFEAT_GUILD_COIN_REWARD, int(user_id)),
            )
            if hasattr(self, "add_pack"):
                self.add_pack(int(user_id), "boss_themed", 1)
            reward_text = f"1 Extra Deck Themed Pack and {GUILD_BOSS_DEFEAT_GUILD_COIN_REWARD} guild coins"
        else:
            self.conn.execute("UPDATE users SET gold = gold + ?, guild_coins = guild_coins + ? WHERE user_id = ?", (GUILD_BOSS_DEFEAT_GOLD_REWARD, GUILD_BOSS_DEFEAT_GUILD_COIN_REWARD, int(user_id)))
            if hasattr(self, "add_pack"):
                self.add_pack(int(user_id), "boss_themed", 1)
            reward_text = f"1 Extra Deck Themed Pack, {GUILD_BOSS_DEFEAT_GOLD_REWARD:,} gold, and {GUILD_BOSS_DEFEAT_GUILD_COIN_REWARD} guild coins"
        self.conn.execute("UPDATE guild_boss_participants SET rewarded_at = ? WHERE guild_boss_id = ? AND user_id = ?", (now_iso, int(guild_boss_id), int(user_id)))
        self.conn.commit()
        return reward_text

    def add_tickets(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        amount = int(amount)
        if amount > 0 and hasattr(self, "add_pack"):
            # Legacy ticket rewards now pay Random Packs 1:1.
            self.add_pack(user_id, "random_themed", amount)
            return
        self.conn.execute("UPDATE users SET tickets = MAX(0, tickets + ?) WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def add_premium_tickets(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        amount = int(amount)
        if amount > 0 and hasattr(self, "add_pack"):
            # Random Pack rewards now pay Random Packs 1:1.
            self.add_pack(user_id, "random_themed", amount)
            return
        self.conn.execute("UPDATE users SET premium_tickets = MAX(0, premium_tickets + ?) WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def get_tutorial_progress(self, user_id: int) -> sqlite3.Row:
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.commit()
        return self.conn.execute("SELECT * FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()

    def mark_tutorial_summon_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET summon_done_at = COALESCE(summon_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def mark_tutorial_premium_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET premium_done_at = COALESCE(premium_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def claim_tutorial_ticket_reward(self, user_id: int, amount: int = 10) -> bool:
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        row = self.conn.execute("SELECT reward_claimed_at FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None and row["reward_claimed_at"]:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE tutorial_progress SET reward_claimed_at = ? WHERE user_id = ?", (now_iso, user_id))
        self.conn.commit()
        self.add_pack(user_id, "random_themed", int(amount))
        return True

    def mark_tutorial_team_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET team_done_at = COALESCE(team_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def claim_tutorial_team_ticket_reward(self, user_id: int, amount: int = 10) -> bool:
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        row = self.conn.execute("SELECT team_reward_claimed_at FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None and row["team_reward_claimed_at"]:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE tutorial_progress SET team_reward_claimed_at = ? WHERE user_id = ?", (now_iso, user_id))
        self.conn.commit()
        self.add_pack(user_id, "random_themed", int(amount))
        return True

    def mark_tutorial_battle_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET battle_done_at = COALESCE(battle_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def mark_tutorial_hearts_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET hearts_done_at = COALESCE(hearts_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def claim_tutorial_battle_ticket_reward(self, user_id: int, amount: int = 10) -> bool:
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        row = self.conn.execute("SELECT battle_reward_claimed_at FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None and row["battle_reward_claimed_at"]:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE tutorial_progress SET battle_reward_claimed_at = ? WHERE user_id = ?", (now_iso, user_id))
        self.conn.commit()
        self.add_pack(user_id, "random_themed", int(amount))
        return True

    def mark_tutorial_monster_generation_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET monster_generation_done_at = COALESCE(monster_generation_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def claim_tutorial_monster_generation_premium_reward(self, user_id: int, amount: int = 3) -> bool:
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        row = self.conn.execute("SELECT monster_generation_reward_claimed_at FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None and row["monster_generation_reward_claimed_at"]:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE tutorial_progress SET monster_generation_done_at = COALESCE(monster_generation_done_at, ?), monster_generation_reward_claimed_at = ? WHERE user_id = ?", (now_iso, now_iso, user_id))
        self.conn.commit()
        self.add_pack(user_id, "random_themed", int(amount))
        return True

    def mark_tutorial_support_cards_done(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        self.conn.execute(
            "UPDATE tutorial_progress SET support_cards_done_at = COALESCE(support_cards_done_at, ?) WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    def claim_tutorial_support_cards_reward(self, user_id: int, amount: int = 30) -> bool:
        from kuribot.cards.support_cards import random_support_card
        self.ensure_user(user_id)
        self.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
        row = self.conn.execute("SELECT support_cards_reward_claimed_at FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()
        if row is not None and row["support_cards_reward_claimed_at"]:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE tutorial_progress SET support_cards_done_at = COALESCE(support_cards_done_at, ?), support_cards_reward_claimed_at = ? WHERE user_id = ?", (now_iso, now_iso, user_id))
        for _ in range(max(0, int(amount))):
            self.add_support_card_to_inventory(user_id, random_support_card().support_card_id, obtained_at=now_iso)
        self.conn.commit()
        return True

    def add_gold(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET gold = gold + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def add_card_dust(self, user_id: int, amount: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET card_dust = card_dust + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def add_win(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET wins = wins + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def add_loss(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET losses = losses + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def update_ranked_record(self, user_id: int, outcome: str):
        self.ensure_user(user_id)
        normalized = str(outcome or '').strip().lower()
        column = {'win': 'ranked_wins', 'loss': 'ranked_losses', 'draw': 'ranked_draws'}.get(normalized)
        if column is None:
            return
        self.conn.execute(f"UPDATE users SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def add_ranked_elo_delta(self, user_id: int, delta: int) -> int:
        """Apply a rating change to the player's CURRENT rating and return the new rating.

        The old code wrote an absolute rating computed from values read minutes earlier,
        so two overlapping ranked results for the same player erased each other.
        """
        self.ensure_user(user_id)
        with self.lock:
            self.conn.execute(
                "UPDATE users SET ranked_elo = MAX(0, ranked_elo + ?) WHERE user_id = ?",
                (int(delta), int(user_id)),
            )
            self.conn.execute(
                "UPDATE users SET ranked_peak_elo = MAX(COALESCE(ranked_peak_elo, 0), ranked_elo) WHERE user_id = ?",
                (int(user_id),),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT ranked_elo FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
        return int(row["ranked_elo"] or 0) if row else 0

    def set_ranked_elo(self, user_id: int, elo: int):
        self.ensure_user(user_id)
        elo_value = max(0, int(round(elo)))
        self.conn.execute(
            "UPDATE users SET ranked_elo = ?, ranked_peak_elo = MAX(ranked_peak_elo, ?) WHERE user_id = ?",
            (elo_value, elo_value, user_id),
        )
        self.conn.commit()

    def reset_all_ranked_elo(self) -> int:
        cursor = self.conn.execute(
            "UPDATE users SET ranked_elo = ?, ranked_peak_elo = ?, ranked_wins = 0, ranked_losses = 0, ranked_draws = 0, ranked_rewards_claimed = ''",
            (RANKED_STARTING_ELO, RANKED_STARTING_ELO),
        )
        self.conn.commit()
        return int(getattr(cursor, 'rowcount', 0) or 0)

    def ranked_reward_thresholds_claimed(self, user_id: int) -> set[int]:
        row = self.get_user(user_id)
        raw = str(row['ranked_rewards_claimed'] or '').strip()
        if not raw:
            return set()
        claimed = set()
        for part in raw.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                claimed.add(int(part))
            except Exception:
                continue
        return claimed

    def mark_ranked_reward_thresholds_claimed(self, user_id: int, thresholds: Sequence[int]):
        self.ensure_user(user_id)
        claimed = self.ranked_reward_thresholds_claimed(user_id)
        claimed.update(int(value) for value in thresholds)
        serialized = ','.join(str(value) for value in sorted(claimed))
        self.conn.execute("UPDATE users SET ranked_rewards_claimed = ? WHERE user_id = ?", (serialized, user_id))
        self.conn.commit()

    def users_with_active_teams(self, exclude_user_id: Optional[int] = None, mode: str = "ranked") -> List[int]:
        mode = self.normalize_team_mode(mode)
        sql = "SELECT DISTINCT user_id FROM team_active_modes WHERE mode = ?"
        params: List[object] = [mode]
        if exclude_user_id is not None:
            sql += " AND user_id != ?"
            params.append(exclude_user_id)
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [int(row['user_id']) for row in rows]

    def try_claim_daily(self, user_id: int) -> Tuple[bool, Optional[timedelta]]:
        self.ensure_user(user_id)
        row = self.get_user(user_id)
        now = utc_now()
        if is_current_reset_window(row["last_daily"], now):
            return False, time_until_next_reset(now)
        self.conn.execute(
            "UPDATE users SET arena_tickets = arena_tickets + ?, last_daily = ? WHERE user_id = ?",
            (DAILY_ARENA_TICKETS, now.isoformat(), user_id),
        )
        self.conn.commit()
        try:
            self.add_pack(user_id, "random_themed", PACKS_PER_DAILY)
        except AttributeError:
            pass
        return True, None

    def get_current_quest_row(self, user_id: int) -> Optional[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            "SELECT user_id, quest_mode, target_key, target_label, generated_at, completed_at FROM quest_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    def clear_quest(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("DELETE FROM quest_progress WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def set_current_quest(self, user_id: int, quest_mode: str, target_key: str, target_label: str, generated_at: Optional[datetime] = None):
        self.ensure_user(user_id)
        stamped_at = (generated_at or utc_now()).isoformat()
        self.conn.execute(
            """
            INSERT INTO quest_progress(user_id, quest_mode, target_key, target_label, generated_at, completed_at)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                quest_mode = excluded.quest_mode,
                target_key = excluded.target_key,
                target_label = excluded.target_label,
                generated_at = excluded.generated_at,
                completed_at = NULL
            """,
            (user_id, str(quest_mode or '').strip().lower(), str(target_key or '').strip(), str(target_label or '').strip(), stamped_at),
        )
        self.conn.execute("UPDATE users SET last_quest_refresh = ? WHERE user_id = ?", (stamped_at, user_id))
        self.conn.commit()

    def mark_quest_completed(self, user_id: int, completed_at: Optional[datetime] = None) -> bool:
        self.ensure_user(user_id)
        row = self.get_current_quest_row(user_id)
        if row is None or row["completed_at"]:
            return False
        stamped_at = (completed_at or utc_now()).isoformat()
        self.conn.execute("UPDATE quest_progress SET completed_at = ? WHERE user_id = ?", (stamped_at, user_id))
        self.conn.commit()
        return True

    def can_refresh_quest(self, user_id: int, now: Optional[datetime] = None) -> bool:
        row = self.get_user(user_id)
        return not is_current_reset_window(row["last_quest_refresh"], now)

    def _shop_refresh_column(self, shop_kind: str) -> str:
        normalized = str(shop_kind or '').strip().lower()
        mapping = {
            'monster': 'last_monster_shop_refresh',
            'card': 'last_card_shop_refresh',
            'equip': 'last_equip_shop_refresh',
        }
        column = mapping.get(normalized)
        if column is None:
            raise ValueError(f"Unknown shop refresh kind: {shop_kind}")
        return column

    def can_use_shop_refresh(self, user_id: int, shop_kind: str, now: Optional[datetime] = None) -> bool:
        row = self.get_user(user_id)
        column = self._shop_refresh_column(shop_kind)
        return not is_current_reset_window(row[column], now)

    def mark_shop_refresh_used(self, user_id: int, shop_kind: str, used_at: Optional[datetime] = None):
        self.ensure_user(user_id)
        stamped_at = (used_at or utc_now()).isoformat()
        column = self._shop_refresh_column(shop_kind)
        self.conn.execute(f"UPDATE users SET {column} = ? WHERE user_id = ?", (stamped_at, user_id))
        self.conn.commit()

    def try_use_shop_refresh(self, user_id: int, shop_kind: str) -> Tuple[bool, Optional[timedelta]]:
        self.ensure_user(user_id)
        now = utc_now()
        row = self.get_user(user_id)
        column = self._shop_refresh_column(shop_kind)
        if is_current_reset_window(row[column], now):
            return False, time_until_next_reset(now)
        self.conn.execute(f"UPDATE users SET {column} = ? WHERE user_id = ?", (now.isoformat(), user_id))
        self.conn.commit()
        return True, None

    def try_use_paid_shop_refresh(self, user_id: int, shop_kind: str, cost: int = SHOP_PAID_REFRESH_COST) -> bool:
        self.ensure_user(user_id)
        row = self.get_user(user_id)
        if int(row["gold"] or 0) < int(cost):
            return False
        column = self._shop_refresh_column(shop_kind)
        now = utc_now().isoformat()
        self.conn.execute(f"UPDATE users SET gold = gold - ?, {column} = ? WHERE user_id = ?", (int(cost), now, user_id))
        self.conn.commit()
        return True

    def upsert_monster_def(self, monster: MonsterDef):
        self.conn.execute(
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
            """,
            (monster.monster_id, monster.name, monster.attribute, monster.hp, monster.atk, monster.defense, monster.spd, monster.image_path),
        )
        self.conn.commit()

    def all_monsters(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM monster_defs ORDER BY name COLLATE NOCASE, attribute COLLATE NOCASE").fetchall()

    def get_monster(self, name: str, attribute: Optional[str] = None) -> Optional[sqlite3.Row]:
        if attribute:
            return self.conn.execute(
                "SELECT * FROM monster_defs WHERE name = ? AND attribute = ?",
                (name, attribute),
            ).fetchone()
        return self.conn.execute(
            "SELECT * FROM monster_defs WHERE name = ? ORDER BY attribute COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()

    def get_monster_by_id(self, monster_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM monster_defs WHERE monster_id = ?", (monster_id,)).fetchone()

    def _normalize_monster_types_for_storage(self, monster_types: Sequence[str]) -> Tuple[str, ...]:
        cleaned = [str(monster_type or "").strip() for monster_type in monster_types if str(monster_type or "").strip()]
        unique_types = list(dict.fromkeys(cleaned))
        return tuple(sorted(unique_types, key=lambda entry: MONSTER_TYPES.index(entry) if entry in MONSTER_TYPES else entry.lower()))

    def add_monster_to_inventory(self, user_id: int, monster_id: str, monster_types: Sequence[str], obtained_at: Optional[str] = None, monster_attribute: Optional[str] = None) -> Dict[str, object]:
        from kuribot.monsters import random_monster_attribute_for_row
        self.ensure_user(user_id)
        monster_row = self.get_monster_by_id(monster_id)
        resolved_attribute = str(monster_attribute or "").strip().upper()
        if not resolved_attribute:
            if monster_row is not None:
                resolved_attribute = random_monster_attribute_for_row(monster_row)
            else:
                resolved_attribute = RNG.choice(ATTRIBUTES)
        normalized_types = self._normalize_monster_types_for_storage(monster_types)
        stored_types = "|".join(normalized_types)
        duplicate_row = self.conn.execute(
            """
            SELECT i.instance_id
            FROM inventory i
            WHERE i.user_id = ? AND i.monster_id = ? AND i.monster_attribute = ? AND i.monster_types = ?
            LIMIT 1
            """,
            (user_id, monster_id, resolved_attribute, stored_types),
        ).fetchone()
        if duplicate_row is not None:
            self.conn.commit()
            return {
                "instance_id": None,
                "is_duplicate": True,
                "premium_tickets_awarded": 0,
                "monster_types": normalized_types,
                "monster_attribute": resolved_attribute,
            }

        instance_id = uuid.uuid4().hex
        self.conn.execute(
            """
            INSERT INTO inventory(instance_id, user_id, monster_id, monster_attribute, monster_types, obtained_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (instance_id, user_id, monster_id, resolved_attribute, stored_types, obtained_at or datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return {
            "instance_id": instance_id,
            "is_duplicate": False,
            "premium_tickets_awarded": 0,
            "monster_types": normalized_types,
            "monster_attribute": resolved_attribute,
        }

    def get_inventory_instance(self, instance_id: str, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        sql = """
            SELECT i.instance_id, i.user_id, i.monster_id, i.monster_types, i.obtained_at,
                   m.name AS monster_name, COALESCE(NULLIF(i.monster_attribute, ''), m.attribute) AS attribute, m.hp, m.atk, m.def, m.spd, m.image_path
            FROM inventory i
            JOIN monster_defs m ON m.monster_id = i.monster_id
            WHERE i.instance_id = ?
        """
        params: List[object] = [instance_id]
        if user_id is not None:
            sql += " AND i.user_id = ?"
            params.append(user_id)
        return self.conn.execute(sql, tuple(params)).fetchone()

    def user_inventory(self, user_id: int) -> List[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            """
            SELECT i.instance_id, i.monster_types, i.obtained_at,
                   m.monster_id, m.name AS monster_name, COALESCE(NULLIF(i.monster_attribute, ''), m.attribute) AS attribute, m.hp, m.atk, m.def, m.spd, m.image_path
            FROM inventory i
            JOIN monster_defs m ON m.monster_id = i.monster_id
            WHERE i.user_id = ?
            ORDER BY m.name COLLATE NOCASE, i.obtained_at ASC, i.instance_id ASC
            """,
            (user_id,),
        ).fetchall()

    def user_has_special_monster_compensation_mail(self, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM mailbox_messages WHERE user_id = ? AND instr(body, ?) > 0 LIMIT 1",
            (user_id, SPECIAL_MONSTER_REMOVAL_BODY_MARKER),
        ).fetchone()
        return bool(row)

    def cleanup_special_monsters_and_compensate(self) -> int:
        """Legacy no-op kept for startup compatibility.

        Extra Deck Monsters should remain in user collections.
        The previous cleanup flow removed them on startup and sent compensation mail.
        That behavior is permanently disabled.
        """
        return 0

    def owns_monster(self, user_id: int, instance_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND instance_id = ?",
            (user_id, instance_id),
        ).fetchone()
        return bool(row)

    def create_team(self, user_id: int, name: str, members: Sequence[str], support_deck_id: Optional[int] = None) -> int:
        from kuribot.monsters import boss_or_signature_team_conflict
        self.ensure_user(user_id)
        conflict = boss_or_signature_team_conflict(user_id, members)
        if conflict:
            raise ValueError(conflict)
        duplicate_spell_id = self._team_duplicate_equip_spell_name(user_id, members)
        if duplicate_spell_id:
            raise ValueError(f"duplicate_equip_spell:{duplicate_spell_id}")
        deck_id = int(support_deck_id) if support_deck_id is not None else None
        if deck_id is not None and self.get_support_deck(user_id, deck_id) is None:
            deck_id = None
        cur = self.conn.execute(
            "INSERT INTO teams(user_id, name, slots_csv, is_active, support_deck_id) VALUES (?, ?, ?, 0, ?)",
            (user_id, name, ",".join(members), deck_id),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def list_teams(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM teams WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()

    def get_team(self, user_id: int, team_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM teams WHERE user_id = ? AND id = ? LIMIT 1",
            (user_id, team_id),
        ).fetchone()

    def update_team_name(self, user_id: int, team_id: int, name: str) -> bool:
        cur = self.conn.execute(
            "UPDATE teams SET name = ? WHERE user_id = ? AND id = ?",
            (name, user_id, team_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_team_members(self, user_id: int, team_id: int, members: Sequence[str]) -> bool:
        from kuribot.monsters import boss_or_signature_team_conflict
        conflict = boss_or_signature_team_conflict(user_id, members)
        if conflict:
            raise ValueError(conflict)
        duplicate_spell_id = self._team_duplicate_equip_spell_name(user_id, members)
        if duplicate_spell_id:
            raise ValueError(f"duplicate_equip_spell:{duplicate_spell_id}")
        cur = self.conn.execute(
            "UPDATE teams SET slots_csv = ? WHERE user_id = ? AND id = ?",
            (",".join(members), user_id, team_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_team(self, user_id: int, team_id: int) -> bool:
        self.conn.execute("DELETE FROM team_active_modes WHERE user_id = ? AND team_id = ?", (user_id, team_id))
        cur = self.conn.execute("DELETE FROM teams WHERE user_id = ? AND id = ?", (user_id, team_id))
        self.conn.commit()
        return cur.rowcount > 0

    def normalize_team_mode(self, mode: str) -> str:
        normalized = str(mode or "campaign").strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {"arena": "arena_offense", "arena_offense_team": "arena_offense", "guild": "guild_boss", "boss": "guild_boss"}
        normalized = aliases.get(normalized, normalized)
        return normalized if normalized in TEAM_ACTIVE_MODES else "campaign"

    def update_team_deck(self, user_id: int, team_id: int, support_deck_id: Optional[int]) -> bool:
        deck_id = int(support_deck_id) if support_deck_id is not None else None
        if deck_id is not None and self.get_support_deck(user_id, deck_id) is None:
            return False
        cur = self.conn.execute(
            "UPDATE teams SET support_deck_id = ? WHERE user_id = ? AND id = ?",
            (deck_id, user_id, team_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def _validate_team_for_active_modes(self, user_id: int, team_id: int) -> Optional[sqlite3.Row]:
        from kuribot.monsters import boss_or_signature_team_conflict
        target_team = self.get_team(user_id, team_id)
        if target_team is None:
            return None
        members = [part.strip() for part in str(target_team["slots_csv"]).split(",") if part.strip()]
        conflict = boss_or_signature_team_conflict(user_id, members)
        if conflict:
            raise ValueError(conflict)
        duplicate_spell_id = self._team_duplicate_equip_spell_name(user_id, members)
        if duplicate_spell_id:
            raise ValueError(f"duplicate_equip_spell:{duplicate_spell_id}")
        return target_team

    def set_active_team(self, user_id: int, team_id: int, mode: str = "campaign") -> bool:
        mode = self.normalize_team_mode(mode)
        if self._validate_team_for_active_modes(user_id, team_id) is None:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO team_active_modes(user_id, mode, team_id, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, mode, team_id, datetime.now(timezone.utc).isoformat()),
        )
        self._sync_legacy_team_active_flag(user_id)
        self.conn.commit()
        return True

    def set_team_active_modes(self, user_id: int, team_id: int, modes: Sequence[str]) -> bool:
        if self.get_team(user_id, team_id) is None:
            return False
        normalized_modes: List[str] = []
        for raw_mode in modes:
            mode = self.normalize_team_mode(raw_mode)
            if mode not in normalized_modes:
                normalized_modes.append(mode)
        if normalized_modes and self._validate_team_for_active_modes(user_id, team_id) is None:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "DELETE FROM team_active_modes WHERE user_id = ? AND team_id = ?",
            (user_id, team_id),
        )
        for mode in normalized_modes:
            self.conn.execute(
                "INSERT OR REPLACE INTO team_active_modes(user_id, mode, team_id, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, mode, team_id, now_iso),
            )
        self._sync_legacy_team_active_flag(user_id)
        self.conn.commit()
        return True

    def _sync_legacy_team_active_flag(self, user_id: int):
        # The old teams.is_active column is kept only as a coarse UI/backward-compatibility flag.
        # Gameplay must use team_active_modes so battle modes never fall back across each other.
        self.conn.execute("UPDATE teams SET is_active = 0 WHERE user_id = ?", (user_id,))
        self.conn.execute(
            """
            UPDATE teams
            SET is_active = 1
            WHERE user_id = ?
              AND id IN (SELECT DISTINCT team_id FROM team_active_modes WHERE user_id = ?)
            """,
            (user_id, user_id),
        )

    def get_active_team(self, user_id: int, mode: str = "campaign") -> Optional[sqlite3.Row]:
        mode = self.normalize_team_mode(mode)
        return self.conn.execute(
            """
            SELECT t.* FROM team_active_modes a
            JOIN teams t ON t.id = a.team_id AND t.user_id = a.user_id
            WHERE a.user_id = ? AND a.mode = ?
            LIMIT 1
            """,
            (user_id, mode),
        ).fetchone()

    def get_team_active_modes(self, user_id: int, team_id: int) -> List[str]:
        rows = self.conn.execute(
            "SELECT mode FROM team_active_modes WHERE user_id = ? AND team_id = ? ORDER BY mode ASC",
            (user_id, team_id),
        ).fetchall()
        return [str(row["mode"]) for row in rows]

    def get_support_deck_for_team(self, user_id: int, team_row: Optional[sqlite3.Row | dict]) -> Optional[sqlite3.Row]:
        if not team_row:
            return None
        try:
            deck_id = team_row["support_deck_id"]
        except Exception:
            deck_id = None
        if deck_id is None:
            return None
        try:
            return self.get_support_deck(user_id, int(deck_id))
        except Exception:
            return None
    def add_support_card_to_inventory(self, user_id: int, support_card_id: str, obtained_at: Optional[str] = None, exp: int = 0) -> str:
        from kuribot.cards.support_cards import canonical_support_card_id, support_card_family_key
        self.ensure_user(user_id)
        canonical_id = canonical_support_card_id(str(support_card_id))
        family_key = support_card_family_key(canonical_id)
        existing = self.get_support_card_instance_by_family(user_id, family_key)
        if existing is not None:
            self.conn.execute("UPDATE users SET card_dust = card_dust + ? WHERE user_id = ?", (SUPPORT_CARD_DUPLICATE_DUST, user_id))
            self.conn.commit()
            return str(existing["instance_id"])
        instance_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO support_card_inventory(instance_id, user_id, support_card_id, exp, obtained_at) VALUES (?, ?, ?, ?, ?)",
            (instance_id, user_id, canonical_id, 0, obtained_at or datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return instance_id

    def user_support_cards(self, user_id: int) -> List[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            "SELECT instance_id, user_id, support_card_id, exp, obtained_at FROM support_card_inventory WHERE user_id = ? ORDER BY obtained_at ASC, instance_id ASC",
            (user_id,),
        ).fetchall()

    def get_support_card_instance(self, instance_id: str, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        sql = "SELECT instance_id, user_id, support_card_id, exp, obtained_at FROM support_card_inventory WHERE instance_id = ?"
        params: List[object] = [instance_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        return self.conn.execute(sql, tuple(params)).fetchone()

    def get_support_card_instance_by_family(self, user_id: int, family_key: str) -> Optional[sqlite3.Row]:
        from kuribot.cards.support_cards import support_card_family_key
        rows = self.user_support_cards(user_id)
        for row in rows:
            if support_card_family_key(str(row["support_card_id"])) == family_key:
                return row
        return None

    def set_support_card_progress(self, instance_id: str, new_support_card_id: str, exp: int):
        self.conn.execute(
            "UPDATE support_card_inventory SET support_card_id = ?, exp = ? WHERE instance_id = ?",
            (new_support_card_id, max(0, int(exp)), instance_id),
        )
        self.conn.commit()

    def create_support_deck(self, user_id: int, name: str, card_instance_ids: Sequence[str]) -> bool:
        from kuribot.cards.support_decks import (
            encode_support_cards, unique_support_card_ids_from_instance_ids,
        )
        self.ensure_user(user_id)
        support_card_ids = unique_support_card_ids_from_instance_ids(user_id, card_instance_ids)
        if not (SUPPORT_DECK_MIN_SIZE <= len(support_card_ids) <= SUPPORT_DECK_SIZE):
            return False
        normalized_name = str(name or "").strip()
        if len(normalized_name) < 2:
            return False
        self.conn.execute(
            "INSERT INTO support_decks(user_id, name, cards_json, is_active) VALUES (?, ?, ?, 0)",
            (user_id, normalized_name, encode_support_cards(support_card_ids)),
        )
        self.conn.commit()
        return True

    def list_support_decks(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM support_decks WHERE user_id = ? ORDER BY id ASC", (user_id,)).fetchall()

    def get_support_deck(self, user_id: int, deck_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM support_decks WHERE user_id = ? AND id = ? LIMIT 1",
            (user_id, deck_id),
        ).fetchone()

    def update_support_deck_name(self, user_id: int, deck_id: int, name: str) -> bool:
        normalized_name = str(name or "").strip()
        if len(normalized_name) < 2:
            return False
        cur = self.conn.execute(
            "UPDATE support_decks SET name = ? WHERE user_id = ? AND id = ?",
            (normalized_name, user_id, deck_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_support_deck_cards(self, user_id: int, deck_id: int, card_instance_ids: Sequence[str]) -> bool:
        from kuribot.cards.support_decks import (
            encode_support_cards, unique_support_card_ids_from_instance_ids,
        )
        support_card_ids = unique_support_card_ids_from_instance_ids(user_id, card_instance_ids)
        if not (SUPPORT_DECK_MIN_SIZE <= len(support_card_ids) <= SUPPORT_DECK_SIZE):
            return False
        cur = self.conn.execute(
            "UPDATE support_decks SET cards_json = ? WHERE user_id = ? AND id = ?",
            (encode_support_cards(support_card_ids), user_id, deck_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_support_deck(self, user_id: int, deck_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM support_decks WHERE user_id = ? AND id = ?", (user_id, deck_id))
        if cur.rowcount > 0:
            self.conn.execute("UPDATE teams SET support_deck_id = NULL WHERE user_id = ? AND support_deck_id = ?", (user_id, deck_id))
        self.conn.commit()
        return cur.rowcount > 0

    def set_active_support_deck(self, user_id: int, deck_id: int) -> bool:
        decks = self.list_support_decks(user_id)
        if not any(deck["id"] == deck_id for deck in decks):
            return False
        self.conn.execute("UPDATE support_decks SET is_active = 0 WHERE user_id = ?", (user_id,))
        self.conn.execute("UPDATE support_decks SET is_active = 1 WHERE user_id = ? AND id = ?", (user_id, deck_id))
        self.conn.commit()
        return True

    def get_active_support_deck(self, user_id: int, mode: str = "campaign") -> Optional[sqlite3.Row]:
        team_row = self.get_active_team(user_id, mode)
        deck_row = self.get_support_deck_for_team(user_id, team_row)
        if deck_row is not None:
            return deck_row
        return self.conn.execute(
            "SELECT * FROM support_decks WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    def clear_monster_shop_offers(self: Database, user_id: int):
        self.conn.execute("DELETE FROM pack_shop_offers WHERE user_id = ?", (int(user_id),))
        self.conn.commit()

    def clear_card_shop_offers(self, user_id: int):
        self.conn.execute("DELETE FROM card_shop_offers WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def clear_equip_shop_offers(self, user_id: int):
        self.conn.execute("DELETE FROM equip_shop_offers WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def reset_user_shops(self, user_id: int):
        self.clear_monster_shop_offers(user_id)
        self.clear_card_shop_offers(user_id)
        self.clear_equip_shop_offers(user_id)

    def get_monster_shop_offers(self: Database, user_id: int) -> List[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            """
            SELECT user_id, slot_index, pack_key, pack_name, theme_kind, theme_value, generated_at, purchased
            FROM pack_shop_offers
            WHERE user_id = ?
            ORDER BY slot_index ASC
            """,
            (int(user_id),),
        ).fetchall()

    def save_monster_shop_offer(self: Database, user_id: int, slot_index: int, pack_key: str, generated_at: Optional[str] = None, *args, **kwargs):
        self.ensure_user(user_id)
        pack_key = normalize_pack_key(pack_key) or "random_themed"
        pack_name, theme_kind, theme_value = PACK_DEFS[pack_key]
        self.conn.execute(
            """
            INSERT OR REPLACE INTO pack_shop_offers(user_id, slot_index, pack_key, pack_name, theme_kind, theme_value, generated_at, purchased)
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT purchased FROM pack_shop_offers WHERE user_id = ? AND slot_index = ?), 0))
            """,
            (int(user_id), int(slot_index), pack_key, pack_name, theme_kind, theme_value, generated_at or datetime.now(timezone.utc).isoformat(), int(user_id), int(slot_index)),
        )
        self.conn.commit()

    def mark_monster_shop_offer_purchased(self: Database, user_id: int, slot_index: int):
        self.conn.execute("UPDATE pack_shop_offers SET purchased = 1 WHERE user_id = ? AND slot_index = ?", (int(user_id), int(slot_index)))
        self.conn.commit()

    def get_card_shop_offers(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT user_id, slot_index, support_card_id, generated_at, purchased
            FROM card_shop_offers
            WHERE user_id = ?
            ORDER BY slot_index ASC
            """,
            (user_id,),
        ).fetchall()

    def save_card_shop_offer(self, user_id: int, slot_index: int, support_card_id: str, generated_at: Optional[str] = None):
        self.ensure_user(user_id)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO card_shop_offers(user_id, slot_index, support_card_id, generated_at, purchased)
            VALUES (?, ?, ?, ?, COALESCE((SELECT purchased FROM card_shop_offers WHERE user_id = ? AND slot_index = ?), 0))
            """,
            (user_id, slot_index, support_card_id, generated_at or datetime.now(timezone.utc).isoformat(), user_id, slot_index),
        )
        self.conn.commit()

    def mark_card_shop_offer_purchased(self, user_id: int, slot_index: int):
        self.conn.execute(
            "UPDATE card_shop_offers SET purchased = 1 WHERE user_id = ? AND slot_index = ?",
            (user_id, slot_index),
        )
        self.conn.commit()

    def get_equip_shop_offers(self, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT user_id, slot_index, equip_spell_id, generated_at, purchased
            FROM equip_shop_offers
            WHERE user_id = ?
            ORDER BY slot_index ASC
            """,
            (user_id,),
        ).fetchall()

    def save_equip_shop_offer(self, user_id: int, slot_index: int, equip_spell_id: str, generated_at: Optional[str] = None):
        self.ensure_user(user_id)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO equip_shop_offers(user_id, slot_index, equip_spell_id, generated_at, purchased)
            VALUES (?, ?, ?, ?, COALESCE((SELECT purchased FROM equip_shop_offers WHERE user_id = ? AND slot_index = ?), 0))
            """,
            (user_id, slot_index, equip_spell_id, generated_at or datetime.now(timezone.utc).isoformat(), user_id, slot_index),
        )
        self.conn.commit()

    def mark_equip_shop_offer_purchased(self, user_id: int, slot_index: int):
        self.conn.execute(
            "UPDATE equip_shop_offers SET purchased = 1 WHERE user_id = ? AND slot_index = ?",
            (user_id, slot_index),
        )
        self.conn.commit()

    def add_equip_spell_to_inventory(self, user_id: int, equip_spell_id: str, obtained_at: Optional[str] = None) -> str:
        self.ensure_user(user_id)
        instance_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO equip_spell_inventory(instance_id, user_id, equip_spell_id, obtained_at) VALUES (?, ?, ?, ?)",
            (instance_id, user_id, equip_spell_id, obtained_at or datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return instance_id

    def add_equip_spell_instance(self, user_id: int, equip_spell_id: str, obtained_at: Optional[str] = None) -> str:
        return self.add_equip_spell_to_inventory(user_id, equip_spell_id, obtained_at)

    def user_equip_spells(self, user_id: int) -> List[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            "SELECT instance_id, user_id, equip_spell_id, obtained_at FROM equip_spell_inventory WHERE user_id = ? ORDER BY obtained_at ASC, instance_id ASC",
            (user_id,),
        ).fetchall()

    def get_equip_spell_instance(self, instance_id: str, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        sql = "SELECT instance_id, user_id, equip_spell_id, obtained_at FROM equip_spell_inventory WHERE instance_id = ?"
        params: List[object] = [instance_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        return self.conn.execute(sql, tuple(params)).fetchone()

    def get_equipped_spell_for_monster(self, monster_instance_id: str, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        sql = (
            "SELECT me.monster_instance_id, me.user_id, me.equip_spell_instance_id, me.equipped_at, "
            "esi.equip_spell_id, esi.obtained_at "
            "FROM monster_equip_spells me "
            "JOIN equip_spell_inventory esi ON esi.instance_id = me.equip_spell_instance_id "
            "WHERE me.monster_instance_id = ?"
        )
        params: List[object] = [monster_instance_id]
        if user_id is not None:
            sql += " AND me.user_id = ?"
            params.append(user_id)
        return self.conn.execute(sql, tuple(params)).fetchone()

    def get_monster_for_equip_spell(self, equip_spell_instance_id: str, user_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        sql = "SELECT * FROM monster_equip_spells WHERE equip_spell_instance_id = ?"
        params: List[object] = [equip_spell_instance_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        return self.conn.execute(sql, tuple(params)).fetchone()

    def _team_duplicate_equip_spell_name(self, user_id: int, member_ids: Sequence[str], overrides: Optional[Dict[str, Optional[str]]] = None) -> Optional[str]:
        overrides = overrides or {}
        counts: Counter[str] = Counter()
        for member_id in member_ids:
            spell_id = overrides.get(member_id, None)
            if member_id not in overrides:
                equipped = self.get_equipped_spell_for_monster(member_id, user_id)
                spell_id = str(equipped["equip_spell_id"]) if equipped else None
            if not spell_id:
                continue
            counts[str(spell_id)] += 1
        for spell_id, count in counts.items():
            if count >= 2:
                return str(spell_id)
        return None

    def _teams_containing_monster(self, user_id: int, monster_instance_id: str) -> List[sqlite3.Row]:
        result = []
        for team in self.list_teams(user_id):
            slots = [part.strip() for part in str(team["slots_csv"]).split(",") if part.strip()]
            if monster_instance_id in slots:
                result.append(team)
        return result

    def equip_spell_to_monster(self, user_id: int, equip_spell_instance_id: str, monster_instance_id: str, replace: bool = False) -> Tuple[bool, str]:
        from kuribot.monsters import is_boss_monster_row
        spell_row = self.get_equip_spell_instance(equip_spell_instance_id, user_id)
        if spell_row is None:
            return False, "You do not own that equip spell."
        monster_row = self.get_inventory_instance(monster_instance_id, user_id)
        if monster_row is None:
            return False, "You do not own that monster."
        if is_boss_monster_row(monster_row):
            return False, "Extra Deck Monsters cannot use Equip Spells."
        existing_on_monster = self.get_equipped_spell_for_monster(monster_instance_id, user_id)
        if existing_on_monster and str(existing_on_monster["equip_spell_instance_id"]) == str(equip_spell_instance_id):
            return True, "That equip spell is already equipped to this monster."
        existing_monster_for_spell = self.get_monster_for_equip_spell(equip_spell_instance_id, user_id)
        if existing_monster_for_spell and str(existing_monster_for_spell["monster_instance_id"]) != str(monster_instance_id):
            return False, "That equip spell is already equipped to another monster. Unequip it first."
        swap_happened = bool(existing_on_monster and str(existing_on_monster["equip_spell_instance_id"]) != str(equip_spell_instance_id))
        overrides = {monster_instance_id: str(spell_row["equip_spell_id"])}
        for team in self._teams_containing_monster(user_id, monster_instance_id):
            members = [part.strip() for part in str(team["slots_csv"]).split(",") if part.strip()]
            duplicate_spell_id = self._team_duplicate_equip_spell_name(user_id, members, overrides=overrides)
            if duplicate_spell_id:
                return False, "That would make a saved team contain duplicate equip spells."
        with self.lock:
            if existing_on_monster:
                self.conn.execute("DELETE FROM monster_equip_spells WHERE monster_instance_id = ? AND user_id = ?", (monster_instance_id, user_id))
            self.conn.execute(
                "INSERT OR REPLACE INTO monster_equip_spells(monster_instance_id, user_id, equip_spell_instance_id, equipped_at) VALUES (?, ?, ?, ?)",
                (monster_instance_id, user_id, equip_spell_instance_id, datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
        return True, "Swapped equip spell successfully." if swap_happened else "Equipped successfully."

    def owns_monster_variant(self, user_id: int, monster_id: str, monster_attribute: str, monster_types: Sequence[str]) -> bool:
        """Same rule add_monster_to_inventory uses to silently discard duplicates."""
        stored_types = "|".join(self._normalize_monster_types_for_storage(monster_types))
        row = self.conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND monster_id = ? AND monster_attribute = ? AND monster_types = ? LIMIT 1",
            (int(user_id), str(monster_id), str(monster_attribute or "").strip().upper(), stored_types),
        ).fetchone()
        return row is not None

    def monster_locked_outside_teams(self, user_id: int, monster_instance_id: str) -> bool:
        """True when the monster is in the player's Arena defense team or one of their Extra Decks."""
        needle = str(monster_instance_id)
        arena = self.conn.execute("SELECT slots_csv FROM arena_teams WHERE user_id = ?", (int(user_id),)).fetchone()
        if arena is not None and needle in str(arena["slots_csv"] or "").split(","):
            return True
        try:
            for deck in self.conn.execute("SELECT monsters_json FROM extra_decks WHERE user_id = ?", (int(user_id),)).fetchall():
                if needle in (json.loads(str(deck["monsters_json"] or "[]")) or []):
                    return True
        except (sqlite3.Error, ValueError, TypeError):
            pass
        return False

    def unequip_spell_from_monster(self, user_id: int, monster_instance_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM monster_equip_spells WHERE monster_instance_id = ? AND user_id = ?", (monster_instance_id, user_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_inventory_instance(self, user_id: int, instance_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM inventory WHERE user_id = ? AND instance_id = ?", (user_id, instance_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_support_card_instance(self, user_id: int, instance_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM support_card_inventory WHERE user_id = ? AND instance_id = ?", (user_id, instance_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_equip_spell_instance(self, user_id: int, instance_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM equip_spell_inventory WHERE user_id = ? AND instance_id = ?", (user_id, instance_id))
        self.conn.commit()
        return cur.rowcount > 0

    def list_market_listings(self, category: Optional[str] = None, search: str = "") -> List[sqlite3.Row]:
        sql = "SELECT * FROM market_listings WHERE status = 'active'"
        params: List[object] = []
        if category:
            sql += " AND category = ?"
            params.append(str(category))
        normalized_search = str(search or "").strip().casefold()
        if normalized_search:
            sql += " AND lower(item_name) LIKE ?"
            params.append(f"%{normalized_search}%")
        sql += " ORDER BY created_at DESC, id DESC"
        return self.conn.execute(sql, tuple(params)).fetchall()

    def list_user_market_listings(self, seller_user_id: int, category: Optional[str] = None, status: str = 'active') -> List[sqlite3.Row]:
        self.ensure_user(seller_user_id)
        sql = "SELECT * FROM market_listings WHERE seller_user_id = ?"
        params: List[object] = [seller_user_id]
        normalized_status = str(status or '').strip().lower()
        if normalized_status:
            sql += " AND status = ?"
            params.append(normalized_status)
        if category:
            sql += " AND category = ?"
            params.append(str(category))
        sql += " ORDER BY created_at DESC, id DESC"
        return self.conn.execute(sql, tuple(params)).fetchall()

    def get_market_listing(self, listing_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM market_listings WHERE id = ? LIMIT 1", (listing_id,)).fetchone()

    def create_market_listing(self, seller_user_id: int, category: str, item_name: str, *, item_ref: str = "", monster_attribute: str = "", monster_types: str = "", quantity: int = 1, price: int = 0) -> int:
        self.ensure_user(seller_user_id)
        cur = self.conn.execute(
            """
            INSERT INTO market_listings(seller_user_id, category, item_name, item_ref, monster_attribute, monster_types, quantity, price, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                seller_user_id,
                str(category),
                str(item_name),
                str(item_ref or ""),
                str(monster_attribute or ""),
                str(monster_types or ""),
                max(1, int(quantity)),
                max(0, int(price)),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def mark_market_listing_sold(self, listing_id: int, buyer_user_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE market_listings SET status = 'sold', buyer_user_id = ?, sold_at = ? WHERE id = ? AND status = 'active'",
            (buyer_user_id, datetime.now(timezone.utc).isoformat(), listing_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def cancel_market_listing(self, listing_id: int, seller_user_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE market_listings SET status = 'cancelled', sold_at = ? WHERE id = ? AND seller_user_id = ? AND status = 'active'",
            (datetime.now(timezone.utc).isoformat(), listing_id, seller_user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def user_has_monster_in_team(self, user_id: int, monster_instance_id: str) -> bool:
        return bool(self._teams_containing_monster(user_id, monster_instance_id))

    def user_has_equip_spell_equipped(self, user_id: int, equip_spell_instance_id: str) -> bool:
        row = self.get_monster_for_equip_spell(equip_spell_instance_id, user_id)
        return bool(row)

    def reset_showcase_data(self) -> dict:
        from kuribot.render.images import invalidate_rgba_image_cache
        rows = self.conn.execute("SELECT user_id, pending_image_path, approved_image_path FROM profile_tag_uploads").fetchall()
        pending_deleted = 0
        approved_deleted = 0
        reset_profiles = 0

        for row in rows:
            pending_path = Path(str(row["pending_image_path"] or "").strip())
            if pending_path.exists():
                try:
                    pending_path.unlink()
                    pending_deleted += 1
                except Exception:
                    pass
            approved_path = Path(str(row["approved_image_path"] or "").strip())
            if approved_path.exists():
                try:
                    approved_path.unlink()
                    approved_deleted += 1
                except Exception:
                    pass

        user_rows = self.conn.execute("SELECT user_id, tag_image_path FROM users WHERE trim(coalesce(tag_image_path, '')) != ''").fetchall()
        for row in user_rows:
            tag_path = Path(str(row["tag_image_path"] or "").strip())
            if tag_path.exists():
                try:
                    tag_path.unlink()
                    approved_deleted += 1
                except Exception:
                    pass
            reset_profiles += 1

        self.conn.execute("UPDATE users SET tag_image_path = ''")
        invalidate_rgba_image_cache()
        pending_requests = int(self.conn.execute("SELECT COUNT(*) AS c FROM profile_tag_uploads WHERE status = 'pending'").fetchone()["c"] or 0)
        self.conn.execute("DELETE FROM profile_tag_uploads")
        self.conn.commit()
        return {
            "reset_profiles": reset_profiles,
            "cleared_requests": pending_requests,
            "pending_files_deleted": pending_deleted,
            "approved_files_deleted": approved_deleted,
        }

    def reset_all_player_data(self):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM profile_tag_uploads")
        cur.execute("DELETE FROM inventory")
        cur.execute("DELETE FROM teams")
        cur.execute("DELETE FROM support_card_inventory")
        cur.execute("DELETE FROM support_decks")
        cur.execute("DELETE FROM equip_spell_inventory")
        cur.execute("DELETE FROM monster_equip_spells")
        cur.execute("DELETE FROM monster_shop_offers")
        cur.execute("DELETE FROM card_shop_offers")
        cur.execute("DELETE FROM equip_shop_offers")
        cur.execute("DELETE FROM mailbox_messages")
        cur.execute("DELETE FROM market_listings")
        cur.execute("DELETE FROM guild_members")
        cur.execute("DELETE FROM guilds")
        self.conn.commit()

    def add_pack(self: Database, user_id: int, pack_key: str, quantity: int = 1):
        self.ensure_user(user_id)
        pack_key = normalize_pack_key(pack_key) or "random_themed"
        quantity = max(0, int(quantity or 0))
        if quantity <= 0:
            return
        pack_name, theme_kind, theme_value = PACK_DEFS[pack_key]
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO pack_inventory(user_id, pack_key, pack_name, theme_kind, theme_value, quantity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, pack_key) DO UPDATE SET
                pack_name=excluded.pack_name,
                theme_kind=excluded.theme_kind,
                theme_value=excluded.theme_value,
                quantity=pack_inventory.quantity + excluded.quantity,
                updated_at=excluded.updated_at
            """,
            (int(user_id), pack_key, pack_name, theme_kind, theme_value, quantity, now_iso),
        )
        self.conn.commit()

    def user_packs(self: Database, user_id: int) -> List[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute(
            "SELECT user_id, pack_key, pack_name, theme_kind, theme_value, quantity, updated_at FROM pack_inventory WHERE user_id = ? AND quantity > 0 ORDER BY pack_name COLLATE NOCASE",
            (int(user_id),),
        ).fetchall()

    def pack_quantity(self: Database, user_id: int, pack_key: str) -> int:
        self.ensure_user(user_id)
        pack_key = normalize_pack_key(pack_key) or "random_themed"
        row = self.conn.execute("SELECT quantity FROM pack_inventory WHERE user_id = ? AND pack_key = ?", (int(user_id), pack_key)).fetchone()
        return int(row["quantity"] or 0) if row else 0

    def spend_pack(self: Database, user_id: int, pack_key: str, quantity: int = 1) -> bool:
        self.ensure_user(user_id)
        pack_key = normalize_pack_key(pack_key) or "random_themed"
        quantity = max(1, int(quantity or 1))
        if self.pack_quantity(user_id, pack_key) < quantity:
            return False
        self.conn.execute(
            "UPDATE pack_inventory SET quantity = quantity - ?, updated_at = ? WHERE user_id = ? AND pack_key = ?",
            (quantity, datetime.now(timezone.utc).isoformat(), int(user_id), pack_key),
        )
        self.conn.execute("DELETE FROM pack_inventory WHERE user_id = ? AND pack_key = ? AND quantity <= 0", (int(user_id), pack_key))
        self.conn.commit()
        return True

    def transfer_pack(self: Database, from_user_id: int, to_user_id: int, pack_key: str, quantity: int) -> bool:
        quantity = max(1, int(quantity or 1))
        pack_key = normalize_pack_key(pack_key) or "random_themed"
        if not self.spend_pack(from_user_id, pack_key, quantity):
            return False
        self.add_pack(to_user_id, pack_key, quantity)
        return True

    def convert_ticket_balances_to_packs(self: Database):
        row = self.conn.execute("SELECT reset_value FROM bot_reset_state WHERE reset_key = 'pack_ticket_conversion_v1'").fetchone()
        if row is not None:
            return
        users = self.conn.execute("SELECT user_id, tickets, premium_tickets, boss_monster_tickets FROM users WHERE registration_complete = 1 OR tickets > 0 OR premium_tickets > 0 OR boss_monster_tickets > 0").fetchall()
        for user in users:
            total = int(user["tickets"] or 0) + int(user["premium_tickets"] or 0) + int(user["boss_monster_tickets"] or 0)
            if total > 0:
                self.add_pack(int(user["user_id"]), "random_themed", total)
        self.conn.execute("UPDATE users SET tickets = 0, premium_tickets = 0, boss_monster_tickets = 0")
        self.conn.execute(
            "INSERT OR REPLACE INTO bot_reset_state(reset_key, reset_value, updated_at) VALUES (?, ?, ?)",
            ("pack_ticket_conversion_v1", "complete", datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def mark_notice_shown_once(self: Database, user_id: int, notice_key: str) -> bool:
        self.ensure_user(user_id)
        try:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO user_notice_flags(user_id, notice_key, shown_at) VALUES (?, ?, ?)",
                (int(user_id), str(notice_key), datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()
            return cur.rowcount > 0
        except sqlite3.OperationalError:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_notice_flags (
                    user_id INTEGER NOT NULL,
                    notice_key TEXT NOT NULL,
                    shown_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, notice_key)
                )
            """)
            self.conn.commit()
            return self.mark_notice_shown_once(user_id, notice_key)

    def get_arena_team(self, user_id: int) -> Optional[sqlite3.Row]:
        self.ensure_user(user_id)
        return self.conn.execute("SELECT user_id, slots_csv, updated_at FROM arena_teams WHERE user_id = ?", (user_id,)).fetchone()

    def set_arena_team(self, user_id: int, members: Sequence[str]):
        from kuribot.monsters import boss_or_signature_team_conflict
        self.ensure_user(user_id)
        member_ids = [str(mid).strip() for mid in list(members or []) if str(mid).strip()][:TEAM_SIZE]
        if len(member_ids) != TEAM_SIZE:
            raise ValueError(f"arena_team_size:{TEAM_SIZE}")
        conflict = boss_or_signature_team_conflict(user_id, member_ids)
        if conflict:
            raise ValueError(conflict)
        duplicate_spell_id = self._team_duplicate_equip_spell_name(user_id, member_ids)
        if duplicate_spell_id:
            raise ValueError(f"duplicate_equip_spell:{duplicate_spell_id}")
        self.conn.execute(
            "INSERT OR REPLACE INTO arena_teams(user_id, slots_csv, updated_at) VALUES (?, ?, ?)",
            (user_id, ",".join(member_ids), datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def clear_arena_team(self, user_id: int):
        self.conn.execute("DELETE FROM arena_teams WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def arena_leaderboard_users(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM users
            WHERE registration_complete = 1
            ORDER BY arena_elo DESC, arena_peak_elo DESC, arena_wins DESC, user_id ASC
            """
        ).fetchall()

    def update_arena_record(self, user_id: int, outcome: str):
        self.ensure_user(user_id)
        column = {'win': 'arena_wins', 'loss': 'arena_losses', 'draw': 'arena_draws'}.get(str(outcome).strip().lower())
        if not column:
            return
        self.conn.execute(f"UPDATE users SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def set_arena_elo(self, user_id: int, elo: int):
        self.ensure_user(user_id)
        value = max(0, int(elo))
        self.conn.execute(
            "UPDATE users SET arena_elo = ?, arena_peak_elo = MAX(arena_peak_elo, ?) WHERE user_id = ?",
            (value, value, user_id),
        )
        self.conn.commit()

    def registered_user_ids(self) -> List[int]:
        rows = self.conn.execute(
            "SELECT user_id FROM users WHERE registration_complete = 1 ORDER BY user_id ASC"
        ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def arena_duelist_rows(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT duelist_key, arena_elo, updated_at FROM arena_duelists ORDER BY duelist_key ASC"
        ).fetchall()

    def get_arena_duelist(self, duelist_key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT duelist_key, arena_elo, updated_at FROM arena_duelists WHERE duelist_key = ?",
            (str(duelist_key),),
        ).fetchone()

    def set_arena_duelist_elo(self, duelist_key: str, elo: int):
        value = max(0, int(elo))
        self.conn.execute(
            "INSERT OR REPLACE INTO arena_duelists(duelist_key, arena_elo, updated_at) VALUES (?, ?, ?)",
            (str(duelist_key), value, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def bulk_set_arena_duelist_elos(self, elo_map: Dict[str, int]):
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = [(str(key), max(0, int(value)), now_iso) for key, value in elo_map.items()]
        self.conn.executemany(
            "INSERT OR REPLACE INTO arena_duelists(duelist_key, arena_elo, updated_at) VALUES (?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def get_arena_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM arena_meta WHERE key = ?", (str(key),)).fetchone()
        if not row:
            return default
        return str(row["value"] or default)

    def set_arena_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO arena_meta(key, value) VALUES (?, ?)",
            (str(key), str(value)),
        )
        self.conn.commit()

    def get_background_image_path(self, user_id: int) -> str:
        row = self.get_user(user_id)
        keys = set(row.keys())
        return str((row["background_image_path"] if "background_image_path" in keys else "") or "").strip()

    def set_background_image_path(self, user_id: int, image_path: str):
        self.ensure_user(user_id)
        try:
            self.conn.execute("UPDATE users SET background_image_path = ? WHERE user_id = ?", (str(image_path or "").strip(), user_id))
            self.conn.commit()
        except Exception:
            pass

    def has_background_unlocked(self, user_id: int) -> bool:
        row = self.get_user(user_id)
        keys = set(row.keys())
        return bool(int((row["background_unlocked"] if "background_unlocked" in keys else 0) or 0))

    def create_profile_customization_upload(self, request_id: str, user_id: int, pending_image_path: str, *, upload_type: str = "showcase", guild_id: Optional[int] = None, channel_id: Optional[int] = None, original_filename: str = ""):
        self.ensure_user(user_id)
        normalized_type = str(upload_type or "showcase").strip().lower()
        if normalized_type not in {"showcase", "background"}:
            normalized_type = "showcase"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO profile_tag_uploads(request_id, user_id, guild_id, channel_id, original_filename, pending_image_path, approved_image_path, upload_type, status, submitted_at, decided_at, decided_by, admin_message_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, 'pending', ?, NULL, NULL, '[]')
            """,
            (request_id, user_id, guild_id, channel_id, str(original_filename or ''), str(pending_image_path), normalized_type, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_pending_profile_customization_upload_for_user(self, user_id: int, upload_type: str) -> Optional[sqlite3.Row]:
        normalized_type = str(upload_type or "showcase").strip().lower()
        return self.conn.execute(
            "SELECT * FROM profile_tag_uploads WHERE user_id = ? AND upload_type = ? AND status = 'pending' ORDER BY submitted_at DESC LIMIT 1",
            (user_id, normalized_type),
        ).fetchone()

    def create_extra_deck(self: Database, user_id: int, name: str, monster_instance_ids: Sequence[str]) -> bool:
        from kuribot.monsters import _extra_deck_monster_ids_from_instance_ids
        self.ensure_user(user_id)
        picks = _extra_deck_monster_ids_from_instance_ids(user_id, monster_instance_ids)
        if not (EXTRA_DECK_MIN_SIZE <= len(picks) <= EXTRA_DECK_SIZE):
            return False
        normalized_name = str(name or "").strip()
        if len(normalized_name) < 2:
            return False
        self.conn.execute(
            "INSERT INTO extra_decks(user_id, name, monsters_json, is_active) VALUES (?, ?, ?, 0)",
            (int(user_id), normalized_name, json.dumps(picks)),
        )
        self.conn.commit()
        return True

    def list_extra_decks(self: Database, user_id: int) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM extra_decks WHERE user_id = ? ORDER BY id ASC", (int(user_id),)).fetchall()

    def get_extra_deck(self: Database, user_id: int, deck_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM extra_decks WHERE user_id = ? AND id = ? LIMIT 1", (int(user_id), int(deck_id))).fetchone()

    def update_extra_deck_name(self: Database, user_id: int, deck_id: int, name: str) -> bool:
        normalized_name = str(name or "").strip()
        if len(normalized_name) < 2:
            return False
        cur = self.conn.execute("UPDATE extra_decks SET name = ? WHERE user_id = ? AND id = ?", (normalized_name, int(user_id), int(deck_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def update_extra_deck_monsters(self: Database, user_id: int, deck_id: int, monster_instance_ids: Sequence[str]) -> bool:
        from kuribot.monsters import _extra_deck_monster_ids_from_instance_ids
        picks = _extra_deck_monster_ids_from_instance_ids(user_id, monster_instance_ids)
        if not (EXTRA_DECK_MIN_SIZE <= len(picks) <= EXTRA_DECK_SIZE):
            return False
        cur = self.conn.execute("UPDATE extra_decks SET monsters_json = ? WHERE user_id = ? AND id = ?", (json.dumps(picks), int(user_id), int(deck_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_extra_deck(self: Database, user_id: int, deck_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM extra_decks WHERE user_id = ? AND id = ?", (int(user_id), int(deck_id)))
        if cur.rowcount > 0:
            self.conn.execute("UPDATE teams SET extra_deck_id = NULL WHERE user_id = ? AND extra_deck_id = ?", (int(user_id), int(deck_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def set_active_extra_deck(self: Database, user_id: int, deck_id: int) -> bool:
        decks = self.list_extra_decks(user_id)
        if not any(int(deck["id"]) == int(deck_id) for deck in decks):
            return False
        self.conn.execute("UPDATE extra_decks SET is_active = 0 WHERE user_id = ?", (int(user_id),))
        self.conn.execute("UPDATE extra_decks SET is_active = 1 WHERE user_id = ? AND id = ?", (int(user_id), int(deck_id)))
        self.conn.commit()
        return True

    def update_team_extra_deck(self: Database, user_id: int, team_id: int, extra_deck_id: Optional[int]) -> bool:
        deck_id = int(extra_deck_id) if extra_deck_id is not None else None
        if deck_id is not None and self.get_extra_deck(user_id, deck_id) is None:
            return False
        cur = self.conn.execute("UPDATE teams SET extra_deck_id = ? WHERE user_id = ? AND id = ?", (deck_id, int(user_id), int(team_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def get_extra_deck_for_team(self: Database, user_id: int, team_row: Optional[sqlite3.Row | dict]) -> Optional[sqlite3.Row]:
        if team_row is None:
            return None
        try:
            deck_id = team_row["extra_deck_id"]
        except Exception:
            deck_id = None
        if deck_id:
            return self.get_extra_deck(user_id, int(deck_id))
        return None

    def get_active_extra_deck(self: Database, user_id: int, mode: str = "campaign") -> Optional[sqlite3.Row]:
        # Extra Decks are equipable team attachments, just like support decks.
        # They do not have a standalone/fallback active state.
        team_row = self.get_active_team(user_id, mode)
        return self.get_extra_deck_for_team(user_id, team_row)


# The shared database connection. Tables are created and upgraded by db.setup(), which
# startup.prepare_database() runs once every module is loaded.
db = Database(DB_PATH)
