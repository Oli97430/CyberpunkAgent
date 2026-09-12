# Changelog

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

### Distribution
- `run_agent.py` / `CyberpunkAgent.exe` : `--check`, `--config`, `--test keys|loot|drive|walk`, durée en minutes.
- `CyberpunkAgent-Setup.exe` : détection du jeu, vérification de CET, copie du mod, installation du programme, téléchargement du modèle, config, raccourci.
