# CyberpunkAgent — V joue seul à Cyberpunk 2077

**[English version](README.en.md)** *(la version anglaise décrit la 0.1.0 ; ce fichier-ci est à jour)*

Un agent autonome qui joue à **Cyberpunk 2077** à votre place. Il utilise un **modèle de langage local** (Ollama : aucune donnée ne sort du PC) ou, si vous préférez, une clé API OpenAI ou Anthropic.

V suit les quêtes et les activités de rue. Il se déplace, conduit, prend les ascenseurs, dialogue, se bat, se soigne et loote tout. Il gère son inventaire, crafte, vend, achète et se fait poser des implants. Il répond au téléphone, écoute la radio et chasse les succès Steam. En direct, il obéit au **chat YouTube**.

Un **HUD « V-800 »** bleu façon Terminator s'affiche par-dessus le jeu, avec des **sons mécaniques** synthétisés. Un narrateur annonce en grand ce que V décide.

> **État : version 0.1.2, expérimentale.** V joue réellement des heures d'affilée.
> - S'il meurt, il recharge lui-même la dernière sauvegarde.
> - Si le jeu plante, il le relance.
> - Il reste imparfait : certains intérieurs le piègent encore et il ne fait pas les courses de compétition.
>
> Chaque session est journalisée, et un **bilan** liste ce qui a vraiment fonctionné en jeu.

---

## Sommaire

