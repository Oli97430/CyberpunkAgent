"""
motion.py -- primitives de mouvement en boucle FERMEE sur l'etat CET (state.bin).

turn_to(yaw) : asservissement de cap par souris relative.
  Calibration Phase 0 : +400 counts -> -20.0 deg  =>  K = 20 counts/deg, +dx = droite.
  Lecon du 1er test (test_turn v1) : agir a 33 Hz sur un etat a 20 Hz = deux
  corrections par mesure -> oscillation +-10 deg. Ici on n'agit QUE sur une mesure
  neuve (seq a change), avec un gain prudent : un retard de 2-3 images est inevitable
  (Lua -> fichier -> Python -> SendInput -> moteur -> Lua).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import input_kbm as kbm
from .config import CFG

STATE_FILE = CFG.state_file()          # <jeu>/bin/x64/plugins/cyber_engine_tweaks/mods/AgentProbe/state.bin

K_COUNTS_PER_DEG = 20.0
SIGN = -1.0            # +dx => yaw diminue
GAIN = 0.45            # fraction de l'erreur corrigee par mesure neuve
MAX_STEP = 400.0       # counts par pas (20 deg) : rapide sans coup de fouet
TOL_DEG = 0.5


def wrap(a: float) -> float:
    return ((a + 180.0) % 360.0) - 180.0


def read_state(last_seq: int | None = None, timeout: float = 0.25) -> dict | None:
    """Derniere lecture coherente ; si last_seq est donne, attend une mesure NEUVE."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            d = json.loads(STATE_FILE.read_bytes().decode('utf-8', 'ignore').strip())
            if d.get('seq') == d.get('seqEnd') and (last_seq is None or d['seq'] != last_seq):
                inter = d.get('interact')
                if isinstance(inter, dict) and inter.get('active') is False:
                    d['interact'] = None          # hub PERIME (V ne regarde plus le dispositif) : on l ignore partout
                return d
        except Exception:
            pass
        time.sleep(0.005)
    return None


def turn_to(target_yaw: float, timeout: float = 3.0, stop=None, tol: float = TOL_DEG) -> dict:
    """Tourne jusqu'a |erreur| < TOL_DEG. Renvoie ok, erreur finale, duree, trace."""
    t0 = time.perf_counter()
    trace: list[float] = []
    seq = None
    err = None
    retried = False
    while time.perf_counter() - t0 < timeout:
        if stop is not None and stop.is_set():
            break
        st = read_state(seq)
        if st is None:
            continue
        seq = st['seq']
        err = wrap(target_yaw - st['yaw'])
        trace.append(round(err, 2))
        if abs(err) < tol:
            return {'ok': True, 'final_err': err, 'seconds': time.perf_counter() - t0, 'trace': trace}
        dx = max(-MAX_STEP, min(MAX_STEP, SIGN * err * K_COUNTS_PER_DEG * GAIN))
        kbm.look(dx, 0)
        if not retried and time.perf_counter() - t0 > 1.0 and len(trace) > 5 and abs(trace[-1] - trace[0]) < 1.0 and abs(err) > 5:
            retried = True
            time.sleep(2.0)                       # souris sans effet : on laisse passer la scene / l appel / la main du joueur
            t0 = time.perf_counter(); trace.clear()
    return {'ok': False, 'final_err': err, 'seconds': time.perf_counter() - t0, 'trace': trace,
            'reason': 'souris sans effet' if (len(trace) > 5 and abs(trace[-1] - trace[0]) < 1.0) else 'rotation trop lente'}


def turn_by(delta_deg: float, **kw) -> dict:
    st = read_state()
    if st is None:
        return {'ok': False, 'final_err': None, 'seconds': 0.0, 'trace': []}
    return turn_to(wrap(st['yaw'] + delta_deg), **kw)


# --- marche vers un point ----------------------------------------------------------
# Convention de cap mesuree en jeu : GetWorldYaw = 0 face au +Y, 90 face au -X
# (sens trigonometrique vu de dessus, Z-up). A VERIFIER par test_walk : si V part a
# l'oppose, inverser le signe de YAW_SIGN ci-dessous.
import math

YAW_SIGN = 1.0
ARRIVE_M = 1.5
STUCK_WINDOW_S = 1.5
STUCK_MIN_M = 0.4


