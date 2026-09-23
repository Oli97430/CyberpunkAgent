"""
explore.py -- competence "explorer" : quand aucun objectif de quete accessible n a de marqueur,
V ne s arrete plus (19/09, demande Olivier : gagner un maximum d XP, de materiel, d argent, monter
en niveau, trouver des recettes rares, decouvrir les secrets du jeu). Il part vers un point de
voyage rapide DEJA DECOUVERT mais pas visite recemment -- ca l amene a traverser de nouveaux
quartiers (nouveaux appels de fixer par SMS, PNJ nommes, hold-ups NCPD, marchands, loot, terminaux)
au lieu de rester bloque sur place une fois la derniere quete connue terminee.
"""
from __future__ import annotations

import time

from . import hud, nav

_visited: dict[int, float] = {}   # index du point -> heure de derniere visite (evite le ping-pong entre 2 points)
REVISIT_S = 2400.0                # 40 min : le temps que de nouveaux appels/hold-ups apparaissent ailleurs


def _key(p: dict) -> int:
    return int(p.get('i') or 0)


def pick(log=print) -> dict | None:
    """Point de voyage rapide connu, pas visite depuis REVISIT_S, le plus lointain d abord
    (explore large plutot que de faire la navette entre 2 points proches)."""
    r = nav._wait(nav._send({'cmd': 'fast_travel_points'}), timeout=6.0)
    pts = [p for p in ((r or {}).get('points') or []) if p.get('x') is not None]
    if not pts:
        log(f"  [exploration] aucun point de voyage rapide connu ({(r or {}).get('reason', 'mod muet')})")
        return None
    now = time.perf_counter()
    cands = [p for p in pts if now - _visited.get(_key(p), -1e9) > REVISIT_S]
    if not cands:
        _visited.clear()          # tous visites recemment : on recommence un tour
        cands = pts
    best = max(cands, key=lambda p: p.get('d') or 0.0)
    _visited[_key(best)] = now
    label = best.get('name') or best.get('district') or '?'
    log(f"  [exploration] aucun objectif accessible : V part decouvrir « {label} » ({best.get('district')}, {best.get('d', 0):.0f} m)")
    hud.event(f'exploration : {label}')
    # hash negatif tres eloigne des hash de quete (floor(x)*100000+floor(y), toujours < 1e9 en valeur absolue
    # sur la carte de Night City) : jamais confondu avec un vrai objectif dans les compteurs de blocage/visite.
    return {'x': best['x'], 'y': best['y'], 'z': best.get('z'), 'text': f"exploration : {label}",
            'hash': -900_000_000 - _key(best)}
