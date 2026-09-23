"""
braindance.py -- competence "danse sensorielle" (braindance) : V prend l editeur en main tout seul.

Etat (mod, state.bd) : active/rew (section rembobinable), fpp (mode lecture : true ; editeur : false), layer
(0 visuel, 1 audio, 2 thermique), t/dur (secondes), paused, exit (sortie autorisee), masks (commandes permises par la
scene), clues (indices de la timeline vus : nom, t0, t1, couche, done), focus (entites-indices a < 40 m : position,
distance, couche, scanne, bloque, enabled, progression, projection ecran sx/sy), look (objet sous le reticule).

Regles du jeu (sources : ScanningComponent, BraindanceControlsTransition) :
  - un indice n est scannable que si la timeline est dans sa fenetre ET que la couche active est la sienne, en editeur ;
  - il se scanne tout seul quand on le VISE assez longtemps (GetTimeNeeded), pas de touche ;
  - Tab = editeur / lecture, Maj = couche suivante (cycle visuel -> audio -> thermique si debloquees), Espace = pause,
    E / Q = avance / recul, R = debut, X = sortie (quand le jeu l autorise) ; la timeline se pilote aussi par script
    (commande bd_jump = SceneSystem.JumpRewindableSection).

Strategie : entrer dans l editeur -> pour chaque indice de la timeline non termine (ou, s il n y en a pas encore,
balayage du temps par couche) : sauter au milieu de sa fenetre, passer sur sa couche, viser l entite-indice devenue
active jusqu a ce qu elle soit scannee -> quand plus rien n est scannable et que la sortie est permise : X.
Les dialogues qui surviennent pendant la danse sont geres par la competence dialogue.
"""
from __future__ import annotations

import math
import time

from . import combat, dialog, input_kbm as kbm, motion, nav

LAYER_NAMES = {0: 'visuel', 1: 'audio', 2: 'thermique'}
MAX_S = 20 * 60.0            # une danse sensorielle de quete peut durer longtemps (dialogues compris)
SCAN_AIM_S = 9.0             # temps max a viser un indice actif
STALL_S = 150.0              # sans progres pendant ce temps : on tente la sortie


def _state() -> tuple[dict, dict]:
    st = motion.read_state() or {}
    return st, (st.get('bd') or {})


def jump(t: float, log=print) -> bool:
    r = nav._wait(nav._send({'cmd': 'bd_jump', 'x': float(t)}), timeout=4.0)
    ok = bool(r and r.get('ok'))
    if not ok:
        log(f"  [bd] saut a {t:.0f} s refuse : {(r or {}).get('reason')}")
    return ok


def speed(sp: int, backward: bool = False) -> bool:
    r = nav._wait(nav._send({'cmd': 'bd_speed', 'x': int(sp), 'y': 1 if backward else 0}), timeout=4.0)
    return bool(r and r.get('ok'))


def dump(log=print) -> dict | None:
    return nav._wait(nav._send({'cmd': 'bd_clues'}), timeout=5.0)


def ensure_editor(log=print, stop=None) -> bool:
    """Passe en mode editeur (camera libre) si le jeu le permet."""
    st, bd = _state()
    if not bd.get('fpp'):
        return True
    if bd.get('masks') and bd['masks'].get('cam') is False:
        return False
    kbm.act('bd_mode', 0.1)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3.0:
        if stop is not None and stop.is_set():
            return False
        st, bd = _state()
        if bd and not bd.get('fpp'):
            log('  [bd] editeur actif')
            time.sleep(0.5)
            return True
        time.sleep(0.2)
    return False


def set_layer(target: int, log=print, stop=None) -> bool:
    for _ in range(4):
        st, bd = _state()
        if not bd:
            return False
        if int(bd.get('layer') or 0) == int(target):
            return True
        if bd.get('masks') and bd['masks'].get('layer') is False:
            log(f"  [bd] changement de couche interdit ici (voulu : {LAYER_NAMES.get(target, target)})")
            return False
        if stop is not None and stop.is_set():
            return False
        kbm.act('bd_layer', 0.1)
        time.sleep(0.7)
    st, bd = _state()
    return int(bd.get('layer') or 0) == int(target)


def _find(bd: dict, fid: str) -> dict | None:
    for f in bd.get('focus') or []:
        if f.get('id') == fid:
            return f
    return None


