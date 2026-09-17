# Diffuser le mod en direct sur YouTube

*English version: [youtube.en.md](youtube.en.md)*

Guide pratique pour streamer **CyberpunkAgent** (V piloté par une IA) sur YouTube : ce qu'il faut montrer, comment
préparer OBS, quoi dire au chat, quoi ne jamais montrer, et une check-list avant le direct.

Logo et miniature prêts à l'emploi : `F:\Downloads\logo_youtube\` (transparent 2048, fond sombre, incrustation 512, miniature 1920×1080).

---

## 1. Le concept en une phrase

> « V ne touche plus au clavier : une IA lit l'état du jeu 20 fois par seconde et joue à sa place, quêtes, combats,
> voiture, marchands, terminaux, secours des passants. Moi je regarde, je commente, et j'interviens seulement si
> ça part en vrille. »

Ce qui rend le direct intéressant : **l'imprévu**. L'agent prend des décisions visibles (journal à l'écran), se
trompe parfois, se corrige. Le spectateur parie sur ce que V va faire.

---

## 2. Matériel et réglages

| Élément | Réglage conseillé |
|---|---|
| Jeu | Fenêtré 3840×1080 (l'agent en dépend), vsync off, image générée 2× max (voir mémo FG) |
| OBS canevas | 1920×1080 (YouTube standard) : capture du jeu **recadrée** au centre 1920×1080 ou mise à l'échelle 32:9 avec bandes |
| Encodeur | NVENC H.264 ou AV1 (RTX 3090 : NVENC H.264, 8 000 à 12 000 kb/s, 60 i/s) |
| Audio | Micro + son du jeu sur deux pistes ; radio du jeu à 30 % (l'agent l'allume tout seul, option « radio ») |
| Latence | Mode « faible latence » de YouTube pour interagir avec le chat |

Astuce 32:9 : garder la scène « pleine largeur » pour les phases de conduite, et une scène « centre 16:9 » pour les
combats et dialogues (le HUD est au centre).

---

## 3. Scènes OBS

1. **Écran d'accueil** (avant le direct) : miniature `logo_V_IA_miniature_1920x1080.png` + compte à rebours.
2. **Jeu + journal** (scène principale) :
   - Capture du jeu (fenêtre `Cyberpunk2077.exe`).
   - **Journal de l'agent** en incrustation : source « Capture de fenêtre » sur la console `CyberpunkAgent.exe`,
     recadrée sur les 12 dernières lignes, en bas à gauche, 40 % de largeur, opacité 85 %. C'est le cœur du direct :
     on lit les décisions (« V engage le combat », « V JOUE LES SAUVEURS », « terminal : V va s'y connecter »).
   - Logo `logo_V_IA_incrustation_512.png` en haut à droite, 10 % de largeur.
   - Bandeau texte en bas : « V est piloté par une IA. Je ne touche pas au clavier. F11 = je reprends la main. »
3. **Webcam + jeu** (optionnel) : webcam en bas à droite, petite.
4. **Pause** : « L'IA réfléchit… » (quand tu appuies sur F11 pour reprendre la main).

Ne jamais capturer **l'écran entier** : le panneau Config et la console CET peuvent afficher la clé API.

---

## 4. Ce qu'il ne faut JAMAIS montrer

- Le **panneau Config** (`CyberpunkAgent-Config.exe`) et le **panneau CET** du mod : ils contiennent la clé API en clair.
  Les régler AVANT le direct, fenêtres fermées.
- Le fichier `agent_config.json` du dossier du mod, `config.json` dans AppData.
- Ton adresse e-mail, ton dossier Steam complet (capture de fenêtre, pas d'écran).

Si la clé apparaît par accident : couper la capture, révoquer la clé chez le fournisseur, en générer une nouvelle.

---

## 5. Déroulé d'un direct (90 min)

| Minute | Séquence | Quoi dire |
|---|---|---|
| 0 à 5 | Accueil, logo, rappel du concept | « Tout ce que vous allez voir est joué par l'IA. » |
| 5 à 10 | Lancement de l'agent (raccourci bureau), lecture du premier inventaire | Expliquer le journal : calibrage des touches, objets à vendre, quête suivie |
| 10 à 40 | Quête suivie en autonomie | Commenter les décisions ; lancer un sondage : « V va prendre la voiture ou le voyage rapide ? » |
| 40 à 55 | Moments forts provoqués : aller dans une zone à combats, près d'un point d'accès | Montrer le Breach Protocol résolu en 6 s, un secours de passant, un vol de voiture |
| 55 à 75 | Questions du chat | Voir la FAQ ci-dessous |
| 75 à 85 | Bêtisier : les moments où l'IA s'est plantée (V coincé, boucle) et comment c'est corrigé | Montrer un extrait du journal et le commit correspondant |
| 85 à 90 | Bilan de session (`=== fin :` dans le journal) et annonce du prochain direct | Chiffres : combats, sauvetages, terminaux, eddies gagnés |

---

## 6. Interventions en direct (touches)

- **F11** : pause / reprise. Tu reprends la main, toutes les touches sont relâchées. Utile pour sortir V d'un endroit
  coincé ou pour montrer quelque chose.
- **F12** : arrêt définitif de l'agent (puis Entrée dans la console).
- **Panneau CET** (touche `~`) : à ne pas ouvrir en direct (clé API).

Règle d'or annoncée au chat : « Je n'interviens que si V est bloqué plus de 2 minutes. »

---

## 7. Moments qui font de bons extraits (clips)

- **Breach Protocol** : la grille apparaît, les cases se sélectionnent seules, « réussi en 5,6 s ».
- **Secours** : « V JOUE LES SAUVEURS » puis le combat, sprints et quickhacks.
- **Vol de voiture** : « V VOLE « Thorton » à 12 m ».
- **Charge sur un ennemi injoignable** : « combat stérile depuis 40 s : V charge la cible ».
- **Les ratés** : V qui parle à un hologramme, V qui entre chez le charcudoc pour changer de coiffure.

Garder le journal visible sur ces extraits : c'est lui qui rend l'action lisible.

---

## 8. FAQ pour le chat

- **C'est un cheat ?** Non : l'IA lit l'état du jeu par un mod (Cyber Engine Tweaks) et appuie sur les mêmes touches
  qu'un joueur. Pas de dégâts modifiés, pas de téléportation hors des bornes de voyage rapide.
- **Quelle IA ?** Un cerveau à règles (combat, navigation, décisions rapides) plus un modèle de langage pour les
  dialogues et certains choix (Ollama en local, ou un modèle en ligne). Le modèle ne pilote pas la souris.
- **Ça marche sur toutes les quêtes ?** Non. Les scènes scriptées, la danse sensorielle et certaines quêtes à
  mécanique unique passent mal. L'agent change de quête quand il est bloqué.
- **Combien ça coûte ?** Avec Ollama en local, rien. Avec un modèle en ligne, quelques centimes par heure.
- **Où télécharger ?** https://github.com/Oli97430/CyberpunkAgent (releases : installateur Windows).
- **Ça marche en multijoueur ?** Non, Cyberpunk 2077 est solo.

---

## 9. Titre, description, tags

**Titres qui marchent** (un par direct) :
- « Une IA joue à Cyberpunk 2077 à ma place, en direct »
- « V piloté par une IA : combats, voitures volées, Breach Protocol, je ne touche à rien »
- « L'IA va-t-elle finir cette quête sans moi ? »

**Description type** :

```
V est contrôlé par une IA (mod CyberpunkAgent). Je ne touche pas au clavier : je commente.
Le journal en bas à gauche montre chaque décision de l'agent.
Mod open source : https://github.com/Oli97430/CyberpunkAgent
Chapitres :
00:00 Le concept
05:00 Lancement de l'agent
10:00 Quête en autonomie
40:00 Breach Protocol et secours
55:00 Vos questions
75:00 Bêtisier
```

**Tags** : cyberpunk 2077, IA, intelligence artificielle, agent autonome, mod, CET, let's play IA, jeu joué par une IA, gaming IA, france.

---

## 10. Check-list avant de cliquer sur « Diffuser »

1. Jeu lancé, sauvegarde chargée, V dehors et hors combat.
2. Panneau Config et panneau CET fermés (clé API invisible). Mod rechargé si mis à jour (« Reload all mods »).
3. Agent lancé par le raccourci du bureau, journal visible dans la scène OBS (12 lignes lisibles).
4. Son : micro OK, jeu à 30 %, radio du jeu pas trop forte.
5. Scène « Jeu + journal » active, logo en place, bandeau texte affiché.
6. Chat en mode faible latence, modération activée (mots-clés).
7. Une quête suivie avec marqueur choisie dans le journal du jeu (l'agent la priorise).
8. F11 et F12 testés une fois.

---

## 11. Après le direct

- Copier la ligne `=== fin : {...}` du journal (`%APPDATA%\CyberpunkAgent\brain_log.txt`) : elle donne les chiffres de
  la session pour la description ou un post communauté.
- Découper 2 ou 3 clips (Breach, secours, raté) pour les Shorts.
- Noter les blocages vus en direct : ils deviennent les correctifs de la prochaine version, et le sujet du prochain
  bêtisier.
