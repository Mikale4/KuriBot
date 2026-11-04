"""NPC duelist data, aliases and name lookups."""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple


DUELIST_THEME_POOLS = json.loads(r"""{"toon": ["Toon Mermaid", "Toon Summoned Skull", "Toon Dark Magician Girl", "Relinquished", "Thousand-Eyes Restrict", "Blue-Eyes Toon Dragon", "Toon Goblin Attack Force", "Toon Gemini Elf"], "panik_fiend": ["Castle of Dark Illusions", "King of Yamimakai", "Barox", "Necrolancer the Timelord", "The Snake Hair", "Dark Chimera"], "labyrinth": ["Labyrinth Wall", "Wall Shadow", "Sanga of the Thunder", "Kazejin", "Suijin", "Gate Guardian"], "gem_burn": ["Millennium Shield", "Cannon Soldier", "Witch of the Black Forest", "Shadow Ghoul", "Guardian Angel Joan", "Fire Princess", "Diamond Head Dragon", "Ruby Dragon"], "seal_guardian": ["Guardian Eatos", "Guardian Dreadscythe", "Guardian Grarl", "Guardian Kayest", "Guardian Baou", "Guardian Ceal"], "armor": ["Armor Dragon LV3", "Armor Dragon LV5", "Armor Dragon LV7", "Fairy Box", "Tiger Axe", "Rocket Warrior"], "valkyrie": ["Valkyrie Brunhilde", "Valkyrie Erste", "Valkyrie Zweite", "Valkyrie Dritte", "Mischief of the Time Goddess", "Ride of the Valkyries"], "sphinx": ["Andro Sphinx", "Sphinx Teleia", "Theinen the Great Sphinx", "Criosphinx", "Hieracosphinx", "Exxod, Master of The Guard"], "dice": ["Orgoth the Relentless", "Strike Ninja", "Sasuke Samurai", "Dice Jar", "Time Wizard", "Dimension Dice"], "joey_warrior": ["Red-Eyes Black Dragon", "Time Wizard", "Baby Dragon", "Flame Swordsman", "Jinzo", "Rocket Warrior", "Gearfried the Iron Knight", "Panther Warrior"], "friendship_fairy": ["Dark Magician Girl", "Magician of Faith", "Shining Friendship", "Fairy's Gift", "Petit Angel", "Etoile Cyber"], "machine_brute": ["Cyber Commander", "Mechanicalchaser", "Zoa", "Machine King", "Robotic Knight", "Jinzo #7"], "dark_magician": ["Dark Magician", "Dark Magician Girl", "Magician of Faith", "Skilled Dark Magician", "Skilled White Magician", "Buster Blader", "Kuriboh"], "blue_eyes": ["Blue-Eyes White Dragon", "Lord of D.", "Kaiser Sea Horse", "X-Head Cannon", "Y-Dragon Head", "Z-Metal Tank", "XYZ-Dragon Cannon", "Vorse Raider"], "harpie": ["Harpie Lady", "Harpie Lady Sisters", "Cyber Harpie Lady", "Harpie's Pet Dragon", "Harpie Queen", "Harpie Channeler", "Amazoness Chain Master", "Amazoness Swords Woman"], "water_ocean": ["The Legendary Fisherman", "Kairyu-Shin", "Fortress Whale", "Levia-Dragon - Daedalus", "Giga Gagagigo", "Abyss Soldier"], "dinosaur": ["Red-Eyes Black Dragon", "Two-Headed King Rex", "Crawling Dragon #2", "Serpent Night Dragon", "Babycerasaurus", "Black Tyranno"], "insect": ["Petit Moth", "Cocoon of Evolution", "Great Moth", "Perfectly Ultimate Great Moth", "Insect Queen", "Parasite Paracide", "Hercules Beetle"], "pandemonium": ["Dark Magician", "Dark Magician Girl", "Dark Eradicator Warlock", "Skilled Dark Magician", "Legion the Fiend Jester", "Skilled White Magician"], "psychic_roba": ["Jinzo", "Jinzo #7", "Reflect Bounder", "Mechanicalchaser", "Cyber Raider", "Machine King"], "exodia": ["Exodia the Forbidden One", "Right Arm of the Forbidden One", "Left Arm of the Forbidden One", "Right Leg of the Forbidden One", "Left Leg of the Forbidden One", "Sangan"], "slime_ra": ["Revival Jam", "Humanoid Slime", "Metal Reflect Slime", "The Winged Dragon of Ra", "Guardian Slime", "Lava Golem"], "gravekeeper": ["Gravekeeper's Assailant", "Gravekeeper's Spear Soldier", "Gravekeeper's Guard", "Gravekeeper's Chief", "Gravekeeper's Vassal", "Mystical Beast of Serket"], "ra_burn": ["The Winged Dragon of Ra", "Lava Golem", "Helpoemer", "Drillago", "Masked Beast Des Gardius", "Holding Arms"], "dark_necrofear": ["Dark Necrofear", "The Earl of Demise", "Headless Knight", "Giant Germ", "Earthbound Spirit", "Diabound Kernel"], "red_eyes_alt": ["Red-Eyes B. Dragon", "Gearfried the Red-Eyes Iron Knight", "Red-Eyes Wyvern", "Red-Eyes Retro Dragon", "The Black Stone of Legend", "Meteor Dragon Red-Eyes Impact"], "dm_dsod": ["Silent Magician LV4", "Silent Magician LV8", "Dark Magician", "Dark Magician Girl", "Gadget Soldier", "Magician of Black Chaos"], "blue_eyes_dsod": ["Blue-Eyes White Dragon", "Alternative White Dragon", "Deep-Eyes White Dragon", "Dragon Spirit of White", "Chaos MAX Dragon", "Blue-Eyes Solid Dragon"], "cubic": ["Vijam the Cubic Seed", "Crimson Nova the Dark Cubic Lord", "Buster Gundil the Cubic Behemoth", "Duza the Meteor Cubic Vessel", "Blade Garoodia the Cubic Beast", "Geira Guile the Cubic King"], "silent_swordsman": ["Silent Swordsman LV3", "Silent Swordsman LV5", "Silent Swordsman LV7", "Silent Swordsman", "Gandora the Dragon of Destruction", "Marshmacaron"], "cyber_angel": ["Cyber Angel Benten", "Cyber Angel Dakini", "Cyber Angel Idaten", "Cyber Petit Angel", "Cyber Blader", "Cyber Prima"], "elemental_hero": ["Elemental HERO Avian", "Elemental HERO Burstinatrix", "Elemental HERO Clayman", "Elemental HERO Sparkman", "Elemental HERO Bubbleman", "Elemental HERO Wildheart", "Elemental HERO Bladedge", "Elemental HERO Neos"], "evil_hero": ["Evil HERO Infernal Prodigy", "Evil HERO Malicious Edge", "Evil HERO Infernal Gainer", "Evil HERO Lightning Golem", "Evil HERO Wild Cyclone", "Evil HERO Dark Gaia"], "ojama": ["Ojama Yellow", "Ojama Black", "Ojama Green", "Armed Dragon LV3", "Armed Dragon LV5", "VWXYZ-Dragon Catapult Cannon"], "chemist": ["Hydrogeddon", "Oxygeddon", "Water Dragon", "Des Frog", "Duoterion", "Hydro Pressure Cannon"], "roid": ["Steamroid", "Drillroid", "Submarineroid", "Expressroid", "Super Vehicroid Jumbo Drill", "Pair Cycroid"], "cyber_dragon": ["Cyber Dragon", "Cyber Dragon Core", "Proto-Cyber Dragon", "Cyber Phoenix", "Cyber Twin Dragon", "Cyber End Dragon"], "red_eyes_darkness": ["Red-Eyes Darkness Dragon", "Red-Eyes Wyvern", "Infernal Dragon", "Red-Eyes Black Dragon", "Masked Dragon", "Red-Eyes Darkness Metal Dragon"], "crystal_beast": ["Crystal Beast Sapphire Pegasus", "Crystal Beast Ruby Carbuncle", "Crystal Beast Amber Mammoth", "Crystal Beast Cobalt Eagle", "Crystal Beast Topaz Tiger", "Rainbow Dragon"], "destiny_hero": ["Destiny HERO - Malicious", "Destiny HERO - Diamond Dude", "Destiny HERO - Plasma", "Destiny HERO - Dogma", "Destiny HERO - Dreadmaster", "Vision HERO Vyon"], "volcanic": ["Volcanic Rocket", "Volcanic Shell", "Volcanic Hammerer", "Volcanic Doomfire", "Royal Firestorm Guards", "Volcanic Counter"], "fossil": ["Fossil Dyna Pachycephalo", "Fossil Warrior Skull Knight", "Fossil Warrior Skull King", "Fossil Machine Skull Convoy", "Weathering Soldier", "Gaia Plate the Earth Giant"], "arcana_force": ["Arcana Force 0 - The Fool", "Arcana Force I - The Magician", "Arcana Force VI - The Lovers", "Arcana Force VII - The Chariot", "Arcana Force XXI - The World", "Arcana Force EX - The Dark Ruler"], "yubel": ["Yubel", "Yubel - Terror Incarnate", "Yubel - The Ultimate Nightmare", "Phantom of Chaos", "Elemental HERO Neos", "Neos Wiseman"], "cloudian": ["Cloudian - Smoke Ball", "Cloudian - Acid Cloud", "Cloudian - Eye of the Typhoon", "Cloudian - Nimbusman", "Cloudian - Turbulence", "Cloudian - Storm Dragon"], "ancient_gear": ["Ancient Gear Golem", "Ancient Gear Soldier", "Ancient Gear Knight", "Ancient Gear Beast", "Ancient Gear Reactor Dragon", "Ancient Gear Gadjiltron Dragon"], "dino_gx": ["Ultimate Tyranno", "Black Tyranno", "Hydrogeddon", "Hyper Hammerhead", "Tyranno Infinity", "Super Conductor Tyranno"], "vampire": ["Vampire Lord", "Vampire Lady", "Vampire Genesis", "Vampire Grace", "Regenerating Mummy", "Pyramid Turtle"], "amazoness": ["Amazoness Swords Woman", "Amazoness Paladin", "Amazoness Tiger", "Amazoness Queen", "Amazoness Chain Master", "Amazoness Archer"], "graveyard_pharaoh": ["Theinen the Great Sphinx", "Hieracosphinx", "Criosphinx", "Pharaonic Protector", "Spirit of the Pharaoh", "Pharaoh's Servant"], "archfiend": ["Terrorking Archfiend", "Archfiend Soldier", "Infernalqueen Archfiend", "Axe Dragonute", "Skull Archfiend of Lightning", "Shadowknight Archfiend"], "synchron": ["Junk Synchron", "Turbo Synchron", "Quickdraw Synchron", "Nitro Synchron", "Junk Warrior", "Stardust Dragon"], "resonator": ["Crimson Resonator", "Synkron Resonator", "Chain Resonator", "Red Dragon Archfiend", "Exploder Dragonwing", "Scarred Dragon Archfiend"], "black_rose": ["Black Rose Dragon", "Twilight Rose Knight", "Blue Rose Dragon", "Witch of the Black Rose", "Rose Tentacles", "Queen Angel of Roses"], "blackwing": ["Blackwing - Shura the Blue Flame", "Blackwing - Bora the Spear", "Blackwing - Gale the Whirlwind", "Blackwing - Kalut the Moon Shadow", "Blackwing Armor Master", "Black-Winged Dragon"], "morphtronic": ["Morphtronic Boomboxen", "Morphtronic Scopen", "Morphtronic Celfon", "Morphtronic Radion", "Power Tool Dragon", "Life Stream Dragon"], "fairy_dragon": ["Ancient Fairy Dragon", "Sun Dragon Inti", "Moon Dragon Quilla", "Regulus", "Rose Fairy", "Miracle Fertilizer"], "goyo_police": ["Goyo Guardian", "Goyo King", "Goyo Defender", "Stygian Sergeants", "Stygian Security", "Stygian Street Patrol", "Worm Apocalypse", "Worm Barses", "Worm Cartaros", "Worm Dimikles", "Worm Erokin", "Worm Falco", "Worm Gulse", "Worm Hope", "Worm Illidan", "Worm Jetelikpse", "Worm King", "Worm Linx", "Worm Noble", "Worm Opera", "Worm Prince", "Worm Queen", "Worm Rakuyeh", "Worm Solid", "Worm Tentacles", "Worm Ugly", "Worm Victory", "Worm Warlord", "Worm Xex", "Worm Yagan", "Worm Zero"], "fortune_lady": ["Fortune Lady Light", "Fortune Lady Wind", "Fortune Lady Earth", "Fortune Lady Water", "Fortune Lady Fire", "Fortune Lady Dark", "Fortune Lady Past", "Fortune Lady Every", "Fortune Fairy Hikari", "Fortune Fairy En", "Fortune Fairy Ann", "Fortune Fairy Chee", "Fortune Fairy Hu", "Fortune Fairy Swee"], "infernity": ["Infernity Archfiend", "Infernity Necromancer", "Infernity Beetle", "Infernity Mirage", "Void Ogre Dragon", "Hundred Eyes Dragon"], "earthbound_monkey": ["Earthbound Immortal Ccarayhua", "Oracle of the Sun", "Supay", "Sun Dragon Inti", "Moon Dragon Quilla", "Earthbound Linewalker"], "earthbound_spider": ["Earthbound Immortal Uru", "Reptilianne Vaskii", "Reptilianne Scylla", "Earthbound Greater Linewalker", "Spider Egg", "Earthbound Servant Geo Gryphon"], "earthbound_giant": ["Earthbound Immortal Cusillu", "Dark Tinker", "Dark Resonator", "Earthbound Greater Linewalker", "Koa'ki Meiru Gravirose", "Earthbound Servant Geo Gremlina"], "psychic_wheeler": ["Thought Ruler Archfiend", "Krebons", "Psychic Commander", "Serene Psychic Witch", "Overdrive Teleporter", "Mental Seeker"], "fleur": ["Chevalier de Fleur", "Swordswoman of Fleur", "Necro Fleur", "Sorciere de Fleur", "Baronne de Fleur", "Fleur Synchron"], "tg": ["T.G. Striker", "T.G. Warwolf", "T.G. Rush Rhino", "T.G. Wonder Magician", "T.G. Blade Blaster", "T.G. Halberd Cannon"], "meklord": ["Meklord Emperor Wisel", "Meklord Emperor Granel", "Meklord Emperor Skiel", "Meklord Astro Mekanikle", "Meklord Army of Wisel", "Meklord Army of Skiel"], "malefic": ["Malefic Stardust Dragon", "Malefic Blue-Eyes White Dragon", "Malefic Red-Eyes B. Dragon", "Malefic Cyber End Dragon", "Malefic Truth Dragon", "Parallel Gear"], "nordic": ["Tanngnjostr of the Nordic Beasts", "Guldfaxe of the Nordic Beasts", "Vanadis of the Nordic Ascendant", "Odin, Father of the Aesir", "Thor, Lord of the Aesir", "Loki, Lord of the Aesir"], "gagaga_utopia": ["Gagaga Magician", "Gagaga Girl", "Gogogo Golem", "Dododo Warrior", "Utopic Onomatopoeia", "Number 39: Utopia"], "photon": ["Photon Thrasher", "Photon Crusher", "Photon Advancer", "Galaxy Wizard", "Galaxy-Eyes Photon Dragon", "Neo Galaxy-Eyes Photon Dragon"], "orbital_7": ["Orbital 7", "Super Defense Robot Lio", "Super Defense Robot Elephan", "Super Defense Robot Monki", "Lillybot", "Galaxy Wizard", "Galaxy Soldier", "Galaxy Knight", "Photon Thrasher", "Photon Crusher", "Photon Advancer", "Galaxy-Eyes Photon Dragon", "Neo Galaxy-Eyes Photon Dragon", "Googly-Eyes Drum Dragon"], "shark": ["Shark Stickers", "Double Fin Shark", "Abyss Shark", "Crystal Shark", "Number 32: Shark Drake", "Bahamut Shark"], "cat_world": ["Rescue Cat", "Kalantosa, Mystical Beast of the Forest", "Baby Tiger", "Cat Girl Magician", "Number 29: Mannequin Cat", "Nekogal #2"], "train": ["Night Express Knight", "Heavy Freight Train Derricrane", "Flying Pegasus Railroad Stampede", "Super Express Bullet Train", "Superdreadnought Rail Cannon Gustav Max", "Number 81: Superdreadnought Rail Cannon Super Dora"], "heraldic": ["Heraldic Beast Leo", "Heraldic Beast Aberconway", "Heraldic Beast Unicorn", "Heraldic Beast Twin-Headed Eagle", "Number 8: Heraldic King Genom-Heritage", "Heraldry Patriarch"], "chronomaly": ["Chronomaly Crystal Bones", "Chronomaly Crystal Skull", "Chronomaly Moai", "Chronomaly Golden Jet", "Number 33: Chronomaly Machu Mech", "Chronomaly Atlandis"], "gimmick_puppet": ["Gimmick Puppet Dreary Doll", "Gimmick Puppet Humpty Dumpty", "Gimmick Puppet Magnet Doll", "Gimmick Puppet Nightmare", "Number 15: Gimmick Puppet Giant Grinder", "Number 40: Gimmick Puppet of Strings"], "heroic": ["Heroic Challenger - Assault Halberd", "Heroic Challenger - Double Lance", "Heroic Challenger - Thousand Blades", "Heroic Champion - Excalibur", "Heroic Champion - Gandiva", "Heroic Champion - Kusanagi"], "barians_alito": ["Battlin' Boxer Switchitter", "Battlin' Boxer Glassjaw", "Battlin' Boxer Headgeared", "Battlin' Boxer Lead Yoke", "Battlin' Boxer Veil", "Battlin' Boxer Shadow"], "barians_girag": ["Fire Hand", "Ice Hand", "Prominence Hand", "Number 106: Giant Hand", "Number C106: Giant Red Hand", "Magic Hand"], "barians_mizar": ["Galaxy-Eyes Cloudragon", "Galaxy Soldier", "Galaxy-Eyes Cipher Dragon", "Galaxy-Eyes Tachyon Dragon", "Number C107: Neo Galaxy-Eyes Tachyon Dragon", "Nebula Dragon"], "barians_dumon": ["Star Seraph Scepter", "Star Seraph Sovereignty", "Star Seraph Scale", "Number 102: Star Seraph Sentry", "Number C102: Archfiend Seraph", "Star Seraph Scout"], "barians_vector": ["Umbral Horror Ghost", "Umbral Horror Unform", "Umbral Horror Will o' the Wisp", "Number 104: Masquerade", "Number C104: Umbral Horror Masquerade", "Umbral Horror Ghoul"], "performapal": ["Performapal Skullcrobat Joker", "Performapal Pendulum Sorcerer", "Odd-Eyes Pendulum Dragon", "Odd-Eyes Persona Dragon", "Performapal Silver Claw", "Performapal Monkeyboard"], "melodious": ["Canon the Melodious Diva", "Aria the Melodious Diva", "Soprano the Melodious Songstress", "Shopina the Melodious Maestra", "Bloom Diva the Melodious Choir", "Bloom Harmonist the Melodious Composer"], "superheavy": ["Superheavy Samurai Blue Brawler", "Superheavy Samurai Big Benkei", "Superheavy Samurai Gigagloves", "Superheavy Samurai Soulpiercer", "Superheavy Samurai Warlord Susanowo", "Superheavy Samurai Stealth Ninja"], "ddd": ["D/D Savant Kepler", "D/D Swirl Slime", "D/D Necro Slime", "D/D/D Flame King Genghis", "D/D/D Oracle King d'Arc", "D/D/D Duo-Dawn King Kali Yuga"], "raidraptor": ["Raidraptor - Tribute Lanius", "Raidraptor - Vanishing Lanius", "Raidraptor - Mimicry Lanius", "Raidraptor - Fuzzy Lanius", "Raidraptor - Rise Falcon", "Raidraptor - Revolution Falcon"], "frightfur": ["Edge Imp Sabres", "Edge Imp Chain", "Fluffal Dog", "Fluffal Bear", "Frightfur Bear", "Frightfur Tiger"], "lunalight": ["Lunalight Kaleido Chick", "Lunalight Yellow Marten", "Lunalight Emerald Bird", "Lunalight Tiger", "Lunalight Leo Dancer", "Lunalight Cat Dancer"], "phantom_knights": ["The Phantom Knights of Silent Boots", "The Phantom Knights of Ancient Cloak", "The Phantom Knights of Ragged Gloves", "The Phantom Knights of Break Sword", "Dark Rebellion Xyz Dragon", "Dark Requiem Xyz Dragon"], "speedroid": ["Speedroid Terrortop", "Speedroid Taketomborg", "Speedroid Double Yoyo", "Hi-Speedroid Kendama", "Clear Wing Synchro Dragon", "Crystal Wing Synchro Dragon"], "predaplant": ["Predaplant Ophrys Scorpio", "Predaplant Darlingtonia Cobra", "Predaplant Flytrap", "Predaplant Squid Drosera", "Starving Venom Fusion Dragon", "Predaplant Chimerafflesia"], "lyrilusc": ["Lyrilusc - Turquoise Warbler", "Lyrilusc - Cobalt Sparrow", "Lyrilusc - Sapphire Swallow", "Lyrilusc - Celestine Wagtail", "Lyrilusc - Assembled Nightingale", "Lyrilusc - Ensemblue Robin"], "gem_knight": ["Gem-Knight Garnet", "Gem-Knight Sapphire", "Gem-Knight Alexandrite", "Gem-Knight Lazuli", "Gem-Knight Lady Brilliant Diamond", "Gem-Knight Master Diamond"], "xsaber": ["XX-Saber Boggart Knight", "XX-Saber Fulhelmknight", "XX-Saber Emmersblade", "X-Saber Airbellum", "XX-Saber Gottoms", "X-Saber Souza"], "code_talker": ["Lady Debug", "Backup Secretary", "Micro Coder", "Code Generator", "Decode Talker", "Firewall Dragon"], "ignister": ["Pikari @Ignister", "Achichi @Ignister", "Doshin @Ignister", "Dark Templar @Ignister", "The Arrival Cyberse @Ignister", "Linguriboh"], "trickstar": ["Trickstar Candina", "Trickstar Lycoris", "Trickstar Lilybell", "Trickstar Narkissus", "Trickstar Holly Angel", "Trickstar Bella Madonna"], "marincess": ["Marincess Blue Tang", "Marincess Sea Horse", "Marincess Pascalus", "Marincess Mandarin", "Marincess Great Bubble Reef", "Marincess Aqua Argonaut"], "salamangreat": ["Salamangreat Gazelle", "Salamangreat Spinny", "Salamangreat Foxy", "Salamangreat Jack Jaguar", "Salamangreat Heatleo", "Salamangreat Pyro Phoenix"], "gouki": ["Gouki Suprex", "Gouki Twistcobra", "Gouki Headbatt", "Gouki Riscorpio", "Gouki The Great Ogre", "Gouki The Giant Ogre"], "rokket": ["Rokket Tracer", "Magnarokket Dragon", "Autorokket Dragon", "Rokket Synchron", "Borreload Dragon", "Borrelsword Dragon"], "sunavalon": ["Sunseed Genius Loci", "Sunvine Thrasher", "Sunvine Maiden", "Sunavalon Dryas", "Sunavalon Dryatrentiay", "Sunavalon Bloom"], "altergeist": ["Altergeist Marionetter", "Altergeist Multifaker", "Altergeist Silquitous", "Altergeist Meluseek", "Altergeist Primebanshee", "Altergeist Hexstia"], "hydradrive": ["Topologic Zeroboros", "Topologic Bomber Dragon", "Topologic Trisbaena", "Hydradrive Thrasher", "Hydradrive Kicker", "Hydradrive Booster"], "drone": ["Drone Carrier", "Drone Torpedo", "Drone Scanner", "Drone Deployer", "Sky Striker Ace - Raye", "Borreload eXcharge Dragon"], "armatos": ["Armatos Legio Speculata", "Armatos Legio Decurion", "Armatos Legio Praetor", "Armatos Legio Gladius", "Armatos Legio Dux", "Armatos Legio Auxilia"], "appliancer": ["Appliancer Socketroll", "Appliancer Celtopus", "Appliancer Laundry Dragon", "Appliancer Propelion", "Appliancer Vacculephant", "Appliancer Kappa Scale"], "seven_road": ["Sevens Road Magician", "Sevens Road Witch", "Spell Archer", "Mystic Dealer", "Curtain of Sparks", "Dark Liberation"], "dragias": ["Multistrike Dragon Dragias", "Fire Guardian", "Piercing Dragon Buster", "Dragonic Slayer", "Blue-Tooth Burst Dragon", "Dragon Knight of Darkness"], "psychic_music": ["Prima Guitarna the Shining Superstar", "CAN:D", "CAN:Re:D", "Psychic Musician", "Guitarna Girl", "Romanpick"], "samurai_yokai": ["Yamiruler the Dark Delayer", "Yamiruler the Shadow Warrior", "The Dragon of Illumination, Sanctuary's Shield", "Wicked Shadow Dark Lurker", "Abysslayer", "Shadow Flower Princess"], "fiend_roa": ["Royal Rebels - Heavy Metal", "Royal Rebels - Battle Shogun", "Royal Rebels - Slash", "Royal Rebels - Invasion", "Royal Rebels - Snare", "Royal Rebels - Riot"], "maximum_nail": ["Yggdrago the Sky Emperor", "Wyrm Excavator the Heavy Mech", "Torna the Windweaver", "Yamiruler", "Maximum Harpia", "Maximum King"], "wyrm_asana": ["Construction Wyrm Shovel Crusher", "Excavator Wyrm Drill Crusher", "Constructor Wyrm Buildragon", "Yamiruler", "Wyrm Excavator the Heavy Mech", "Romanpick"], "goha_yuga": ["Magician's Valkyria", "Sevens Wonder Fusion", "Mystic Dealer", "Road Magic - Explosion", "Fusion Sorcerer", "Darkness Drape"], "galactica": ["Galactica Oblivion", "Galactica Jammin' Dragon", "Astral Leviathan", "Cosmic String Noodryad", "Nebula the Star Warrior", "Galactica Force"], "jointech": ["Jointech Rex", "Jointech Stegodon", "Jointech Tridynabase", "Jointech Pressor", "Heavy Cavalry", "Jointech Killer"], "velgear": ["Transamu Rainac", "Voidvelg Requiem", "Voidvelg Apocalypse", "Dark Matter Dragon", "Velgear Emperor", "Galactica Oblivion"], "rush_spellcaster": ["Sevens Road Enchanter", "Dreaming Reality Princess", "Fusion Sorcerer", "Darkness Drape", "Maginical Miracle", "Talismanic Seal Array"], "beast_forest": ["Baboon of the Forest", "Green Baboon, Defender of the Forest", "Des Koala", "Behemoth the King of All Animals", "Metal Baboon, Stalwarts of the Forest", "Sea Koala"], "standard_botanical": ["Abyss Flower", "Dark Plant", "Firegrass", "Kattapillar", "Man-Eating Plant", "Tentacle Plant"], "standard_elements": ["Fire Eye", "Flame Viper", "Mega Thunderball", "LaLa Li-oon", "Thunder Kid", "Turu-Purun"], "standard_critters": ["Curtain of the Dark Ones", "Goblin Calligrapher", "Harpie Girl", "Mokey Mokey", "Mystical Shine Ball", "Skull Servant"], "standard_underworld": ["Archfiend Mirror", "Candle of Fate", "Fiend's Hand", "Fire Reaper", "Mystery Hand", "Phantom Ghost"], "standard_beatdown": ["Battle Warrior", "Axe Raider", "Vorse Raider", "Mystic Horseman", "Blade Knight", "Luster Dragon"], "standard_spellcaster": ["Skilled Dark Magician", "Skilled White Magician", "Apprentice Magician", "Old Vindictive Magician", "Magician of Faith", "Dark Elf"], "standard_machine": ["Mechanicalchaser", "Cyber Falcon", "X-Head Cannon", "Gadget Soldier", "Slot Machine", "Launcher Spider"], "standard_warrior": ["Warrior Dai Grepher", "Marauding Captain", "Blade Knight", "Gearfried the Iron Knight", "Goblin Attack Force", "Freed the Brave Wanderer"], "fairy_heal": ["Guardian Angel Joan", "Marie the Fallen One", "Cure Mermaid", "Shining Friendship", "Dunames Dark Witch", "Skelengel"], "toy_box": ["Toy Soldier", "Doll Monster Bear-Bear", "Box of Friends", "Toy Magician", "Wind-Up Soldier", "Gimmick Puppet Humpty Dumpty"], "ninja": ["Ninja Grandmaster Hanzo", "Upstart Golden Ninja", "Senior Silver Ninja", "Blade Armor Ninja", "Black Dragon Ninja", "White Dragon Ninja"]}""")