def bearing_to(x0, y0, x1, y1) -> float:
    """Cap (convention jeu) pour aller de (x0,y0) vers (x1,y1)."""
    return wrap(YAW_SIGN * math.degrees(math.atan2(-(x1 - x0), (y1 - y0))))


def dist2d(a, b) -> float:
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def walk_to(x: float, y: float, timeout: float = 20.0, stop=None, interrupt=None, sprint: bool = False) -> dict:
    """
    Se tourne vers (x,y), avance en W, recale le cap en continu (petites corrections
    souris), s'arrete a ARRIVE_M. Detecte un blocage : < STUCK_MIN_M parcourus en
    STUCK_WINDOW_S alors que W est tenu.
    """
    t0 = time.perf_counter()
    st = read_state()
    if st is None:
        return {'ok': False, 'reason': 'etat illisible', 'seconds': 0.0}
    target = {'x': x, 'y': y}
    r = turn_to(bearing_to(st['x'], st['y'], x, y), stop=stop, tol=2.0)   # 2 deg suffisent pour marcher
    if not r['ok'] and (r.get('final_err') is None or abs(r['final_err']) > 5.0):
        err = r.get('final_err')
        return {'ok': False, 'reason': f"rotation initiale echouee (erreur restante {err:+.0f} deg, {len(r['trace'])} mesures)"
                if err is not None else 'rotation initiale echouee (etat illisible)',
                'seconds': time.perf_counter() - t0}

    seq = None
    last_check_t, last_check_pos = time.perf_counter(), st
    path_len = 0.0
    prev = st
    kbm.hold('W')
    sprinting = False
    door_tries = 0
    if sprint:                       # follow_path : le chemin restant est long -> on court des le depart
        kbm.act_hold('sprint'); sprinting = True
    try:
        while time.perf_counter() - t0 < timeout:
            if stop is not None and stop.is_set():
                return {'ok': False, 'reason': 'arret d urgence', 'seconds': time.perf_counter() - t0}
            st = read_state(seq)
            if st is None:
                continue
            seq = st['seq']
            if interrupt is not None and interrupt(st):
                return {'ok': False, 'reason': 'interrompu', 'final_dist': dist2d(st, target), 'seconds': time.perf_counter() - t0}
            path_len += dist2d(prev, st); prev = st
            d = dist2d(st, target)
            # sprint des que la cible est a plus de 12 m ; en fin de chemin (sprint=False) on
            # marche pour finir precisement
            if d > 12.0 and not sprinting and not st.get('swim'):
                kbm.act_hold('sprint'); sprinting = True
            elif d <= 6.0 and sprinting and not sprint:
                kbm.act_release('sprint'); sprinting = False
            if d < ARRIVE_M:
                return {'ok': True, 'final_dist': d, 'path_m': path_len, 'seconds': time.perf_counter() - t0}
            # recalage de cap : correction douce, proportionnelle, sans arreter la marche
            err = wrap(bearing_to(st['x'], st['y'], x, y) - st['yaw'])
            if abs(err) > 2.0:
                kbm.look(max(-150.0, min(150.0, SIGN * err * K_COUNTS_PER_DEG * 0.35)), 0)
            # blocage
            now = time.perf_counter()
            if now - last_check_t > STUCK_WINDOW_S:
                if dist2d(st, last_check_pos) < STUCK_MIN_M:
                    # une PORTE ? (invite « Ouvrir » active, ou porte a < 3,5 m ouvrable par script) -> on l ouvre et on repart
                    if door_tries < 2 and try_door(st):
                        door_tries += 1
                        kbm.hold('W')
                        if sprinting:
                            kbm.act_hold('sprint')
                        seq = None
                        last_check_t, last_check_pos = time.perf_counter(), st
                        continue
                    return {'ok': False, 'reason': 'bloque', 'final_dist': d, 'seconds': now - t0,
                            'moved_m': dist2d(st, last_check_pos), 'pos': (round(st['x']), round(st['y']))}
                last_check_t, last_check_pos = now, st
        return {'ok': False, 'reason': 'timeout', 'final_dist': dist2d(prev, target), 'seconds': time.perf_counter() - t0}
    finally:
        kbm.release('W'); kbm.act_release('sprint')


DOOR_WORDS = ('ouvrir', 'activer', 'utiliser', 'forcer', 'open', 'use', 'appeler')
NO_DOOR = ('saisir', 'porter', 'contr', 'enfourcher', 'pirater')


