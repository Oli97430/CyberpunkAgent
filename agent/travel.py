"""
travel.py -- choisir le moyen de transport d apres ce qui MARCHE vraiment (23/09, revue multi-agents).

Constat sur tout le journal : 3 vraies arrivees en voiture sur ~193 essais (~4 h perdues : embarquement echoue,
autodrive bloque, sans itineraire, ejecte...), alors que le voyage rapide arrive en ~11 s. L autodrive du jeu suit
l itineraire GPS du jeu : quand la quete SUIVIE a un marqueur ailleurs, il part vers lui (« autodrive s eloigne »).
Sans marqueur suivi, 2 des 3 arrivees visaient une autre cible (marqueur choisi par V, charcudoc).

Regles :
  - pas de voiture vers une autre cible quand la quete suivie a un marqueur ailleurs ;
  - disjoncteur : 2 echecs de suite -> plus de voiture pendant 30 min ;
  - voiture peu fiable (moins de 2 reussites sur les 10 derniers essais) -> un seul nouvel essai toutes les 30 min ;
  - sinon : voyage rapide d abord (brain.py / vendor.py), puis a pied.
Les resultats sont gardes d une session a l autre dans %APPDATA%/CyberpunkAgent/travel_stats.json.
"""
from __future__ import annotations

import json
import time

from .config import DATA_DIR

STATS_FILE = DATA_DIR / 'travel_stats.json'
WINDOW = 10            # derniers essais de conduite pris en compte
MIN_OK = 2             # reussites minimales sur la fenetre pour garder la voiture en premier choix
RETEST_S = 1800.0      # voiture peu fiable : un nouvel essai au plus toutes les 30 min
BREAKER_FAILS = 2      # echecs de suite qui coupent la voiture...
BREAKER_S = 1800.0     # ... pendant 30 min
KEEP = 50

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            d = json.loads(STATS_FILE.read_text(encoding='utf-8'))
            _cache = d if isinstance(d, dict) else {}
        except Exception:
            _cache = {}
        if not isinstance(_cache.get('drive'), list):
            _cache['drive'] = []
    return _cache


def record(mode: str, ok: bool, reason: str | None = None) -> None:
    """Resultat d un essai ('drive'). Jamais bloquant : un fichier illisible n arrete pas la session."""
    d = _load()
    runs = d.setdefault(mode, [])
    runs.append({'t': time.time(), 'ok': bool(ok), 'reason': reason})
    del runs[:-KEEP]
    try:
        STATS_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def _fail_streak(runs: list) -> int:
    n = 0
    for r in reversed(runs):
        if r.get('ok'):
            break
        n += 1
    return n


def drive_allowed(route_ok: bool, now: float | None = None) -> tuple[bool, str]:
    """(voiture autorisee ?, raison sinon). `route_ok` : faux quand la quete suivie a un marqueur AILLEURS que la
    cible (l autodrive du jeu partirait vers lui)."""
    if not route_ok:
        return False, 'la quete suivie a un marqueur ailleurs (l autodrive irait vers lui)'
    now = time.time() if now is None else now
    runs = _load()['drive'][-WINDOW:]
    if not runs:
        return True, ''
    since = now - float(runs[-1].get('t') or 0)
    streak = _fail_streak(runs)
    if streak >= BREAKER_FAILS and since < BREAKER_S:
        return False, f'{streak} echecs de voiture de suite : pas de voiture avant {(BREAKER_S - since) / 60:.0f} min'
    oks = sum(1 for r in runs if r.get('ok'))
    if len(runs) >= 5 and oks < MIN_OK and since < RETEST_S:
        return False, f'voiture peu fiable ({oks}/{len(runs)} reussites) : nouvel essai dans {(RETEST_S - since) / 60:.0f} min'
    return True, ''


def choose(dist: float | None, route_ok: bool, drive_possible: bool, since_last_drive: float,
           fasttravel_on: bool, ft_recently_tried: bool) -> tuple[str, str]:
    """'drive' / 'fasttravel' / 'walk' + raison. `drive_possible` : touche autodrive et comportement « conduite »
    actifs ; `since_last_drive` : secondes depuis la fin de la derniere conduite (negatif = pause imposee)."""
    if dist is None or dist <= 500.0:
        return 'walk', ''
    ok, why = drive_allowed(route_ok)
    if ok and not drive_possible:
        ok, why = False, 'conduite desactivee ou pas de touche autodrive'
    if ok and since_last_drive < 240.0:
        ok, why = False, 'conduite ratee il y a moins de 4 min'
    if ok:
        return 'drive', ''
    if fasttravel_on and not ft_recently_tried:
        return 'fasttravel', why
    return 'walk', why


def summary() -> str:
    runs = _load()['drive'][-WINDOW:]
    if not runs:
        return 'voiture : aucun essai enregistre'
    oks = sum(1 for r in runs if r.get('ok'))
    ok_now, why = drive_allowed(True)
    return f"voiture : {oks} reussite(s) sur les {len(runs)} derniers essais" + ('' if ok_now else f' ({why})')
