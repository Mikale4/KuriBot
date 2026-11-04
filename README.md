# KuriBot

A Discord bot for a 3v3 Yu-Gi-Oh!-style gacha RPG. Players collect monsters from packs, build
teams with Support Decks, Equip Spells and Extra Decks, and fight automatic speed-gauge battles
across a campaign, Dueling Grounds (normal / ranked / lobby), an Arena ladder, tournaments and
guild bosses. Battle and menu screens are rendered as images with Pillow.

The code is the `kuribot/` package, split by feature (see [Code layout](#code-layout)); `bot.py`
just starts it. Player data is a SQLite file next to it.

## Requirements
- Windows PC with **Python 3.12+** (3.12 and 3.13 both work)
- A Discord bot token with the **Message Content** and **Server Members** intents enabled
  (Discord Developer Portal → your app → Bot → Privileged Gateway Intents)

## Setup
1. Copy `.env.example` to `.env` and paste your bot token into `KURIBOT_TOKEN`.
   The folder settings can stay as they are; they default to the folders next to `bot.py`.
2. Double-click **`run_kuribot.bat`**. The first run creates a `.venv` folder and installs
   the dependencies from `requirements.txt`; later runs just start the bot.

Manual alternative:
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Configuration (`.env`)
| Key | Meaning |
|---|---|
| `KURIBOT_TOKEN` | Discord bot token (required). `DISCORD_TOKEN` / `DISCORD_BOT_TOKEN` also work. |
| `CARDS_DIR` | Monster art. Each image (`.png/.jpg/.jpeg/.webp`) becomes one monster; the file name is the monster name. Default: `resources/Cards` |
| `AVATARS_DIR` | Player avatar choices. Default: `resources/Portraits` |
| `DUELISTS_DIR` | NPC duelist portraits. Default: `resources/Duelists` |
| `KURIBOT_FONT_DIR` | Optional custom font folder. Default: `fonts/` |

## Folders and files
| Path | What it is |
|---|---|
| `bot.py` | Starts the bot (`python bot.py`, or `python -m kuribot`) |
| `kuribot/` | The bot's code, see [Code layout](#code-layout) |
| `kuribot.sqlite3` (+ `-wal`, `-shm`) | **Live player data.** Never copy only the `.sqlite3` file while the bot runs — the `-wal` file holds recent saves. Use the daily backups instead. |
| `backups/` | Automatic daily database backups (last 7 days), made safely while the bot runs |
| `kuribot.log` | Warnings and errors with full tracebacks (rotates at 5 MB) |
| `resources/` | Card art, portraits, duelists, map and panel backgrounds, fonts |
| `profile_tags/`, `profile_backgrounds/` | Player-uploaded showcase and background images (`pending/` awaits moderation) |
| `fonts/` | Bundled fonts used for rendering |
| `devtools/` | The offline checks (`devtools/sandbox/` holds their throwaway database copies) |

**Not in the Git repo** (see `.gitignore`): `.env`, the database and its backups, player
uploads, logs, and the large art folders `resources/Cards`, `resources/Portraits` and
`resources/Duelists` (1+ GB). After cloning, copy those art folders in from your own copy.

## Commands
The prefix is `;`. `;help` shows everything in Discord.

- **Getting started:** `;start`, `;tutorial`, `;daily`, `;quest`, `;hardclaim`
- **Player:** `;profile`, `;rank`, `;leaderboard` (`;lb`), `;settings`, `;inbox`
- **Collection:** `;inventory` (`;inv`), `;binder`, `;monster`, `;cards`, `;deck`, `;ed`, `;team`, `;equip`
- **Packs & economy:** `;packs`, `;shop`, `;market`
- **Battles:** `;pve`, `;campaign`, `;pvp`, `;ranked`, `;arena`, `;lobby`, `;normal`, `;tournament`
- **Guilds & community:** `;guild`, `;idea`, `;glossary`
- **Admin** (`;adminhelp`): `;givepack`, `;givegold`, `;getguildcoin`, `;givecard`, `;getspell`,
  `;bossmonster`, `;resetboss`, `;attempts`, `;list`, `;ping`, `;debug`, `;hardmode`,
  and the global wipes `;reset`, `;resetcamp`, `;resetelo`, `;resetshowcase` (use with care).

## Code layout
`kuribot/` is built in layers, foundation first. Each module imports what it needs from the
layers listed above it in this table. In the few places where something has to reach a layer
below it (for example a menu's Back button opening a parent menu), the import sits inside that
function instead of at the top of the file, which avoids circular imports.

| Where | What |
|---|---|
| `config.py`, `models.py`, `timeutil.py`, `settings.py` | `.env` settings, tuning constants, data classes, daily reset times |
| `core.py` | The Discord `bot` object, the logger, admin checks, the `;debug` channel lock |
| `database.py` | The `Database` class and the shared `db` object |
| `cards/`, `monsters.py`, `packs.py`, `players.py` | Support cards, equip spells, support decks, monster generation, packs |
| `battle/` | The battle engine: fighters, stats, effects (`mechanics.py`), knockouts, Extra Deck |
| `duelists.py`, `opponents.py`, `campaign.py`, `dueling_grounds.py`, `ranked.py`, `arena.py`, `tournament.py`, `guilds.py`, `gacha.py`, `pack_opening.py`, `shops.py`, `mail.py`, `quests.py`, `profile.py`, `tutorial.py` | Feature rules and data |
| `render/` | Pillow image rendering (profiles, binders, shops, hubs, battles) |
| `ui/` | Discord menus: views, buttons, dropdowns and pop-up forms, one module per feature |
| `modes/` | Running a whole battle for each game mode |
| `commands/` | The `;` commands |
| `events.py`, `errors.py` | `on_ready` / `on_message` / the daily reset loop; error messages and the log file |
| `startup.py`, `maintenance.py` | Database setup at startup; indexes, backups, path fixes, one-time data fixes |
| `app.py` | Loads every module (which registers the commands and menus) and runs the bot |

New modules are picked up automatically: `app.load()` imports every module in the package.
Scripts that use the bot's code (tests, one-off tools) should call `kuribot.app.load()` first,
since it also creates and upgrades the database tables.

## Checking changes
Double-click **`run_checks.bat`** after changing the code, before restarting the real bot. It
runs two offline checks on a sandbox copy of the database (nothing reaches Discord or players):
- `devtools/tests/battle_check.py` plays real battles in every mode (normal, campaign, ranked,
  guild boss, arena, duelist vs duelist) with players' actual teams.
- `devtools/tests/menu_check.py` runs every player command for a few players and presses the
  buttons and dropdowns in the menus that come back (never delete/reset/sell-all buttons).

Both report crashes and anything Discord would reject (too-long messages, too many buttons,
...), and exit with an error if they find a problem. Add a number to run more battles or
players, for example `python devtools\tests\battle_check.py 20`.

## Maintenance notes
- **Startup checks:** every start runs a quick SQLite integrity check (a loud warning is
  printed if the file is damaged), makes the day's backup, adds missing database indexes, and
  re-points stored image paths at this folder (so the bot folder can be moved safely).
- **Restoring a backup:** stop the bot, move `kuribot.sqlite3`, `kuribot.sqlite3-wal` and
  `kuribot.sqlite3-shm` aside, copy `backups/kuribot-YYYY-MM-DD.sqlite3` to `kuribot.sqlite3`,
  and start the bot.
- **Errors:** check `kuribot.log`. Players see a short "something went wrong" message instead
  of Discord's silent "This interaction failed".
- Keep the database on a normal local drive. Shared/sync folders (phone shared storage,
  OneDrive/Dropbox) can corrupt SQLite files.
