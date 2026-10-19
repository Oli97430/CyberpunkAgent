"""Ascenseurs (19/10, Olivier : « 3 ») : quand l objectif est a un AUTRE ETAGE (ex. « Parler a Hanako Arasaka » : 17 m a plat,
mais en haut d une tour), le navmesh ne trouve aucun chemin et V tournait en rond (5 cas dans la session 15:14).

V cherche un terminal d ascenseur a son etage (commande `lifts` du mod : ElevatorFloorTerminal / LiftDevice a 40 m), y
va, appelle l ascenseur (invite « Appeler... »), entre dans la cabine, choisit l etage dans la bonne direction (le plus haut
numero pour monter, le plus bas pour descendre, a defaut le dernier / premier choix) et verifie que son altitude change.
Tout est journalise « [ascenseur] » : premiere version, a valider en jeu. 45 s au plus."""
from __future__ import annotations

import math
import re
import time

from . import input_kbm as kbm, motion, nav

FLOOR_WORDS = ('etage', 'hall', 'toit', 'rez', 'niveau', 'floor', 'lobby', 'sous-sol', 'parking', 'roof', 'level')
CALL_WORDS = ('appeler', 'ascenseur', 'call', 'elevator', 'lift')
MAX_S = 45.0


def scan() -> list[dict]:
    r = nav._wait(nav._send({'cmd': 'lifts'}), timeout=4.0) or {}
    return [l for l in (r.get('lifts') or []) if l.get('x') is not None]


def _fold(s: str) -> str:
    import unicodedata
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii').lower()


def floor_pick(choices: list, up: bool) -> int | None:
    """Indice de l etage a choisir parmi les invites de la cabine (None si ce ne sont pas des etages)."""
    floors = [i for i, c in enumerate(choices) if re.search(r'\d', str(c)) or any(w in _fold(c) for w in FLOOR_WORDS)]
    if len(floors) < 2:
        return None
    nums = []
    for i in floors:
        m = re.search(r'-?\d+', str(choices[i]))
        if m:
            nums.append((int(m.group(0)), i))
        elif any(w in _fold(choices[i]) for w in ('rez', 'hall', 'lobby')):
            nums.append((0, i))
        elif any(w in _fold(choices[i]) for w in ('toit', 'roof')):
            nums.append((999, i))
        elif any(w in _fold(choices[i]) for w in ('sous-sol', 'parking')):
            nums.append((-1, i))
    if len(nums) >= 2:
        return (max(nums) if up else min(nums))[1]
    return floors[-1] if up else floors[0]


def _choose(idx: int) -> None:
    """Invite numero idx (0 = premiere) : molette depuis la premiere, puis F."""
    from . import dialog
    w = dialog._wheel_sign[0] or -1               # sens appris sur les dialogues ; a defaut, molette vers le bas
    for _ in range(idx):
        kbm.wheel(w); time.sleep(0.12)
    kbm.act('interact', 0.3)


def try_reach(tz: float, stop=None, log=print) -> bool:
    """Vrai si V a change d etage vers l objectif (altitude rapprochee d au moins 3 m)."""
    t0 = time.perf_counter()
    st = motion.read_state() or {}
    if st.get('z') is None:
        return False
    z0 = st['z']
    dz0 = tz - z0
    up = dz0 > 0
    lifts = scan()
    here = sorted([l for l in lifts if abs(float(l.get('z') or 0) - z0) < 3.5], key=lambda l: l.get('d') or 999)
    log(f"[ascenseur] objectif {abs(dz0):.0f} m plus {'haut' if up else 'bas'} : {len(lifts)} element(s) d ascenseur a 40 m, "
        f"{len(here)} a cet etage ({', '.join(sorted({str(l.get('cls')) for l in lifts})) or 'aucun'})")
    if not here:
        return False
    t = here[0]
    nav.goto(lambda: nav.request_path_to(t['x'], t['y'], t.get('z')), arrive_m=1.6, max_legs=3, timeout=25.0, stop=stop,
             log=lambda *a, **k: None)
    try:
        s1 = motion.read_state() or {}
        motion.turn_to(motion.bearing_to(s1['x'], s1['y'], t['x'], t['y']), timeout=2.0, stop=stop)
    except Exception:
        pass
    called = entered = False
    while time.perf_counter() - t0 < MAX_S:
        if stop is not None and stop.is_set():
            return False
        s = motion.read_state() or {}
        if s.get('z') is not None and abs(tz - s['z']) < abs(dz0) - 3.0:
            log(f"[ascenseur] etage atteint : altitude {z0:.0f} -> {s['z']:.0f} m (objectif {tz:.0f} m)")
            return True
        choices = (s.get('interact') or {}).get('choices') or []
        fi = floor_pick(choices, up) if choices else None
        if fi is not None:
            log(f"[ascenseur] cabine : etages {' / '.join(str(c) for c in choices)[:120]} -> « {choices[fi]} »")
            _choose(fi)
            for _ in range(24):                           # trajet de la cabine : jusqu a 12 s
                time.sleep(0.5)
                s2 = motion.read_state() or {}
                if s2.get('z') is not None and abs(tz - s2['z']) < abs(dz0) - 3.0:
                    log(f"[ascenseur] etage atteint : altitude {z0:.0f} -> {s2['z']:.0f} m (objectif {tz:.0f} m)")
                    return True
            continue
        if choices and not called:
            ci = next((i for i, c in enumerate(choices) if any(w in _fold(c) for w in CALL_WORDS)), 0)
            log(f"[ascenseur] terminal : « {choices[ci]} »")
            _choose(ci); called = True
            time.sleep(6.0)                               # l ascenseur arrive, les portes s ouvrent
            continue
        if called and not entered:
            cab = sorted([l for l in scan() if l.get('cls') == 'LiftDevice'], key=lambda l: l.get('d') or 999)
            if cab:
                log(f"[ascenseur] V entre dans la cabine (a {cab[0].get('d', 0):.0f} m)")
                old = motion.ARRIVE_M; motion.ARRIVE_M = 1.0
                try:
                    motion.walk_to(cab[0]['x'], cab[0]['y'], timeout=6.0, stop=stop)
                finally:
                    motion.ARRIVE_M = old
            entered = True
            continue
        kbm.look(150, 0); time.sleep(0.6)               # chercher l invite (panneau a cote de V)
    s = motion.read_state() or {}
    log(f"[ascenseur] echec en {MAX_S:.0f} s (altitude {z0:.0f} -> {float(s.get('z') or z0):.0f} m, objectif {tz:.0f} m)")
    return False
