"""
scanning.py -- V SCANNE son environnement hors combat pour progresser (23/10, Olivier : « meme hors combat V doit pouvoir
scanner son environnement pour progresser »).

Le mod liste les indices (clue) et objets de quete pas encore scannes a moins de 30 m (commande `scan_targets`) ; V se
tourne vers chacun, MAINTIENT le scanner (Tab) en gardant le reticule dessus jusqu a ce que le jeu le marque scanne
(`scan_check`, 4,5 s au plus), puis relache. Declenchement : objectif qui parle de scanner / d indices / d examiner,
arrivee au marqueur sans invite (avant de chercher une machine), sinon un tour toutes les 45 s hors combat.
Sans cible connue mais avec un objectif « scanner » : balayage a 360 deg scanner maintenu (le jeu surligne ce qui compte).
"""
from __future__ import annotations

import time

from . import input_kbm as kbm, motion, nav

SCAN_WORDS = ('scann', 'scan ', 'indice', 'examin', 'inspect', 'analys', 'enquet', 'trace', 'clue')
PERIOD_S = 45.0
RANGE_M = 25.0                      # au-dela, le scanner n accroche pas toujours : on laisse
RETRY_S = 600.0                     # une cible tentee sans succes n est pas reessayee avant 10 min
_last = {'t': -1e9, 'sweep_obj': None}
_tried: dict = {}                   # (x, y) arrondis -> heure du dernier essai
STATS = {'scans': 0, 'tentes': 0, 'balayages': 0, 'caches': 0}
_diag: dict = {}                    # 01/11 : ce que le jeu a vu pendant le dernier scan (journal)


def objective_wants_scan(st: dict) -> bool:
    txt = str(((st.get('quest') or {}).get('text')) or '').lower()
    return any(w in txt for w in SCAN_WORDS)


def _busy(st: dict) -> bool:
    return bool(st.get('combat') or st.get('vehicle') or st.get('menu') or st.get('carrying') or st.get('swim')
                or (st.get('dialog') or {}).get('choices'))


def targets(radius: float = 30.0) -> list:
    r = nav._wait(nav._send({'cmd': 'scan_targets', 'x': radius}), timeout=3.0) or {}
    return list(r.get('targets') or []) if r.get('ok') else []


def _key(t: dict) -> tuple:
    return (round(t['x']), round(t['y']))


def scan_one(t: dict, stop=None, log=print) -> bool:
    """Reticule sur la cible, scanner maintenu jusqu au scan complet (4,5 s au plus). Vrai si le jeu la dit scannee."""
    from . import combat
    st = motion.read_state() or {}
    if st.get('x') is None:
        return False
    motion.turn_to(motion.bearing_to(st['x'], st['y'], t['x'], t['y']), timeout=1.5, stop=stop)
    # 20/11 (journal : indice a 1 m, reticule dessus 0 fois sur 17) : colle a la cible, la visee ne se pose jamais -> V recule
    # d abord de quelques pas, puis se retourne vers elle
    if (t.get('d') or 99) < 2.5 and not (stop is not None and stop.is_set()):
        kbm.hold('S'); time.sleep(1.1); kbm.release('S')
        st = motion.read_state() or st
        if st.get('x') is not None:
            motion.turn_to(motion.bearing_to(st['x'], st['y'], t['x'], t['y']), timeout=1.5, stop=stop)
    kbm.act_hold('scanner')
    ok = False
    _diag.clear(); _diag.update(n=0, vise=0, prog=0.0, etat=None)
    try:
        time.sleep(0.35)
        t0 = time.perf_counter()
        # 20/11 (journal : « Tequila » a 20 m, reticule dessus 10/16, progression 0.52 au bout de 4,5 s) : le scan avancait, il
        # manquait du temps -> tant que la progression a bouge, on tient jusqu a 12 s ; sans progression, 4,5 s comme avant
        while time.perf_counter() - t0 < (12.0 if _diag['prog'] > 0.05 else 4.5):
            _diag['dur'] = time.perf_counter() - t0
            if stop is not None and stop.is_set():
                break
            r = nav._wait(nav._send({'cmd': 'scan_check', 'x': t['i']}), timeout=1.0) or {}
            if r.get('scanned') is True:
                ok = True
                break
            _diag['n'] += 1
            _diag['vise'] += 1 if r.get('vise') else 0
            _diag['prog'] = max(_diag['prog'], r.get('prog') or 0.0)
            _diag['etat'] = r.get('etat') or _diag['etat']
            s2 = motion.read_state() or st
            if s2.get('x') is not None:
                combat.aim_at({'x': r.get('x', t['x']), 'y': r.get('y', t['y']), 'sy': r.get('sy')}, s2)
            time.sleep(0.15)
    finally:
        kbm.act_release('scanner')
    time.sleep(0.2)
    return ok