def make_auto_duelist_entry(name: str, pool_key: str, theme_label: str) -> dict:
    pool = list(DUELIST_THEME_POOLS.get(pool_key, []))
    return {
        "name": name,
        "pool": pool,
        "pre": f"{name} strides in with a {theme_label} game plan!",
        "win": f"{name} controls the duel with {theme_label} momentum!",
        "loss": f"{name} falls back for now, but their {theme_label} strategy will return stronger.",
    }


DUELIST_DATA = {'duke_devlin': {'name': 'Duke Devlin',
                 'pool': ['Orgoth the Relentless',
                          'Strike Ninja',
                          'Sasuke Samurai',
                          'Dice Jar',
                          'Time Wizard',
                          'Dimension Dice'],
                 'pre': "Heh! Let's roll the dice and see if luck smiles on you today!",
                 'win': 'Looks like the dice came up in my favor. Better luck on the next throw!',
                 'loss': "No way! You beat me fair and square. Guess your luck's hotter than mine!"},
 'joey_wheeler': {'name': 'Joey Wheeler',
                  'pool': ['Red-Eyes Black Dragon',
                           'Time Wizard',
                           'Baby Dragon',
                           'Flame Swordsman',
                           'Jinzo',
                           'Rocket Warrior',
                           'Gearfried the Iron Knight',
                           'Panther Warrior'],
                  'pre': "Alright! I'm fired up and ready ta duel! Don't expect me ta go easy on ya!",
                  'win': "Yes! That's how Joey Wheeler does it!",
                  'loss': "Aw man... You got me this time, but I ain't stayin' down forever!"},
 'serenity_wheeler': {'name': 'Serenity Wheeler',
                      'pool': ['Baby Dragon',
                               'Time Wizard',
                               'Dark Magician Girl',
                               'Magician of Faith',
                               'Shining Friendship',
                               "Fairy's Gift",
                               'Petit Angel'],
                      'pre': "I'm doing this for my brother and for myself. Let's have a kind, honest duel!",
                      'win': "I did it! Joey's going to be so proud of how far I've come!",
                      'loss': "That was a wonderful duel. I'll keep practicing and come back even stronger!"},
 'tea_gardner': {'name': 'Tea Gardner',
                 'pool': ['Magician of Faith',
                          'Dark Magician Girl',
                          'Shining Friendship',
                          'Petit Angel',
                          "Fairy's Gift",
                          'Etoile Cyber'],
                 'pre': "Friendship gives me all the courage I need. Let's have a great duel!",
                 'win': 'I believed in my cards, and they believed in me!',
                 'loss': "You were amazing! I'll cheer even louder while I train for our rematch!"},
 'tristan_taylor': {'name': 'Tristan Taylor',
                    'pool': ['Cyber Commander',
                             'Mechanicalchaser',
                             'Zoa',
                             'Machine King',
                             'Robotic Knight',
                             'Jinzo #7'],
                    'pre': "Time for me to show everybody I've got game too! Let's do this!",
                    'win': 'Ha! Bet nobody saw that coming!',
                    'loss': "Okay, okay, you got me. Next time I'm bringin' the heat from the start!"},
 'yugi_muto': {'name': 'Yugi Muto',
               'pool': ['Dark Magician',
                        'Dark Magician Girl',
                        'Kuriboh',
                        'Summoned Skull',
                        'Celtic Guardian',
                        'Gazelle the King of Mythical Beasts',
                        'Berfomet',
                        'Alpha The Magnet Warrior',
                        'Beta The Magnet Warrior',
                        'Gamma The Magnet Warrior'],
               'pre': "My friends are with me, and together we'll take on any challenge!",
               'win': 'The heart of the cards guided me to victory!',
               'loss': 'Incredible! Your bond with your cards is every bit as strong as mine.'},
 'bandit_keith': {'name': 'Bandit Keith',
                  'pool': ['Slot Machine',
                           'Barrel Dragon',
                           'Launcher Spider',
                           'BM-4 Blast Spider',
                           'Pendulum Machine',
                           'Machine King',
                           'Zera the Mant'],
                  'pre': 'In America, champions crush rookies like you. Now eat lead!',
                  'win': "That's what happens when you step to Bandit Keith!",
                  'loss': "What?! No way! This ain't over, not by a long shot!"},
 'bonz': {'name': 'Bonz',
          'pool': ['Pumpking the King of Ghosts', 'Dragon Zombie', 'Armored Zombie', 'Clown Zombie', 'The Snake Hair'],
          'pre': 'Heheheh... Welcome to my graveyard of ghouls!',
          'win': 'My zombies dragged you straight into the darkness!',
          'loss': 'N-no! My zombies... they let me down!'},
 'mai_valentine': {'name': 'Mai Valentine',
                   'pool': ['Harpie Lady',
                            'Harpie Lady Sisters',
                            'Cyber Harpie Lady',
                            "Harpie's Pet Dragon",
                            'Amazoness Chain Master',
                            'Amazoness Swords Woman'],
                   'pre': "Try to keep up, because my beauties won't wait around for you.",
                   'win': 'Too slow. You never had a chance against me.',
                   'loss': "Not bad. You've got enough style to earn my respect."},
 'mako_tsunami': {'name': 'Mako Tsunami',
                  'pool': ['The Legendary Fisherman',
                           'Fortress Whale',
                           'Kairyu-Shin',
                           'Great White',
                           'Flyfang',
                           'Ameba',
                           'Ocean Dragon Lord - Neo-Daedalus'],
                  'pre': "The ocean's roar is behind me! Brace yourself for a tidal wave!",
                  'win': 'You were swept away by the sea!',
                  'loss': 'The current turned against me... You dueled like a true sailor.'},
 'rex_raptor': {'name': 'Rex Raptor',
                'pool': ['Red-Eyes Black Dragon',
                         'Two-Headed King Rex',
                         'Serpent Night Dragon',
                         'Crawling Dragon #2',
                         'Megazowler',
                         'Black Tyranno'],
                'pre': "Dinosaurs rule! Let's see your cards survive my prehistoric power!",
                'win': "Roar! That's dinosaur domination!",
                'loss': 'What?! My dinos got stomped?!'},
 'weevil_underwood': {'name': 'Weevil Underwood',
                      'pool': ['Insect Queen',
                               'Great Moth',
                               'Perfectly Ultimate Great Moth',
                               'Hercules Beetle',
                               'Killer Needle',
                               'Basic Insect',
                               'Parasite Paracide'],
                      'pre': 'Ehehehehe! My insects will crawl all over your strategy!',
                      'win': "That's the sweet sting of an insect expert!",
                      'loss': 'No! My beautiful bugs! How disgusting!'},
 'arkana': {'name': 'Arkana',
            'pool': ['Dark Magician',
                     'Dark Eradicator Warlock',
                     'Skilled Dark Magician',
                     'Double Coston',
                     'Legion the Fiend Jester'],
            'pre': 'Witness the true master of dark magic as your hopes disappear in smoke!',
            'win': 'Magnificent! My dark stage has claimed another victim!',
            'loss': 'Impossible... My perfect dark magic act has been ruined!'},
 'espa_roba': {'name': 'Espa Roba',
               'pool': ['Jinzo',
                        'Jinzo #7',
                        'Mechanicalchaser',
                        'Cyber Raider',
                        'The Fiend Megacyber',
                        'Reflect Bounder'],
               'pre': 'My ESP already told me how this duel ends. You lose!',
               'win': 'See? I predicted every move you made!',
               'loss': 'What?! My ESP was way off!'},
 'lumis': {'name': 'Lumis',
           'pool': ['Mask of Darkness',
                    'Melchid the Four-Face Beast',
                    'Masked Beast Des Gardius',
                    'Grand Tiki Elder',
                    'Mask of Dispel',
                    'The Masked Beast'],
           'pre': 'From behind the mask, we will bury you in fear and deception!',
           'win': 'Our masked strategy was flawless!',
           'loss': 'Our shadows were split apart...'},
 'seeker': {'name': 'Seeker',
            'pool': ['Exodia the Forbidden One',
                     'Right Arm of the Forbidden One',
                     'Left Arm of the Forbidden One',
                     'Right Leg of the Forbidden One',
                     'Left Leg of the Forbidden One',
                     'Dark Necrofear'],
            'pre': "I've hunted rare cards everywhere. Now I'll claim victory from you too.",
            'win': 'Another duel, another prize for Seeker.',
            'loss': 'No... My hunt ends here?'},
 'strings': {'name': 'Strings',
             'pool': ['Slifer the Sky Dragon',
                      'Revival Jam',
                      'Jam Defender',
                      'Humanoid Slime',
                      'Humanoid Worm Drake',
                      'Card of Safe Return'],
             'pre': '...',
             'win': 'Strings says nothing. Slifer has already spoken.',
             'loss': 'The puppet falls silent as the gods abandon the field.'},
 'umbra': {'name': 'Umbra',
           'pool': ['Mask of Darkness',
                    'The Masked Beast',
                    'Masked Beast Des Gardius',
                    'Masked Sorcerer',
                    'Dark Necrofear',
                    'Grand Tiki Elder'],
           'pre': 'Let the shadows swallow the arena whole!',
           'win': 'Darkness is all that remains!',
           'loss': 'The darkness... has been pierced!'},
 'ishizu_ishtar': {'name': 'Ishizu Ishtar',
                   'pool': ["Gravekeeper's Chief",
                            "Gravekeeper's Guard",
                            "Gravekeeper's Spear Soldier",
                            'Keldo',
                            'Mudora',
                            'Exchange of the Spirit'],
                   'pre': "Destiny's sands are already in motion. Let us see where they lead.",
                   'win': 'As foretold, the duel has reached its destined conclusion.',
                   'loss': 'So the future has shifted... then there is still hope.'},
 'marik_ishtar': {'name': 'Marik Ishtar',
                  'pool': ['The Winged Dragon of Ra',
                           'Lava Golem',
                           'Helpoemer',
                           'Drillago',
                           'Revival Jam',
                           'Holding Arms'],
                  'pre': 'Scream all you want. Your pain will only make this duel sweeter.',
                  'win': 'Delicious. Your despair was worth every second.',
                  'loss': "You dare deny me?! I'll carve that defiance out of you next time!"},
 'odion': {'name': 'Odion',
           'pool': ['Mystical Beast of Serket',
                    'Temple of the Kings',
                    'Embodiment of Apophis',
                    'Exchange of the Spirit',
                    "Gravekeeper's Guard",
                    "Gravekeeper's Chief"],
           'pre': 'I duel in silence and discipline. Prepare yourself.',
           'win': 'Discipline has carried the day.',
           'loss': 'Your resolve overcame mine. Well dueled.'},
 'seto_kaiba': {'name': 'Seto Kaiba',
                'pool': ['Blue-Eyes White Dragon',
                         'Obelisk the Tormentor',
                         'XYZ-Dragon Cannon',
                         'Vorse Raider',
                         'La Jinn the Mystical Genie of the Lamp',
                         'Blade Knight',
                         'Kaiser Glider'],
                'pre': "You're standing in the path of the ultimate duelist. Try not to embarrass yourself.",
                'win': 'Predictable. No one outduels Seto Kaiba.',
                'loss': 'Tch. Enjoy this moment while it lasts.'},
 'yami_bakura': {'name': 'Yami Bakura',
                 'pool': ['Dark Necrofear',
                          'The Earl of Demise',
                          'Headless Knight',
                          'Diabound Kernel',
                          'Destiny Board',
                          'Dark Spirit of the Silent'],
                 'pre': "Let's see how long your soul lasts once the shadows close in.",
                 'win': 'Your terror was exquisite.',
                 'loss': "Hmph. You've only delayed the darkness."},
 'yami_yugi': {'name': 'Yami Yugi',
               'pool': ['Dark Magician',
                        'Slifer the Sky Dragon',
                        'Valkyrion the Magna Warrior',
                        'Magician of Black Chaos',
                        'Black Luster Soldier',
                        'Kuriboh',
                        'Summoned Skull',
                        'Buster Blader'],
               'pre': "This is where our souls meet in battle. Come at me with everything you've got!",
               'win': 'The cards answered my call. Victory is mine.',
               'loss': 'Excellent duel. You earned that victory with true strength.'}}


