"""Contrats de rue (19/10, Olivier : « 4 » -- « plus de combats » ; session 16:49 : 0 combat en 21 min) : les activites NCPD
et de gangs de la carte (agression en cours, crime organise, planque suspecte, client en detresse, convoi, prime, mission
NCPD, cyberpsycho) n etaient pas dans la liste des quetes que V lit -- il ne les voyait jamais. Commande `street_jobs` du mod
(marqueurs actifs, non termines) ; V prend la plus proche (lieux de mort recents evites, chacune ecartee 2 h une fois faite).
A l arrivee, fouille en spirale jusqu au contact (comme un cyberpsycho)."""
from __future__ import annotations

import math
import time

from . import nav

LABELS = {'GangWatchVariant': 'AGRESSION EN COURS', 'OutpostVariant': 'CRIME ORGANISE', 'HiddenStashVariant': 'PLANQUE SUSPECTE',
          'ClientInDistressVariant': 'CLIENT EN DETRESSE', 'ConvoyVariant': 'CONVOI', 'BountyHuntVariant': 'PRIME',
          'Zzz06_NCPDGigVariant': 'MISSION NCPD', 'HuntForPsychoVariant': 'CYBERPSYCHO', 'MinorActivityVariant': 'ACTIVITE DE RUE'}
COMBAT_KINDS = set(LABELS)                 # activites de rue (ecartees 2 h une fois atteintes)
SEARCH_KINDS = COMBAT_KINDS - {'HiddenStashVariant'}   # a l arrivee : fouille jusqu au contact (une planque se fouille)
DONE_S = 7200.0
_done: dict[tuple, float] = {}


def _key(x: float, y: float) -> tuple:
    return (round(x / 20.0), round(y / 20.0))


def scan() -> list[dict]:
    r = nav._wait(nav._send({'cmd': 'street_jobs'}), timeout=5.0) or {}
    return [j for j in (r.get('jobs') or []) if j.get('x') is not None and j.get('variant') in LABELS]


def mark_done(x: float, y: float) -> None:
    _done[_key(x, y)] = time.time()


def pick(max_d: float = 2500.0, log=print) -> dict | None:
    """Contrat le plus proche (ni fait depuis 2 h, ni pres d une mort recente) ; cible au format des marqueurs."""
    from . import quests
    now = time.time()
    jobs = [j for j in scan() if (j.get('dist') or 1e9) <= max_d and now - _done.get(_key(j['x'], j['y']), -1e9) > DONE_S
            and not quests.near_danger(j['x'], j['y'])]
    if not jobs:
        return None
    j = min(jobs, key=lambda j: j['dist'])
    lab = LABELS.get(j['variant'], j['variant'])
    log(f"[contrat] {lab} signale a {j['dist']:.0f} m ({len(jobs)} activite(s) de rue disponible(s)) : V intervient")
    return {'x': j['x'], 'y': j['y'], 'z': j.get('z'), 'text': j['variant'], 'dist': j['dist'],
            'hash': -800_000_000 - (int(math.floor(j['x'])) * 1000 + int(math.floor(j['y']))) % 100_000_000}
