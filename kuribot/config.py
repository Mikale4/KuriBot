"""Settings from .env, file locations, and the game's tuning constants."""

from __future__ import annotations

import os
import random
import re
from pathlib import Path


# The bot folder (this file lives in <bot folder>/kuribot/).
BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=BASE_DIR / ".env")
except Exception:
    pass


PREFIX = ";"
# KURIBOT_DB_PATH lets the offline checks (devtools/tests) run the bot on a sandbox copy.
DB_PATH = os.getenv("KURIBOT_DB_PATH", "").strip() or str(BASE_DIR / "kuribot.sqlite3")
TOKEN = (
    os.getenv("KURIBOT_TOKEN", "").strip()
    or os.getenv("DISCORD_BOT_TOKEN", "").strip()
    or os.getenv("DISCORD_TOKEN", "").strip()
)

cards_env = os.getenv("CARDS_DIR", "").strip()
if cards_env:
    CARDS_DIR = Path(cards_env).expanduser()
else:
    default_cards_dir = BASE_DIR / "resources" / "Cards"
    CARDS_DIR = default_cards_dir if default_cards_dir.exists() else Path(r"C:\Games\KuriBot\resources\Cards")
QUEST_TICKETS = 10
PREMIUM_WELCOME_TICKETS = 1
PACKS_PER_DAILY = 10
PACK_TICKET_CONVERSION_RATE = 1
PACK_PURCHASE_COST = 5000
PACK_SELL_GUILD_COINS = 20
CAMPAIGN_NORMAL_DUELIST_GOLD_RANGES = {
    "domino_city": (100, 300),
    "duelist_kingdom": (200, 400),
    "battle_city": (300, 500),
    "kaiba_corp": (400, 500),
}
CAMPAIGN_HARD_DUELIST_GOLD_RANGES = {
    "domino_city": (200, 400),
    "duelist_kingdom": (400, 600),
    "battle_city": (600, 800),
    "kaiba_corp": (800, 1000),
}
CAMPAIGN_NORMAL_AREA_CLEAR_TICKETS = 10
CAMPAIGN_NORMAL_AREA_CLEAR_PREMIUM_TICKETS = 1
CAMPAIGN_NORMAL_DUELIST_10_WIN_TICKETS = 10
CAMPAIGN_NORMAL_COMPLETION_TICKETS = 100
CAMPAIGN_NORMAL_COMPLETION_GOLD = 50000
CAMPAIGN_NORMAL_COMPLETION_PREMIUM_TICKETS = 10
CAMPAIGN_HARD_DUELIST_TICKET_DROP = 1
CAMPAIGN_HARD_AREA_CLEAR_SUPPORT_CARDS = 20
CAMPAIGN_HARD_AREA_CLEAR_EQUIP_SPELLS = 5
CAMPAIGN_HARD_AREA_CLEAR_PREMIUM_TICKETS = 1
CAMPAIGN_HARD_DUELIST_10_WIN_SUPPORT_CARDS = 10
CAMPAIGN_HARD_COMPLETION_GOLD = 1000000
CAMPAIGN_HARD_MODE_ENEMY_SUPPORT_BONUSES = {
    "domino_city": [25],
    "duelist_kingdom": [25],
    "battle_city": [25],
    "kaiba_corp": [25],
}
LEVELING_MAX_LEVEL = 500
LEVELING_MESSAGE_XP_MIN = 5
LEVELING_MESSAGE_XP_MAX = 25
LEVELING_MESSAGE_COOLDOWN_SECONDS = 60
LEVELING_REWARD_PACKS = 5
LEVELING_REWARD_GOLD = 5000
LEVELING_FALLBACK_CHANNEL_ID = 1452049493171109949
GACHA_COST = 1
GACHA_X10_COST = 10
FEATURED_MONSTER_COUNT = 4
TEAM_SIZE = 3
TEAM_ACTIVE_MODES = ("campaign", "ranked", "arena_offense", "lobby", "guild_boss")
TEAM_ACTIVE_MODE_LABELS = {
    "campaign": "Campaign",
    "ranked": "Ranked",
    "arena_offense": "Arena Offense",
    "lobby": "Lobby",
    "guild_boss": "Guild Boss",
}
TEAM_ACTIVE_MODE_HIGHLIGHT_COLORS = {
    "campaign": (72, 190, 255),
    "ranked": (255, 215, 90),
    "arena_offense": (255, 112, 112),
    "lobby": (126, 232, 154),
    "guild_boss": (190, 132, 255),
}
BOSS_MONSTER_SUMMON_RATE = 0.002
SIGNATURE_MONSTER_SUMMON_RATE = 0.0
FEATURED_MONSTER_SUMMON_RATE = 0.05
STANDARD_MONSTER_SUMMON_RATE = 0.943
BOSS_MONSTER_STAT_BOOST = 0.50
SIGNATURE_MONSTER_STAT_BOOST = BOSS_MONSTER_STAT_BOOST
FEATURED_MONSTER_DUAL_TYPE_CHANCE = 0.50
SPECIAL_MONSTER_COMPENSATION_TICKETS = 200
SPECIAL_MONSTER_COMPENSATION_PREMIUM_TICKETS = 20
SPECIAL_MONSTER_REMOVAL_BODY_MARKER = "Extra Deck Monster cleanup compensation"
BATTLE_HEARTS = 5
BASE_CRIT_RATE = 0.15
BASE_CRIT_DAMAGE = 1.50
BASE_ACCURACY = 0.90
BASE_DODGE = 0.00
SPEED_GAUGE_MAX = 100.0
ATTRIBUTES = ["FIRE", "WATER", "EARTH", "WIND", "LIGHT", "DARK", "DIVINE"]
MONSTER_TYPES = [
    "Aqua", "Beast", "Beast-Warrior", "Black Magic", "Charisma", "Celestial Warrior",
    "Creator God", "Cyberse", "Cyborg", "Dinosaur", "Divine-Beast", "Dragon",
    "Dragon Magic", "Fairy", "Fiend", "Fish", "Galaxy", "High Dragon", "Human",
    "Illusion", "Illusion Magic", "Immortal", "Insect", "Machine", "Magical Knight",
    "Omega Psychic", "Plant", "Psychic", "Pyro", "Reptile", "Rock", "Sea Beast",
    "Sea Serpent", "Spellcaster", "Thunder", "Warrior", "White Magic", "Winged Beast",
    "Wyrm", "Yokai",
]
BOSS_MONSTER_EXACT_NAMES = {
    "holactie the creator of light",
    "holactie the creator of",
    "slifer the sky dragon",
    "obelisk the tormentor",
    "uria, lord of searing flames",
    "hamon, lord of striking thunder",
    "crimson dragon",
    "ultimaya tzolkin",
    "phantasmal lord ultimitl bishbaalkin",
    "number 100 numeron dragon",
    "the arrival cyberse @ignister",
    "dark master - zorc",
    "darkness neosphere",
    "sephylon, the ultimate timelord",
    "the winged dragon of ra",
    "the creator",
    "the creator incarnate",
    "the wicked avatar",
    "the wicked eraser",
    "the wicked dreadroot",
}

