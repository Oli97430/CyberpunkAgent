"""
quests.py -- competence "changer de quete".

Quand l objectif suivi est inaccessible (dialogue qui boucle sur un choix grise, pas
de marqueur, trajet impossible), on liste les quetes actives via le mod (commande
list_quests -> path.json.quests), on ecarte celles deja jugees bloquees, et on suit
(commande track <hash>) l objectif le plus proche qui a un marqueur.
"""
from __future__ import annotations

import time

from . import nav

_blocked: set[int] = set()      # hashes d objectifs juges inaccessibles dans cette session
import json, math
from pathlib import Path
from .config import CFG
_DEATHS = CFG.deaths_file               # %APPDATA%/CyberpunkAgent/deaths.json
try:
    _danger: list = json.loads(_DEATHS.read_text(encoding='utf-8'))     # lieux de mort / zones trop dangereuses (persistants)
except Exception:
    _danger = []


def mark_death(x: float, y: float) -> None:
    _danger.append({'x': x, 'y': y, 'kind': 'mort'})
    try:
        _DEATHS.write_text(json.dumps(_danger), encoding='utf-8')
    except Exception:
        pass


def mark_danger(x: float, y: float) -> None:
    _danger.append({'x': x, 'y': y, 'kind': 'groupe'})


def near_danger(x: float, y: float, r: float = 80.0) -> bool:
    return any(math.hypot(x - d['x'], y - d['y']) < r for d in _danger)


_visited: dict[int, float] = {}  # hash -> heure d arrivee sur ce marqueur (exclu 15 min : il ne se re-choisit pas en boucle)


def mark_visited(hash_: int | None) -> None:
    if hash_ is not None:
        _visited[int(hash_)] = time.perf_counter()


def mark_blocked(hash_: int | None) -> None:
    if hash_ is not None:
        _blocked.add(int(hash_))


def list_active() -> list[dict]:
    resp = nav._wait(nav._send({'cmd': 'list_quests'}), timeout=4.0)
    if not resp or not resp.get('ok'):
        return []
    return resp.get('quests', [])


def track(hash_: int) -> bool:
    resp = nav._wait(nav._send({'cmd': 'track', 'hash': int(hash_)}), timeout=4.0)
    return bool(resp and resp.get('ok'))


LEVEL_MARGIN = 2                # une quete est « de son niveau » si niveau recommande <= niveau de V + 2


def pick_next(quests: list[dict], current_hash: int | None = None, player_level: int | None = None) -> dict | None:
    """Objectif de son NIVEAU d abord (niveau recommande connu et <= niveau de V + 2), puis le plus proche ;
    avec marqueur, pas bloque, pas celui en cours, pas dans une zone dangereuse."""
    cands = [q for q in quests
             if q.get('hasMappin') and q.get('dist') is not None
             and int(q['hash']) not in _blocked and q['hash'] != current_hash
             and q['dist'] >= 8.0 and time.perf_counter() - _visited.get(int(q['hash']), -1e9) > 900.0
             and not near_danger(q['x'], q['y'])]
    if not cands:
        return None
    def over(q):                 # 0 = de son niveau (ou inconnu), 1 = trop haute
        lv = q.get('lvl')
        return 1 if (lv and player_level and lv > player_level + LEVEL_MARGIN) else 0
    def known(q):
        return 0 if (q.get('lvl') and player_level and q['lvl'] <= player_level + LEVEL_MARGIN) else 1
    # a pied, une quete a < 400 m est realiste ; au-dela, on n y va qu a defaut
    return min(cands, key=lambda q: (over(q), known(q), q['dist'] >= 400, q['dist']))


def switch(current_hash: int | None, log=print) -> dict | None:
    mark_blocked(current_hash)
    quests = list_active()
    if not quests:
        log('  [quetes] liste vide ou mod muet')
        return None
    log(f"  [quetes] {len(quests)} objectif(s) actifs, {sum(1 for q in quests if q.get('hasMappin'))} avec marqueur")
    from . import motion as _m
    _st = _m.read_state() or {}
    nxt = pick_next(quests, current_hash, player_level=_st.get('level'))
    if not nxt:
        log('  [quetes] aucun autre objectif accessible')
        return None
    # JournalManager.GetQuests plante le jeu : on ne suit plus via le journal, on renvoie
    # la position du marqueur de quete choisi ; le cerveau y va directement.
    log(f"  [quetes] nouvelle cible : marqueur « {nxt.get('text')} » a {nxt['dist']:.0f} m ({nxt['x']:.0f},{nxt['y']:.0f})"
        + (f" niveau {nxt['lvl']} (V : {_st.get('level')})" if nxt.get('lvl') else ''))
    return nxt
