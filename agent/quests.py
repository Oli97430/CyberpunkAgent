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

_blocked: dict[int, float] = {}  # hash -> heure de blocage ; expire (contrairement a l ancien set() permanent :
                                  # switch() bloquait tout objectif traverse, meme sur un signal transitoire)
BLOCKED_S = 1200.0                # 20 min : le temps qu une porte recalcitrante ou une foule se degage
import json, math
from pathlib import Path
from .config import CFG
_DEATHS = CFG.deaths_file               # %APPDATA%/CyberpunkAgent/deaths.json
def _load_danger() -> list:
    """Lieux de mort / zones trop dangereuses (persistants) : valides (dict avec x, y numeriques), 200 derniers."""
    try:
        raw = json.loads(_DEATHS.read_text(encoding='utf-8'))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict) and isinstance(d.get('x'), (int, float)) and isinstance(d.get('y'), (int, float))][-200:]


_danger: list = _load_danger()


def mark_death(x: float, y: float) -> None:
    if near_danger(x, y, 10.0):
        return                                  # deja connu : pas de doublon
    _danger.append({'x': x, 'y': y, 'kind': 'mort'})
    del _danger[:-200]
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
        _blocked[int(hash_)] = time.perf_counter()
        if len(_blocked) > 200:                                     # purge : on garde les 100 plus recents
            for old_h in sorted(_blocked, key=lambda h: _blocked[h])[:len(_blocked) - 100]:
                _blocked.pop(old_h, None)


def list_active() -> list[dict]:
    resp = nav._wait(nav._send({'cmd': 'list_quests'}), timeout=4.0)
    if not resp or not resp.get('ok'):
        return []
    return resp.get('quests', [])


def track(hash_: int) -> bool:
    resp = nav._wait(nav._send({'cmd': 'track', 'hash': int(hash_)}), timeout=4.0)
    return bool(resp and resp.get('ok'))


LEVEL_MARGIN = 2                # une quete est « de son niveau » si niveau recommande <= niveau de V + 2

# 24/09 : marqueurs STERILES memorises d une session a l autre (V revisitait 17-18 fois les memes marqueurs vides :
# ~283 min de revisites sur le journal). Exclusion croissante a chaque visite sterile de suite ; une visite utile remet
# a zero.
MEMORY_FILE = CFG.log_file.parent / 'markers_memory.json'
BACKOFF_S = (900.0, 3600.0, 4 * 3600.0, 16 * 3600.0, 72 * 3600.0)
_mem: dict | None = None


def _mem_load() -> dict:
    global _mem
    if _mem is None:
        try:
            d = json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
            _mem = d if isinstance(d, dict) else {}
        except Exception:
            _mem = {}
    return _mem


def _mkey(text, x: float, y: float, z=None) -> str:
    zc = round(float(z) / 3.0) if isinstance(z, (int, float)) else '?'
    return f'{text}|{round(float(x) / 2.0)},{round(float(y) / 2.0)},{zc}'


def _find(text, x: float, y: float, z=None) -> str:
    """Cle memorisee du marqueur : meme cellule (2 m, etage), sinon meme variante a moins de 2,5 m et meme etage
    (bord de cellule ; des marqueurs distincts a 3-10 m l un de l autre ne doivent pas se confondre)."""
    m = _mem_load()
    k = _mkey(text, x, y, z)
    if k in m:
        return k
    for kk, e in m.items():
        try:
            if not (isinstance(e, dict) and e.get('text') == text and math.hypot(float(e['x']) - x, float(e['y']) - y) < 2.5):
                continue
            if isinstance(z, (int, float)) and isinstance(e.get('z'), (int, float)) and abs(e['z'] - z) >= 3.0:
                continue
            return kk
        except (KeyError, TypeError, ValueError):
            continue
    return k


def marker_wait(q: dict, now: float | None = None) -> float:
    """Secondes restantes d exclusion d un marqueur juge sterile (0 = libre)."""
    if q.get('x') is None or q.get('y') is None:
        return 0.0
    e = _mem_load().get(_find(q.get('text'), float(q['x']), float(q['y']), q.get('z')))
    if not isinstance(e, dict):
        return 0.0
    try:
        return max(0.0, float(e.get('until') or 0) - (time.time() if now is None else now))
    except (TypeError, ValueError):
        return 0.0