AUTO_DUELIST_SPECS = json.loads(r"""[["maximillion_pegasus", "Maximillion Pegasus", "toon", "Toon"], ["panik", "PaniK", "panik_fiend", "fiend"], ["paradox_brothers", "Paradox Brothers", "labyrinth", "labyrinth"], ["rebecca_hawkins", "Rebecca Hawkins", "gem_burn", "burn-and-dragon"], ["solomon_muto", "Solomon Muto", "dark_magician", "classic"], ["mokuba_kaiba", "Mokuba Kaiba", "blue_eyes", "Kaiba family"], ["noah_kaiba", "Noah Kaiba", "machine_brute", "virtual world"], ["vivian_wong", "Vivian Wong", "harpie", "duelist kingdom"], ["valon", "Valon", "armor", "armor"], ["alister", "Alister", "machine_brute", "machine"], ["raphael", "Raphael", "seal_guardian", "guardian"], ["dartz", "Dartz", "seal_guardian", "Orichalcos"], ["zigfried_von_schroeder", "Zigfried von Schroeder", "valkyrie", "Valkyrie"], ["leon_von_schroeder", "Leon von Schroeder", "fairy_dragon", "storybook"], ["anubis", "Anubis", "sphinx", "ancient"], ["aigami", "Aigami", "cubic", "Cubic"], ["jaden_yuki", "Jaden Yuki", "elemental_hero", "elemental hero"], ["jaden_yubel", "Jaden/Yubel", "yubel", "Yubel"], ["alexis_rhodes", "Alexis Rhodes", "cyber_angel", "ritual-cyber"], ["chazz_princeton", "Chazz Princeton", "ojama", "Ojama-Armed Dragon"], ["bastion_misawa", "Bastion Misawa", "chemist", "science"], ["syrus_truesdale", "Syrus Truesdale", "roid", "roid"], ["zane_truesdale", "Zane Truesdale", "cyber_dragon", "Cyber Dragon"], ["atticus_rhodes", "Atticus Rhodes", "red_eyes_darkness", "Red-Eyes"], ["aster_phoenix", "Aster Phoenix", "destiny_hero", "Destiny HERO"], ["jesse_anderson", "Jesse Anderson", "crystal_beast", "Crystal Beast"], ["tyranno_hassleberry", "Tyranno Hassleberry", "dino_gx", "dinosaur"], ["blair_flannigan", "Blair Flannigan", "fairy_heal", "fairy"], ["dr_vellian_crowler", "Dr. Vellian Crowler", "ancient_gear", "Ancient Gear"], ["banner", "Banner", "graveyard_pharaoh", "alchemical"], ["camula", "Camula", "vampire", "vampire"], ["tania", "Tania", "amazoness", "Amazoness"], ["titan", "Titan", "archfiend", "dark fiend"], ["nightshroud", "Nightshroud", "red_eyes_darkness", "dark dragon"], ["amnael", "Amnael", "graveyard_pharaoh", "Helios"], ["adrian_gecko", "Adrian Gecko", "cloudian", "Cloudian"], ["axel_brodie", "Axel Brodie", "volcanic", "Volcanic"], ["jim_crocodile_cook", "Jim Crocodile Cook", "fossil", "Fossil"], ["sartorius_kumar", "Sartorius Kumar", "arcana_force", "Arcana Force"], ["yubel", "Yubel", "yubel", "Yubel"], ["marcel_bonaparte", "Marcel Bonaparte", "yubel", "possession"], ["professor_bonaparte", "Professor Bonaparte", "toy_box", "toy"], ["kagemaru", "Kagemaru", "archfiend", "dark fiend"], ["thelonious_viper", "Thelonious Viper", "volcanic", "survival"], ["belowski", "Belowski", "cloudian", "Cloudian"], ["mindy", "Mindy", "friendship_fairy", "cheerful"], ["jasmine", "Jasmine", "friendship_fairy", "cheerful"], ["chumley_huffington", "Chumley Huffington", "beast_forest", "beast"], ["abidos_the_third", "Abidos the Third", "graveyard_pharaoh", "pharaonic"], ["fonda_fontaine", "Fonda Fontaine", "amazoness", "Amazoness"], ["harrington_rosewood", "Harrington Rosewood", "beast_forest", "beast"], ["yusei_fudo", "Yusei Fudo", "synchron", "Synchron"], ["jack_atlas", "Jack Atlas", "resonator", "Resonator"], ["akiza_izinski", "Akiza Izinski", "black_rose", "Rose Dragon"], ["crow_hogan", "Crow Hogan", "blackwing", "Blackwing"], ["leo", "Leo", "morphtronic", "Morphtronic"], ["luna", "Luna", "fairy_dragon", "forest-fairy"], ["tetsu_trudge", "Tetsu Trudge", "goyo_police", "police"], ["carly_carmine", "Carly Carmine", "fortune_lady", "Fortune Lady"], ["dark_signer_carly_carmine", "Dark Signer Carly Carmine", "fortune_lady", "Dark Signer"], ["kalin_kessler", "Kalin Kessler", "infernity", "Infernity"], ["dark_signer_kalin_kessler", "Dark Signer Kalin Kessler", "infernity", "Dark Signer"], ["roman_goodwin", "Roman Goodwin", "earthbound_monkey", "Earthbound"], ["rex_goodwin", "Rex Goodwin", "earthbound_spider", "Earthbound"], ["misty_tredwell", "Misty Tredwell", "earthbound_spider", "reptile"], ["dark_signer_misty_tredwell", "Dark Signer Misty Tredwell", "earthbound_spider", "Dark Signer"], ["devack", "Devack", "earthbound_monkey", "Earthbound"], ["dark_signer_devack", "Dark Signer Devack", "earthbound_monkey", "Dark Signer"], ["greiger", "Greiger", "machine_brute", "reactor"], ["dark_signer_greiger", "Dark Signer Greiger", "earthbound_giant", "Dark Signer"], ["sayer", "Sayer", "psychic_wheeler", "Psychic"], ["lazar", "Lazar", "machine_brute", "machine"], ["sherry_leblanc", "Sherry LeBlanc", "fleur", "Fleur"], ["bruno", "Bruno", "tg", "T.G."], ["antinomy", "Antinomy", "tg", "T.G."], ["aporia", "Aporia", "meklord", "Meklord"], ["paradox", "Paradox", "malefic", "Malefic"], ["placido", "Placido", "meklord", "Meklord"], ["jose", "Jose", "meklord", "Meklord"], ["luciano", "Luciano", "meklord", "Meklord"], ["andre", "Andre", "nordic", "Nordic"], ["jean", "Jean", "nordic", "Nordic"], ["breo", "Breo", "nordic", "Nordic"], ["clark_smith", "Clark Smith", "beast_forest", "beast"], ["miller", "Miller", "machine_brute", "machine"], ["bolt_tanner", "Bolt Tanner", "beast_forest", "battle-beast"], ["yuma_tsukumo", "Yuma Tsukumo", "gagaga_utopia", "Utopia"], ["astral", "Astral", "gagaga_utopia", "Utopia"], ["kite_tenjo", "Kite Tenjo", "photon", "Photon"], ["reginald_kastle", "Reginald Kastle", "shark", "Shark"], ["rio_kastle", "Rio Kastle", "shark", "ice-shark"], ["bronk_stone", "Bronk Stone", "standard_beatdown", "beatdown"], ["tori_meadows", "Tori Meadows", "friendship_fairy", "fairy"], ["cathy_katherine", "Cathy Katherine", "cat_world", "cat"], ["flip_turner", "Flip Turner", "standard_critters", "gimmick"], ["caswell_francis", "Caswell Francis", "standard_spellcaster", "bookish"], ["anna_kaboom", "Anna Kaboom", "train", "train"], ["trey", "Trey", "chronomaly", "Chronomaly"], ["quattro", "Quattro", "gimmick_puppet", "Gimmick Puppet"], ["quinton", "Quinton", "photon", "space-machine"], ["nistro", "Nistro", "heroic", "Heroic"], ["dextra", "Dextra", "heroic", "Butterspy"], ["vetrix", "Vetrix", "heraldic", "Heraldic Beast"], ["dr_faker", "Dr. Faker", "photon", "galaxy"], ["mr_heartland", "Mr. Heartland", "shark", "Number"], ["alito", "Alito", "barians_alito", "Battlin Boxer"], ["girag", "Girag", "barians_girag", "Hand"], ["mizar", "Mizar", "barians_mizar", "Tachyon"], ["dumon", "Dumon", "barians_dumon", "Star Seraph"], ["vector", "Vector", "barians_vector", "Umbral Horror"], ["nash", "Nash", "shark", "Barian-shark"], ["marin", "Marin", "shark", "water"], ["orbital_7", "Orbital 7", "orbital_7", "orbital"], ["nelson_andrews", "Nelson Andrews", "standard_critters", "mascot"], ["princess_cologne", "Princess Cologne", "cat_world", "doll"], ["eliphas", "Eliphas", "gagaga_utopia", "Number"], ["yuya_sakaki", "Yuya Sakaki", "performapal", "Performapal"], ["zuzu_boyle", "Zuzu Boyle", "melodious", "Melodious"], ["gong_strong", "Gong Strong", "superheavy", "Superheavy Samurai"], ["sylvio_sawatari", "Sylvio Sawatari", "performapal", "showman"], ["declan_akaba", "Declan Akaba", "ddd", "D/D/D"], ["shay_obsidian", "Shay Obsidian", "raidraptor", "Raidraptor"], ["sora_perse", "Sora Perse", "frightfur", "Frightfur"], ["celina", "Celina", "lunalight", "Lunalight"], ["yuto", "Yuto", "phantom_knights", "Phantom Knights"], ["yugo", "Yugo", "speedroid", "Speedroid"], ["yuri", "Yuri", "predaplant", "Predaplant"], ["rin", "Rin", "speedroid", "Windwitch"], ["lulu_obsidian", "Lulu Obsidian", "lyrilusc", "Lyrilusc"], ["moon_shadow", "Moon Shadow", "ninja", "ninja"], ["dennis_mcfield", "Dennis McField", "performapal", "performer"], ["julia_krystal", "Julia Krystal", "gem_knight", "Gem-Knight"], ["kit_blade", "Kit Blade", "xsaber", "X-Saber"], ["shuzo_hiragi", "Shuzo Hiragi", "performapal", "entertainment"], ["yoko_sakaki", "Yoko Sakaki", "performapal", "entertainment"], ["jean_michel_roget", "Jean-Michel Roget", "machine_brute", "security"], ["barrett", "Barrett", "raidraptor", "Obsidian resistance"], ["sergey_volkov", "Sergey Volkov", "earthbound_spider", "Earthbound"], ["melissa_claire", "Melissa Claire", "friendship_fairy", "broadcast"], ["aura_sentia", "Aura Sentia", "fairy_heal", "prediction"], ["dipper_orion", "Dipper O'Rion", "photon", "stellar"], ["grace_tyler", "Grace Tyler", "amazoness", "tag-duel"], ["gloria_tyler", "Gloria Tyler", "amazoness", "tag-duel"], ["riley_akaba", "Riley Akaba", "fairy_heal", "enchanted"], ["leo_akaba", "Leo Akaba", "ddd", "dimension"], ["z_arc", "Z-ARC", "performapal", "supreme king"], ["yusaku_fujiki", "Yusaku Fujiki", "code_talker", "Code Talker"], ["playmaker", "Playmaker", "code_talker", "Code Talker"], ["ai", "Ai", "ignister", "@Ignister"], ["playmaker_and_ai", "Playmaker and Ai", "ignister", "@Ignister"], ["skye_zaizen", "Skye Zaizen", "trickstar", "Trickstar"], ["blue_angel", "Blue Angel", "trickstar", "Trickstar"], ["blue_maiden", "Blue Maiden", "marincess", "Marincess"], ["soulburner", "Soulburner", "salamangreat", "Salamangreat"], ["the_gore", "The Gore", "gouki", "Gouki"], ["varis", "Varis", "rokket", "Rokket"], ["spectre", "Spectre", "sunavalon", "Sunavalon"], ["ghost_gal", "Ghost Gal", "altergeist", "Altergeist"], ["emma_bessho", "Emma Bessho", "altergeist", "Altergeist"], ["akira_zaizen", "Akira Zaizen", "machine_brute", "corporate"], ["naoki_shima", "Naoki Shima", "beast_forest", "beast-link"], ["bohman", "Bohman", "hydradrive", "Hydradrive"], ["roboppi", "Roboppi", "appliancer", "Appliancer"], ["blood_shepherd", "Blood Shepherd", "drone", "Drone"], ["windy", "Windy", "code_talker", "storm-cyberse"], ["lightning", "Lightning", "armatos", "Armatos Legio"], ["pandor", "Pandor", "rokket", "Rokket"], ["kusanagi", "Kusanagi", "code_talker", "Cyberse"], ["yuga_ohdo", "Yuga Ohdo", "seven_road", "Seven Road"], ["luke", "Luke", "dragias", "Dragias"], ["romin_kassidy", "Romin Kassidy", "psychic_music", "music"], ["gavin_sogetsu", "Gavin Sogetsu", "samurai_yokai", "yokai-samurai"], ["roa_kassidy", "Roa Kassidy", "fiend_roa", "Royal Rebels"], ["nail_saionji", "Nail Saionji", "maximum_nail", "Maximum"], ["asana_mutsuba", "Asana Mutsuba", "wyrm_asana", "construction wyrm"], ["yuo_goha", "Yuo Goha", "maximum_nail", "Maximum"], ["yuga_goha", "Yuga Goha", "seven_road", "spellcaster"], ["yuran_goha", "Yuran Goha", "wyrm_asana", "wyrm"], ["tiger", "Tiger", "dragias", "dragon"], ["mimi_imimi", "Mimi Imimi", "standard_spellcaster", "office-ace"], ["yosh_imimi", "Yosh Imimi", "fiend_roa", "showbiz"], ["sebastian", "Sebastian", "machine_brute", "machine"], ["goha_yuga", "Goha Yuga", "goha_yuga", "fusion-spellcaster"], ["yudias_velgear", "Yudias Velgear", "galactica", "Galactica"], ["yuhi_ohdo", "Yuhi Ohdo", "jointech", "Jointech"], ["yuamu_ohdo", "Yuamu Ohdo", "rush_spellcaster", "spellcaster"], ["zwijo_zir_velgear", "Zwijo Zir Velgear", "velgear", "Voidvelg"], ["mitsuko_hiramori", "Mitsuko Hiramori", "jointech", "jointech"], ["manabu_sogetsu", "Manabu Sogetsu", "samurai_yokai", "law-enforcer"], ["epoch", "Epoch", "rush_spellcaster", "mystic"], ["tremolo", "Tremolo", "velgear", "Voidvelg"], ["phaser", "Phaser", "galactica", "space"], ["dinois_aleir", "Dinois Aleir", "beast_forest", "beast"], ["alyssa", "Alyssa", "standard_botanical", "botanical"], ["andrew", "Andrew", "standard_warrior", "warrior"], ["ashley", "Ashley", "standard_critters", "critter"], ["bella", "Bella", "standard_elements", "elemental"], ["chloe", "Chloe", "standard_machine", "machine"], ["christine", "Christine", "standard_spellcaster", "spellcaster"], ["daniel", "Daniel", "standard_warrior", "warrior"], ["david", "David", "standard_beatdown", "beatdown"], ["emma", "Emma", "standard_spellcaster", "spellcaster"], ["emmeline", "Emmeline", "standard_botanical", "botanical"], ["erika", "Erika", "standard_machine", "machine"], ["evan", "Evan", "standard_beatdown", "beatdown"], ["fani", "Fani", "standard_botanical", "botanical"], ["hailey", "Hailey", "standard_spellcaster", "spellcaster"], ["handsome_stan", "Handsome Stan", "standard_beatdown", "showy beatdown"], ["henry", "Henry", "standard_warrior", "warrior"], ["jay", "Jay", "standard_critters", "critter"], ["jess", "Jess", "standard_spellcaster", "spellcaster"], ["josh", "Josh", "standard_warrior", "warrior"], ["knight_of_hanoi", "Knight of Hanoi", "rokket", "Hanoi"], ["kylie", "Kylie", "standard_botanical", "botanical"], ["liam", "Liam", "standard_machine", "machine"], ["logan", "Logan", "standard_beatdown", "beatdown"], ["madison", "Madison", "standard_spellcaster", "spellcaster"], ["margaret", "Margaret", "standard_botanical", "botanical"], ["mickey", "Mickey", "standard_critters", "critter"], ["meg", "Meg", "standard_critters", "critter"], ["mia", "Mia", "standard_elements", "elemental"], ["nick", "Nick", "standard_critters", "critter"], ["oliver", "Oliver", "standard_beatdown", "beatdown"], ["ruby", "Ruby", "standard_elements", "elemental"], ["sector_security", "Sector Security", "standard_machine", "security"], ["sunny", "Sunny", "standard_botanical", "botanical"], ["theo", "Theo", "standard_elements", "elemental"], ["vega", "Vega", "standard_machine", "machine"], ["wild_dan", "Wild Dan", "standard_underworld", "underworld"], ["zachary", "Zachary", "standard_beatdown", "beatdown"], ["brave_battler", "Brave Battler", "gouki", "battle"]]""")

