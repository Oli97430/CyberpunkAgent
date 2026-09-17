# Changelog

## 0.1.2 — 2026-09-17 — combat fiable, secours, terminaux, vol de voiture, Breach Protocol validé

- Combat : les emplacements d arme a degainer sont relus APRES la reaffectation de l inventaire (V tapait « 2 » pour l arme a feu alors que le fusil venait de passer en 3 : 3 combats a 26 m avec une matraque, 0 coup, puis « combat sterile »).
- Combat : plus d abandon apres 75 s sans resultat ; V charge d abord la cible (2 fois, sprint + sauts) avant de conclure.
- Armes, cause reelle trouvee : le mod listait la zone d equipement « Weapon » alors que les touches 1/2/3 lisent « WeaponWheel » (GetWeaponSlotItem). Le mod exporte maintenant ce que chaque touche degaine vraiment (`hotkeys`) et degaine par la requete du jeu (`weapon_slot`, le chemin exact des touches, sans clavier ni focus) ; la touche reste en repli.
- Terminal PIRATE PAR SCRIPT le 17/09 12:23 (connexion sans bouger, grille 6x6 resolue en 6 s). Calibrage des touches : non concluant si les trois touches donnent la meme arme (zone sans armes) -> emplacements conserves, nouvel essai 5 min plus tard.
- Terminaux : connexion par script tentee AVANT de bouger (portee du Breach a distance), marche tout droit s il n y a pas de chemin a moins de 15 m, nouvel essai par script une fois a cote.
- Vues d appareil (longue-vue, cameras) : Retour arriere avant Echap pour en sortir, dans la sortie de scene et dans la fermeture des menus (Echap y ouvrait le menu pause).
- Armes : CALIBRAGE des touches 1/2/3 hors combat (type reellement en main apres chaque touche, toutes les 10 min ou si l inventaire change) : le listing du mod ne correspond pas aux touches (la 3 sortait le fusil place en 2 -> 8 combats de 3 min a 0 coup le 17/09). Combat sterile : charge et abandon des 40 s sans coup ni tir, quelle que soit la vie.
- Terminaux : connexion par script (action ToggleNetrunnerDive du jeu, comme le Breach a distance) avant l approche a pied ; balayage vers le haut pour les panneaux muraux. Vente : objets sans nom ignores (3 refus de suite chez chaque marchand). Vehicule : plus de Despawn avant l appel.
- Terminaux (option « terminals ») : V repere les points d acces non pirates a moins de 40 m (commande access_points), s y connecte et resout le Breach Protocol ; memoire persistante terminals_skip.json (pirate 7 jours, injoignable / sans mini-jeu 1 jour).
- Breach Protocol REUSSI le 17/09 10:06 (5 selections verifiees, 2 daemons) ; l ecran de resume est ferme par Echap apres 2,5 s au lieu d attendre 12 s.
- Armes : les emplacements a degainer sont relus dans le JEU apres la reaffectation (equip() repondait ok sans effet : V tapait 3 pour un fusil reste en 2 et gardait sa matraque a 24 m) ; si la reaffectation est sans effet, on n insiste plus.
- Breach Protocol (1er essai reel le 17/09 : grille et solution justes, clics a cote) : le mod signale chaque case selectionnee (OnPositionSelected) ; le 1er clic, libre, balaye des decalages ecran jusqu a une selection et apprend le decalage canevas -> ecran ; chaque clic suivant est verifie et le chemin est recalcule depuis les cases jouees si le jeu en prend une autre.
- Vol de vehicule (option « steal ») : V vole la voiture ou moto arretee la plus proche (F maintenu pour forcer), par envie (1 fois sur 4 quand elle est a moins de 15 m) ou quand la sienne n arrive pas / est interdite ; jamais en combat ni avec la police a moins de 30 m. Options « sprint » et « steal » ajoutees au panneau du jeu.
- Courses / charcudoc / appel de vehicule jamais avec une menace proche ou en combat (V laissait un hostile a 10 m pour appeler sa voiture, que le mod ne scanne pas en combat) ; vente : un objet refuse ne bloque plus les autres et n ecarte pas le marchand ; vehicule de V deja present a moins de 150 m : reutilise au lieu d etre retire et rappele.
- Revue du 17/09 (10 correctifs) : la liste de restrictions du jeu est un catalogue statique (plus de retour anticipe, seul IsSummoningVehiclesRestricted fait foi) ; vehicules de V toujours dans l export (jamais evinces par 4 inconnus) ; hostile intouchable mis en sourdine des un combat sterile, cle a 3 m, echecs oublies apres 10 min ; les agresseurs sont les ATTAQUANTS (agressif ou gang), jamais la victime ; marchand ecarte seulement si le mod a repondu et qu il y avait a vendre ; scene quittee selon la meme regle de menace que les trajets ; reload repond ok seulement si le jeu a accepte ; export PNJ : les 5 plus proches toujours presents ; hack a distance seulement une fois vise.
- Vehicule : si le jeu interdit l appel ici (VehicleNoSummoning / NoInteraction), on n attend plus 40 s ; quete assignee inaccessible 3 fois de suite : V fait une autre quete avant d y revenir.
- Marchands : memoire persistante (vendors_skip.json) des charcudocs / marchands inutiles : rien en stock ou ne parle pas -> ecartes 7 jours ; rien vendu ni achete, ou injoignable deux fois -> 1 jour (V retournait chez Nurse et Fingers toutes les 30 min).
- Vehicule : la reponse du mod a l appel etait perdue depuis la veille (liste de restrictions en CName non encodable en JSON -> « mod muet » a chaque appel) ; corrige.
- Vehicule : la voiture de V est reconnue par son record TweakDB (IsPlayerVehicle repondait faux : 4 appels « sans arrivee » alors que la Galena etait a 23 m) ; si le mod ne repond pas en 5 s (menu), on attend 8 s de plus avant la touche d appel.
- Dialogues : les choix qui ouvrent un editeur (« changer d apparence » chez le charcudoc) sont ecartes ; un menu qui resiste a 2 Echap recoit la touche de confirmation (« quitter sans sauvegarder ? »).
- Hostile intouchable au contact (vitre, autre niveau, scene) : 5 coups sans reaction suffisent, et apres 2 echecs il est ignore 3 min meme a 1 m (34 assauts en boucle le 17/09) ; une agression = PNJ EN COMBAT (les videurs « agressifs » ne sont plus des cibles de secours).
- Secours (17/09) : l approche ne s arrete plus pour 3 hacks et un tir (hacks d ouverture seulement a > 12 m, rafale), l engagement dure tant que V se rapproche, second assaut si l agresseur ne reagit pas, plus de boucle « trajet interrompu » a 3 Hz (cellules de tous les agresseurs marquees, une interruption par minute).
- Mort hors combat : detectee AVANT le traitement des menus (l ecran de mort figeait l agent 11 min), rechargement par la commande du jeu (LoadLastCheckpoint) puis touche en secours ; un menu encore ouvert apres 4 Echap est signale toutes les 30 s et retente.
- Secours : V s implique dans toute agression a moins de 30 m (courage temeraire, sans consulter le modele, jusqu a 5 agresseurs), les trajets sont interrompus pour y aller, les agresseurs (gang / agressifs) sont frappes avant la victime, tour d horizon de 8 s a l arrivee ; export des PNJ a 45 m (8 max, combattants d abord).
- Combat : passes de sprint de biais au contact et entre deux tirs (perk +60 % de regeneration en sprint), sprint des 4,5 m a l approche ; option « sprint » du panneau.
- Combat : quickhacks aussi en mode arme a feu (toutes les 4 s, cibles alternees) et 2-3 hacks d ouverture avant le contact.
- Scene sans dialogue : plus d Echap / recul / saut quand V se deplace ou se bat (15 min de boucle pendant une chasse au cyberpsycho) ; delai double a chaque echec.
- Vehicule : l exemplaire precedent est retire avant le rappel (le 2e appel echouait et la moto « venait » de trop loin) ; export des vehicules de V a 400 m pendant 45 s apres un appel ; attente 40 s.