SIGNATURE_MONSTER_EXACT_NAMES = {
    "orgoth the relentless",
    "red-eyes black dragon",
    "goddess with the third eye",
    "magician of faith",
    "super roboyarou",
    "gandora the dragon of destruction",
    "barrel dragon",
    "pumpking the king of ghosts",
    "harpie lady",
    "the legendary fisherman",
    "two-headed king rex",
    "insect queen",
    "dark magician",
    "jinzo",
    "masked beast des gardius",
    "the masked beast",
    "exodia the forbidden one",
    "black luster soldier",
    "mudora",
    "mystical beast of serket",
    "blue-eyes white dragon",
    "dark necrofear",
    "kelbek",
    "blue-eyes ultimate dragon",
    "opened skull",
    "gaia the fierce knight",
    "elemental hero neos",
    "number 39 utopia",
    "stardust dragon",
    "black rose dragon",
    "relinquished",
    "perfectly ultimate great moth",
    "cyber dragon",
    "cyber end dragon",
    "destiny hero plasma",
    "rainbow dragon",
    "elemental hero flame wingman",
    "armed dragon lv10",
    "ojama king",
    "vwxyz-dragon catapult cannon",
    "red dragon archfiend",
    "ancient fairy dragon",
    "black-winged dragon",
    "blackwing armor master",
    "infernity doom dragon",
    "hundred eyes dragon",
    "power tool dragon",
    "cyber blader",
    "super vehicroid stealth union",
    "water dragon",
    "red-eyes darkness dragon",
    "fossil skull king",
    "volcanic doomfire",
    "arcana force ex - the light ruler",
    "sun dragon inti",
    "moon dragon quilla",
    "number 32 shark drake",
    "galaxy-eyes photon dragon",
    "chronomaly crystal chrononaut",
    "number 6 chronomaly atlandis",
    "gimmick puppet giant grinder",
    "number 9 dyson sphere",
    "number 94 crystalzero",
    "number 103 ragnazero",
    "heroic champion - excalibur",
    "superdreadnought rail cannon gustav max",
    "odd-eyes pendulum dragon",
    "bloom diva the melodious choir",
    "ddd doom king armageddon",
    "raidraptor - rise falcon",
    "frightfur bear",
    "frightfur tiger",
    "dark rebellion xyz dragon",
    "clear wing synchro dragon",
    "starving venom fusion dragon",
    "lunalight cat dancer",
    "lunalight leo dancer",
    "scarlight red dragon archfiend",
    "decode talker",
    "firewall dragon",
    "salamangreat heatleo",
    "trickstar holly angel",
    "borreload dragon",
    "gouki the great ogre",
    "sunavalon dryatrentiay",
    "topologic gumblar dragon",
    "dark templar @ignister",
}

