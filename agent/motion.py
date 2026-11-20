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
LOCO_WORKSPOT = 9   # 12/10 : valeur NATIVE de gamePSMLocomotionStates.Workspot (metadata.json), pas 8


def game_lock(st: dict) -> str | None:
    """12/10 : V fige PAR LE JEU (workspot de scene, tier de scene 4-5, effet NoMovement) -> ni touche ni voyage n y
    peuvent rien. Jamais en vehicule (les sieges sont des workspots)."""
    if not st or st.get('vehicle'):
        return None
    f = st.get('frz')
    if isinstance(f, dict):
        if f.get('ws'):
            return 'workspot' + (f" {f['wst']}" if f.get('wst') else '')
        hl = f.get('hl')
        if isinstance(hl, (int, float)) and 4 <= hl <= 5:
            return f'scene tier {int(hl)}'
        return 'NoMovement' if 'NoMovement' in (f.get('tags') or []) else None
    return 'workspot' if st.get('locomotion') == LOCO_WORKSPOT else None
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


LAST_DOOR = {'open': None}       # 21/10 : resultat du dernier door_open (None = inconnu / pas de script)
CHARGE_JUMP_S = 0.9              # 22/10 (Olivier : « maintenir et relacher espace lui permet de sauter plus haut ») : saut charge


def big_jump(forward: bool = True, charge: float | None = None) -> None:
    """22/10 : saut CHARGE pour franchir un obstacle -- espace MAINTENU puis relache (plus haut qu un appui bref), en
    avancant (W) avant, pendant et apres le saut ; le 2e appui en l air (double saut) est tente aussi."""
    if forward:
        kbm.hold('W'); time.sleep(0.12)
    kbm.act('jump', CHARGE_JUMP_S if charge is None else charge)    # maintenu : la charge ; relache : le saut
    time.sleep(0.25)
    kbm.act('jump', 0.08)                                           # double saut si V l a (sans effet sinon)
    time.sleep(0.6)
    if forward:
        kbm.release('W')


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
            rd = nav._wait(nav._send({'cmd': 'door_open', 'x': ds[0]['i']}), timeout=3.0) or {}
            LAST_DOOR['open'] = rd.get('open')               # 21/10 : False = porte restee fermee (verrouillee)
            time.sleep(0.8)
            return True
    except Exception:
        pass
    return False