for duelist_key, duelist_name, pool_key, theme_label in AUTO_DUELIST_SPECS:
    DUELIST_DATA.setdefault(duelist_key, make_auto_duelist_entry(duelist_name, pool_key, theme_label))

if len(DUELIST_DATA) != 255:
    raise RuntimeError(f"Expected 255 duelists in DUELIST_DATA, found {len(DUELIST_DATA)}")


DUELIST_DATA.update({
    "lester": make_auto_duelist_entry("Lester", "meklord", "Meklord"),
    "declan_akaba": make_auto_duelist_entry("Declan Akaba", "ddd", "D/D/D"),
    "carly_carmine": {
        "name": "Carly Carmine",
        "pool": [
            "Fortune Lady Light",
            "Fortune Lady Wind",
            "Fortune Lady Earth",
            "Fortune Lady Water",
            "Fortune Lady Fire",
            "Fortune Lady Dark",
            "Fortune Lady Past",
            "Fortune Lady Every",
            "Fortune Fairy Hikari",
            "Fortune Fairy En",
            "Fortune Fairy Ann",
            "Fortune Fairy Chee",
            "Fortune Fairy Hu",
            "Fortune Fairy Swee",
        ],
        "pre": "The future is clear. My Fortune Ladies and Fortune Fairies will decide this duel!",
        "win": "Just as predicted. Fortune Lady and Fortune Fairy magic carried me to victory!",
        "loss": "My reading was off... but my Fortune Ladies and Fortune Fairies will shine again!",
    },
    "jakob": make_auto_duelist_entry("Jakob", "meklord", "Meklord"),
    "jaden_yuki": make_auto_duelist_entry("Jaden Yuki", "elemental_hero", "Elemental HERO"),
    "supreme_king": make_auto_duelist_entry("Supreme King", "evil_hero", "Evil HERO"),
    "yami_marik": {
        "name": "Yami Marik",
        "pool": [
            "The Winged Dragon of Ra",
            "The Winged Dragon of Ra - Sphere Mode",
            "The Winged Dragon of Ra - Immortal Phoenix",
            "Egyptian God Slime",
            "Guardian Slime",
            "Reactor Slime",
        ],
        "pre": "I offer your monsters to Ra. Scream as the sun god descends!",
        "win": "Ra has judged you unworthy. Burn that defeat into your memory.",
        "loss": "Impossible... even Ra's divine fire couldn't reduce you to ash!",
    },
    "primo": make_auto_duelist_entry("Primo", "meklord", "Meklord"),
})


