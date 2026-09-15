# CyberpunkAgent — V plays Cyberpunk 2077 on their own

**[Version française](README.md)**

An autonomous agent that plays **Cyberpunk 2077** for you, driven by a **local language model** (Ollama, nothing leaves your PC) or, if you prefer, an OpenAI or Anthropic API key: it follows quests, moves around, drives, talks, fights, heals, loots everything, manages inventory, crafts, sells, buys, answers the phone, listens to the radio, and hands control back whenever you ask.

> **Status: version 0.1.0, experimental.** The agent genuinely plays for tens of minutes at a stretch, but it is not perfect: it sometimes dies (and reloads the last save by itself), gets stuck in some interiors, and does not race. Every session is logged so behaviours can be improved.

---

## Contents

1. [What the agent does](#what-the-agent-does)
2. [How it works](#how-it-works)
3. [Requirements](#requirements)
4. [Two-minute install (exe)](#two-minute-install-exe)
5. [Manual install (sources)](#manual-install-sources)
6. [Usage](#usage)
7. [Keys and safety](#keys-and-safety)
8. [Configuration](#configuration)
9. [Logs and diagnostics](#logs-and-diagnostics)
10. [Known limits](#known-limits)
11. [Code layout](#code-layout)
12. [Building the release](#building-the-release)
13. [FAQ](#faq)
14. [Licence and credits](#licence-and-credits)

---

## What the agent does

| Area | Behaviour |
|---|---|
| **Quests** | Follows the tracked objective, walks to the marker along the game's navmesh, switches quest when the objective is unreachable or too far, remembers reached markers, **prefers quests matching V's level** (recommended level read from the game journal). |
| **Movement** | Walks, sprints, dodges, jumps, unsticks itself; when locked in (room, rooftop) it looks for a way out: doors, "Open / Activate" prompts, probes in 8 directions. |
| **Driving** | Summons one of V's vehicles at random (car or bike), mounts, engages **autodrive** towards the objective when it is more than 500 m away, dismounts on arrival. Fast travel as a fallback for very distant objectives. |
| **Dialogue** | Reads the displayed choices, picks with the language model (consistent with the quest context), avoids loops, never orders at street food stands. |
| **Combat** | Melee first (best melee weapon, blunt weapons preferred), stealth approach with silent takedowns when undetected, grenades on groups, knife throws, firearm beyond 12 m or against elevated targets, **quickhacks** (best available), iconic cyberware, frequent dodges and dashes, parries, retreats, **flight** when outnumbered (6+), immediate flight from the police, pointless fights cut short. |
| **Survival** | Heals (inhalers), **buffs** before and during combat (food, drinks, boosters), avoids places where it died, never attacks the police except in self-defence. |
| **Rescue** | When an assault happens nearby with visible aggressors, decides for itself whether to step in (model + caution rules). |
| **Loot** | Picks up **everything**: containers, items on the ground, bodies — without ever carrying a body by mistake. |
| **Inventory** | Auto-equips the best weapons (2 melee + 1 firearm) and clothes, disassembles junk, reads shards, keeps the best for itself. |
| **Crafting** | Crafts heals, grenades, ammo, then any gear it can make at its level (Rare and better). |
| **Errands** | Sells anything useless to the nearest vendor (never a ripperdoc), buys heals, grenades and ammo, visits the **ripperdoc** to install better cyberware when rich enough. |
| **Progression** | Spends attribute and perk points (melee build: Body, Reflexes, Cool). |
| **Life** | Answers **phone calls**, reads and answers **text messages**, listens to the **radio** now and then (random station), summons one of V's vehicles at random, uses **fast-travel terminals**, **swims** without drowning, visits the mirror once a month, buys and learns **crafting specs**. |
| **Temperament** | Configurable: courage (cautious / balanced / bold), combat style (melee / mixed / ranged), aggressiveness (defensive / normal / hunter). The quest **tracked by the player** takes priority over everything else. |

---

## How it works

```
┌──────────────────────┐   state.bin (20 Hz)   ┌──────────────────────────────┐
│  Cyberpunk 2077      │ ────────────────────▶ │  CyberpunkAgent (Python/exe)  │
│  + Cyber Engine      │                       │   perception → planner        │
│    Tweaks            │ ◀──────────────────── │   → skills → keyboard/mouse   │
│  Lua mod AgentProbe  │   commands (SQLite)   │     (SendInput)               │
└──────────────────────┘                       └──────────────┬───────────────┘
                                                              │ open decisions
                                                              ▼
                                          Ollama (llama3.2, local) / OpenAI / Anthropic
```

- **The Lua mod `AgentProbe`** (runs inside Cyber Engine Tweaks) exports the game state 20 times per second: position, heading, health, combat, enemies, NPCs, lootable objects, dialogues, available hacks, quest objective and recommended level, vehicles, buffs, held weapon, phone. It also executes commands: path finding, quest listing and tracking, inventory, equip, disassemble, craft, sell, buy, loot, doors, vehicle summon, fast travel, radio, perks.
- **The Python agent** reads that state, decides (clear-cut rules first, language model for open cases: talk or not, rescue or not, which dialogue line) and acts **exactly like a player**: injected keys and mouse (physical scancodes, read from *your* game bindings, so AZERTY and remapped keys work).
- **No gameplay is modified** on the game side: the mod only reads state and calls the same functions the UI would (equip, disassemble, craft, sell at the game's price, transfer the content of a container V has reached).

---

## Requirements

| Component | Version | Role |
|---|---|---|
| Cyberpunk 2077 | 2.3 or later (autodrive), Steam / GOG / Epic | the game, in **French** (prompt keywords are French) |
| [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) | 1.35+ | runs the Lua mod |
| [Ollama](https://ollama.com/download/windows) | recent | local decision model (`llama3.2`, ~2 GB) — optional if you use an API key |
| Windows | 10 / 11, 64-bit | keyboard/mouse injection |
| GPU | 8 GB VRAM recommended | the game + 2 GB for the local model |

The game must run **borderless windowed or full screen**, with the mouse free (no overlay capturing it).

---

## Two-minute install (exe)

1. Install **Cyber Engine Tweaks** and launch the game once (it asks for its console key).
2. Install **Ollama** (the installer can download the model for you) — or have an OpenAI / Anthropic key ready.
3. Run **`CyberpunkAgent-Setup.exe`**: it finds the game, copies the mod, installs the program into `%LOCALAPPDATA%\Programs\CyberpunkAgent`, asks which model provider to use, downloads `llama3.2` if needed, writes the configuration and creates two desktop shortcuts (agent and configuration panel).
4. **Restart the game** (the mod loads at start-up), load a save, V on foot.
5. Double-click the **CyberpunkAgent** shortcut, go back to the game: V plays.

Silent install: `CyberpunkAgent-Setup.exe /S /GAME="D:\Games\Cyberpunk 2077"`

---

## Manual install (sources)

```bash
git clone https://github.com/Oli97430/CyberpunkAgent.git
cd CyberpunkAgent
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

Copy `mod\AgentProbe` into `<game>\bin\x64\plugins\cyber_engine_tweaks\mods\`, restart the game, then:

```bash
python run_agent.py --check     # checks game, CET, mod, model provider
python run_agent.py 20          # V plays for 20 minutes
python run_agent.py 20 --loop   # back-to-back sessions until F12
python agent_gui.py             # configuration panel
```

If the game is not detected, create `config.json` (see [Configuration](#configuration)).

---

## Usage

```
CyberpunkAgent.exe              V plays for 20 minutes
CyberpunkAgent.exe 60           V plays for 60 minutes
CyberpunkAgent.exe 20 --loop    back-to-back sessions (F12 stops)
CyberpunkAgent.exe --check      checks the installation
CyberpunkAgent.exe --config     prints the detected paths and settings
CyberpunkAgent.exe --test keys  checks the keys read from your bindings
CyberpunkAgent.exe --test loot  V loots whatever is around
CyberpunkAgent.exe --test drive V summons a vehicle and drives to the quest
CyberpunkAgent-Config.exe       configuration panel
```

A session goes like this:

1. the launcher warms the model up (10 to 30 s the first time);
2. it waits for **the game to be in the foreground** (click the game window);
3. V plays: the console shows every decision, fight, loot;
4. **F11** pauses (you take the controls, all keys are released), **F11** again resumes, **F12** stops;
5. if V dies, the agent reloads the last save by itself, remembers the place, and carries on.

Tips: keep the game window active while V plays (inputs are suspended as soon as the game loses focus), do not open menus (the agent detects the frozen state and waits), and leave the mouse alone (V uses it to aim).

---

## Keys and safety

- **Keys are read from your bindings** (`UserSettings.json`): interact, cyberware, quick melee, dodge, sprint, vehicle summon, autodrive, phone, etc. Scancodes are physical and resolved for the active layout: AZERTY, QWERTY, remapped keys.
- **F11**: pause / resume. **F12**: stop. Both are watched even when the game is not focused.
- **No input is sent** when the game is not in the foreground, or when the game state is frozen (menu, map, loading screen).
- **Only one instance** can run at a time.
- The agent **modifies no game file** outside the mod folder; its logs live in `%APPDATA%\CyberpunkAgent`.
- No anti-cheat, no server: Cyberpunk 2077 is single-player. Never use this kind of tool on an online game.

---

## Configuration

### Configuration panel

Two ways to tune the agent without editing files:

- **`CyberpunkAgent-Config.exe`** ("CyberpunkAgent Configuration" shortcut): a Windows window with the model provider (Ollama / OpenAI / Claude), the API key (masked), model names, V's behaviours (radio, driving, rescues, errands, ripperdoc, buffs), session length, a **Check** button and a **Launch V** button.
- **In game**: open the Cyber Engine Tweaks console; the **CyberpunkAgent** window offers the same settings; "Save" writes them into the mod folder and the agent reads them first at the next launch.

### Choosing the decision model: Ollama, OpenAI or Claude

| `provider` | Cost | Privacy | Config keys |
|---|---|---|---|
| `ollama` (default) | free, 2 GB VRAM shared with the game | nothing leaves the PC | `model` (default `llama3.2:latest`), `ollama_url` |
| `openai` | pay per call (a few cents per hour of play with `gpt-4o-mini`) | game situations (text, never images) are sent to OpenAI | `api_key` or `OPENAI_API_KEY`, `openai_model`, `openai_base_url` (compatible APIs) |
| `anthropic` | pay per call | same, sent to Anthropic | `api_key` or `ANTHROPIC_API_KEY`, `anthropic_model` (default `claude-haiku-4-5-20251001`) |

Optional `config.json`, next to the exe or in `%APPDATA%\CyberpunkAgent\`:

```json
{
  "game_dir": "D:\\Games\\Cyberpunk 2077",
  "provider": "openai",
  "api_key": "sk-...",
  "openai_model": "gpt-4o-mini",
  "features": {"radio": true, "driving": true, "rescue": true, "sell": true, "ripperdoc": true, "buffs": true},
  "minutes": 20
}
```

Everything is auto-detected when a key is missing (Steam through the registry and `libraryfolders.vdf`, GOG and Epic at their usual locations, Ollama on the PATH). The key is never written to the logs (`--config` shows it masked). Calls are short (a few dozen tokens, JSON answers); if the provider is down, the agent falls back to its rules.

`bench_llm.py` compares models on the game's own decision prompts: gpt-4o-mini 9/11 (1.6 s), llama3.2 8/11 (0.3 s), qwen2.5:14b 7/11 (0.6 s). A bigger model does not help much: the rules do most of the work, llama3.2 is enough.

Behaviour tuning (constants at the top of the modules):

| File | Constant | Role |
|---|---|---|
| `agent/combat.py` | `HEAL_BELOW`, `RETREAT_HP`, `RANGED_MIN_M`, `RANGED_BACK_M`, `DODGE_CD`, `GRENADE_*` | heal, retreat, firearm range, dodge rate, grenades |
| `agent/planner.py` | rules in `decide()` | when to attack, avoid, rescue, talk |
| `agent/crafting.py` | `WANT`, `AMMO_WANT` | target stocks |
| `agent/vendor.py` | `HEAL_WANT`, `MONEY_RESERVE`, `CYBER_RESERVE`, `CYBER_PRIORITY` | errands and ripperdoc |
| `agent/driving.py` | `MAX_DRIVE_S`, `ARRIVE_M` | driving |
| `agent/radio.py` | `CHANCE`, `LISTEN_MIN_S`, `LISTEN_MAX_S` | radio habit |

---

## Logs and diagnostics

- `%APPDATA%\CyberpunkAgent\brain_log.txt`: timestamped log of every session (decisions, fights with figures, loot, sales, driving, errors).
- `%APPDATA%\CyberpunkAgent\deaths.json`: remembered death locations (objectives within 80 m are avoided).
- `<game>\...\mods\AgentProbe\probe_progress.txt`: mod journal (every command is logged `RUN` before and `OK` after: the last `RUN` without `OK` points at the culprit of a crash).
- `CyberpunkAgent.exe --check`: full installation diagnostic.

Attach these three files to any bug report.

---

## Known limits

- **Races** (Beat on the Brat by car, street races): autodrive does not race.
- **Breach Protocol** and **braindance**: an automatic solver and editor control ship since 0.1.1, still being validated in-game (unhandled cases end with Escape).
- **Complex interiors** (elevators, locked doors): the escape routine often works, not always.
- **High-level areas**: V flees and avoids the area afterwards, but can die when caught between several groups.
- **Language**: prompt keywords are French. Other languages need `DOOR_WORDS`, `NO_GRAB`, `HEAL_WORDS`, `ALCOHOL` adjusted.
- One game instance, one screen: the agent injects into the foreground window.

---

## Code layout

```
mod/AgentProbe/init.lua     Cyber Engine Tweaks mod: state export (state.bin) + commands (SQLite) + in-game settings window
run_agent.py                launcher (exe): check, tests, play sessions, --loop
agent_gui.py                configuration panel (tkinter)
agent/
  config.py                 detected paths, config.json, in-game settings, feature flags
  brain.py                  main loop: perception → decision → skill
  planner.py                rules + language model: objective / talk / attack / rescue / avoid / switch quest
  llm_client.py             one call site for Ollama / OpenAI / Anthropic
  motion.py                 state reading, closed-loop turning, walking, unsticking, doors
  nav.py                    navmesh paths (mod command), multi-leg trips, fast travel
  combat.py                 combat state machine, stealth approach, loot around (UI + script)
  dialog.py                 dialogue (wheel + confirm), model choice, food stands skipped
  inventory.py              optimal gear, disassembly, sellables, shards
  crafting.py               crafting: consumables, ammo, gear
  vendor.py                 selling and buying, ripperdoc shopping
  quests.py                 quests by markers, level preference, danger memory
  driving.py                vehicle: random summon, mount, autodrive, dismount
  escape.py                 getting out of closed areas (doors, prompts, probes)
  buffs.py                  food / drinks / boosters
  radio.py                  radio habit
  input_kbm.py              keyboard + mouse (SendInput), player bindings, F11/F12
  llm.py                    Ollama watchdog
installer/install.py        installer (built into CyberpunkAgent-Setup.exe)
build_release.py            builds the three executables and the release archive
test_*.py                   in-game tests (walk, turn, loot, drive, dialogue, inventory)
```

Important convention on the mod side: every potentially dangerous native call is wrapped in a `RUN`/`OK` journal line and a `pcall`, because some game functions crash the process when called from Lua (`TimeSystem:SetTimeDilation`, `JournalManager:GetQuests`). Lua locals used by `handleCommand` must be defined before it.

---

## Building the release

```bash
pip install -r requirements.txt pyinstaller
python build_release.py
```

Produces in `dist/`: `CyberpunkAgent.exe` (the agent), `CyberpunkAgent-Config.exe` (the configuration panel), `CyberpunkAgent-Setup.exe` (the installer, embedding both and the mod) and `CyberpunkAgent-v<version>-win64.zip`.

---

## FAQ

**V does not move.** The game is not in the foreground, a menu is open, or the mod is not loaded: run `--check`, then restart the game after installing the mod.

**V presses the wrong keys.** `--test keys` prints the keys read from your bindings. If `UserSettings.json` is not found, set it in `config.json`.

**Dialogue answers make no sense.** The model provider is not answering (the agent falls back to its rules): check `ollama list` and that `llama3.2` is present, or your API key with `--check`.

**The game crashes.** Read `probe_progress.txt`: the last `RUN` without `OK` names the faulty command. Open an issue with that file.

**Can I use another model?** Yes: `"model": "qwen2.5:7b"` for Ollama, or any OpenAI / Anthropic model name. Bigger models decide better but share the GPU with the game (Ollama) or cost more (API).

---

## Licence and credits

Code under the **MIT** licence (see `LICENSE`). Cyberpunk 2077 is a trademark of CD PROJEKT S.A.; this project is neither affiliated with nor endorsed by CD PROJEKT RED. Thanks to the authors of Cyber Engine Tweaks and Ollama.