def approach_machine(tx: float, ty: float, dist: float, log, stop) -> bool:
    global ARRIVE_M
    """Au marqueur sans invite active : V avance jusqu a ~1 m du point exact, le regarde (regard un peu baisse),
    balaie a gauche/droite et appuie des que l invite de la machine devient active (E court puis E long).
    Lecon du 13/09 (Olivier) : « il s avance encore d un metre et c est bon »."""
    log(f'au marqueur ({dist:.1f} m) sans invite : V s approche a 1 m et cherche la machine')
    old_a = ARRIVE_M; ARRIVE_M = 0.9
    try:
        r = walk_to(tx, ty, timeout=8.0, stop=stop)
    finally:
        ARRIVE_M = old_a
    s_c = read_state() or {}
    if not s_c:
        return False
    if not r.get('ok') and math.hypot(s_c['x'] - tx, s_c['y'] - ty) > 2.5:
        # bloque a plus de 2,5 m : contourner (pas de cote) puis re-essayer une fois
        for side in ('A', 'D'):
            if stop is not None and stop.is_set():
                return False
            kbm.hold(side); time.sleep(0.6); kbm.release(side)
            walk_to(tx, ty, timeout=5.0, stop=stop)
            s_c = read_state() or s_c
            if math.hypot(s_c['x'] - tx, s_c['y'] - ty) <= 2.5:
                break
    turn_to(bearing_to(s_c['x'], s_c['y'], tx, ty), timeout=2.0, stop=stop)
    for dyaw, dpitch in ((0, 150), (0, -300), (0, 100), (-25, 0), (50, 0), (-25, -250), (0, 100), (0, 150)):   # bas, HAUT (panneaux muraux), puis balayage
        if stop is not None and stop.is_set():          # 12/10 : F11/F12 -> plus aucune touche ni mouvement de souris
            return False
        if dyaw:
            turn_by(dyaw, timeout=1.2, stop=stop)
        if dpitch:
            for _ in range(abs(dpitch) // 50):
                kbm.look(0, 50 if dpitch > 0 else -50); time.sleep(0.02)
        time.sleep(0.45)
        s_p = read_state() or {}
        ip = s_p.get('interact') or {}
        chp = str((ip.get('choices') or [''])[0]).lower()
        if chp and not any(w in chp for w in ('saisir', 'porter', 'prendre le contr', 'enfourcher')):
            if stop is not None and stop.is_set():
                return False
            log(f"  machine trouvee : « {ip['choices'][0]} » -> E")
            kbm.act('interact', 0.15); time.sleep(1.0)
            s_q = read_state() or {}
            if (s_q.get('interact') or {}).get('choices'):
                kbm.act('interact', 1.0)
            return True
    log('  rien a activer ici : on continue')
    return False


_unstick_n = [0]


def unstick() -> None:
    """Manoeuvre de deblocage, alternee : (1) saut en avancant (franchit rebords et
    obstacles bas), (2) recul + pas de cote gauche, (3) recul + pas de cote droit."""
    k = _unstick_n[0] % 3
    _unstick_n[0] += 1
    if k == 0:
        big_jump()                                                   # 22/10 : saut charge
    else:
        kbm.hold('S'); time.sleep(0.5); kbm.release('S')
        side = 'A' if k == 1 else 'D'
        kbm.hold(side); time.sleep(0.7); kbm.release(side)
    time.sleep(0.2)


def diagnose_block(x: float, y: float) -> dict | None:
    """19/10 : demande au mod CE QUI bloque V en marchant vers (x, y) (commande probe_block : rayons physiques,
    mur du navmesh, cote libre, point de recul). None si le mod ne repond pas."""
    try:
        from . import nav
        return nav._wait(nav._send({'cmd': 'probe_block', 'x': x, 'y': y}), timeout=2.0)
    except Exception:
        return None


def unstick_smart(x: float, y: float) -> str:
    """19/10 (reference PATHFINDING_CP2077 §4.3) : manoeuvre ADAPTEE a l obstacle au lieu de l alternance a
    l aveugle. Renvoie la manoeuvre faite (pour le journal). Repli sur unstick() si pas de diagnostic."""
    r = diagnose_block(x, y)
    if not r or not r.get('ok'):
        unstick()
        return 'aveugle (pas de diagnostic)'
    kind, side, cls = r.get('kind'), r.get('side'), r.get('cls')
    st = read_state() or {}
    if kind == 'porte' and st:
        LAST_DOOR['open'] = None
        if try_door(st):
            # 21/10 (session 19:38 : ~40 s perdues par objet derriere une porte verrouillee) : porte restee fermee
            if LAST_DOOR['open'] is False or str(LAST_DOOR['open']).lower() == 'no':
                return 'porte verrouillee'
            return f'porte ({cls})'
    if kind == 'bas':
        big_jump()                                                   # 22/10 : saut charge
        return 'saut (obstacle bas)'
    if kind == 'pnj':
        time.sleep(1.0)                    # un passant s ecarte souvent de lui-meme ; sinon on contourne ci-dessous
    if kind in ('rien', 'mur_navmesh') and r.get('back'):
        b = r['back']
        walk_to(b['x'], b['y'], timeout=3.0)
        return f'recul vers le navmesh ({kind})'
    if side in ('A', 'D'):
        kbm.hold('S'); time.sleep(0.35); kbm.release('S')
        kbm.hold(side); time.sleep(0.8); kbm.release(side)
        return f"contournement {'gauche' if side == 'A' else 'droite'} ({kind}{', ' + cls if cls else ''})"
    unstick()
    return f'aveugle ({kind}, aucun cote libre)'


def follow_path(points, stop=None, skip_closer_than: float = 2.0, interrupt=None) -> dict:
    """
    Suit une liste de waypoints [(x,y), ...] (ex. NavigationPath.path du navmesh).
    Les waypoints deja a moins de skip_closer_than sont sautes (le navmesh en produit
    de tres rapproches). Un blocage sur un waypoint tente le suivant une fois avant
    d'echouer : c'est a la couche superieure de replanifier.
    """
    t0 = time.perf_counter()
    done, failed = 0, 0
    unsticks = []                          # 19/10 : manoeuvres de deblocage faites (remontees au journal par nav.goto)
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
            unsticks.append(unstick_smart(x, y))
            if unsticks[-1] == 'porte verrouillee':
                # 21/10 : inutile d insister (nav.goto ne redemande pas de troncon sur cette raison)
                return {'ok': False, 'reason': 'porte verrouillee', 'waypoint': i, 'waypoints_done': done,
                        'seconds': time.perf_counter() - t0, 'unsticks': unsticks}
            r = walk_to(x, y, timeout=10.0, stop=stop, interrupt=interrupt)
        if r['ok']:
            done += 1; failed = 0
        else:
            failed += 1
            if failed >= 2 or is_last:
                return {'ok': False, 'reason': r.get('reason'), 'waypoint': i, 'waypoints_done': done,
                        'seconds': time.perf_counter() - t0, 'unsticks': unsticks}
    return {'ok': True, 'waypoints_done': done, 'seconds': time.perf_counter() - t0, 'unsticks': unsticks}