BOSS_MONSTER_NAME_PATTERNS = (
    re.compile(r"^the winged dragon of ra(?:\b|\s|-|$)"),
    re.compile(r"^raviel(?:\b|\s|-|$)"),
    re.compile(r"^armityle(?:\b|\s|-|$)"),
    re.compile(r"^numerounius(?:\b|\s|-|$)"),
    re.compile(r"(?:^|\b)z-arc(?:\b|\s|-|$)"),
)

TYPE_EFFECTIVENESS = {
    "Aqua": {"strong": {"Pyro", "Rock", "Machine"}, "weak": {"Plant", "Thunder", "Sea Serpent"}},
    "Beast": {"strong": {"Human", "Spellcaster", "Charisma"}, "weak": {"Beast-Warrior", "Dinosaur", "Fiend", "Fairy"}},
    "Beast-Warrior": {"strong": {"Beast", "Human", "Warrior"}, "weak": {"Fairy", "Celestial Warrior", "Illusion"}},
    "Black Magic": {"strong": {"Spellcaster", "Charisma", "Illusion Magic", "Yokai"}, "weak": {"White Magic", "Creator God", "Divine-Beast"}},
    "Charisma": {"strong": {"Human", "Warrior", "Beast-Warrior"}, "weak": {"Psychic", "Yokai", "Black Magic"}},
    "Celestial Warrior": {"strong": {"Dragon", "Fiend", "Yokai", "Immortal"}, "weak": {"Black Magic", "Illusion Magic", "Galaxy"}},
    "Creator God": {"strong": {"Divine-Beast", "Immortal", "Black Magic", "Fiend", "Galaxy"}, "weak": {"Wyrm"}},
    "Cyberse": {"strong": {"Machine", "Psychic", "Illusion", "Spellcaster"}, "weak": {"Cyborg", "Thunder", "Galaxy"}},
    "Cyborg": {"strong": {"Human", "Cyberse", "Psychic"}, "weak": {"Machine", "Thunder", "Aqua"}},
    "Dinosaur": {"strong": {"Beast", "Reptile", "Human", "Plant"}, "weak": {"Dragon", "Machine", "Aqua"}},
    "Divine-Beast": {"strong": {"Fiend", "Yokai", "Immortal", "Black Magic"}, "weak": {"Creator God", "Galaxy"}},
    "Dragon": {"strong": {"Beast", "Plant", "Warrior", "Thunder"}, "weak": {"High Dragon", "Celestial Warrior", "Dragon Magic"}},
    "Dragon Magic": {"strong": {"Dragon", "High Dragon", "Wyrm", "Spellcaster"}, "weak": {"White Magic", "Magical Knight", "Fairy"}},
    "Fairy": {"strong": {"Fiend", "Yokai", "Beast-Warrior", "Immortal"}, "weak": {"Black Magic", "Thunder", "Rock"}},
    "Fiend": {"strong": {"Psychic", "Human", "Charisma", "Immortal"}, "weak": {"Fairy", "Celestial Warrior", "White Magic", "Divine-Beast"}},
    "Fish": {"strong": {"Pyro", "Sea Beast", "Insect"}, "weak": {"Thunder", "Aqua", "Sea Serpent"}},
    "Galaxy": {"strong": {"Creator God", "Divine-Beast", "Dragon", "Celestial Warrior", "Cyberse"}, "weak": {"Omega Psychic", "Wyrm"}},
    "High Dragon": {"strong": {"Dragon", "Wyrm", "Beast", "Warrior"}, "weak": {"Fairy", "Illusion Magic", "Dragon Magic"}},
    "Human": {"strong": {"Charisma", "Spellcaster"}, "weak": {"Beast", "Fiend", "Yokai", "Cyborg"}},
    "Illusion": {"strong": {"Beast-Warrior", "Human", "Warrior", "Dragon"}, "weak": {"Spellcaster", "Psychic", "Cyberse"}},
    "Illusion Magic": {"strong": {"Celestial Warrior", "High Dragon", "Warrior", "Dragon"}, "weak": {"White Magic", "Black Magic", "Spellcaster"}},
    "Immortal": {"strong": {"Human", "Beast", "Charisma"}, "weak": {"Creator God", "Divine-Beast", "Celestial Warrior", "Fairy"}},
    "Insect": {"strong": {"Plant", "Beast", "Human"}, "weak": {"Pyro", "Winged Beast", "Rock"}},
    "Machine": {"strong": {"Cyborg", "Winged Beast", "Dinosaur"}, "weak": {"Cyberse", "Thunder", "Aqua"}},
    "Magical Knight": {"strong": {"Dragon Magic", "Fiend", "Black Magic", "Yokai"}, "weak": {"Celestial Warrior", "Psychic"}},
    "Omega Psychic": {"strong": {"Psychic", "Galaxy", "Illusion", "Charisma"}, "weak": {"Fiend", "Cyberse", "White Magic"}},
    "Plant": {"strong": {"Rock", "Aqua", "Sea Beast"}, "weak": {"Pyro", "Insect", "Dragon", "Reptile"}},
    "Psychic": {"strong": {"Illusion", "Charisma", "Human", "Spellcaster"}, "weak": {"Fiend", "Omega Psychic", "Cyberse"}},
    "Pyro": {"strong": {"Plant", "Insect", "Beast", "Yokai"}, "weak": {"Aqua", "Rock", "Sea Serpent"}},
    "Reptile": {"strong": {"Plant", "Beast", "Insect"}, "weak": {"Dinosaur", "Winged Beast", "Dragon"}},
    "Rock": {"strong": {"Thunder", "Pyro", "Winged Beast", "Insect"}, "weak": {"Aqua", "Plant", "Sea Beast"}},
    "Sea Beast": {"strong": {"Rock", "Pyro", "Machine"}, "weak": {"Fish", "Thunder", "Plant"}},
    "Sea Serpent": {"strong": {"Aqua", "Pyro", "Fish", "Rock"}, "weak": {"Thunder", "Wyrm", "Galaxy"}},
    "Spellcaster": {"strong": {"Illusion", "Black Magic", "Illusion Magic", "Yokai"}, "weak": {"Beast", "Dragon Magic", "Psychic", "Human"}},
    "Thunder": {"strong": {"Aqua", "Machine", "Cyborg", "Winged Beast", "Sea Serpent", "Fish"}, "weak": {"Rock", "Dragon", "Plant"}},
    "Warrior": {"strong": {"Human", "Spellcaster", "Charisma"}, "weak": {"Dragon", "Beast-Warrior", "Illusion"}},
    "White Magic": {"strong": {"Black Magic", "Fiend", "Yokai", "Illusion Magic", "Immortal"}, "weak": {"Dragon Magic", "Galaxy"}},
    "Winged Beast": {"strong": {"Insect", "Reptile", "Plant"}, "weak": {"Thunder", "Rock", "Machine"}},
    "Wyrm": {"strong": {"Creator God", "Sea Serpent", "Galaxy", "Divine-Beast"}, "weak": {"Dragon Magic", "High Dragon"}},
    "Yokai": {"strong": {"Human", "Charisma", "Psychic"}, "weak": {"Fairy", "Divine-Beast", "White Magic", "Magical Knight"}},
}
TYPE_SUPER_MULTIPLIER = 1.5
TYPE_RESIST_MULTIPLIER = 0.75
SINGLE_TYPE_CHANCE = 0.95
CARD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
BATTLE_TURN_DELAY = 0.45
SUPPORT_CARD_DROP_CHANCE = 0.20
SUPPORT_DECK_SIZE = 15
SUPPORT_DECK_MIN_SIZE = 1
SUPPORT_DRAW_COUNT = 3
SUPPORT_CARD_IMAGE_PATH = BASE_DIR / "resources" / "Support Card.png"
BINDER_PANEL_IMAGE_PATH = BASE_DIR / "resources" / "Binder Panel.png"
BINDER_PANEL_ALPHA = 285
SUPPORT_CARD_DUPLICATE_DUST = 5
MONSTER_SHOP_SIZE = 6
CARD_SHOP_SIZE = 6
CARD_SHOP_MIN_COST = 50
CARD_SHOP_MAX_COST = 50
EQUIP_SHOP_SIZE = 6
EQUIP_SHOP_MIN_COST = 5000
EQUIP_SHOP_MAX_COST = 5000
SHOP_PAID_REFRESH_COST = 10000
ATB_TIME_ACCELERATION = 3.2
ATB_READY_EPSILON = 1e-9
RNG = random.Random()
BOT_CREATOR_ID = 1151194810368135200
SECOND_BOT_CREATOR_ID = 1230465670139875331
BOT_CREATOR_IDS = {
    int(BOT_CREATOR_ID),
    int(SECOND_BOT_CREATOR_ID),
    769891829624864768,
    175960013762723841,
    1151194810368135200,
}
ADMIN_COMMAND_USER_IDS = {
    769891829624864768,
    1151194810368135200,
    175960013762723841,
}
DEBUG_ALLOWED_CHANNEL_ID = 1487063990722105347


