"""
escape.py -- competence "sortir d ici" : V est sur un ilot de maillage ferme (piece, local, toit,
epave) : aucun chemin, meme partiel, et la ligne droite est bloquee. Dans l ordre :
  1. PORTES / dispositifs proches (mod : `doors`) : aller a la plus proche, la regarder, E court puis
     E long ; puis ouverture par script (`door_open`) ; on verifie qu un chemin redevient possible.
  2. balayage a 360 deg a la recherche d une invite (Ouvrir / Activer / Utiliser / Forcer) -> E.
  3. sondes de mouvement : 8 directions, sprint + saut 2,5 s chacune ; on garde la direction qui a
     fait avancer V le plus, et on la pousse 6 s de plus.
Renvoie {'ok': bool, 'moyen': str}. Journal detaille.
"""
from __future__ import annotations

import math
import time

from . import input_kbm as kbm, motion, nav

PROMPT_WORDS = ('ouvrir', 'activer', 'utiliser', 'forcer', 'pirater', 'monter', 'descendre', 'appeler', 'open', 'use')
BAD_WORDS = ('saisir', 'porter', 'prendre le contr', 'enfourcher')


def _path_possible(tx, ty, tz) -> bool:
    r = nav._wait(nav._send({'cmd': 'path_to', 'x': tx, 'y': ty, 'z': tz}), timeout=5.0)
    return bool(r and r.get('ok'))


_pressed: dict[str, int] = {}


def _try_prompt(log) -> bool:
    st = motion.read_state() or {}
    inter = st.get('interact') or {}
    ch = str((inter.get('choices') or [''])[0]).lower()
    if ch and _pressed.get(ch, 0) >= 2:
        return False                                  # deja essayee deux fois sans effet : on passe
    if ch and any(w in ch for w in PROMPT_WORDS) and not any(w in ch for w in BAD_WORDS):
        _pressed[ch] = _pressed.get(ch, 0) + 1
        log(f"  [sortie] invite « {ch} » -> E court puis E long")
        kbm.act('interact', 0.1); time.sleep(0.8)
        kbm.act('interact', 1.2); time.sleep(1.0)
        return True
    return False


def escape(target_xy: tuple | None, stop=None, log=print) -> dict:
    _pressed.clear()   # sourdine des invites : propre a CET episode de blocage, pas a la session entiere
    t0 = time.perf_counter()
    st0 = motion.read_state()
    if not st0:
        return {'ok': False, 'moyen': 'etat illisible'}
    x0, y0 = st0['x'], st0['y']
    tz = st0.get('z')

    # 1. portes et dispositifs
    r = nav._wait(nav._send({'cmd': 'doors'}), timeout=5.0)
    doors = (r or {}).get('doors') or []
    if doors:
        log(f"  [sortie] {len(doors)} porte(s)/dispositif(s) : " + ', '.join(f"{d.get('name', '?')} a {d['d']:.0f} m" for d in doors[:4]))
    for d in doors[:3]:
        if stop is not None and stop.is_set():
            break
        old = motion.ARRIVE_M; motion.ARRIVE_M = 1.4
        try:
            motion.walk_to(d['x'], d['y'], timeout=12.0, stop=stop)
        finally:
            motion.ARRIVE_M = old
        s = motion.read_state() or st0
        motion.turn_to(motion.bearing_to(s['x'], s['y'], d['x'], d['y']), timeout=2.0, stop=stop)
        time.sleep(0.3)
        if not _try_prompt(log):
            kbm.act('interact', 0.1); time.sleep(0.6); kbm.act('interact', 1.2); time.sleep(0.8)
        ro = nav._wait(nav._send({'cmd': 'door_open', 'x': d['i']}), timeout=5.0)
        if ro and ro.get('ok'):
            log(f"  [sortie] porte « {d.get('name', '?')} » : script [{','.join(ro.get('methodes') or [])}] open={ro.get('open')}")
        time.sleep(1.0)
        if target_xy and _path_possible(target_xy[0], target_xy[1], tz):
            return {'ok': True, 'moyen': f"porte « {d.get('name', '?')} »", 'seconds': time.perf_counter() - t0}

    # 2. balayage d invites
    for k in range(12):
        if stop is not None and stop.is_set():
            break
        motion.turn_by(30.0, timeout=1.5, stop=stop)
        time.sleep(0.15)
        if _try_prompt(log):
            time.sleep(1.0)
            if target_xy and _path_possible(target_xy[0], target_xy[1], tz):
                return {'ok': True, 'moyen': 'invite', 'seconds': time.perf_counter() - t0}

    # 3. sondes de mouvement (8 directions)
    best, best_gain = None, 0.0
    for k in range(8):
        if stop is not None and stop.is_set():
            break
        s = motion.read_state() or {}
        if not s:
            break
        heading = motion.wrap((s.get('yaw') or 0) + 45.0 * k)
        motion.turn_to(heading, timeout=1.5, stop=stop)
        sx, sy = s['x'], s['y']
        kbm.hold('W'); kbm.act_hold('sprint'); time.sleep(0.3); kbm.act('jump', 0.1); time.sleep(2.2)
        kbm.act_release('sprint'); kbm.release('W')
        s2 = motion.read_state() or s
        gain = math.hypot(s2['x'] - sx, s2['y'] - sy)
        if gain > best_gain:
            best, best_gain = heading, gain
        if target_xy and gain > 3.0 and _path_possible(target_xy[0], target_xy[1], tz):
            return {'ok': True, 'moyen': f'sonde {k} ({gain:.0f} m)', 'seconds': time.perf_counter() - t0}
    if best is not None and best_gain > 3.0:
        log(f'  [sortie] meilleure direction {best:.0f} deg ({best_gain:.0f} m) : on pousse 6 s')
        motion.turn_to(best, timeout=1.5, stop=stop)
        kbm.hold('W'); kbm.act_hold('sprint'); time.sleep(6.0); kbm.act_release('sprint'); kbm.release('W')
        if target_xy and _path_possible(target_xy[0], target_xy[1], tz):
            return {'ok': True, 'moyen': 'poussee', 'seconds': time.perf_counter() - t0}
    s3 = motion.read_state() or st0
    return {'ok': False, 'moyen': 'coince', 'deplace': math.hypot(s3['x'] - x0, s3['y'] - y0), 'seconds': time.perf_counter() - t0}
