"""Database setup that runs once at startup, in the order the features were added."""

from __future__ import annotations

from kuribot.config import BASE_DIR
from kuribot.packs import init_pack_system_tables
from kuribot.database import db
from kuribot.battle.extra_deck import init_extra_deck_schema
from kuribot.campaign import init_campaign_tables
from kuribot.arena import ensure_arena_schema
from kuribot.tournament import _tourney_storage_setup
from kuribot.profile import _ensure_background_customization_schema
from kuribot.maintenance import (
    backfill_one_time_reward_flags, backup_database_daily, check_database_health,
    ensure_performance_indexes, relocate_stored_file_paths,
)


def prepare_database() -> None:
    """Create and upgrade database tables, then run maintenance and one-time data fixes.

    Called once by kuribot.app.load() after every module is imported. The steps run in the
    order the features were added, because later steps rely on tables made by earlier ones."""
    db.setup()

    init_pack_system_tables(db)

    db.convert_ticket_balances_to_packs()

    init_campaign_tables(db)

    ensure_arena_schema()

    _tourney_storage_setup()

    _ensure_background_customization_schema()

    try:
        init_extra_deck_schema(db)
    except Exception as exc:
        print(f"Extra Deck schema migration failed: {exc}")

    try:
        _created_index_count = ensure_performance_indexes()
        if _created_index_count:
            print(f"Created {_created_index_count} database index(es) for faster lookups.")
    except Exception as exc:
        print(f"Database index creation failed: {exc}")

    try:
        _relocated_path_count = relocate_stored_file_paths()
        if _relocated_path_count:
            print(f"Re-pointed {_relocated_path_count} stored file path(s) to this bot folder ({BASE_DIR}).")
    except Exception as exc:
        print(f"Stored file path relocation failed: {exc}")

    try:
        if check_database_health():
            _backup_path = backup_database_daily()
            if _backup_path is not None:
                print(f"Database backed up to {_backup_path}")
    except Exception as exc:
        print(f"Database health check / backup failed: {exc}")

    try:
        backfill_one_time_reward_flags()
    except Exception as exc:
        print(f"Starter reward flag backfill failed: {exc}")
