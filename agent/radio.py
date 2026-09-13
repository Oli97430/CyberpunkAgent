"""
radio.py -- V aime ecouter la radio de temps en temps (radioport, patch 2.x), tous les styles :
c est lui qui choisit la station, au hasard. Pas de musique en combat, en dialogue ni en vehicule
(la voiture a sa propre radio).

Rythme : toutes les minutes, hors combat, une chance sur ~12 d allumer la radio (soit environ une
ecoute par quart d heure), pour 3 a 6 minutes, sur une station tiree au sort ; puis il l eteint.
Commande du mod : `radio` (x = 1 allumer / 0 eteindre / 2 station suivante).
"""
from __future__ import annotations

import random
import time

from . import nav

CHECK_S = 60.0
CHANCE = 1 / 12
LISTEN_MIN_S, LISTEN_MAX_S = 180.0, 360.0

_state = {'on': False, 'until': 0.0, 'last_check': -999.0, 'station': None}


def _cmd(x: int) -> dict | None:
    return nav._wait(nav._send({'cmd': 'radio', 'x': x}), timeout=4.0)


def turn_off(log=print) -> None:
    if _state['on']:
        _cmd(0)
        _state['on'] = False
        log('  [radio] eteinte')


def tick(st: dict, log=print, busy: bool = False) -> None:
    """A appeler a chaque tour de boucle. `busy` = combat / dialogue / vehicule : on coupe."""
    from .config import CFG
    if not CFG.features.get('radio', True):
        return
    now = time.perf_counter()
    if busy:
        if _state['on']:
            turn_off(log)
        return
    if _state['on']:
        if now > _state['until']:
            turn_off(log)
        return
    if now - _state['last_check'] < CHECK_S:
        return
    _state['last_check'] = now
    if random.random() > CHANCE:
        return
    r = _cmd(1)
    if not (r and r.get('ok')):
        log(f"  [radio] impossible d allumer : {(r or {}).get('reason', 'mod muet')}")
        _state['last_check'] = now + 600.0          # on n insiste pas avant 10 min
        return
    # station au hasard : quelques « suivante »
    hops = random.randint(0, 12)
    name = r.get('station')
    for _ in range(hops):
        r2 = _cmd(2)
        if r2 and r2.get('ok'):
            name = r2.get('station') or name
        time.sleep(0.15)
    _state.update(on=True, until=now + random.uniform(LISTEN_MIN_S, LISTEN_MAX_S), station=name)
    log(f"  [radio] V allume la radio : « {name or 'station inconnue'} » pour {(_state['until'] - now) / 60:.0f} min")