def scan_focus(f: dict, log=print, stop=None) -> bool:
    """Vise l entite-indice (approche si loin) jusqu a ce qu elle soit scannee."""
    fid = f.get('id')
    name = f.get('name') or f.get('cls') or fid
    st, bd = _state()
    cur = _find(bd, fid) or f
    if (cur.get('d') or 0) > 7.0:
        log(f"  [bd] approche de « {name} » ({cur['d']:.1f} m)")
        old = motion.ARRIVE_M; motion.ARRIVE_M = 4.0
        try:
            motion.walk_to(cur['x'], cur['y'], timeout=15.0, stop=stop)
        finally:
            motion.ARRIVE_M = old
    t0 = time.perf_counter()
    last_prog, last_log = -1.0, 0.0
    while time.perf_counter() - t0 < SCAN_AIM_S:
        if stop is not None and stop.is_set():
            return False
        st, bd = _state()
        if not bd:
            return False
        cur = _find(bd, fid)
        if cur is None:
            time.sleep(0.2); continue
        if cur.get('scanned') or cur.get('insp'):
            log(f"  [bd] indice scanne : « {name} »")
            return True
        if cur.get('blocked'):
            # plus dans la fenetre (la lecture a avance ?) : on met en pause et on laisse l appelant re-sauter
            speed(0)
            time.sleep(0.3)
            st, bd = _state(); cur = _find(bd, fid) or cur
            if cur.get('blocked'):
                log(f"  [bd] « {name} » n est plus actif (couche {cur.get('layer')}, t={bd.get('t')})")
                return False
        combat.aim_at({'x': cur['x'], 'y': cur['y'], 'sx': cur.get('sx'), 'sy': cur.get('sy')}, st)
        prog = float(cur.get('prog') or 0.0)
        if prog > last_prog + 0.15 and time.perf_counter() - last_log > 0.8:
            log(f"  [bd] analyse de « {name} » : {prog * 100:.0f} %")
            last_log = time.perf_counter()
        last_prog = max(last_prog, prog)
        time.sleep(0.08)
    st, bd = _state()
    cur = _find(bd, fid) or {}
    return bool(cur.get('scanned'))


def _pending_focus(bd: dict) -> list[dict]:
    return [f for f in (bd.get('focus') or []) if not f.get('scanned') and not f.get('insp')]


