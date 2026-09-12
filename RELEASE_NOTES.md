# CyberpunkAgent v0.1.0 — V joue seul à Cyberpunk 2077

*(Texte de la release GitHub — brouillon, à publier avec les fichiers de `dist/`.)*

Première version jouable : un agent autonome, piloté par un **modèle de langage local** (Ollama, rien ne sort de votre PC), qui joue à Cyberpunk 2077 comme un joueur au clavier et à la souris.

## Ce que V sait faire

- **Suivre ses quêtes** et changer d'objectif quand l'un est inaccessible ; mémoriser les lieux où il est mort.
- **Se déplacer** par le maillage du jeu, se débloquer, sortir d'une pièce fermée.
- **Conduire** : appeler sa voiture ou sa moto, monter, autodrive vers l'objectif, descendre.
- **Dialoguer** avec des réponses cohérentes choisies par le modèle local.
- **Se battre** : mêlée, grenades, couteaux lancés, quickhacks, cyberware, esquives, replis, fuite quand il est submergé. Jamais d'attaque contre la police, légitime défense seulement.
- **Survivre** : soins, nourriture, boissons, boosters avant et pendant le combat.
- **Looter tout**, équiper le meilleur, démonter la camelote, crafter, vendre, acheter des soins.
- **Décider seul** d'intervenir dans une agression voisine.

## Installation

1. Installez [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) et [Ollama](https://ollama.com/download/windows).
2. Lancez `CyberpunkAgent-Setup.exe` (détecte le jeu, copie le mod, télécharge le modèle, crée un raccourci).
3. Relancez le jeu, chargez une partie, lancez **CyberpunkAgent** depuis le Bureau.

**F11** met en pause et reprend, **F12** arrête. Les touches sont lues dans vos réglages (AZERTY, réassignations).

## Fichiers

| Fichier | Contenu |
|---|---|
| `CyberpunkAgent-Setup.exe` | installateur complet (agent + mod) |
| `CyberpunkAgent.exe` | l'agent seul, si le mod est déjà en place |
| `CyberpunkAgent-v0.1.0-win64.zip` | les deux exécutables, le mod Lua, README, licence |

## Limites connues

Courses de compétition non gérées, Breach Protocol détecté mais non résolu, certains intérieurs (ascenseurs, portes verrouillées) bloquent encore V, jeu en français requis pour les mots-clés des invites.

## Prérequis

Cyberpunk 2077 2.3+, Windows 10/11, Cyber Engine Tweaks, Ollama (`llama3.2`, ~2 Go), 8 Go de VRAM conseillés.

Licence MIT. Projet indépendant, non affilié à CD PROJEKT RED.