PROFILE_TAG_MODERATION_CHANNEL_IDS = [1491510992499118161, 1494093385458516019]
IDEA_SUBMISSION_CHANNEL_ID = 1494093385458516019
PROFILE_TAGS_DIR = BASE_DIR / "profile_tags"
PROFILE_TAGS_PENDING_DIR = PROFILE_TAGS_DIR / "pending"
PROFILE_TAGS_APPROVED_DIR = PROFILE_TAGS_DIR / "approved"
PROFILE_TAG_SIZE = (248, 248)
PROFILE_TAG_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
PROFILE_TAG_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
PROFILE_TAG_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
avatars_env = os.getenv("AVATARS_DIR", "").strip()
if avatars_env:
    AVATARS_DIR = Path(avatars_env).expanduser()
else:
    default_avatars_dir = BASE_DIR / "resources" / "Portraits"
    AVATARS_DIR = default_avatars_dir if default_avatars_dir.exists() else Path(r"C:\Games\KuriBot\resources\Portraits")
duelists_env = os.getenv("DUELISTS_DIR", "").strip()
if duelists_env:
    DUELISTS_DIR = Path(duelists_env).expanduser()
else:
    default_duelists_dir = BASE_DIR / "resources" / "Duelists"
    DUELISTS_DIR = default_duelists_dir if default_duelists_dir.exists() else Path(r"C:\Games\KuriBot\resources\Duelists")
