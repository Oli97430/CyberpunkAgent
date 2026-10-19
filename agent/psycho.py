"""Chasse aux cyberpsychos (19/10, Olivier : « 3 ») : un marqueur HuntForPsychoVariant couvre une ZONE ; le cyberpsycho
n est pas au centre (V atteignait le centre, ne voyait rien et jugeait le marqueur « sans interet » : 2 fois le 12/10).
V fouille la zone en spirale (12, 25 puis 40 m, 8 directions) jusqu a ce que le combat se declenche ou que des hostiles
apparaissent ; 3 min au plus. Succes vise : « La loi, c est moi ! » (IAmMaxTac)."""
from __future__ import annotations

import math
import time

from . import motion, nav

RINGS = (12.0, 25.0, 40.0)
MAX_S = 180.0


def _hostiles(st: dict) -> list[dict]:
    return [e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('police') and float(e.get('d') or 999) < 60.0]


def search(x: float, y: float, z: float | None, stop=None, log=print, tag: str = 'cyberpsycho') -> bool:
    """Vrai si un combat / des hostiles ont ete trouves (le combat prend le relais), faux sinon."""
    t0 = time.perf_counter()
    log(f'  [{tag}] zone de traque ({x:.0f},{y:.0f}) : fouille en spirale (12, 25, 40 m)')
    for r in RINGS:
        for k in range(8):
            if stop is not None and stop.is_set():
                return False
            if time.perf_counter() - t0 > MAX_S:
                log(f'  [{tag}] 3 min de fouille sans contact : on passe')
                return False
            st = motion.read_state() or {}
            if st.get('combat') or _hostiles(st):
                log(f"  [{tag}] contact ! ({len(_hostiles(st))} hostile(s), combat={bool(st.get('combat'))})")
                return True
            a = k * math.pi / 4 + (r / 12.0) * 0.4          # decalage par anneau : les points ne s alignent pas
            px, py = x + r * math.sin(a), y + r * math.cos(a)
            nav.goto(lambda px=px, py=py: nav.request_path_to(px, py, z), arrive_m=4.0, max_legs=2, timeout=25.0,
                     stop=stop, log=lambda *a, **k: None, interrupt=lambda s: bool(s.get('combat') or _hostiles(s)))
    st = motion.read_state() or {}
    found = bool(st.get('combat') or _hostiles(st))
    if not found:
        log(f'  [{tag}] zone fouillee sans contact')
    return found
