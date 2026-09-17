# CyberpunkAgent — V joue seul à Cyberpunk 2077

**[English version](README.en.md)**

Un agent autonome qui joue à **Cyberpunk 2077** à votre place, avec un **modèle de langage local** (Ollama, aucune donnée envoyée sur internet) ou, si vous préférez, une clé API OpenAI ou Anthropic : il suit les quêtes, se déplace, conduit, dialogue, se bat, se soigne, loote tout, gère son inventaire, craft, vend, achète, répond au téléphone, écoute la radio, et rend la main à la demande.

> **État : version 0.1.0, expérimentale.** L'agent joue réellement plusieurs dizaines de minutes d'affilée, mais il reste imparfait : il meurt parfois (et recharge alors lui-même la dernière sauvegarde), se coince dans certains intérieurs, et ne fait pas les courses de compétition. Chaque session est journalisée pour pouvoir améliorer les comportements.

---

## Sommaire

1. [Ce que fait l'agent](#ce-que-fait-lagent)
2. [Comment ça marche](#comment-ça-marche)
3. [Prérequis](#prérequis)
4. [Installation en 2 minutes (exe)](#installation-en-2-minutes-exe)
5. [Installation manuelle (sources)](#installation-manuelle-sources)
6. [Utilisation](#utilisation)
7. [Touches et sécurité](#touches-et-sécurité)
8. [Configuration](#configuration)
9. [Journaux et diagnostic](#journaux-et-diagnostic)
10. [Limites connues](#limites-connues)
11. [Architecture du code](#architecture-du-code)
12. [Construire la release](#construire-la-release)
13. [FAQ](#faq)
14. [Licence et crédits](#licence-et-crédits)

---

## Ce que fait l'agent

| Domaine | Comportement |
|---|---|
| **Quêtes** | Suit l'objectif actif, se rend au marqueur par le maillage de navigation du jeu, change de quête quand l'objectif est inaccessible ou trop loin, mémorise les marqueurs atteints, **privilégie les quêtes de son niveau** (niveau recommandé lu dans le journal du jeu). |
| **Déplacement** | Marche, sprint, esquive, saute, se débloque tout seul ; s'il est enfermé (pièce, toit), cherche une sortie : portes, invites « Ouvrir / Activer », sondes dans 8 directions. |
| **Conduite** | Appelle sa voiture ou sa moto, monte dedans, enclenche l'**autodrive** vers l'objectif dès que celui-ci est à plus de 500 m, descend à l'arrivée. |
| **Dialogues** | Lit les choix affichés, choisit avec le modèle local (cohérent avec le contexte de la quête), évite les boucles. |
| **Combat** | Mêlée en priorité (meilleure arme de mêlée, contondantes favorisées), approche en **discrétion** avec élimination furtive, grenades sur les groupes, couteaux lancés, arme à feu au-delà de 12 m ou sur les cibles en hauteur, **quickhacks** (le meilleur disponible), cyberware iconique, esquives et dash fréquents, parades, replis, **fuite** à 6 hostiles ou face à la police, combats stériles coupés. |
| **Survie** | Se soigne (inhalateurs), se **buffe** avant et pendant le combat (nourriture, boisson, boosters), évite les zones où il est mort, ne s'attaque **jamais à la police** sauf pour se défendre. |
| **Sauvetage** | Quand une agression a lieu à proximité, décide lui-même d'intervenir ou non (modèle local + règles de prudence). |
| **Loot** | Ramasse **tout** : conteneurs, objets au sol, corps, sans rien oublier, sans jamais porter un corps par erreur. |
| **Inventaire** | Équipe automatiquement les meilleures armes (2 mêlée + 1 distance) et les meilleurs vêtements, démonte la camelote, garde le meilleur pour lui. |
| **Craft** | Fabrique soins, grenades, munitions, puis tout l'équipement faisable à son niveau (Rare et mieux). |
| **Courses** | Va vendre tout ce qui ne sert pas au marchand le plus proche (jamais un ripperdoc), achète soins, grenades et munitions, va chez le **charcudoc** poser du meilleur cyberware quand il est assez riche. |
| **Progression** | Dépense les points d'attribut et de perk (build mêlée : Corps, Réflexes, Sang-froid). |
| **Vie** | Répond aux **appels**, lit et répond aux **SMS**, écoute la **radio** de temps en temps (station au hasard), appelle au hasard l'un de ses véhicules, utilise les **bornes de voyage rapide**, **nage** sans se noyer, passe au miroir une fois par mois, achète et apprend des **plans de craft**. |
| **Tempérament** | Réglable : courage (prudent / équilibré / téméraire), style de combat (mêlée / mixte / distance), agressivité (défensif / normal / chasseur). La **quête suivie** par le joueur est prioritaire sur tout le reste. |

---

## Comment ça marche

```
┌──────────────────────┐   state.bin (20 Hz)   ┌──────────────────────────────┐
│  Cyberpunk 2077      │ ────────────────────▶ │  CyberpunkAgent (Python/exe)  │
│  + Cyber Engine      │                       │   perception → planificateur  │
│    Tweaks            │ ◀──────────────────── │   → compétences → clavier/    │
│  mod Lua AgentProbe  │   commandes (SQLite)  │     souris (SendInput)        │
└──────────────────────┘                       └──────────────┬───────────────┘
                                                              │ décisions ouvertes
                                                              ▼
                                                    Ollama (llama3.2, local)
```

- **Le mod Lua `AgentProbe`** (tourne dans Cyber Engine Tweaks) exporte 20 fois par seconde l'état du jeu : position, cap, vie, combat, ennemis, PNJ, objets lootables, dialogues, hacks disponibles, objectif de quête, véhicules, buffs. Il exécute aussi des commandes : calcul de chemin, liste et suivi des quêtes, inventaire, équipement, démontage, craft, vente, achat, loot, portes.
- **L'agent Python** lit cet état, décide (règles nettes d'abord, modèle local pour les cas ouverts : parler ou non, secourir ou non, quel choix de dialogue) et agit **exactement comme un joueur** : touches et souris injectées (scancodes physiques, lus dans *vos* réglages du jeu, donc compatible AZERTY et touches réassignées).
- **Aucune modification du gameplay** n'est faite côté jeu : le mod ne fait que lire l'état et appeler les mêmes fonctions que l'interface (équiper, démonter, crafter, vendre au prix du jeu, transférer le contenu d'un conteneur que V a atteint).

---

## Prérequis

| Composant | Version | Rôle |
|---|---|---|
| Cyberpunk 2077 | 2.3 ou plus (autodrive), Steam / GOG / Epic | le jeu, en **français** (les mots des invites sont français) |
| [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) | 1.35+ | exécute le mod Lua |
| [Ollama](https://ollama.com/download/windows) | récent | modèle local de décision (`llama3.2`, ~2 Go) |
| Windows | 10 / 11, 64 bits | injection clavier/souris |
| GPU | 8 Go VRAM conseillés | le jeu + 2 Go pour le modèle |

Le jeu doit tourner **en fenêtré sans bordure ou plein écran**, avec la souris libre (pas d'overlay qui capture la souris).

---

## Installation en 2 minutes (exe)

1. Installez **Cyber Engine Tweaks** et lancez le jeu une fois (il vous demande la touche de sa console).
2. Installez **Ollama** (l'installateur sait télécharger le modèle pour vous).
3. Lancez **`CyberpunkAgent-Setup.exe`** : il trouve le jeu, copie le mod, installe le programme dans `%LOCALAPPDATA%\Programs\CyberpunkAgent`, télécharge `llama3.2` si besoin, écrit la configuration et crée un raccourci sur le Bureau.
4. **Relancez le jeu** (le mod se charge au démarrage), chargez une partie, V à pied.
5. Double-cliquez le raccourci **CyberpunkAgent**, revenez sur le jeu : V joue.

Installation silencieuse : `CyberpunkAgent-Setup.exe /S /GAME="D:\Jeux\Cyberpunk 2077"`

---

## Installation manuelle (sources)

```bash
git clone <ce dépôt> CyberpunkAgent
cd CyberpunkAgent
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

Copiez `mod\AgentProbe` dans `<jeu>\bin\x64\plugins\cyber_engine_tweaks\mods\`, relancez le jeu, puis :

```bash
python run_agent.py --check     # vérifie jeu, CET, mod, Ollama, modèle
python run_agent.py 20          # V joue 20 minutes
```

Si le jeu n'est pas détecté, créez `config.json` (voir [Configuration](#configuration)).

---

## Utilisation

```
CyberpunkAgent.exe              V joue 20 minutes
CyberpunkAgent.exe 60           V joue 60 minutes
CyberpunkAgent.exe --check      vérifie l'installation
CyberpunkAgent.exe --config     affiche les chemins détectés
CyberpunkAgent.exe --test keys  vérifie les touches lues dans vos réglages
CyberpunkAgent.exe --test loot  V loote ce qu'il y a autour de lui
CyberpunkAgent.exe --test drive V appelle son véhicule et conduit vers la quête
```

Déroulé d'une session :

1. le lanceur préchauffe le modèle (10 à 30 s la première fois) ;
2. il attend que **le jeu soit au premier plan** (cliquez sur la fenêtre du jeu) ;
3. V joue : la console affiche chaque décision, chaque combat, chaque loot ;
4. **F11** met en pause (vous reprenez la main, toutes les touches sont relâchées), **F11** à nouveau reprend, **F12** arrête ;
5. si V meurt, l'agent s'arrête, mémorise le lieu, et vous laisse recharger une sauvegarde.

Conseils : gardez la fenêtre du jeu active pendant que V joue (l'agent suspend ses entrées dès que le jeu perd le focus), n'ouvrez pas de menu (l'agent détecte l'état figé et attend), et laissez la souris tranquille (V l'utilise pour viser).

---

## Touches et sécurité

- **Les touches sont lues dans vos réglages** (`UserSettings.json`) : interagir, cyberware, quick melee, esquive, sprint, marche/course, appel du véhicule, autodrive, etc. Les scancodes sont physiques, résolus pour la disposition active : AZERTY, QWERTY, touches réassignées.
- **F11** : pause / reprise. **F12** : arrêt définitif. Ces deux touches sont surveillées même si le jeu n'a pas le focus.
- **Aucune entrée n'est envoyée** quand le jeu n'est pas au premier plan, ou quand l'état du jeu est figé (menu, carte, chargement).
- L'agent **ne modifie aucun fichier du jeu** hors le dossier du mod ; il n'écrit ses journaux que dans `%APPDATA%\CyberpunkAgent`.
- Ni anti-triche ni serveur : Cyberpunk 2077 est solo. N'utilisez jamais ce type d'outil sur un jeu en ligne.

---

## Configuration

Fichier optionnel `config.json`, à côté de l'exe ou dans `%APPDATA%\CyberpunkAgent\` :

```json
{
  "game_dir": "D:\\Jeux\\Cyberpunk 2077",
  "user_settings": "C:\\Users\\moi\\AppData\\Local\\CD Projekt Red\\Cyberpunk 2077\\UserSettings.json",
  "ollama_exe": "C:\\Users\\moi\\AppData\\Local\\Programs\\Ollama\\ollama.exe",
  "ollama_url": "http://127.0.0.1:11434",
  "model": "llama3.2:latest"
}
```

Tout est détecté automatiquement quand la clé est absente (Steam via le registre et `libraryfolders.vdf`, GOG et Epic aux emplacements habituels, Ollama dans le PATH).

### Panneau de configuration

Deux façons de régler l'agent sans toucher aux fichiers :

- **`CyberpunkAgent-Config.exe`** (raccourci « CyberpunkAgent Configuration ») : fenêtre Windows avec le choix du modèle (Ollama / OpenAI / Claude), la clé API (masquée), les modèles, les comportements de V (radio, conduite, sauvetages, courses, charcudoc, buffs), la durée de session, un bouton **Vérifier** et un bouton **Lancer V**.
- **Dans le jeu** : ouvrez la console Cyber Engine Tweaks, la fenêtre **CyberpunkAgent** propose les mêmes réglages ; « Enregistrer » les écrit dans le dossier du mod, et l'agent les lit en priorité au lancement suivant.

### Choisir le modèle de décision : Ollama, OpenAI ou Claude

L'agent prend ses décisions ouvertes (parler ou non, secourir ou non, quel choix de dialogue) avec un modèle de langage. Trois fournisseurs, au choix de l'utilisateur (l'installateur le demande, ou `config.json`) :

| `provider` | Coût | Confidentialité | Clés de config |
|---|---|---|---|
| `ollama` (défaut) | gratuit, 2 Go de VRAM partagés avec le jeu | rien ne sort du PC | `model` (défaut `llama3.2:latest`), `ollama_url` |
| `openai` | payant à l'appel (quelques centimes par heure de jeu avec `gpt-4o-mini`) | les situations de jeu (texte, jamais d'image) sont envoyées à OpenAI | `api_key` ou variable `OPENAI_API_KEY`, `openai_model`, `openai_base_url` (API compatible) |
| `anthropic` | payant à l'appel | idem, vers Anthropic | `api_key` ou variable `ANTHROPIC_API_KEY`, `anthropic_model` (défaut `claude-haiku-4-5-20251001`) |

Exemple avec OpenAI :

```json
{ "provider": "openai", "api_key": "sk-...", "openai_model": "gpt-4o-mini" }
```

La clé est lue dans `config.json` ou dans la variable d'environnement ; elle n'est jamais écrite dans les journaux (`--config` l'affiche masquée). Les appels sont courts (quelques dizaines de jetons, réponse JSON), donc rapides même via internet ; en cas de panne réseau, l'agent retombe sur ses règles. Vérification : `CyberpunkAgent.exe --check` fait un appel de test au fournisseur choisi.

Le banc `bench_llm.py` compare des modèles sur les décisions du jeu : gpt-4o-mini 9/11 (1,6 s), llama3.2 8/11 (0,3 s), qwen2.5:14b 7/11 (0,6 s). Un modèle plus gros n'apporte pas grand-chose : les règles font l'essentiel, llama3.2 suffit.

Réglages de comportement (constantes en tête des modules, à ajuster si vous le souhaitez) :

| Fichier | Constante | Rôle |
|---|---|---|
| `agent/combat.py` | `HEAL_BELOW`, `RETREAT_HP`, `OUTNUMBERED`, `GRENADE_*`, `QUICKHACK_CD` | seuils de soin, repli, grenades, hacks |
| `agent/planner.py` | règles de `decide()` | quand attaquer, éviter, secourir, parler |
| `agent/crafting.py` | `WANT`, `AMMO_WANT` | stocks cibles de soins, grenades, munitions |
| `agent/vendor.py` | `HEAL_WANT`, `MONEY_RESERVE`, `MAX_VENDOR_M` | courses |
| `agent/driving.py` | `MAX_DRIVE_S`, `ARRIVE_M` | conduite |
| `agent/brain.py` | `500.0` (distance de prise du véhicule), périodes inventaire / niveau | boucle principale |

---

## Journaux et diagnostic

- `%APPDATA%\CyberpunkAgent\brain_log.txt` : journal horodaté de chaque session (décisions, combats avec bilan chiffré, loot, ventes, conduite, erreurs).
- `%APPDATA%\CyberpunkAgent\deaths.json` : lieux de mort mémorisés (objectifs à moins de 80 m évités).
- `<jeu>\...\mods\AgentProbe\probe_progress.txt` : journal du mod (chaque commande est notée `RUN` avant et `OK` après : le dernier `RUN` sans `OK` désigne le coupable d'un plantage).
- `CyberpunkAgent.exe --check` : diagnostic complet de l'installation.

En cas de problème, joignez ces trois fichiers à votre rapport.

---

## Limites connues

- **Courses de compétition** (Beat on the Brat en voiture, courses de rue) : l'autodrive ne fait pas la course.
- **Breach Protocol** : résolveur validé en jeu le 17/09/2026 (points d'accès piratés par script, grilles 5×5 et 6×6 réussies). **Danse sensorielle** : pilotage de l'éditeur intégré, encore en cours de validation en jeu (les cas non gérés se terminent par Échap).
- **Intérieurs complexes** (ascenseurs, portes verrouillées) : la sortie d'îlot fonctionne souvent, pas toujours.
- **Zones de haut niveau** : V fuit et évite ensuite la zone, mais il peut mourir s'il est pris entre plusieurs groupes.
- **Langue** : les mots-clés des invites sont en français. Les autres langues demandent d'adapter `DOOR_WORDS`, `NO_GRAB`, `HEAL_WORDS`, `ALCOHOL`.
- Une seule instance du jeu, un seul écran : l'agent injecte dans la fenêtre au premier plan.

---

## Architecture du code

```
mod/AgentProbe/init.lua     mod Cyber Engine Tweaks : export de l'état (state.bin) + commandes (SQLite)
run_agent.py                lanceur (exe) : check, tests, session de jeu
agent/
  config.py                 chemins détectés, config.json, dossier de données
  brain.py                  boucle principale : perception → décision → compétence
  planner.py                règles + modèle local : objectif / parler / attaquer / secourir / éviter / changer_quête
  motion.py                 lecture de l'état, rotation en boucle fermée, marche, déblocage
  nav.py                    chemins par le maillage du jeu (commande au mod), trajets par tronçons
  combat.py                 machine à états de combat, loot autour (interface + script)
  dialog.py                 dialogues (molette + confirmation), choix par le modèle
  inventory.py              équipement optimal, démontage, objets à vendre
  crafting.py               craft : consommables, munitions, équipement
  vendor.py                 vente et achat chez le marchand
  quests.py                 quêtes par marqueurs, mémoire des lieux dangereux
  driving.py                véhicule : appel, montée, autodrive, descente
  escape.py                 sortie d'un îlot fermé (portes, invites, sondes)
  buffs.py                  nourriture / boissons / boosters
  input_kbm.py              clavier + souris (SendInput), touches du joueur, F11/F12
  llm.py                    chien de garde Ollama
installer/install.py        installateur (compilé en CyberpunkAgent-Setup.exe)
build_release.py            construit les deux exécutables et l'archive de release
test_*.py                   tests en jeu (marche, rotation, loot, conduite, dialogues, inventaire)
```

Convention importante côté mod : chaque appel natif potentiellement dangereux est encadré par un journal `RUN`/`OK` et un `pcall`, car certaines fonctions du jeu font planter le processus quand elles sont appelées depuis Lua (`TimeSystem:SetTimeDilation`, `JournalManager:GetQuests`).

---

## Construire la release

```bash
pip install -r requirements.txt pyinstaller
python build_release.py
```

Produit dans `dist/` : `CyberpunkAgent.exe` (l'agent), `CyberpunkAgent-Setup.exe` (l'installateur, qui embarque l'agent et le mod) et `CyberpunkAgent-v<version>-win64.zip` (les deux exécutables, le mod, le README).

---

## FAQ

**V ne bouge pas.** Le jeu n'est pas au premier plan, ou un menu est ouvert, ou le mod n'est pas chargé : `--check`, puis relancez le jeu après l'installation du mod.

**V appuie sur les mauvaises touches.** `--test keys` affiche les touches lues dans vos réglages. Si `UserSettings.json` n'est pas trouvé, indiquez-le dans `config.json`.

**Les réponses de dialogue sont absurdes.** Ollama ne répond pas (l'agent retombe sur ses règles) : vérifiez `ollama list` et que `llama3.2` est présent.

**Le jeu plante.** Lisez `probe_progress.txt` : le dernier `RUN` sans `OK` indique la commande fautive. Ouvrez un ticket avec ce fichier.

**Puis-je utiliser un autre modèle ?** Oui : `"model": "qwen2.5:7b"` dans `config.json`. Un modèle plus gros décide mieux mais partage la carte graphique avec le jeu.

---

## Licence et crédits

Code sous licence **MIT** (voir `LICENSE`). Cyberpunk 2077 est une marque de CD PROJEKT S.A. ; ce projet n'est ni affilié ni approuvé par CD PROJEKT RED. Merci aux auteurs de Cyber Engine Tweaks et d'Ollama.