PLAYER_AVATAR_PAGE_SIZE = 16
PLAYER_AVATAR_RENDER_SIZE = (960, 960)
PLAYER_AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


GUILD_CREATE_COST = 10000
GUILD_MAX_MEMBERS = 12
GUILD_MAX_OFFICERS = 2
GUILD_JOIN_PAGE_SIZE = 5
GUILD_LIST_PAGE_SIZE = 5
GUILD_NAME_MIN_LENGTH = 2
GUILD_NAME_MAX_LENGTH = 32
GUILD_ROLE_LEADER = "leader"
GUILD_ROLE_OFFICER = "officer"
GUILD_ROLE_MEMBER = "member"
GUILD_ACCEPTANCE_FREE_JOIN = "free_join"
GUILD_ACCEPTANCE_APPLY = "apply"
GUILD_ACCEPTANCE_LABELS = {GUILD_ACCEPTANCE_FREE_JOIN: "Free Join", GUILD_ACCEPTANCE_APPLY: "Apply"}
GUILD_APPLICATION_STATUS_PENDING = "pending"
GUILD_APPLICATION_STATUS_ACCEPTED = "accepted"
GUILD_APPLICATION_STATUS_DENIED = "denied"
GUILD_BOSS_HP_MULTIPLIER = 100
GUILD_BOSS_ATK_MULTIPLIER = 2
GUILD_BOSS_MAX_ROUNDS = 12
GUILD_BOSS_SUPPORT_DISABLED_ROUNDS = 4
GUILD_BOSS_MAX_PARTICIPATION_CHANCES = 3
GUILD_BOSS_DEFEAT_GOLD_REWARD = 50_000
GUILD_BOSS_DEFEAT_GUILD_COIN_REWARD = 100
GUILD_BOSS_RANDOM_BUFFS = ("IMMUNITY", "PROTECTION", "REFLECT", "PRECOG", "COUNTER", "SHIELD")
GUILD_SHOP_ITEMS = [
    {"key": "guild_boss_ticket", "name": "Guild Boss Ticket", "cost": 100, "description": "Reset your guild boss limit so you can fight it again."},
    {"key": "guild_boss_challenge", "name": "Guild Boss Challenge", "cost": 20, "description": "Gain 1 extra guild boss challenge attempt for the active boss."},
    {"key": "equip_spell_ticket", "name": "Equip Spell Ticket", "cost": 75, "description": "Use from inventory to choose an equip spell by name search."},
    {"key": "arena_tickets", "name": "Arena Tickets", "cost": 50, "description": "Gain 10 arena tickets used to battle in Arena."},
]
GUILD_SHOP_ITEM_BY_KEY = {str(item["key"]): item for item in GUILD_SHOP_ITEMS}
DAILY_ARENA_TICKETS = 10
GUILD_SHOP_ARENA_TICKET_AMOUNT = 10
GUILD_DONATION_OPTIONS = [
    {"key": "donate_5k", "label": "5k gold", "gold": 5_000, "guild_coins": 5},
    {"key": "donate_15k", "label": "15k gold", "gold": 15_000, "guild_coins": 10},
    {"key": "donate_25k", "label": "25k gold", "gold": 25_000, "guild_coins": 15},
]
GUILD_DONATION_OPTION_BY_KEY = {str(option["key"]): option for option in GUILD_DONATION_OPTIONS}
GUILD_DAILY_CLAIM_COINS = 10
GUILD_BOSS_ATTEMPT_GUILD_COIN_REWARD = 5
GUILD_BOSS_TOP_DAMAGE_BONUS_COINS = 10
GUILD_BOSS_KILLING_BLOW_BONUS_COINS = 10