def sweep(stop=None, log=print) -> None:
    """Objectif « scanner » sans cible connue : tour complet scanner maintenu (6 pas de 60 deg)."""
    st = motion.read_state() or {}
    if st.get('yaw') is None:
        return
    STATS['balayages'] += 1
    log('  [scan] objectif a scanner sans cible connue : balayage a 360 deg, scanner maintenu')
    kbm.act_hold('scanner')
    try:
        for k in range(1, 7):
            if stop is not None and stop.is_set():
                break
            motion.turn_to(motion.wrap(st['yaw'] + 60.0 * k), timeout=1.2, stop=stop)
            time.sleep(0.5)
    finally:
        kbm.act_release('scanner')


def run(st: dict, stop=None, log=print, force: bool = False) -> int:
    """Un tour de scan si le moment s y prete. Renvoie le nombre de cibles scannees."""
    if not st or _busy(st) or not kbm.ACTIONS.get('scanner'):
        return 0
    want = objective_wants_scan(st)
    now = time.perf_counter()
    if not (force or want or now - _last['t'] > PERIOD_S):
        return 0
    if want and not force and now - _last['t'] < 8.0:
        return 0                                    # objectif « scanner » : un tour toutes les 8 s au plus
    _last['t'] = now
    ts = [t for t in targets() if t.get('d', 99) <= RANGE_M and now - _tried.get(_key(t), -1e9) > RETRY_S]
    # 01/11 (journal : 0 scan reussi sur 28, cibles listees a travers les murs) : sans ligne de vue, on ne perd pas 4,5 s
    for t in [t for t in ts if t.get('los') is False]:
        _tried[_key(t)] = now
        STATS['caches'] += 1
        log(f"  [scan] « {t.get('name') or t.get('cls') or '?'} » a {t.get('d', 0):.0f} m : derriere un obstacle, ignore")
    ts = [t for t in ts if t.get('los') is not False]
    if not ts:
        qtxt = (st.get('quest') or {}).get('text')
        if want and _last['sweep_obj'] != qtxt:     # un balayage par objectif
            _last['sweep_obj'] = qtxt
            sweep(stop=stop, log=log)
        return 0
    done = 0
    for t in ts[:3]:
        if stop is not None and stop.is_set():
            break
        _tried[_key(t)] = time.perf_counter()
        kind = 'indice' if t.get('clue') else 'objet de quete'
        name = t.get('name') or t.get('cls') or '?'
        STATS['tentes'] += 1
        ok = scan_one(t, stop=stop, log=log)
        if ok:
            done += 1; STATS['scans'] += 1
            try:
                from . import hud
                hud.event(f'scan : {kind} {name}')          # fil d evenements du HUD
            except Exception:
                pass
        why = '' if ok else f" (reticule dessus {_diag.get('vise')}/{_diag.get('n')}, progression {_diag.get('prog', 0.0):.2f}, etat {_diag.get('etat')})"
        log(f"  [scan] {kind} « {name} » a {t.get('d', 0):.0f} m : {'scanne' if ok else f'pas de scan complet en {_diag.get("dur", 4.5):.1f} s'}{why}")
    return done