def marker_result(text, x: float, y: float, useful: bool, why: str = '', z=None, log=print) -> None:
    """Bilan d une visite de marqueur choisi par V : utile (remise a zero) ou sterile (exclusion croissante)."""
    if x is None or y is None:
        return
    m = _mem_load()
    k = _find(text, float(x), float(y), z)
    e = m.get(k) if isinstance(m.get(k), dict) else {}
    e['text'], e['x'], e['y'] = text, round(float(x), 1), round(float(y), 1)
    if isinstance(z, (int, float)):
        e['z'] = round(float(z), 1)
    now = time.time()
    e['visits'] = int(e.get('visits') or 0) + 1 if str(e.get('visits') or 0).isdigit() else 1
    e['t'] = now
    if useful:
        e['streak'] = 0; e['until'] = 0; e['last'] = 'utile'
        log(f'  [quetes] marqueur « {text} » ({x:.0f},{y:.0f}) : visite utile ({why})')
    else:
        try:
            n = int(e.get('streak') or 0) + 1
        except (TypeError, ValueError):
            n = 1
        wait = BACKOFF_S[min(n, len(BACKOFF_S)) - 1]
        e['streak'] = n; e['until'] = now + wait; e['last'] = 'sterile'
        log(f'  [quetes] marqueur « {text} » ({x:.0f},{y:.0f}) sans interet ({why}, {n}x de suite) : ignore '
            + (f'{wait / 3600:.0f} h' if wait >= 3600 else f'{wait / 60:.0f} min'))
    m[k] = e
    if len(m) > 500:                                             # purge : les 300 plus recents
        for old in sorted(m, key=lambda kk: float((m[kk] or {}).get('t') or 0) if isinstance(m[kk], dict) else 0)[:len(m) - 300]:
            m.pop(old, None)
    try:
        MEMORY_FILE.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    except Exception as ex:
        log(f'  [quetes] memoire des marqueurs non sauvee : {ex}')


def pick_next(quests: list[dict], current_hash: int | None = None, player_level: int | None = None, prefer_givers: bool = False,
              avoid: tuple | None = None) -> dict | None:
    """Objectif de son NIVEAU d abord (niveau recommande connu et <= niveau de V + 2), puis le plus proche ;
    avec marqueur, pas bloque, pas celui en cours, pas dans une zone dangereuse."""
    cands = [q for q in quests
             if q.get('hasMappin') and q.get('dist') is not None
             and time.perf_counter() - _blocked.get(int(q['hash']), -1e9) > BLOCKED_S and q['hash'] != current_hash
             and q['dist'] >= 8.0 and time.perf_counter() - _visited.get(int(q['hash']), -1e9) > 900.0
             and marker_wait(q) <= 0.0
             and not near_danger(q['x'], q['y'])
             and not any(math.hypot(q['x'] - a[0], q['y'] - a[1]) < a[2] for a in _avoids(avoid))]   # 12/10 : (x, y, rayon) ecartes
    if not cands:
        return None
    def over(q):                 # 0 = de son niveau (ou inconnu), 1 = trop haute
        lv = q.get('lvl')
        return 1 if (lv and player_level and lv > player_level + LEVEL_MARGIN) else 0
    def known(q):
        return 0 if (q.get('lvl') and player_level and q['lvl'] <= player_level + LEVEL_MARGIN) else 1
    # a pied, une quete a < 400 m est realiste ; au-dela, on n y va qu a defaut
    def giver(q):                # 0 = donneur de quete (on lui parle pour lancer une quete), 1 = autre marqueur
        return 0 if (prefer_givers and 'QuestGiver' in str(q.get('text') or '')) else 1
    return min(cands, key=lambda q: (over(q), giver(q), known(q), q['dist'] >= 400, q['dist']))


def _avoids(avoid) -> list:
    """avoid : (x, y, rayon), ou une liste de ces triplets (None ignores)."""
    if not avoid:
        return []
    if isinstance(avoid, tuple) and len(avoid) == 3 and not isinstance(avoid[0], (tuple, list)):
        return [avoid]
    return [a for a in avoid if a and a[0] is not None and a[1] is not None]


def switch(current_hash: int | None, log=print, prefer_givers: bool = False, avoid=None) -> dict | None:
    mark_blocked(current_hash)
    quests = list_active()
    if not quests:
        log('  [quetes] liste vide ou mod muet')
        return None
    log(f"  [quetes] {len(quests)} objectif(s) actifs, {sum(1 for q in quests if q.get('hasMappin'))} avec marqueur")
    from . import motion as _m
    _st = _m.read_state() or {}
    nxt = pick_next(quests, current_hash, player_level=_st.get('level'), prefer_givers=prefer_givers, avoid=avoid)
    if nxt and prefer_givers and 'QuestGiver' in str(nxt.get('text') or ''):
        log('  [quetes] aucune quete suivie : V va voir un donneur de quete de son niveau pour en decrocher une')
    if not nxt:
        log('  [quetes] aucun autre objectif accessible')
        return None
    # JournalManager.GetQuests plante le jeu : on ne suit plus via le journal, on renvoie
    # la position du marqueur de quete choisi ; le cerveau y va directement.
    log(f"  [quetes] nouvelle cible : marqueur « {nxt.get('text')} » a {nxt['dist']:.0f} m ({nxt['x']:.0f},{nxt['y']:.0f})"
        + (f" niveau {nxt['lvl']} (V : {_st.get('level')})" if nxt.get('lvl') else ''))
    return nxt