def run(stop=None, log=print) -> dict:
    t0 = time.perf_counter()
    scans, last_progress = 0, time.perf_counter()
    tried_windows: set[tuple] = set()
    sweep_done: set[int] = set()
    exit_tries = 0
    fail_counts: dict[str, int] = {}   # id d indice -> echecs de scan consecutifs ; 3 -> abandonne (23/09 : un
                                        # gameAudioClueObject reste bloque a 0 % pour toujours -- rien ne l ecartait,
                                        # V a passe 2x 10-12 min a le reviser sans jamais avancer)
    given_up: set[str] = set()
    while time.perf_counter() - t0 < MAX_S:
        if stop is not None and stop.is_set():
            return {'ok': False, 'reason': 'arret', 'scans': scans, 'seconds': time.perf_counter() - t0}
        st, bd = _state()
        if not bd or not (bd.get('active') or bd.get('rew')):
            return {'ok': True, 'reason': 'terminee', 'scans': scans, 'seconds': time.perf_counter() - t0}
        # dialogues (Judy commente, choix de reponse) : prioritaires
        if (st.get('dialog') or {}).get('choices'):
            dialog.answer_once(stop=stop, log=log)
            time.sleep(0.5); continue
        # mode lecture impose par la scene (cinematique) : on attend
        if bd.get('fpp'):
            if not ensure_editor(log, stop):
                time.sleep(1.0)
                if time.perf_counter() - last_progress > STALL_S and bd.get('exit'):
                    kbm.act('bd_exit', 0.1); time.sleep(2.0)
                continue
            last_progress = time.perf_counter()
        pend = [f for f in _pending_focus(bd) if f.get('id') not in given_up]
        active = [f for f in pend if not f.get('blocked')]
        if active:
            f = active[0]
            if scan_focus(f, log, stop):
                scans += 1; last_progress = time.perf_counter()
                fail_counts.pop(f.get('id'), None)
            else:
                fid = f.get('id')
                fail_counts[fid] = fail_counts.get(fid, 0) + 1
                if fail_counts[fid] >= 3:
                    given_up.add(fid)
                    log(f"  [bd] « {f.get('name') or f.get('cls') or fid} » impossible a scanner apres 3 essais : abandonne")
            continue
        # indices de la timeline non termines : on saute dans leur fenetre, sur leur couche
        todo = [c for c in (bd.get('clues') or []) if not c.get('done') and c.get('t0') is not None
                and (c['name'], round(c['t0'])) not in tried_windows]
        if todo:
            c = todo[0]
            tried_windows.add((c['name'], round(c['t0'])))
            tm = (float(c['t0']) + float(c.get('t1') or c['t0'])) / 2.0
            log(f"  [bd] indice « {c['name']} » : fenetre {c['t0']:.0f}-{(c.get('t1') or c['t0']):.0f} s, couche {LAYER_NAMES.get(c.get('layer'), c.get('layer'))} -> saut a {tm:.0f} s")
            jump(tm, log)
            time.sleep(0.8)
            if c.get('layer') is not None:
                set_layer(int(c['layer']), log, stop)
            time.sleep(0.6)
            continue
        # entites-indices connues mais bloquees (hors fenetre) : balayage du temps sur leur couche
        blocked = [f for f in pend if f.get('layer') is not None]
        layers = sorted({int(f['layer']) for f in blocked}) or [int(bd.get('layer') or 0)]
        dur = float(bd.get('dur') or 0.0)
        swept = False
        for layer in layers:
            if layer in sweep_done:
                continue
            sweep_done.add(layer)
            if not set_layer(layer, log, stop):
                continue
            log(f"  [bd] balayage de la timeline sur la couche {LAYER_NAMES.get(layer, layer)} ({dur:.0f} s)")
            steps = max(6, min(16, int(dur // 8))) if dur > 0 else 8
            for k in range(steps):
                if stop is not None and stop.is_set():
                    break
                tt = (dur * (k + 0.5) / steps) if dur > 0 else k * 8.0
                if not jump(tt, log):
                    break
                time.sleep(0.6)
                st, bd = _state()
                act = [f for f in _pending_focus(bd) if not f.get('blocked') and f.get('id') not in given_up]
                if act:
                    log(f"  [bd] indice actif a {tt:.0f} s : « {act[0].get('name') or act[0].get('cls')} »")
                    if scan_focus(act[0], log, stop):
                        scans += 1; last_progress = time.perf_counter()
                        fail_counts.pop(act[0].get('id'), None)
                    else:
                        fid = act[0].get('id')
                        fail_counts[fid] = fail_counts.get(fid, 0) + 1
                        if fail_counts[fid] >= 3:
                            given_up.add(fid)
                            log(f"  [bd] « {act[0].get('name') or act[0].get('cls') or fid} » impossible a scanner apres 3 essais : abandonne")
                    swept = True
                    break
            if swept:
                break
        if swept:
            sweep_done.clear()           # un scan peut debloquer d autres indices : on pourra rebalayer
            continue
        # plus rien a faire : sortie si permise, sinon lecture normale pour laisser la scene avancer
        if bd.get('exit') and not pend:
            exit_tries += 1
            log(f"  [bd] plus d indice a scanner : sortie ({exit_tries})")
            kbm.act('bd_exit', 0.12); time.sleep(2.0)
            if exit_tries >= 2:
                kbm.act_hold('bd_exit'); time.sleep(1.5); kbm.act_release('bd_exit'); time.sleep(2.0)
            if exit_tries >= 4:
                return {'ok': False, 'reason': 'sortie refusee', 'scans': scans, 'seconds': time.perf_counter() - t0}
            continue
        if time.perf_counter() - last_progress > STALL_S:
            log('  [bd] aucun progres : lecture normale puis nouvelle tentative')
            sweep_done.clear(); tried_windows.clear()
            speed(2)
            time.sleep(8.0)
            speed(0)
            last_progress = time.perf_counter()
            continue
        # la scene doit peut-etre avancer (indices reveles par le dialogue) : lecture normale quelques secondes
        speed(2); time.sleep(4.0); speed(0)
        sweep_done.clear()
    return {'ok': False, 'reason': 'temps ecoule', 'scans': scans, 'seconds': time.perf_counter() - t0}
