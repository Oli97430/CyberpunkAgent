# Streaming the mod live on YouTube

Practical guide for streaming **CyberpunkAgent** (V driven by an AI) on YouTube: what to show, how to set up OBS,
what to tell chat, what never to show, and a pre-stream checklist.

Logo and thumbnail, ready to use: `F:\Downloads\logo_youtube\` (transparent 2048, dark background, 512 px overlay, 1920×1080 thumbnail).

---

## 1. The concept in one sentence

> "V no longer touches the keyboard: an AI reads the game state 20 times a second and plays instead of me, quests,
> fights, car, vendors, terminals, rescuing bystanders. I watch, I comment, and I only step in when it goes sideways."

What makes the stream interesting is the **unexpected**. The agent makes visible decisions (the journal is on
screen), sometimes gets it wrong, then corrects itself. Viewers bet on what V will do next.

---

## 2. Hardware and settings

| Item | Recommended setting |
|---|---|
| Game | Windowed 3840×1080 (the agent depends on it), vsync off, frame generation 2× max |
| OBS canvas | 1920×1080 (YouTube standard): game capture **cropped** to the central 1920×1080, or scaled 32:9 with bars |
| Encoder | NVENC H.264 or AV1 (RTX 3090: NVENC H.264, 8,000 to 12,000 kb/s, 60 fps) |
| Audio | Mic + game on two tracks; in-game radio at 30 % (the agent turns it on by itself, "radio" option) |
| Latency | YouTube "low latency" mode to interact with chat |

32:9 tip: keep a "full width" scene for driving, and a "16:9 center" scene for fights and dialogues (the HUD sits in
the center).

---

## 3. OBS scenes

1. **Starting soon** (before going live): thumbnail `logo_V_IA_miniature_1920x1080.png` + countdown.
2. **Game + journal** (main scene):
   - Game capture (window `Cyberpunk2077.exe`).
   - **Agent journal** overlay: a "Window capture" source on the `CyberpunkAgent.exe` console, cropped to the last
     12 lines, bottom left, 40 % width, 85 % opacity. This is the heart of the stream: viewers read the decisions
     ("V engages the fight", "V PLAYS THE HERO", "terminal: V is going to jack in").
   - Logo `logo_V_IA_incrustation_512.png` top right, 10 % width.
   - Text banner at the bottom: "V is driven by an AI. I do not touch the keyboard. F11 = I take over."
3. **Webcam + game** (optional): small webcam bottom right.
4. **Paused**: "The AI is thinking…" (when you press F11 to take over).

Never capture the **whole screen**: the Config panel and the CET console can display the API key.

---

## 4. What must NEVER be shown

- The **Config panel** (`CyberpunkAgent-Config.exe`) and the mod's **CET panel**: both contain the API key in clear
  text. Set them up BEFORE going live, windows closed.
- The mod folder's `agent_config.json`, `config.json` in AppData.
- Your e-mail address, your full Steam folder (window capture, not display capture).

If the key shows up by accident: cut the capture, revoke the key at the provider, generate a new one.

---

## 5. Stream structure (90 min)

| Minute | Segment | What to say |
|---|---|---|
| 0 to 5 | Welcome, logo, the concept | "Everything you are about to see is played by the AI." |
| 5 to 10 | Launch the agent (desktop shortcut), read the first inventory | Explain the journal: key calibration, items to sell, tracked quest |
| 10 to 40 | Tracked quest, fully autonomous | Comment the decisions; run a poll: "Will V take the car or fast travel?" |
| 40 to 55 | Provoked highlights: go to a combat zone, near an access point | Show Breach Protocol solved in 6 s, a bystander rescue, a car theft |
| 55 to 75 | Chat questions | See the FAQ below |
| 75 to 85 | Blooper reel: moments where the AI failed (V stuck, loops) and how it was fixed | Show a journal excerpt and the matching commit |
| 85 to 90 | Session recap (`=== fin :` line in the journal) and next stream announcement | Numbers: fights, rescues, terminals, eddies earned |

---

## 6. Live interventions (keys)

- **F11**: pause / resume. You take over, every key is released. Useful to move V out of a stuck spot or to show something.
- **F12**: stops the agent for good (then Enter in the console).
- **CET panel** (`~` key): do not open it live (API key).

Golden rule announced to chat: "I only step in if V has been stuck for more than 2 minutes."

---

## 7. Moments that make good clips

- **Breach Protocol**: the grid appears, cells get selected on their own, "succeeded in 5.6 s".
- **Rescue**: "V PLAYS THE HERO" then the fight, sprints and quickhacks.
- **Car theft**: "V STEALS « Thorton » 12 m away".
- **Charging an unreachable enemy**: "sterile fight for 40 s: V charges the target".
- **The fails**: V talking to a hologram, V walking into the ripperdoc to change hairstyle.

Keep the journal visible on these clips: it is what makes the action readable.

---

## 8. FAQ for chat

- **Is it a cheat?** No: the AI reads the game state through a mod (Cyber Engine Tweaks) and presses the same keys a
  player would. No modified damage, no teleport outside fast-travel points.
- **Which AI?** A rule-based brain (combat, navigation, fast decisions) plus a language model for dialogues and some
  choices (Ollama locally, or an online model). The model never drives the mouse.
- **Does it work on every quest?** No. Scripted scenes, braindance and some quests with unique mechanics go badly.
  The agent switches quests when stuck.
- **How much does it cost?** With local Ollama, nothing. With an online model, a few cents per hour.
- **Where to download?** https://github.com/Oli97430/CyberpunkAgent (releases: Windows installer).
- **Multiplayer?** No, Cyberpunk 2077 is single player.

---

## 9. Title, description, tags

**Titles that work** (one per stream):
- "An AI plays Cyberpunk 2077 instead of me, live"
- "V driven by an AI: fights, stolen cars, Breach Protocol, I touch nothing"
- "Will the AI finish this quest without me?"

**Description template**:

```
V is controlled by an AI (CyberpunkAgent mod). I do not touch the keyboard: I comment.
The journal at the bottom left shows every decision the agent makes.
Open source mod: https://github.com/Oli97430/CyberpunkAgent
Chapters:
00:00 The concept
05:00 Launching the agent
10:00 Quest on autopilot
40:00 Breach Protocol and rescues
55:00 Your questions
75:00 Bloopers
```

**Tags**: cyberpunk 2077, AI, artificial intelligence, autonomous agent, mod, CET, AI let's play, game played by an AI, AI gaming.

---

## 10. Checklist before clicking "Go live"

1. Game running, save loaded, V outdoors and out of combat.
2. Config panel and CET panel closed (API key invisible). Mod reloaded if updated ("Reload all mods").
3. Agent launched from the desktop shortcut, journal visible in the OBS scene (12 readable lines).
4. Sound: mic OK, game at 30 %, in-game radio not too loud.
5. "Game + journal" scene active, logo in place, text banner shown.
6. Chat in low-latency mode, moderation on (keyword filters).
7. A tracked quest with a map marker chosen in the game journal (the agent prioritizes it).
8. F11 and F12 tested once.

---

## 11. After the stream

- Copy the `=== fin : {...}` line from the journal (`%APPDATA%\CyberpunkAgent\brain_log.txt`): it gives the session
  numbers for the description or a community post.
- Cut 2 or 3 clips (Breach, rescue, fail) for Shorts.
- Note the blockers seen live: they become the fixes of the next version, and the subject of the next blooper reel.
