"""world.py -- LE CHAT CONTROLE LE MONDE (30/10/2026, Olivier).

Les spectateurs votent aussi pour des evenements dans la partie (meme vote de 60 s que les ordres de V) :
!pluie !soleil !brouillard !tempete (meteo forcee 3 min, puis rendue au jeu), !nuit !jour (heure), !police (recherche
NCPD niveau 2). Le mod execute la commande « world » (AgentProbe). Pas de ralenti : SetTimeDilation plante le jeu depuis CET.

Garde-fous : chaque evenement a son temps de recharge ; la police est refusee en vehicule de quete / scene (V fige) et
pas plus d une fois toutes les 10 min.
"""
from __future__ import annotations

import threading
import time

from . import nav

WEATHER = ('pluie', 'soleil', 'brouillard', 'tempete')
EVENTS = WEATHER + ('nuit', 'jour', 'police')
WEATHER_S = 180.0                                  # meteo forcee pendant 3 min
COOLDOWN = {'police': 600.0, 'nuit': 120.0, 'jour': 120.0}
COOLDOWN_DEFAULT = 60.0
_last: dict[str, float] = {}
_reset = {'timer': None}


def _cmd(x: str) -> dict | None:
    return nav._wait(nav._send({'cmd': 'world', 'x': x}), timeout=4.0)


def _reset_weather(log) -> None:
    r = _cmd('meteo_reset')
    log(f"  [monde] meteo rendue au jeu{'' if (r or {}).get('ok') else ' : echec (' + str((r or {}).get('reason')) + ')'}")


def trigger(ev: str, st: dict | None = None, log=print) -> bool:
    """Execute un evenement vote par le chat. Vrai si le jeu l a accepte."""
    from . import narrator
    st = st or {}
    now = time.time()
    left = COOLDOWN.get(ev, COOLDOWN_DEFAULT) - (now - _last.get(ev, -1e9))
    if left > 0:
        log(f'  [monde] {ev} : recharge encore {left:.0f} s')
        narrator.say(f'{ev.upper()} : RECHARGE EN COURS.', force=True)
        return False
    if ev == 'police' and st.get('scene'):
        log('  [monde] police refusee : scene en cours')
        return False
    r = _cmd(ev)
    if not (r or {}).get('ok'):
        log(f"  [monde] {ev} : refuse par le jeu ({(r or {}).get('reason') or 'pas de reponse'})")
        return False
    _last[ev] = now
    log(f'  [monde] le chat declenche : {ev}')
    if ev in WEATHER:
        t = _reset.get('timer')
        if t:
            t.cancel()                              # nouvelle meteo : le compte a rebours repart
        t = threading.Timer(WEATHER_S, _reset_weather, args=(log,))
        t.daemon = True
        t.start()
        _reset['timer'] = t
    try:
        from . import hud
        hud.event(f'le chat controle le monde : {ev}')
    except Exception:
        pass
    return True
