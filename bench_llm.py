"""
bench_llm.py -- compare des modeles Ollama sur les decisions de V (memes prompts que le jeu).

    python bench_llm.py llama3.2:latest qwen2.5:14b gemma4:e4b

Pour chaque modele : precision sur des situations a reponse evidente (decision + dialogue) et latence.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.config import CFG  # noqa: E402
from agent import dialog, planner  # noqa: E402

import os
CFG.provider = os.environ.get('BENCH_PROVIDER', 'ollama')

# situations ouvertes (le planificateur appelle le modele) : etat synthetique -> action attendue
SITUATIONS = [
    ({'hp': 90, 'combat': False, 'enemies': [], 'npcs': [{'name': 'Judy', 'd': 3, 'aggressive': False}],
      'interact': {'title': 'Judy', 'choices': ['Parler']}, 'quest': {'text': 'Parler a Judy.', 'hasMappin': True}},
     {'dist_m': 4, 'history': 'rien'}, 'parler'),
    ({'hp': 95, 'combat': False, 'enemies': [], 'npcs': [],
      'interact': {'title': 'Porte', 'choices': ['Ouvrir']}, 'quest': {'text': 'Entrer dans l entrepot.', 'hasMappin': True}},
     {'dist_m': 2, 'history': 'rien'}, 'parler'),
    ({'hp': 100, 'combat': False, 'enemies': [], 'npcs': [{'name': 'Resident', 'd': 6, 'aggressive': False}],
      'interact': {'title': 'Resident', 'choices': ['Parler']}, 'quest': {'text': 'Rejoindre Jackie au bar.', 'hasMappin': True}},
     {'dist_m': 640, 'history': 'rien'}, 'objectif'),
    ({'hp': 40, 'combat': False, 'enemies': [], 'npcs': [],
      'interact': {'title': 'Distributeur', 'choices': ['Acheter']}, 'quest': {'text': 'Retrouver Takemura.', 'hasMappin': True}},
     {'dist_m': 900, 'history': 'a engage un combat'}, 'objectif'),
]
# agressions : (n agresseurs, distance, vie) -> attendu
RESCUES = [((1, 18.0, 95.0), 'secourir'), ((2, 25.0, 90.0), 'secourir'), ((3, 12.0, 35.0), 'objectif')]
# dialogues : (titre, choix) -> index attendu
DIALOGS = [
    ('Jackie', ['[Partir]', 'Raconte-moi ce qui s est passe.', 'Je n ai pas le temps.'], 1),
    ('Fixer', ['Accepter le contrat.', 'Refuser.', 'Insulter le fixer.'], 0),
    ('Viktor', ['Je reviendrai quand j aurai l argent.', 'Payer 21 000 eddies (tu as 3 000 eddies).'], 0),
    ('Garde', ['[Attaquer] Dégage.', 'Je cherche juste mon chemin.', 'Rien.'], 1),
]


def bench(model: str) -> dict:
    CFG.model = model
    planner.MODEL = model
    dialog.MODEL = model
    # chauffe
    try:
        dialog.llm_choice('test', ['a', 'b'], timeout=180)
    except Exception:
        pass
    ok, n, lat = 0, 0, []
    for st, extra, want in SITUATIONS:
        t0 = time.perf_counter()
        act, why = planner._decide_llm(st, extra, timeout=30.0)
        lat.append(time.perf_counter() - t0); n += 1; ok += (act == want)
        print(f'  [{model}] situation -> {act:9s} (attendu {want:9s}) {lat[-1]:.1f}s  {why[:70]}')
    for (k, d, hp), want in RESCUES:
        st = {'hp': hp, 'quest': {'text': 'Trouver Jotaro.'}}
        t0 = time.perf_counter()
        act, why = planner._decide_rescue(st, {'history': 'rien'}, k, d, timeout=30.0)
        lat.append(time.perf_counter() - t0); n += 1; ok += (act == want)
        print(f'  [{model}] secours {k} agr/{hp:.0f}% -> {act:9s} (attendu {want:9s}) {lat[-1]:.1f}s')
    for title, choices, want in DIALOGS:
        t0 = time.perf_counter()
        k = dialog.llm_choice(title, choices, timeout=30.0)
        lat.append(time.perf_counter() - t0); n += 1; ok += (k == want)
        print(f'  [{model}] dialogue « {title} » -> {k} (attendu {want}) {lat[-1]:.1f}s')
    return {'model': model, 'precision': ok / n, 'ok': ok, 'n': n, 'latence_mediane_s': statistics.median(lat), 'latence_max_s': max(lat)}


if __name__ == '__main__':
    models = sys.argv[1:] or ['llama3.2:latest']
    results = [bench(m) for m in models]
    print('\n=== bilan ===')
    for r in results:
        print(f"{r['model']:22s} precision {r['ok']}/{r['n']} ({r['precision']:.0%})  latence mediane {r['latence_mediane_s']:.1f} s  max {r['latence_max_s']:.1f} s")