1. [Ce que fait l'agent](#ce-que-fait-lagent)
2. [Comment ça marche](#comment-ça-marche)
3. [Prérequis](#prérequis)
4. [Installation en 2 minutes (exe)](#installation-en-2-minutes-exe)
5. [Installation manuelle (sources)](#installation-manuelle-sources)
6. [Utilisation](#utilisation)
7. [Touches et sécurité](#touches-et-sécurité)
8. [HUD V-800, narrateur et sons](#hud-v-800-narrateur-et-sons)
9. [Streamer : chat YouTube, votes, points, clips](#streamer--chat-youtube-votes-points-clips)
10. [Piloter V à distance (Telegram)](#piloter-v-à-distance-telegram)
11. [Robustesse : crash, mort, blocages](#robustesse--crash-mort-blocages)
12. [Mémoire et profils de personnage](#mémoire-et-profils-de-personnage)
13. [Configuration](#configuration)
14. [Journaux, bilan et diagnostic](#journaux-bilan-et-diagnostic)
15. [Limites connues](#limites-connues)
16. [Architecture du code](#architecture-du-code)
17. [Construire la release](#construire-la-release)
18. [FAQ](#faq)
19. [Licence et crédits](#licence-et-crédits)

---

## Ce que fait l'agent

| Domaine | Comportement |
|---|---|
| **Quêtes** | Suit l'objectif actif et se rend au marqueur par le maillage de navigation du jeu. Il change de quête quand l'objectif est inaccessible ou trop loin, et **privilégie les quêtes de son niveau**. Il retient les marqueurs sans intérêt pour ne pas y revenir. |
| **Activités de rue** | Agressions en cours, crime organisé, planques suspectes, clients en détresse, convois, primes, missions NCPD, **cyberpsychos**. V en prend une quand il n'a plus de quête, et aussi après **15 min sans combat** (« envie de baston »). Une fois sur place, il fouille la zone en spirale jusqu'au contact. |
| **Déplacement** | Marche, sprint, esquive, saute et se débloque seul. Enfermé, il cherche une sortie : portes, invites, sondes. Si l'objectif est **à un autre étage**, il prend l'**ascenseur** par script (appel de la cabine, choix de l'étage par altitude). Il utilise les bornes de **voyage rapide** et **nage** sans se noyer. |
| **Conduite** | Appelle sa voiture ou vole celle d'à côté, puis enclenche l'**autodrive** vers l'objectif. Si la cible est un marqueur qu'il a choisi, il fait d'abord **suivre cette quête par le jeu**, sinon l'autodrive roule sans destination. Garé hors route, il **manœuvre** jusqu'à une voie. S'il voit un groupe d'ennemis à l'arrivée, il s'arrête et descend avant. |
| **Dialogues** | Lit les choix et décide avec le modèle (en cohérence avec la quête), en évitant les boucles. Il privilégie les répliques de **parcours de vie**, qui comptent pour le succès Stanislavski. Il n'ouvre **jamais l'éditeur d'apparence**. |
| **Combat** | Mêlée ou distance selon le style. Approche **furtive**, grenades adaptées à l'ennemi, armes changées selon la distance, **quickhacks** (cible gardée sous le réticule pendant l'ouverture du panneau). Il utilise aussi le cyberware iconique, les esquives, les parades, le **Gun Fu** et les rafales **Kerenzikov**. Il se replie, **fuit** face au nombre, et ne tire **jamais sur la police**, sauf pour se défendre. |
| **Survie** | Se soigne, se buffe (nourriture, boissons, boosters) et évite **1 h** les lieux où il est mort. Ensuite il y revient pour une **revanche**, soigné à fond. |
| **Loot** | **Fouille groupée** : tout ce qui est fouillable à moins de 6 m (conteneurs, sacs, objets au sol, corps) est pris d'un coup, sans marcher. Il ne marche que vers les objets plus loin. Il ne prend **jamais** un objet de quête ni un PNJ vivant. Il garde un corps quand la quête le demande (« Soulever Evelyn »). |
| **Inventaire** | Équipe les meilleures armes et les meilleurs vêtements, et démonte la camelote. **Sac léger** : au-dessus de 70 % de charge, il démonte ce qui rapporte le moins par kilo. Les épiques et légendaires restent pour la vente. |
| **Craft, courses, implants** | Crafte soins, grenades, munitions et équipement. Vend tout ce qui ne sert pas, puis achète soins et munitions. Chez le **charcudoc**, y compris ceux qui s'ouvrent par un dialogue, il remplit d'abord les zones vides de cyberware, puis vise le **Kerenzikov**. |
| **Progression** | Dépense les points d'attribut et de perk. |
| **Vie** | Répond aux **appels** et aux **SMS**, écoute la **radio**, pirate les **points d'accès** (Breach Protocol résolu par script), passe les **danses sensorielles**. |
| **Succès Steam** | Lit les succès débloqués sur votre compte et oriente le jeu vers ceux qui restent : Grand voyageur, T-2077, Tu es déjà mort, La loi c'est moi, Gun Fu, Sûrement des rats, Stanislavski… Le tableau sur le HUD montre la progression. **Aucun succès n'est jamais débloqué artificiellement** : tout se gagne en jouant. |
| **Tempérament** | Réglable : courage (prudent / équilibré / téméraire), style (mêlée / mixte / distance), agressivité (défensif / normal / chasseur). La **quête suivie** reste prioritaire. |

---

## Comment ça marche

```
┌──────────────────────┐   state.bin (20 Hz)   ┌──────────────────────────────┐
│  Cyberpunk 2077      │ ────────────────────▶ │  CyberpunkAgent (Python/exe)  │
│  + Cyber Engine      │                       │   perception → planificateur  │
│    Tweaks            │ ◀──────────────────── │   → compétences → clavier/    │
│  mod Lua AgentProbe  │   commandes (SQLite)  │     souris (SendInput)        │
│  + HUD V-800 (ImGui) │ ◀── table hud (1 Hz) ─│   narrateur, sons, chat       │
└──────────────────────┘                       └──────────────┬───────────────┘
                                                              │ décisions ouvertes
                                                              ▼
                                       Ollama (llama3.2, local) / OpenAI / Claude
```

- **Le mod Lua `AgentProbe`** tourne dans Cyber Engine Tweaks.
  - Il exporte l'état du jeu 20 fois par seconde : position, cap, vie, RAM, combat, ennemis projetés à l'écran, PNJ, véhicules, objets lootables, dialogues, invites, quickhacks, objectif, buffs.
  - Il exécute des commandes : chemins, quêtes, activités de rue, inventaire, craft, vente, achat, loot, portes, ascenseurs, autodrive, points d'accès, identité du personnage.
  - Il dessine le **HUD V-800**.
- **L'agent Python** lit cet état et décide : règles nettes d'abord, modèle de langage pour les cas ouverts. Il agit **comme un joueur** : touches et souris injectées en scancodes physiques, lus dans *vos* réglages du jeu (AZERTY et touches réassignées compris).
- **Aucune triche de gameplay** : le mod n'appelle que des fonctions que le jeu utilise lui-même. Par exemple, équiper, vendre au prix du jeu, appeler un ascenseur ou suivre une quête comme un clic sur la carte.

---

## Prérequis

| Composant | Version | Rôle |
|---|---|---|
| Cyberpunk 2077 | 2.3 ou plus (autodrive), Steam / GOG / Epic | le jeu, en **français** (les mots des invites sont français) |
| [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) | 1.35+ | exécute le mod Lua et le HUD |
| [Ollama](https://ollama.com/download/windows) | récent | modèle local de décision (`llama3.2`, environ 2 Go) |
| Windows | 10 / 11, 64 bits | injection clavier/souris, sons |
| GPU | 8 Go de VRAM conseillés | le jeu, plus environ 2 Go pour le modèle |
| OBS Studio *(facultatif)* | 28+ (obs-websocket intégré) | clips automatiques |
| ffmpeg *(facultatif)* | récent, dans le PATH | Shorts verticaux |

Le jeu doit tourner en **fenêtré sans bordure ou en plein écran**, avec la souris libre.

---

## Installation en 2 minutes (exe)

1. Installez **Cyber Engine Tweaks** et lancez le jeu une fois.
2. Installez **Ollama**.
3. Lancez **`CyberpunkAgent-Setup.exe`**. Il trouve le jeu, copie le mod, installe le programme dans `%LOCALAPPDATA%\Programs\CyberpunkAgent`, télécharge `llama3.2` si besoin et crée les raccourcis.
4. **Relancez le jeu**, puis chargez une partie, avec V à pied.
5. Double-cliquez le raccourci **CyberpunkAgent** et revenez sur le jeu : V joue.

Installation silencieuse : `CyberpunkAgent-Setup.exe /S /GAME="D:\Jeux\Cyberpunk 2077"`

---

## Installation manuelle (sources)

```bash
git clone https://github.com/Oli97430/CyberpunkAgent.git
cd CyberpunkAgent
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

Copiez `mod\AgentProbe` dans `<jeu>\bin\x64\plugins\cyber_engine_tweaks\mods\` et relancez le jeu. Ensuite :

```bash
python run_agent.py --check     # vérifie jeu, CET, mod, Ollama, modèle
python run_agent.py 20          # V joue 20 minutes
```

Après une modification du mod, cliquez sur **« Reload all mods »** dans la console CET (inutile de relancer le jeu).

---

## Utilisation

```
CyberpunkAgent.exe              V joue (durée du panneau de configuration)
CyberpunkAgent.exe 60           V joue 60 minutes
CyberpunkAgent.exe --check      vérifie l'installation
CyberpunkAgent.exe --config     affiche les chemins détectés
CyberpunkAgent.exe --test keys  vérifie les touches lues dans vos réglages
CyberpunkAgent.exe --test loot  V loote ce qu'il y a autour de lui
CyberpunkAgent.exe --test drive V appelle son véhicule et conduit vers la quête
```

Déroulé d'une session :

1. Le lanceur préchauffe le modèle. Le HUD joue sa **séquence de démarrage**.
2. L'agent attend que **le jeu soit au premier plan**.
3. V joue. La console et le HUD affichent les décisions, et le narrateur les annonce.
4. **F11** met en pause : vous reprenez la main et toutes les touches sont relâchées. **F11** à nouveau reprend, **F12** arrête.
5. À l'arrêt, un **bilan** est écrit dans le journal et envoyé sur Telegram.

---

## Touches et sécurité

- **Les touches sont lues dans vos réglages** (`UserSettings.json`), en scancodes physiques : AZERTY, QWERTY et touches réassignées fonctionnent.
- **F11** : pause / reprise. **F12** : arrêt définitif. Elles sont surveillées même hors focus.
- **Aucune entrée n'est envoyée** quand le jeu n'est pas au premier plan, ni quand son état est figé (menu, carte, chargement).
- Touches de la console CET (onglet Bindings) :
  - « Afficher / masquer le HUD de V » ;
  - « HUD de V : V-800 / classique » ;
  - « HUD de V-800 : bleu / rouge ».
- L'agent ne modifie aucun fichier du jeu hors du dossier du mod. Ses données sont dans `%APPDATA%\CyberpunkAgent`.
- Cyberpunk 2077 est un jeu solo. N'utilisez jamais ce type d'outil dans un jeu en ligne.

---

## HUD V-800, narrateur et sons

**HUD « Anatomie du Signal »** : il est dessiné en primitives ImGui dans une fenêtre transparente plein écran, sans aucune entrée captée. La maquette et sa philosophie visuelle sont dans `docs/hud/`.

| Zone | Contenu |
|---|---|
| **Gauche** | Vie en chiffres 7 segments, échelle de 50 graduations (clignote sous 30 %), RAM, niveau, eddies, mode, hostiles, éliminés (« +1 »). |
| **Bas gauche** | **Radar tactique** vu de dessus, qui tourne avec le regard de V (60 m à pied, 200 m en véhicule). Il montre les hostiles en rouge, la police en ambre, les PNJ, les véhicules, la voiture de V et l'objectif, avec un faisceau de balayage. |
| **Haut centre** | **Narrateur** : la décision de V en grand, écrite lettre par lettre, avec une onde sonore. Sinon, la directive en cours. |
| **Droite** | **Registre des succès** : total Steam, Grand voyageur en 157 cases, T-2077, Kerenzikov et top 3 du chat. |
| **Centre** | Réticule gradué, avec des **chevrons de menace** autour (même derrière V). **Verrou animé** sur la cible la plus proche (distance, classe, menace). |
| **Bas** | Cap gradué avec l'**objectif** (losange et distance), et le bandeau de **vote du chat** pendant un direct. |

Animations : séquence de démarrage, flash rouge sur les bords quand V prend des dégâts, verrou qui se referme sur la cible.

La couleur est **bleue par défaut**. Le rouge est réservé aux alertes, la police est en ambre.

Si le HUD V-800 ne peut pas s'afficher, l'ancien HUD revient automatiquement et la raison est notée dans le journal du mod.

**Sons** : ils sont synthétisés par l'agent (aucun extrait de film) et sortent sur la sortie audio par défaut, donc OBS les capte.
- Le registre est **mécanique et grave** : relais, servomoteurs, bourdonnement électrique, bips sourds. Il n'y a aucune mélodie.
- Il y a un son pour : phrase du narrateur (avec crépitement d'écriture), cible acquise, exploration, alarme de vie critique, dégât, élimination, quickhack, voiture, succès, mort, démarrage.
- Un son important n'est jamais coupé par un son mineur.
- Pour les couper : `sons.json` → `{"enabled": false}`. Pour régler le volume : `"volume": 0.5`.

---

## Streamer : chat YouTube, votes, points, clips

Guide OBS complet : [youtube.md](youtube.md). Textes prêts à coller (description, message d'accueil) : `docs/stream/`.

**Chat en direct** : configurez `youtube.json` → `{"channel": "@VotreChaine"}`.
- V lit le chat public du direct en cours, en **lecture seule** : sans clé API, sans quota, sans rien écrire.
- Sans direct en cours, il réessaie chaque minute.

**Commandes des spectateurs** : elles sont **votées**. Chacun a une voix par tour de 30 s, et la commande la plus demandée gagne.

| Commande | Effet |
|---|---|
| `!attaque` | attaquer les ennemis en vue |
| `!objectif` | revenir à la quête suivie |
| `!skip` | passer à une autre quête |
| `!mission` | le chat choisit la mission parmi 3 (`!1` `!2` `!3`) |
| `!explore` | explorer un nouveau quartier |
| `!shop` | aller vendre son butin |
| `!charcudoc` | aller se faire poser des implants |
| `!heal` | se soigner |
| `!vole` | voler la voiture la plus proche |
| `!voiture` | appeler sa voiture |
| `!loot` | fouiller la zone |
| `!fete` | faire la fête |
| `!radio` | allumer la radio / station suivante |
| `!psycho` | chasser un cyberpsycho |
| `!etat` | afficher vie, niveau et eddies |
| `!melee` / `!distance` / `!mixte` | changer le style de combat |
| `!prudent` / `!yolo` | changer le comportement |
| `!points` | afficher son score |

- **Modérateurs et propriétaire** : leurs ordres sont immédiats, sans vote. Ils ont aussi `!stop`, `!pause`, `!reprendre`, `!style`, `!courage`, `!aggro`, `!photo` et `!hud`.
- **Sondage de mission** : toutes les 15 min en direct (hors combat), 3 quêtes sont proposées dans le bandeau pendant 45 s.
- **Points du chat** :
  - commande gagnante au vote : +10 ;
  - bon choix au sondage : +15 ;
  - Super Chat : +50 ;
  - nouveau membre ou sticker : +30 ;
  - abonnements offerts : +50.

  Le top 3 s'affiche sur le HUD et V salue le MVP en fin de session. Le cumul est gardé d'un direct à l'autre.
- **Super Chats et nouveaux membres** : remerciement en grand (pseudo et montant nettoyés) et fête de V. Une commande contenue dans un Super Chat est exécutée tout de suite.
- **Sécurité** : le texte des spectateurs n'est **jamais** affiché tel quel. Les pseudos sont nettoyés et le narrateur n'interprète jamais une ligne venue du chat.

**Clips automatiques** :
- À chaque moment fort (succès, Gun Fu, Kerenzikov, cyberpsycho, mort, leurre, fête), OBS sauvegarde son tampon de rediffusion. Le fichier est renommé `V_<moment>_<date>.mkv`.
- Prérequis : obs-websocket activé et **tampon de rediffusion** activé dans OBS (Paramètres → Sortie).
- **Shorts** : chaque clip devient une vidéo verticale 1080×1920 avec la phrase du narrateur incrustée, dans le dossier `Shorts`. Rien n'est publié automatiquement.

---

## Piloter V à distance (Telegram)

Avec `telegram.json` → `{"token": "...", "chat_id": "..."}` (jamais commité), vous recevez :
- les morts ;
- les crashs ;
- le bilan de session.

Vous pouvez aussi donner des ordres :

`stop`, `pause`, `reprendre`, `attaque`, `objectif`, `marchand`, `charcudoc`, `explore`, `changer_quete`, `mission`, `va_a <lieu>`, `status`, `photo`, `niveau`, `soigne`, `stats`, `bilan`, `courage <prudent|equilibre|temeraire>`, `style <melee|mixte|distance>`, `aggro <…>`, `modeles`, `modele <nom>`, `hud`, `config`.

Les phrases libres sont comprises aussi (« va vendre ton butin », « psycho »…).

---

## Robustesse : crash, mort, blocages

| Situation | Réaction |
|---|---|
| **Le jeu plante** | Relance par Steam, Entrée à l'écran-titre (« Continuer »), reprise de la session. Au 3e crash en 1 h, V s'arrête et vous prévient. |
| **V meurt** | Lieu et cause notés, rechargement de la dernière sauvegarde, lieu évité 1 h. |
| **Écran figé, menu ouvert** | Échap, puis rechargement si l'écran reste figé. |
| **Îlot fermé** | Recherche de sortie (portes, invites, sondes), voyage rapide de sortie. |
| **Objectif à un autre étage** | Ascenseur par script. Sans ascenseur à son étage, V change de cible tout de suite. |
| **Autodrive hors route** | Manœuvre jusqu'à une voie, puis relance. |
| **Scène de jeu** | V laisse la scène se dérouler sans toucher à rien. |

---

## Mémoire et profils de personnage

Tout est dans `%APPDATA%\CyberpunkAgent\`.

| Mémoire | Fichier | Portée |
|---|---|---|
| Terminaux piratés, lieux de mort, marqueurs sans intérêt, suivi du charcudoc | `profils/<personnage>/…` | **propre au personnage** |
| Marchands injoignables, charcudocs avec du stock, statistiques de transport | `vendors_skip.json`, `ripperdocs_known.json`, `travel_stats.json` | partagée (la ville est la même) |
| Succès Steam, points du chat, fonctions validées, config | `features_seen.json`, `chat_points.json`… | partagée (compte) |

- Le personnage est reconnu par son **parcours de vie et son corps**, lus dans le jeu (exemple : `nomade_femme`).
- Un nouveau personnage repart avec une mémoire de partie vierge.
- Pour séparer deux personnages de même parcours et même corps : `config.json` → `{"profil": "nom"}`.

---

## Configuration

Le fichier `config.json` est facultatif. Il se place à côté de l'exe ou dans `%APPDATA%\CyberpunkAgent\` :

```json
{
  "game_dir": "D:\\Jeux\\Cyberpunk 2077",
  "provider": "ollama",
  "model": "llama3.2:latest",
  "minutes": 240,
  "courage": "temeraire", "style": "mixte", "aggro": "chasseur",
  "features": { "radio": true, "driving": true, "steal": true, "sell": true, "ripperdoc": true,
                "terminals": true, "fasttravel": true, "sms": true, "appearance": false },
  "profil": "nomade_femme"
}
```

| Fichier | Rôle |
|---|---|
| `youtube.json` | chaîne du direct : `{"channel": "@VotreChaine"}` |
| `telegram.json` | bot Telegram (jeton, chat) |
| `clips.json` | OBS : `{"enabled": true, "port": 4455, "shorts": true}` (mot de passe lu dans la config d'OBS) |
| `sons.json` | sons du HUD : `{"enabled": true, "volume": 0.5}` |

**Panneau de configuration** : `CyberpunkAgent-Config.exe` (fenêtre Windows), ou dans le jeu la fenêtre **CyberpunkAgent** de la console CET. On y règle le modèle, la clé API (masquée), les comportements, la durée et le tempérament.

**Modèle de décision** :

| `provider` | Coût | Confidentialité |
|---|---|---|
| `ollama` (défaut) | gratuit, environ 2 Go de VRAM partagés avec le jeu | rien ne sort du PC |
| `openai` | quelques centimes par heure avec `gpt-4o-mini` | situations de jeu envoyées (texte seulement) |
| `anthropic` | payant à l'appel | idem, vers Anthropic |

Conseil : restez sur un **petit modèle** (`llama3.2`).
- Un modèle de 8 milliards de paramètres qui « réfléchit » (qwen3-vl par exemple) met plus de 10 s par décision quand le jeu occupe la carte graphique. L'agent coupe à 6 s, donc toutes ses décisions retombent sur les règles.
- Il remplit aussi la VRAM, ce qui favorise les plantages.

---

## Journaux, bilan et diagnostic

- `%APPDATA%\CyberpunkAgent\brain_log.txt` : journal horodaté de chaque session.
- **Bilan de session** : il est donné en fin de session et à la demande (« bilan »). Il contient :
  - combats, ennemis neutralisés, morts, revanches ;
  - loot, eddies, kilomètres ;
  - succès gagnés, chat, clips, crashs relancés ;
  - **la liste des fonctions vues en jeu** cette session, celles restées à 0, et celles **jamais vues** (cumul dans `features_seen.json`).
- `<jeu>\...\mods\AgentProbe\probe_progress.txt` : journal du mod. Chaque commande y est notée `RUN` avant et `OK` après : le dernier `RUN` sans `OK` désigne le coupable d'un plantage. On y trouve aussi les fonctions de dessin ImGui disponibles.
- `CyberpunkAgent.exe --check` : diagnostic complet.

---

## Limites connues

- **Courses de compétition** : l'autodrive ne fait pas la course, V passe à autre chose.
- **Intérieurs complexes** : les ascenseurs sont gérés par script (encore en validation en jeu), mais certaines pièces fermées piègent encore V.
- **Frame generation / DLSS expérimental** : à faible fréquence d'images, certaines actions demandent plus de temps (des délais ont été élargis). Un jeu figé plus de 120 s est coupé par le moteur ; l'agent le relance.
- **Langue** : les mots-clés des invites sont en français.
- Un seul écran, une seule instance du jeu.

---

## Architecture du code

```
mod/AgentProbe/init.lua     mod CET : état (state.bin), commandes (SQLite), HUD V-800 (ImGui)
run_agent.py                lanceur (exe) : check, tests, session de jeu
agent_gui.py                panneau de configuration Windows
agent/
  brain.py                  boucle principale : perception → décision → compétence
  planner.py                règles + modèle : objectif / parler / attaquer / secourir / éviter / changer_quête
  config.py                 chemins détectés, config.json, dossier de données
  motion.py, nav.py, navprog.py   état, rotation, marche, chemins, échelle de déblocage
  escape.py, lift.py        sortie d'îlot, ascenseurs (script + invites en secours)
  driving.py, travel.py     véhicule (appel, vol, autodrive, manœuvre hors route), choix du transport
  combat.py, psycho.py      combat, loot (fouille groupée), Kerenzikov, Gun Fu ; fouille de zone
  quests.py, contracts.py, explore.py   quêtes, activités de rue, exploration (Grand voyageur)
  dialog.py, sms.py, braindance.py, breach.py, terminals.py   dialogues, SMS, danse sensorielle, Breach Protocol
  inventory.py, crafting.py, vendor.py, buffs.py   inventaire (sac léger), craft, marchands et charcudocs, buffs
  achievements.py, features.py   succès Steam (tableau du HUD), fonctions vues en jeu (bilan)
  profiles.py               mémoire de partie par personnage
  relaunch.py               relance du jeu après un crash
  hud.py, narrator.py, sounds.py   données du HUD, narrateur, sons synthétisés
  youtube_chat.py, remote.py   chat YouTube (votes, sondages, points, Super Chats), Telegram
  clips.py, shorts.py       clips OBS, Shorts verticaux (ffmpeg)
  input_kbm.py, inputs.py   clavier + souris (SendInput), touches du joueur, F11/F12
  llm.py, llm_client.py     Ollama / OpenAI / Anthropic
docs/hud/                   maquette du HUD V-800 et philosophie visuelle
docs/stream/                textes prêts pour YouTube
installer/install.py        installateur (CyberpunkAgent-Setup.exe)
build_release.py            construit les exécutables et l'archive de release
```

Convention côté mod :
- chaque appel natif risqué est encadré par `RUN`/`OK` dans le journal et par un `pcall` ;
- le nouveau code Lua va dans des **fonctions globales**, car LuaJIT limite à 60 les upvalues par fonction.

Le HUD est vérifié hors jeu : le vrai code Lua est exécuté dans LuaJIT avec un ImGui simulé, puis dessiné.

---

## Construire la release

```bash
pip install -r requirements.txt pyinstaller
python build_release.py
```

Le dossier `dist/` contient alors :
- `CyberpunkAgent.exe` : l'agent ;
- `CyberpunkAgent-Config.exe` : le panneau de configuration ;
- `CyberpunkAgent-Setup.exe` : l'installateur ;
- `CyberpunkAgent-v<version>-win64.zip` : l'archive de release.

---

## FAQ

**V ne bouge pas.** Le jeu n'est pas au premier plan, un menu est ouvert, ou le mod n'est pas chargé. Lancez `--check`, puis faites « Reload all mods » dans CET.

**Le HUD V-800 n'apparaît pas.** Lisez `probe_progress.txt`. Une ligne « HUD V-800 indisponible… » donne la raison, et l'ancien HUD prend le relais.

**V appuie sur les mauvaises touches.** `--test keys` affiche les touches lues dans vos réglages.

**Les dialogues sont absurdes.** Ollama ne répond pas : vérifiez `ollama list`.

**Le chat n'est pas lu.** Le bilan indique « chat YouTube : pas de direct trouvé » : vérifiez `youtube.json` et que le direct est public.

**Aucun clip.** Activez le tampon de rediffusion dans OBS, ainsi que le serveur WebSocket (Outils → Paramètres du serveur WebSocket).

**Le jeu plante.** Le dernier `RUN` sans `OK` dans `probe_progress.txt` désigne la commande fautive. L'agent relance le jeu automatiquement.

---

## Licence et crédits

Code sous licence **MIT** (voir `LICENSE`). Cyberpunk 2077 est une marque de CD PROJEKT S.A. ; ce projet n'est ni affilié ni approuvé par CD PROJEKT RED. Merci aux auteurs de Cyber Engine Tweaks, d'Ollama, d'OBS Studio et de ffmpeg.