def try_door(st: dict) -> bool:
    """V est bloque : s il regarde une invite « Ouvrir / Activer » (hub ACTIF), il appuie dessus ; sinon, si
    une porte est a moins de 3,5 m (mod : `doors`), il se tourne vers elle, appuie, et demande au mod
    de l ouvrir par script. Renvoie True si quelque chose a ete tente (on reprend la marche ensuite).
    Lecon du 13/09 : chez Viktor, c etait le joueur qui devait ouvrir les portes."""
    inter = st.get('interact') or {}
    ch = str((inter.get('choices') or [''])[0]).lower()
    if ch and any(w in ch for w in DOOR_WORDS) and not any(w in ch for w in NO_DOOR):
        kbm.release('W'); kbm.act_release('sprint')
        kbm.act('interact', 0.15); time.sleep(1.2)
        return True
    try:
        from . import nav
        r = nav._wait(nav._send({'cmd': 'doors'}), timeout=3.0)
        ds = [d for d in ((r or {}).get('doors') or []) if (d.get('d') or 99) < 3.5 and d.get('kind') == 'Door']
        if ds:
            kbm.release('W'); kbm.act_release('sprint')
            turn_to(bearing_to(st['x'], st['y'], ds[0]['x'], ds[0]['y']), timeout=1.5)
            time.sleep(0.3)
            kbm.act('interact', 0.15); time.sleep(0.8)
            nav._wait(nav._send({'cmd': 'door_open', 'x': ds[0]['i']}), timeout=3.0)
            time.sleep(0.8)
            return True
    except Exception:
        pass
    return False


_unstick_n = [0]


def unstick() -> None:
    """Manoeuvre de deblocage, alternee : (1) saut en avancant (franchit rebords et
    obstacles bas), (2) recul + pas de cote gauche, (3) recul + pas de cote droit."""
    k = _unstick_n[0] % 3
    _unstick_n[0] += 1
    if k == 0:
        kbm.hold('W'); time.sleep(0.15); kbm.act('jump', 0.1); time.sleep(0.7); kbm.release('W')
    else:
        kbm.hold('S'); time.sleep(0.5); kbm.release('S')
        side = 'A' if k == 1 else 'D'
        kbm.hold(side); time.sleep(0.7); kbm.release(side)
    time.sleep(0.2)


def follow_path(points, stop=None, skip_closer_than: float = 2.0, interrupt=None) -> dict:
    """
    Suit une liste de waypoints [(x,y), ...] (ex. NavigationPath.path du navmesh).
    Les waypoints deja a moins de skip_closer_than sont sautes (le navmesh en produit
    de tres rapproches). Un blocage sur un waypoint tente le suivant une fois avant
    d'echouer : c'est a la couche superieure de replanifier.
    """
    t0 = time.perf_counter()
    done, failed = 0, 0
    for i, (x, y) in enumerate(points):
        if stop is not None and stop.is_set():
            return {'ok': False, 'reason': 'arret d urgence', 'waypoints_done': done, 'seconds': time.perf_counter() - t0}
        st = read_state()
        if st is None:
            return {'ok': False, 'reason': 'etat illisible', 'waypoints_done': done, 'seconds': time.perf_counter() - t0}
        is_last = (i == len(points) - 1)
        if not is_last and dist2d(st, {'x': x, 'y': y}) < skip_closer_than:
            continue
        # les waypoints du navmesh sont a ~1 m : on court tant que la FIN du chemin est a > 15 m
        remaining = math.hypot(points[-1][0] - st['x'], points[-1][1] - st['y'])
        r = walk_to(x, y, timeout=15.0, stop=stop, interrupt=interrupt, sprint=remaining > 15.0)
        if not r['ok'] and r.get('reason') == 'interrompu':
            return {'ok': False, 'reason': 'interrompu', 'waypoints_done': done, 'seconds': time.perf_counter() - t0}
        if not r['ok'] and r.get('reason') == 'bloque':
            unstick()
            r = walk_to(x, y, timeout=10.0, stop=stop, interrupt=interrupt)
        if r['ok']:
            done += 1; failed = 0
        else:
            failed += 1
            if failed >= 2 or is_last:
                return {'ok': False, 'reason': r.get('reason'), 'waypoint': i, 'waypoints_done': done,
                        'seconds': time.perf_counter() - t0}
    return {'ok': True, 'waypoints_done': done, 'seconds': time.perf_counter() - t0}
