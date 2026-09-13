# Revue de code complète — CyberpunkAgent (13/09/2026)

Trois revues indépendantes (mod Lua `init.lua`, cœur Python `brain/planner/combat/motion/input_kbm`, services Python + packaging),
consolidées ici. Chaque constat est classé **CRITIQUE / MAJEUR / MINEUR**. La colonne *État* dit ce qui a été corrigé dans le commit
qui accompagne ce document.

## 1. Corrigé dans ce commit

| Sévérité | Où | Constat | Correction |
|---|---|---|---|
| CRITIQUE | `init.lua` onInit | `lastCmdSeq` était une **globale** (le `local` était déclaré 600 lignes plus bas) : la dernière commande SQLite de la session précédente (`sell`, `buy`, `teleport`…) était **rejouée** à chaque chargement du mod. | Déclaration remontée en tête ; table `cmd` purgée au démarrage. |
| CRITIQUE | `init.lua` `sell` | Le `Bool` de `TransferItem` n'était pas lu et l'argent était crédité quoi qu'il arrive : `qty` > possédé = **argent infini**. Vendeur = premier PNJ à 6 m (l'infirmière). Objets de quête / iconiques / équipés vendables. | Marchand = `findNearbyVendor` ; `qty` bornée au stock de V ; argent crédité seulement si le transfert a réussi ; objets protégés refusés ; prix inconnu → estimation par qualité, jamais 0. |
| CRITIQUE | `init.lua` `buy` | Repli `GiveItem` qui **créait l'objet ex nihilo** ; paiement non vérifié ; `qty` non bornée ; marchand pouvant différer de celui de `vendor_stock`. | Même marchand exigé (hash), `qty` bornée au stock, paiement vérifié **avant** transfert, remboursement si le transfert échoue, plus aucun `GiveItem` d'objet. Idem pour `loot` (objet au sol). |
| CRITIQUE | `brain.run` | Aucun `try/finally` : une exception dans une compétence tuait la session, touches maintenues (W, Shift) **jamais relâchées**, pas de ligne de fin. | `try/finally` global (release_all, déchargement du modèle, radio, stats) + `try/except` par tour avec traceback journalisé ; arrêt après 5 erreurs. |
| CRITIQUE | `combat.engage/fight` | Double appui sur la touche d'emplacement (bascule) : `engage()` dégainait, `fight()` **rengainait**. `Wea_Fists` compté comme arme de mêlée. | `_ensure_weapon(slot, melee)` piloté sur l'arme réellement tenue ; poings = rien en main. |
| CRITIQUE | `run_agent --loop` | F12 testé seulement si la touche était **encore enfoncée** 5 s plus tard : l'utilisateur ne pouvait pas arrêter la boucle. | `play()` renvoie l'état du kill switch ; la boucle s'arrête dessus. |
| CRITIQUE | `config.py` | `agent_config.json` (dossier du **jeu**, inscriptible par tout mod) surchargeait **toutes** les clés : `ollama_exe` (exécutable lancé par l'agent), `openai_base_url` (clé envoyée à un hôte arbitraire), `ollama_url`, `user_settings`. | Liste blanche `INGAME_KEYS` (fournisseur, clé, modèles, comportements) ; `openai_base_url` doit être https ou local ; `null` tolérés. |
| CRITIQUE | `input_kbm` | Aucune entrée n'était bloquée hors focus pendant les compétences longues (60–300 s), contrairement au README. | Garde `game_focused_cached()` (250 ms) dans `key()` (appuis), `mouse()` et `look()` ; les relâchements passent toujours. |
| MAJEUR | `init.lua` export | Dépassement possible de `STATE_WIDTH` (5000 o) → troncature silencieuse → JSON invalide → **état figé** sans journal. Échec du tick muet. | Garde de taille avec dégradation ordonnée (crimes/véhicules/PNJ, puis loot/corps, puis hacks/ennemis) et journal `STATE trop long` ; erreurs du tick et des commandes journalisées (`STATE erreur`, `FAIL cmd`). |
| MAJEUR | `init.lua` perf | Hors combat, 5 `GetTargetParts` par tick dont un `TSQ_ALL` à **150 m** (estimation 8–25 ms par image). | Scans larges (loot 20 m, véhicules 150 m, PNJ 30 m) à 4 Hz avec cache ; ennemis toujours à 20 Hz. |
| MAJEUR | `init.lua` corps | Clé spatiale (case de 2 m) : un ennemi qui marche laissait des **faux corps** ; horloge `os.clock()` (temps CPU). | Clé = hash d'entité ; horloge = temps de jeu. |
| MAJEUR | `init.lua` `teleport` | Aucune borne : Python pouvait téléporter V n'importe où. | Refusé sauf borne → borne (V et cible à < 12 m d'un point de voyage rapide connu). |
| MAJEUR | `init.lua` `door_open` | Handle d'entité conservé entre deux commandes (porte déchargée = crash natif) ; 9 méthodes toutes appelées (forçage des serrures). | `EntityID` conservé et ré-résolu ; arrêt à la première méthode qui ouvre. |
| MAJEUR | `brain.py` | Zone bloquante mémorisée à la position de **départ** du trajet ; test « Échap a ouvert le menu » comparant un `seq` vieux de 60 s ; état figé à vie 0 traité comme un menu (Échap ×3 avant la détection de mort). | États relus juste avant ; la branche « gel » laisse passer la mort. |
| MAJEUR | `brain._reload_last_save` | Jusqu'à 3 min sans regarder F12 ; Entrée + confirmation ×3 à l'aveugle. | `_sleep(s, stop)` interruptible ; une seule touche de confirmation. |
| MAJEUR | `vendor.ripperdoc_shop` | Implant non posé **revendu immédiatement** (perte ~80 %) ; `equip` sans réponse (timeout) → revente d'un implant peut-être posé. | Implant conservé dans l'inventaire ; pas de revente automatique. |
| MAJEUR | `vendor.buy_heals/buy_supplies` | Prix inconnu traité comme **1 eddy** → achat à l'aveugle. | Prix ≤ 0 ignoré (comme ripperdoc et recettes). |
| MAJEUR | `buffs` | Indices d'inventaire mis en cache 600 s alors que chaque `inventory` les renumérote : `use` pouvait consommer **le mauvais objet** (MaxDoc, grenade). | `inventory.LISTING_SEQ` ; cache invalidé à chaque nouvelle liste ; en combat, pas de consommation sur indices périmés. |
| MAJEUR | `crafting` | Boucle fabrique → vendu → refabriqué à chaque passe (composants fondus). | Mémoire des équipements fabriqués dans la session. |
| MAJEUR | `nav` | `seq` non monotone entre sessions : la première commande pouvait recevoir la **réponse de la session précédente**. | `seq` en ms, `path.json` supprimé au démarrage. |
| MAJEUR | `inventory._val` | `'Prt_'` comparé par égalité : pièces estimées à 100 % → détours de 700 m pour vendre des babioles. | Préfixe. |
| MAJEUR | `run_agent.single_instance` | `GetLastError` via `windll` non fiable ; échec de `CreateMutexW` = succès ; `--test` exempté. | `WinDLL(use_last_error=True)`, échec fermé, seuls `--check/--config` exemptés. |
| MAJEUR | `build_release` | Tout `mod/` embarqué (un `agent_config.json` avec la clé API partirait dans la release). | Seul `init.lua` est embarqué. |
| MINEUR | `input_kbm` | Flèches sans `KEYEVENTF_EXTENDEDKEY` (= pavé numérique) ; `act()` levait si l'action n'a pas de touche (touches maintenues jamais relâchées). | Drapeau étendu pour UP/DOWN/LEFT/RIGHT ; `act()` tolérant ; `with kbm.held(...)`. |
| MINEUR | `planner`, `dialog`, `quests`, `appearance` | `choices == []` → IndexError ; lambda de secours à 1 argument ; `c['d']` ; signatures de hub en `d['title']` ; `deaths.json` non validé/non borné ; « regarder » déclenchait des touches au hasard. | Accès protégés, `hub_signature` unique, chargement validé (200 max, dédoublonné), mot retiré. |