CAMPAIGN_REQUIRED_WINS_PER_DUELIST = 5
CAMPAIGN_BATTLE_HEARTS = 5
CAMPAIGN_PANEL_BG_ALPHA = 104
CAMPAIGN_AREA_BG_ALPHA = 118
CAMPAIGN_PANEL_TINT_ALPHA = 132
CAMPAIGN_AREA_TINT_ALPHA = 124
CAMPAIGN_AREAS = [
    {
        "key": "domino_city",
        "label": "Domino City",
        "description": "Domino City is where you begin, facing off against Duke, Joey, Serenity, Téa, Tristan, and Yugi.",
        "accent": (110, 180, 255),
        "background_path": str(BASE_DIR / "resources" / "Domino City.png"),
    },
    {
        "key": "duelist_kingdom",
        "label": "Duelist Kingdom",
        "description": "Duelist Kingdom tosses Bandit Keith, Bonz, Mai, Mako, Rex, and Weevil in your path.",
        "accent": (255, 195, 90),
        "background_path": str(BASE_DIR / "resources" / "Duelist Kingdom.png"),
    },
    {
        "key": "battle_city",
        "label": "Battle City",
        "description": "Battle City has skilled duelists like Arkana, Espa Roba, Lumis, Seeker, Strings, and Umbra.",
        "accent": (255, 110, 110),
        "background_path": str(BASE_DIR / "resources" / "Battle City.png"),
    },
    {
        "key": "kaiba_corp",
        "label": "Duel Tower",
        "description": "Duel Tower is the final climb, where Ishizu, Marik, Odion, Kaiba, Yami Bakura, and Yami Yugi reside.",
        "accent": (180, 135, 255),
        "background_path": str(BASE_DIR / "resources" / "Alcatraz Island.png"),
    },
]

CAMPAIGN_DUELIST_ORDER = {
    "domino_city": ["duke_devlin", "joey_wheeler", "serenity_wheeler", "tea_gardner", "tristan_taylor", "yugi_muto"],
    "duelist_kingdom": ["bandit_keith", "bonz", "mai_valentine", "mako_tsunami", "rex_raptor", "weevil_underwood"],
    "battle_city": ["arkana", "espa_roba", "lumis", "seeker", "strings", "umbra"],
    "kaiba_corp": ["ishizu_ishtar", "marik_ishtar", "odion", "seto_kaiba", "yami_bakura", "yami_yugi"],
}


DUELING_GROUNDS_BATTLE_HEARTS = 5
DUELING_GROUNDS_TICKET_DROP_CHANCE = 0.08
DUELING_GROUNDS_GOLD_MIN = 120
DUELING_GROUNDS_GOLD_MAX = 240


RANKED_STARTING_ELO = 1000


# Legacy Boss/Signature monsters are unified as Extra Deck Monsters.
# Internal legacy names are kept where renaming them would break older saves, commands,
# packs, or admin tools. Player-facing behavior and text use Extra Deck Monster.
EXTRA_DECK_SIZE = 5
EXTRA_DECK_MIN_SIZE = 1
