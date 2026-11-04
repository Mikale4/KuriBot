"""Load every KuriBot module and run the bot."""

from __future__ import annotations

import importlib
import pkgutil

import kuribot

_loaded = False


def load() -> None:
    """Import every module in the package (this registers the commands, events and menus)
    and prepare the database. Safe to call more than once."""
    global _loaded
    if _loaded:
        return
    for info in pkgutil.walk_packages(kuribot.__path__, "kuribot."):
        if info.name not in ("kuribot.app", "kuribot.__main__"):
            importlib.import_module(info.name)
    from kuribot.startup import prepare_database

    prepare_database()
    _loaded = True


def main() -> None:
    """Check the token, set up logging, load the bot and connect to Discord."""
    from kuribot.config import BASE_DIR, CARDS_DIR, TOKEN

    if not TOKEN or "your_" in TOKEN.lower():
        env_path = BASE_DIR / ".env"
        raise SystemExit(
            "Missing Discord bot token. Create or update "
            f"{env_path} with one of these keys:\n"
            "KURIBOT_TOKEN=your_discord_bot_token_here\n"
            "or DISCORD_TOKEN=your_discord_bot_token_here\n\n"
            f"Cards directory currently resolves to: {CARDS_DIR}"
        )
    from kuribot.errors import setup_kuribot_logging

    setup_kuribot_logging()
    load()
    from kuribot.core import bot

    bot.run(TOKEN, log_handler=None)