## 2. À faire (non corrigé, par priorité)

### Économie / correction
- **Identifiants d'objets stables** (mod + Python) : `sell_all` ré-identifie encore les objets par **nom** entre deux listes ; `equip_attempts` est indexé par position. Solution : exporter un identifiant stable par item et l'accepter dans `use/equip/sell/disassemble`.
- `inventory.manage()` appelé après chaque combat **et** toutes les 5 min ; `fetch()` deux fois de suite dans le bloc courses.
- `crafting` : munitions comparées par égalité de nom (3 lots fabriqués à chaque passe) ; équipement fabriqué seulement s'il **bat** l'équipement courant.
- `list_quests` renvoie un pseudo-hash positionnel que `track` ne peut pas consommer ; hash de quête signé vs Uint32.
- `computePath` : division par zéro si la cible est sur V (`NaN` envoyé au moteur) ; jusqu'à 50 appels de pathfinding dans une image.

### Robustesse
- Focus et pause intégrés au drapeau `stop` passé aux compétences (aujourd'hui : garde dans `key()` seulement ; la compétence continue de « tourner » à vide).
- Mémoires jamais purgées en `--loop` (`obj_inter_tries`, `mute_hostiles`, `looted`, `rescued`…) → classe `Cooldown(ttl, cell_m)`.
- `release_all()` depuis le thread du KillSwitch : course avec `hold('W')` du thread principal ; `hold()` devrait être un no-op en pause.
- 60 sondes (`RUN_PROBES`) et le bloc `VITALS` (pathfinding toutes les 10 s) rejoués à chaque session : à désactiver par défaut.
- `GetAllBlackboardDefs()` deux fois par tick ; `questLevelOf` (TweakDB + journal) dans le tick 20 Hz.
- Échap « à l'aveugle » (état figé, souris sans effet) : exporter `menuOpen`/`inScene` depuis le mod.
- `llm.ensure()` (jusqu'à 25 s) appelé dans le chemin d'exception d'un dialogue ; modèle déchargé après chaque conversation (rechargement de 2 Go au prochain choix).

### Sécurité / vie privée
- La fenêtre in-game écrit la **clé API en clair** dans le dossier du jeu (`agent_config.json`). Option : DPAPI côté Python, ou ne saisir la clé que dans le panneau Windows. À documenter dans le README dans l'intervalle.
- `install.py` : chemins non échappés dans PowerShell (apostrophe dans le profil), `returncode` ignoré, `config.json` écrasé (clé et comportements perdus), mode silencieux déduit de `stdin`, aucune gestion de `PermissionError`.

### Architecture (dette)
- `brain.run` : 600 lignes, ~45 variables d'état, 7 copies du bloc « changer de quête » → classe `Brain` + handlers ordonnés + `switch_quest()`.
- Filtre « hostiles visibles » dupliqué 8 fois → `planner.hostiles(st)`. Constantes magiques (3,5 m ×8, `ARRIVE_M` muté depuis 6 modules, `MAX_VENDOR_M`) → paramètres explicites.
- `nav._wait(nav._send(...))` dans 10 modules → `nav.call(cmd, **args)` public avec validation des réponses.
- `init.lua` : 8 boucles `GetTargetParts → GetComponent → GetEntity → dédoublonnage` identiques → `scanEntities()` ; code mort (`if false`, `for i = 1, 0`, repli `cmd.json`).
- Aucun test unitaire : `quests.pick_next`, plan d'inventaire (sur `inventory_dump.json`), achats avec prix 0, `config.Config` (priorités, liste blanche), `nav._wait` (réponse périmée) sont testables sans le jeu.

## 3. Vérifications à faire en jeu après « Reload all mods »
1. `sell` et `buy` chez un marchand : journal `OK sell/buy` avec `qty` ; aucun `FAIL ... TransferItem refuse` inattendu.
2. Voyage rapide borne → borne : `teleport` accepté quand V est à côté d'une borne, refusé sinon.
3. Aucune ligne `STATE trop long` répétée ; si elle apparaît, porter `STATE_WIDTH` à 8000 dans `init.lua` (Python lit la ligne entière, rien à changer).
4. Combat : plus de « rengainage » au début du combat (journal `arme tenue ... on degaine`).
5. F12 pendant une session `--loop` : la boucle s'arrête.