## 0.1.1 — 2026-09-15 — revue de code, Breach Protocol, danse sensorielle
- **Breach Protocol** : résolveur intégré (lecture de la grille et des séquences dans l'interface du mini-jeu, combinaison des daemons, clics souris) ; le mod continue de répondre pendant la pause du jeu. À valider en jeu.
- **Danse sensorielle** : V prend l'éditeur en main (indices de la timeline, couches, saut dans la timeline par script, analyse des indices, sortie). À valider en jeu.
- **Sortie d'îlot** : voyage rapide par le système du jeu (utilisable sans borne), tentative de sortie même pour une cible lointaine ; appel de véhicule : résultat du spawn et cooldown lus.
- **Menus et scènes** : Échap sur un menu laissé ouvert, extraction d'une scène muette après 25 s.
- **Combat** : V n'abandonne plus l'approche (course au-delà de 12 m, durée d'engagement proportionnelle à la distance, cible de secours suivie en direct, premier coup ou premiers tirs pour déclencher le combat).
- **Revue de code complète (13/09)** : voir `REVIEW.md` (trois revues : mod Lua, cœur Python, services + packaging). Corrigé :
- **Mod** : la dernière commande SQLite de la session précédente n'est plus rejouée au chargement (portée de `lastCmdSeq`) ; `sell`/`buy` lisent le résultat de `TransferItem`, bornent les quantités, exigent le bon marchand, ne créent plus ni argent ni objet ; objets de quête / iconiques / équipés invendables ; `teleport` limité borne → borne ; `door_open` par `EntityID` ; garde de taille de l'état (plus d'état figé silencieux) ; erreurs du tick et des commandes journalisées ; scans larges à 4 Hz ; corps identifiés par entité.
- **Agent** : `try/finally` et `try/except` par tour dans la boucle principale (touches relâchées, journal de fin garanti) ; arme dégainée selon l'arme réellement tenue (plus de rengainage au début du combat) ; aucune entrée envoyée hors focus, même pendant une compétence longue ; F12 arrête vraiment `--loop` ; rechargement après la mort interruptible ; états relus après un trajet ; implant non posé conservé (plus revendu à perte) ; prix inconnu = pas d'achat ; indices d'inventaire périmés invalidés (buffs) ; fin de la boucle craft → vente → craft ; `seq` monotone entre sessions ; flèches envoyées avec le drapeau étendu.
- **Sécurité** : `agent_config.json` (dossier du jeu) limité à une liste blanche de clés (plus d'exécutable ni d'URL imposables par un fichier) ; `openai_base_url` https ou local seulement ; la release n'embarque que `init.lua`.

## 0.1.0 — 2026-09-12 — première release

Première version jouable de bout en bout : V joue seul plusieurs dizaines de minutes.

### Perception (mod Lua AgentProbe)
- Export de l'état à 20 Hz dans `state.bin` (position, cap, vie, combat, ennemis dédupliqués par entité, police par affiliation, PNJ nommés et agresseurs, agressions à proximité, objets lootables, objet sous le réticule, dialogues, hacks disponibles, objectif de quête, véhicules proches, buffs actifs, invite d'interaction avec son état actif).
- Commandes via SQLite : chemin par le maillage (avec zones à éviter et chemins partiels échantillonnés), quêtes par marqueurs, inventaire, équiper, démonter, recettes, crafter, utiliser, niveau, vendre, stock du marchand, acheter, loot par script, portes.
- Journal `RUN`/`OK` de chaque appel natif pour diagnostiquer les plantages.

### Agent (Python)
- Boucle principale : décision par règles puis modèle local (llama3.2 via Ollama), garde d'état figé (menus, chargements), garde de focus, mort débouncée, mémoire persistante des lieux de mort.
- Déplacement en boucle fermée (rotation, marche, sprint, déblocage), trajets par tronçons, marche en ligne droite de secours, sortie d'îlot (portes, invites, sondes).
- Conduite : appel du véhicule, montée par poses, autodrive (G maintenu 1,5 s), arrivée détectée.
- Dialogues : navigation à la molette, choix par le modèle, choix grisés ignorés, anti-boucle.
- Combat v2 : mêlée au meilleur DPS, attaques chargées, esquives, strafes, parades, grenades sur les groupes, couteaux lancés, arme à distance, quickhacks (meilleur disponible), cyberware iconique, repli, fuite quand submergé, police en légitime défense seulement.
- Survie : soins, buffs (nourriture, boisson, boosters) avant et pendant le combat, évitement des zones trop dangereuses, sauvetages décidés par le modèle.
- Loot : interface (réticule + E court) puis transfert par script pour ne rien oublier, jamais de portage de corps.
- Inventaire : meilleures armes et vêtements équipés, démontage de la camelote, liste des objets à vendre.
- Craft : consommables, munitions, puis équipement Rare et mieux.
- Courses : vente au marchand le plus proche (ripperdocs exclus, rotation des marchands injoignables), achat de soins.
- Progression : points d'attribut dépensés (build mêlée).
- Touches lues dans les réglages du joueur (AZERTY, réassignations), F11 pause/reprise, F12 arrêt.

### Modele de decision
- Trois fournisseurs au choix : Ollama local (defaut), OpenAI (cle API, gpt-4o-mini) ou Anthropic/Claude (cle API) ; choix dans l installateur ou config.json, cle jamais journalisee, repli sur les regles si le fournisseur ne repond pas.

### Ajouts du 13/09
- Interfaces de configuration : panneau Windows (`CyberpunkAgent-Config.exe`) et fenetre in-game (overlay CET) ; comportements activables.
- Telephone : reponse aux appels entrants. Radio : ecoute occasionnelle, station au hasard.
- Vehicule au hasard parmi ceux de V ; voyage rapide quand l objectif est tres loin et qu aucun vehicule ne vient.
- Mort : rechargement automatique de la derniere sauvegarde, lieu memorise, la session continue ; `--loop` enchaine les sessions (F12 arrete).
- Combat : discretion (approche accroupie, elimination furtive), plus d esquives et dash, armes a feu des 9 m, armes contondantes preferees, fuite devant la police et face a 6 hostiles, combats steriles coupes.
- Courses : vente plus volontaire (valeur a encaisser, vehicule si loin), achat de soins, grenades et munitions ; charcudoc par script ; lecture des eclats.
- Quetes : priorite aux quetes du niveau de V (niveau recommande lu dans le journal du jeu).

### Apres-midi du 13/09
- Temperament reglable (courage, style, agressivite), quete suivie prioritaire, options discretion / voyage rapide / appels / SMS / apparence / plans.
- Nage sans noyade, bornes de voyage rapide (approche a 1 m, activation, teleportation borne a borne en secours), vehicule au hasard suivi jusqu a 150 m.
- SMS : lecture des contacts et reponses via le journal ; telephone : reponse aux appels.
- Machines a quetes et donneurs de quete : approche a 1 m ; sans quete suivie, V va voir un donneur de quete de son niveau.
- Dialogue : choix trop chers ecartes ; menus ouverts par accident refermes ; une seule instance de l agent.
- Banc de modeles (bench_llm.py).

### Distribution
- `run_agent.py` / `CyberpunkAgent.exe` : `--check`, `--config`, `--test keys|loot|drive|walk`, durée en minutes.
- `CyberpunkAgent-Setup.exe` : détection du jeu, vérification de CET, copie du mod, installation du programme, téléchargement du modèle, config, raccourci.