DUELIST_ALIAS_KEYS = {
    "duke": "duke_devlin",
    "devlin": "duke_devlin",
    "joey": "joey_wheeler",
    "jonouchi": "joey_wheeler",
    "katsuya": "joey_wheeler",
    "katsuya_jonouchi": "joey_wheeler",
    "joey_wheeler": "joey_wheeler",
    "serenity": "serenity_wheeler",
    "shizuka": "serenity_wheeler",
    "tea": "tea_gardner",
    "anzu": "tea_gardner",
    "anzu_mazaki": "tea_gardner",
    "tea_gardner": "tea_gardner",
    "tea_gardner_": "tea_gardner",
    "teagardner": "tea_gardner",
    "reginald": "reginald_kastle",
    "reginald_kastle": "reginald_kastle",
    "reginaldsharkkastle": "reginald_kastle",
    "reginald_shark_kastle": "reginald_kastle",
    "reginald shark kastle": "reginald_kastle",
    "shark": "reginald_kastle",
    "shark_kastle": "reginald_kastle",
    "sharkkastle": "reginald_kastle",
    "kamishiro_ryoga": "reginald_kastle",
    "ryoga_kamishiro": "reginald_kastle",
    "supremeking": "supreme_king",
    "supreme_king": "supreme_king",
    "supreme king": "supreme_king",
    "haou": "supreme_king",
    "nash": "nash",
    "tristan": "tristan_taylor",
    "honda": "tristan_taylor",
    "hiroto_honda": "tristan_taylor",
    "yugi": "yugi_muto",
    "yugi_moto": "yugi_muto",
    "yugi_mutou": "yugi_muto",
    "muto_yugi": "yugi_muto",
    "mutou_yugi": "yugi_muto",
    "atem": "yami_yugi",
    "pharaoh": "yami_yugi",
    "dark_yugi": "yami_yugi",
    "yugi_oh": "yami_yugi",
    "banditkeith": "bandit_keith",
    "keith": "bandit_keith",
    "keith_howard": "bandit_keith",
    "mai": "mai_valentine",
    "mai_kujyaku": "mai_valentine",
    "mako": "mako_tsunami",
    "ryota_kajiki": "mako_tsunami",
    "rex": "rex_raptor",
    "dinosaur_ryuzaki": "rex_raptor",
    "weevil": "weevil_underwood",
    "insector_haga": "weevil_underwood",
    "espa": "espa_roba",
    "roba": "espa_roba",
    "lumisandumbra": "lumis",
    "lumis_umbra": "lumis",
    "umbra_lumis": "umbra",
    "seeker": "seeker",
    "rare_hunter": "seeker",
    "strings": "strings",
    "ishizu": "ishizu_ishtar",
    "marik": "marik_ishtar",
    "malik": "marik_ishtar",
    "odion": "odion",
    "rishid": "odion",
    "kaiba": "seto_kaiba",
    "seto": "seto_kaiba",
    "seto_kaiba": "seto_kaiba",
    "bakura": "yami_bakura",
    "ryo_bakura": "yami_bakura",
    "yamibakura": "yami_bakura",
    "darkbakura": "yami_bakura",
    "yamiyugi": "yami_yugi",
    "yami_yugi": "yami_yugi",
    "jaden": "jaden_yuki",
    "judai": "jaden_yuki",
    "judai_yuki": "jaden_yuki",
    "jaden_yuki": "jaden_yuki",
    "jaden_yubel": "jaden_yubel",
    "yubel_jaden": "jaden_yubel",
    "alexis": "alexis_rhodes",
    "asuka_tenjoin": "alexis_rhodes",
    "chazz": "chazz_princeton",
    "manjoume": "chazz_princeton",
    "zane": "zane_truesdale",
    "ryo_marufuji": "zane_truesdale",
    "syrus": "syrus_truesdale",
    "sho_marufuji": "syrus_truesdale",
    "aster": "aster_phoenix",
    "edo_phoenix": "aster_phoenix",
    "jesse": "jesse_anderson",
    "johan_andersen": "jesse_anderson",
    "crowler": "dr_vellian_crowler",
    "cronoss": "dr_vellian_crowler",
    "yusei": "yusei_fudo",
    "yusei_fudo": "yusei_fudo",
    "jack": "jack_atlas",
    "jack_atlas": "jack_atlas",
    "akiza": "akiza_izinski",
    "aki_izayoi": "akiza_izinski",
    "crow": "crow_hogan",
    "carly": "carly_carmine",
    "kiryu_kyosuke": "kalin_kessler",
    "kalin": "kalin_kessler",
    "bruno": "bruno",
    "antinomy": "antinomy",
    "yuma": "yuma_tsukumo",
    "yuma_tsukumo": "yuma_tsukumo",
    "astral": "astral",
    "kite": "kite_tenjo",
    "kaito_tenjo": "kite_tenjo",
    "rio": "rio_kastle",
    "rio_kamishiro": "rio_kastle",
    "anna": "anna_kaboom",
    "anna_kozuki": "anna_kaboom",
    "yuya": "yuya_sakaki",
    "yuya_sakaki": "yuya_sakaki",
    "yuzu_hiiragi": "zuzu_boyle",
    "zuzu": "zuzu_boyle",
    "gongenzaka": "gong_strong",
    "gong": "gong_strong",
    "reiiji_akaba": "declan_akaba",
    "declan": "declan_akaba",
    "shun_kurosaki": "shay_obsidian",
    "shay": "shay_obsidian",
    "sora": "sora_perse",
    "serena": "celina",
    "yuto": "yuto",
    "yugo": "yugo",
    "yuri": "yuri",
    "ruri_kurosaki": "lulu_obsidian",
    "lulu": "lulu_obsidian",
    "yusaku": "yusaku_fujiki",
    "playmaker": "playmaker",
    "ai": "ai",
    "a_i": "ai",
    "aoi_zaizen": "skye_zaizen",
    "skye": "skye_zaizen",
    "blue_angel": "blue_angel",
    "blue_maiden": "blue_maiden",
    "soulburner": "soulburner",
    "takeru_homura": "soulburner",
    "revolver": "varis",
    "varis": "varis",
    "ryoken_kogami": "varis",
    "go_onizuka": "the_gore",
    "gore": "the_gore",
    "yuga": "yuga_ohdo",
    "yuga_ohdo": "yuga_ohdo",
    "luke": "luke",
    "tatsuhisa_kamijo": "luke",
    "romin": "romin_kassidy",
    "gakuto_sogetsu": "gavin_sogetsu",
    "gavin": "gavin_sogetsu",
    "roa": "roa_kassidy",
    "nail": "nail_saionji",
    "yudias": "yudias_velgear",
    "yuhi": "yuhi_ohdo",
    "yuamu": "yuamu_ohdo",
    "zwijo": "zwijo_zir_velgear",
}

