"""Gouts de V en vehicules (12/10, Olivier : « jamais vu V prendre le controle d un vehicule qui lui plairait »).

Score = score du mod (constructeur, Sport, ch/kg, classe Premium / Poor ; init.lua AgentVehInfo) + affinites de V selon le
nom du record + humeur de la session (voiture ou moto). Situation : gare (vide, conducteur inconscient, abandonne) ou
carjacking (conducteur arrete au feu). Garde-fous : police, quete, verrous, combat, etoiles, 1 vol / 10 min."""
from __future__ import annotations

import random
import time

from . import travel

LIKES = {'caliburn': 10, 'aerondight': 8, 'quadra_turbo': 10, 'type66': 10, 'porsche_911': 12, 'v_sportbike2_arch': 12,
         'kusanagi': 8, 'shion': 8, 'outlaw': 5, 'riptide': 5, 'alvarado': 5, 'emperor': 6, 'apollo': 4,
         'maimai': -20, 'galena': -20, 'colby': -10, 'supron': -10, 'columbus': -10, 'v_utility4_': -40, '_poor': -15}
NEVER_RID = ('_police', '_quest', 'zeya', 'delamain')
NEVER_MK = ('Delamain', 'Zetatech', 'Kaukaz')
HEART, NEAR_OK = 60, 40                       # coup de coeur / acceptable s il est gare tout pres
MOOD = random.choice(('Car', 'Bike', None))   # humeur de la session : +5
_seen: dict = {}                              # identite -> dernier tirage (pas de nouveau tirage avant 5 min)


def score(v: dict) -> int:
    rid = str(v.get('rid') or '').lower()
    if not rid or v.get('cop') or v.get('mk') in NEVER_MK or any(n in rid for n in NEVER_RID):
        return 0
    s = float(v.get('pr') or 0) + sum(b for k, b in LIKES.items() if k in rid) + (5 if MOOD and v.get('vt') == MOOD else 0)
    return int(max(0, min(100, round(s))))


def situation(v: dict) -> str | None:
    if v.get('player') or v.get('cop') or v.get('qst') or v.get('hos') or v.get('lock') in (2, 3, 4) or float(v.get('speed') or 0) > 0.5:
        return None
    occ = v.get('occ')
    if occ == 0 and (v.get('aban') or not v.get('lane')):
        return 'gare'                          # vide, hors voie de circulation (ou abandonne)
    if occ == 1 and v.get('lane') and v.get('civ') is True and not v.get('pax'):
        return 'carjack'                       # civil seul, arrete au feu (revue : pas de gang, pas de passagers)
    return None                                # siege occupe par un corps, passagers, gang, occupation inconnue


def fallback_ok(v: dict) -> bool:
    """Vol de secours (sa voiture n est pas venue) : memes exclusions, score minimal 25 (revue)."""
    return situation(v) == 'gare' and score(v) >= 25


def police_veto(st: dict) -> bool:
    """Police a portee : PNJ (distance calculee par le mod AVANT la troncature a 8) ou voiture de police a < 50 m."""
    dx = st.get('dx') or {}
    if dx.get('wl') or float(dx.get('copd') or 999) < 40:
        return True
    return any(v.get('cop') and float(v.get('d') or 999) < 50 for v in (st.get('vehicles') or []))


def _steal_runs() -> list:
    try:
        return list(travel._load().get('steal') or [])
    except Exception:
        return []


def pace_ok(now: float | None = None) -> tuple[bool, str]:
    """1 vol / 10 min ; 2 echecs de suite -> 30 min ; autodrive refuse 3 fois de suite (vehicule vole) -> 1 / heure."""
    now = time.time() if now is None else now
    runs = _steal_runs()
    if not runs:
        return True, ''
    since = now - float(runs[-1].get('t') or 0)
    streak = travel._fail_streak(runs)
    refused = 0
    for r in reversed(runs):
        if r.get('ok') or 'autodrive refuse' not in str(r.get('reason') or ''):
            break
        refused += 1
    wait = 3600.0 if refused >= 3 else 1800.0 if streak >= 2 else 600.0
    if since < wait:
        return False, f'dernier vol il y a {since / 60:.0f} min (pause {wait / 60:.0f} min)'
    return True, ''


def pick(st: dict, carjack_on: bool = True, log=print, police_near: bool = False) -> dict | None:
    """Le vehicule qui plait a V, s il est prenable maintenant ; sinon None (une ligne de journal par refus utile)."""
    if st.get('combat') or st.get('vehicle') or police_near or police_veto(st):
        return None
    ok, why = pace_ok()
    if not ok:
        return None
    best = None
    for v in st.get('vehicles') or []:
        mode, s, d = situation(v), score(v), float(v.get('d') or 99)
        good = (mode == 'gare' and ((s >= HEART and d <= 40) or (s >= NEAR_OK and d <= 15))) or \
               (mode == 'carjack' and carjack_on and s >= HEART and d <= 8)
        if good and (best is None or s > best[0]):
            best = (s, mode, v)
    if not best:
        return None
    s, mode, v = best
    ident = v.get('h') or (round(float(v.get('x') or 0)), round(float(v.get('y') or 0)))
    now = time.time()
    if now - _seen.get(ident, -1e9) < 300:
        return None
    _seen[ident] = now
    if random.random() >= min(0.9, (s - 35) / 60.0):       # Caliburn 95 -> 0,9 ; Type-66 72 -> 0,62 ; 45 -> 0,17
        log(f"  [gouts] V regarde « {v.get('name')} » ({v.get('mk')}, score {s}) et passe son chemin")
        return None
    return dict(v, score=s, mode=mode)
