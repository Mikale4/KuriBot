"""Database indexes, portable file paths, health checks, backups and one-time data fixes."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Optional

from kuribot.config import AVATARS_DIR, CARDS_DIR, DB_PATH, DUELISTS_DIR, PROFILE_TAGS_DIR
from kuribot.timeutil import current_et_date_string, utc_now
from kuribot.core import bot
from kuribot.database import db
from kuribot.profile import PROFILE_BACKGROUNDS_DIR
from kuribot.ui.profile import ProfileTagModerationView


# The large per-player tables had no indexes, so every "this player's monsters / mail /
# cards" lookup scanned the whole table (47k inventory rows, 21k mail rows, ...).
_PERFORMANCE_INDEXES = (
    ("inventory", ("user_id", "monster_id")),
    ("inventory", ("monster_id",)),
    ("mailbox_messages", ("user_id", "is_read")),
    ("support_card_inventory", ("user_id", "support_card_id")),
    ("equip_spell_inventory", ("user_id",)),
    ("monster_equip_spells", ("user_id",)),
    ("monster_equip_spells", ("equip_spell_instance_id",)),
    ("teams", ("user_id",)),
    ("support_decks", ("user_id",)),
    ("extra_decks", ("user_id",)),
    ("market_listings", ("seller_user_id",)),
    ("guild_members", ("user_id",)),
    ("guild_bosses", ("guild_id",)),
    ("profile_tag_uploads", ("user_id",)),
    ("profile_tag_uploads", ("status",)),
)


def ensure_performance_indexes() -> int:
    created = 0
    with db.lock:
        for table, columns in _PERFORMANCE_INDEXES:
            try:
                existing = {str(row["name"]) for row in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            except sqlite3.Error:
                continue
            if not existing or not set(columns) <= existing:
                continue
            index_name = f"idx_{table}_{'_'.join(columns)}"
            already = db.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)).fetchone()
            if already:
                continue
            db.conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({', '.join(columns)})")
            created += 1
        if created:
            db.conn.commit()
        try:
            db.conn.execute("PRAGMA optimize")
        except sqlite3.Error:
            pass
    return created


# The database stores absolute image paths (avatars, showcase tags, profile backgrounds,
# card art, pending uploads). When the bot folder moves - phone <-> PC, or a different
# Termux storage path - every one of those paths breaks. On each startup, any stored path
# that no longer exists is re-pointed at the same file inside this bot's folders.
def _portable_path_anchor_dirs() -> Dict[str, Path]:
    return {
        "profile_backgrounds": Path(PROFILE_BACKGROUNDS_DIR),
        "profile_tags": Path(PROFILE_TAGS_DIR),
        "portraits": Path(AVATARS_DIR),
        "duelists": Path(DUELISTS_DIR),
        "cards": Path(CARDS_DIR),
    }


def relocate_stored_path(value: object, anchors: Optional[Dict[str, Path]] = None) -> str:
    """Return a working path for a stored file path, or the original value if none is found."""
    raw = str(value or "").strip()
    if not raw:
        return raw
    anchors = anchors or _portable_path_anchor_dirs()
    # Fast path: already inside this bot's folders - re-pointing could not improve it,
    # so skip the disk check (this runs for ~10k rows on every startup).
    normalized_raw = raw.replace("\\", "/")
    for anchor_dir in anchors.values():
        anchor_prefix = str(anchor_dir).replace("\\", "/").rstrip("/") + "/"
        if normalized_raw.startswith(anchor_prefix):
            return raw
    try:
        if Path(raw).exists():
            return raw
    except OSError:
        pass
    parts = [part for part in normalized_raw.split("/") if part]
    # Walk folder names right-to-left so ".../KuriBot/resources/Cards/x.jpg" anchors on "Cards".
    for index in range(len(parts) - 2, -1, -1):
        base_dir = anchors.get(parts[index].lower())
        if base_dir is None:
            continue
        candidate = base_dir.joinpath(*parts[index + 1:])
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            pass
        break
    return raw


_RELOCATABLE_PATH_COLUMNS = (
    ("users", ("avatar_path", "tag_image_path", "background_image_path")),
    ("player_profiles", ("avatar_path",)),
    ("monster_defs", ("image_path",)),
    ("profile_tag_uploads", ("pending_image_path", "approved_image_path")),
)


def relocate_stored_file_paths() -> int:
    anchors = _portable_path_anchor_dirs()
    updated = 0
    with db.lock:
        for table, columns in _RELOCATABLE_PATH_COLUMNS:
            try:
                existing = {str(row["name"]) for row in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            except sqlite3.Error:
                continue
            for column in columns:
                if column not in existing:
                    continue
                # Alias rowid explicitly: on tables with an INTEGER PRIMARY KEY, SQLite
                # reports a bare "rowid" column under that key's name instead.
                rows = db.conn.execute(
                    f"SELECT rowid AS row_ref, {column} AS value FROM {table} WHERE trim(coalesce({column}, '')) != ''"
                ).fetchall()
                changes = []
                for row in rows:
                    old_value = str(row["value"])
                    new_value = relocate_stored_path(old_value, anchors)
                    if new_value != old_value:
                        changes.append((new_value, int(row["row_ref"])))
                if changes:
                    db.conn.executemany(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", changes)
                    updated += len(changes)
        if updated:
            db.conn.commit()
    return updated


# The live database was found corrupted in Nov 2026 (out-of-order rows in `users`, a broken
# support-card index) - most likely from running SQLite on Android shared storage. These
# checks make any future damage visible immediately and keep restorable daily copies.
BACKUP_DIR = Path(DB_PATH).resolve().parent / "backups"  # next to whichever database is in use
BACKUP_KEEP_DAYS = 7


def check_database_health() -> bool:
    try:
        result = [str(row[0]) for row in db.conn.execute("PRAGMA quick_check(5)").fetchall()]
    except sqlite3.Error as exc:
        result = [f"quick_check failed: {exc}"]
    if result == ["ok"]:
        return True
    print("!" * 78)
    print("WARNING: kuribot.sqlite3 failed its integrity check. Stop the bot and restore the")
    print(f"newest file from {BACKUP_DIR} before players lose progress. Details: {result[:5]}")
    print("!" * 78)
    return False


def backup_database_daily() -> Optional[Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"kuribot-{current_et_date_string(utc_now())}.sqlite3"
    if target.exists():
        return None
    temp_target = target.with_suffix(".partial")
    destination = sqlite3.connect(str(temp_target))
    try:
        with db.lock:
            db.conn._conn.backup(destination)  # consistent snapshot, includes the WAL
    finally:
        destination.close()
    temp_target.replace(target)
    backups = sorted(BACKUP_DIR.glob("kuribot-*.sqlite3"))
    for old_backup in backups[:-BACKUP_KEEP_DAYS]:
        try:
            old_backup.unlink()
        except OSError:
            pass
    return target


def _set_bot_state_flag(key: str, value: str) -> None:
    now_iso = utc_now().isoformat()
    db.conn.execute(
        "INSERT INTO bot_reset_state(reset_key, reset_value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(reset_key) DO UPDATE SET reset_value = excluded.reset_value, updated_at = excluded.updated_at",
        (key, value, now_iso),
    )
    db.conn.commit()


def restore_moderation_views() -> int:
    """Re-attach Accept/Deny buttons for pending image requests after a restart."""
    pending = db.list_pending_profile_tag_uploads()
    if not db.get_bot_reset_state("moderation_views_persistent_v1"):
        # Messages posted before persistent buttons existed can never be re-attached, so
        # clear them once; on_ready then re-posts each pending request with working buttons.
        for row in pending:
            db.conn.execute("UPDATE profile_tag_uploads SET admin_message_ids_json = '[]' WHERE request_id = ?", (str(row["request_id"]),))
        db.conn.commit()
        _set_bot_state_flag("moderation_views_persistent_v1", "complete")
    for row in pending:
        bot.add_view(ProfileTagModerationView(str(row["request_id"])))
    return len(pending)


def backfill_one_time_reward_flags() -> int:
    """Players who already registered have had their starter rewards; record that permanently."""
    if db.get_bot_reset_state("starter_reward_flags_backfill_v1"):
        return 0
    now_iso = utc_now().isoformat()
    total = 0
    for key in ("starter_welcome_pack_v1", "starter_rewards_v1"):
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO user_notice_flags(user_id, notice_key, shown_at) "
            "SELECT user_id, ?, ? FROM users WHERE COALESCE(registration_complete, 0) = 1",
            (key, now_iso),
        )
        total += int(cur.rowcount or 0)
    db.conn.commit()
    _set_bot_state_flag("starter_reward_flags_backfill_v1", "complete")
    return total