DUELIST_FILENAME_NOISE_TOKENS = {
    "dm", "gx", "5ds", "zexal", "arc", "v", "arcv", "vrains", "sevens", "go", "rush",
    "anime", "manga", "tcg", "ocg", "game", "games", "duel", "duelist", "duelists",
    "portrait", "avatar", "full", "body", "new", "old", "alt", "alternate", "render", "image",
    "normal", "hard", "campaign", "ground", "grounds", "dsod", "movie",
}


def slugify_duelist_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return cleaned or "duelist"


def normalize_duelist_lookup_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def duelist_filename_lookup_variants(value: str) -> List[str]:
    """Return safe lookup variants for Duelists-folder image stems.

    The Duelists folder is user-managed, so a portrait may be named with a full
    name, dub/Japanese name, series tag, parenthetical label, or small suffix such
    as "Yugi Muto (DM)" or "Carly Carmine - Dark Signer". These variants are
    intentionally conservative: they only normalize labels and aliases, then the
    resolver still requires an exact hit in DUELIST_LOOKUP_MAP.
    """
    raw = str(value or "").strip()
    if not raw:
        return []

    bases: List[str] = [raw]
    stripped_parenthetical = re.sub(r"\s*[\(\[\{].*?[\)\]\}]\s*", " ", raw).strip()
    if stripped_parenthetical and stripped_parenthetical != raw:
        bases.append(stripped_parenthetical)
    for separator in (" - ", " — ", " – ", " | ", "_"):
        if separator in raw:
            for part in raw.split(separator):
                part = part.strip()
                if part:
                    bases.append(part)

    variants: List[str] = []
    seen: set[str] = set()

    def add(candidate: str):
        normalized = normalize_duelist_lookup_key(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            variants.append(candidate)

    for base in bases:
        cleaned = re.sub(r"[^A-Za-z0-9@]+", " ", base).strip()
        if not cleaned:
            continue
        add(cleaned)
        slug = slugify_duelist_key(cleaned)
        add(slug)
        tokens = [token for token in re.split(r"[^a-z0-9@]+", cleaned.lower()) if token]
        if not tokens:
            continue

        trimmed = list(tokens)
        while trimmed and trimmed[-1] in DUELIST_FILENAME_NOISE_TOKENS:
            trimmed.pop()
        while trimmed and trimmed[0] in DUELIST_FILENAME_NOISE_TOKENS:
            trimmed.pop(0)
        if trimmed and trimmed != tokens:
            add("_".join(trimmed))

        filtered = [token for token in tokens if token not in DUELIST_FILENAME_NOISE_TOKENS]
        if filtered and filtered != tokens:
            add("_".join(filtered))

        if len(tokens) >= 3 and tokens[-2:] == ["dark", "signer"]:
            add("dark_signer_" + "_".join(tokens[:-2]))
        if len(tokens) >= 3 and tokens[:2] == ["dark", "signer"]:
            add("_".join(tokens[2:]))
        if len(tokens) >= 2 and tokens[-1] in {"ds", "dsigner"}:
            add("dark_signer_" + "_".join(tokens[:-1]))

        if "and" in tokens:
            add("_".join(token for token in tokens if token != "and"))
        if "with" in tokens:
            add("_".join(token for token in tokens if token != "with"))

    return variants


def _build_duelist_lookup_tables() -> Tuple[Dict[str, str], Dict[str, set[str]]]:
    lookup: Dict[str, str] = {}
    tokens_by_key: Dict[str, set[str]] = {}
    for canonical_key, data in DUELIST_DATA.items():
        name = str(data.get("name", canonical_key)).strip()
        candidates = {
            canonical_key,
            slugify_duelist_key(canonical_key),
            normalize_duelist_lookup_key(canonical_key),
            slugify_duelist_key(name),
            normalize_duelist_lookup_key(name),
        }
        for candidate in candidates:
            for variant in duelist_filename_lookup_variants(str(candidate)) or [str(candidate)]:
                normalized = normalize_duelist_lookup_key(variant)
                if normalized:
                    lookup[normalized] = canonical_key
        name_tokens = {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}
        key_tokens = {token for token in re.split(r"[^a-z0-9]+", canonical_key.lower()) if token}
        tokens_by_key[canonical_key] = name_tokens | key_tokens
    for alias, canonical_key in DUELIST_ALIAS_KEYS.items():
        if canonical_key not in DUELIST_DATA:
            continue
        for variant in duelist_filename_lookup_variants(alias) or [str(alias)]:
            normalized_alias = normalize_duelist_lookup_key(variant)
            if normalized_alias:
                lookup[normalized_alias] = canonical_key
    return lookup, tokens_by_key


DUELIST_LOOKUP_MAP, DUELIST_TOKENS_BY_KEY = _build_duelist_lookup_tables()


def resolve_dueling_grounds_source_key(duelist_key: str, display_name: Optional[str] = None) -> Optional[str]:
    """Resolve a Duelists-folder image stem to known duelist data.

    This still avoids fuzzy token-overlap matching, but it now accepts common safe
    filename variations: full names vs short names, dub/Japanese aliases, media
    suffixes, parenthetical labels, and reordered "Dark Signer" labels.
    """
    candidates = [duelist_key, display_name]
    for candidate in candidates:
        for variant in duelist_filename_lookup_variants(candidate or ""):
            normalized = normalize_duelist_lookup_key(variant)
            if not normalized:
                continue
            resolved = DUELIST_LOOKUP_MAP.get(normalized)
            if resolved:
                return resolved
    return None
