"""
terminals.py -- points d acces (terminaux) : V s y connecte et joue le Breach Protocol pour en tirer profit
(eddies, composants, daemons). Demande d Olivier du 17/09 : « V peut maintenant se connecter a n importe quel
terminal et en tirer profit ».

  scan()      -> points d acces non pirates a < 40 m (commande Lua access_points)
  pick()      -> le plus proche qui n a pas deja ete traite (memoire persistante terminals_skip.json)
  jack_in(ap) -> V y va, se met face au terminal, appuie sur l invite (« Se connecter ») ; 'minigame' si le
                 Breach Protocol s est ouvert (le cerveau le resout ensuite via breach.run), 'failed' sinon.
"""
from __future__ import annotations

import json
import math
import time

from . import motion, nav
from .config import CFG

SCAN_M = 40.0
SKIP_FILE = CFG.log_file.parent / 'terminals_skip.json'
_skip: dict[str, dict] = {}
try:
    _skip = json.loads(SKIP_FILE.read_text(encoding='utf-8')) if SKIP_FILE.exists() else {}
    if not isinstance(_skip, dict):
        _skip = {}
except Exception:
    _skip = {}


def _key(ap: dict) -> str:
    return f"{round(ap.get('x', 0))},{round(ap.get('y', 0))}"


def is_skipped(ap: dict) -> bool:
    e = _skip.get(_key(ap))
    return bool(e) and time.time() - float(e.get('t', 0)) < float(e.get('days', 7)) * 86400.0


def mark(ap: dict, reason: str, days: float, log=print) -> None:
    _skip[_key(ap)] = {'t': time.time(), 'days': days, 'name': ap.get('name'), 'reason': reason}
    try:
        SKIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIP_FILE.write_text(json.dumps(_skip, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception as e:
        log(f'  [terminal] memoire non sauvee : {e}')


def scan() -> list[dict]:
    r = nav._wait(nav._send({'cmd': 'access_points'}), timeout=4.0)
    return [a for a in ((r or {}).get('points') or []) if not a.get('breached')]


def pick(log=print) -> dict | None:
    cands = [a for a in scan() if not is_skipped(a) and (a.get('d') or 99) <= SCAN_M]
    return min(cands, key=lambda a: a['d']) if cands else None


def _script_dive(ap: dict, log=print) -> bool:
    """Connexion par script (action ToggleNetrunnerDive du jeu, comme le Breach a distance) : vrai si le mini-jeu s ouvre."""
    if ap.get('i') is None:
        return False
    rj = nav._wait(nav._send({'cmd': 'jack_in', 'x': int(ap['i'])}), timeout=4.0)
    log(f"  [terminal] connexion par script : {(rj or {}).get('methodes') or (rj or {}).get('reason') or 'mod muet'}")
    t_s = time.perf_counter()
    while time.perf_counter() - t_s < 4.0:
        b = (motion.read_state() or {}).get('breach') or {}
        if int(b.get('state') or 0) == 1:
            return True
        time.sleep(0.25)
    return False


def jack_in(ap: dict, stop=None, log=print) -> str:
    """V se connecte au point d acces. 'minigame' si le Breach Protocol s ouvre, sinon 'failed'.
    1. par script, sans bouger (le Breach a distance n exige pas d etre colle au terminal) ;
    2. sinon a pied : maillage, ou tout droit s il n y a pas de chemin et qu il est a moins de 15 m, puis l invite."""
    t0 = time.perf_counter()
    if _script_dive(ap, log=log):
        log(f"  [terminal] connecte a « {ap.get('name')} » par script : Breach Protocol ouvert")
        mark(ap, 'pirate', 7.0, log=log)
        return 'minigame'
    if (ap.get('d') or 0) > 2.5:
        r = nav.goto(lambda: nav.request_path_to(ap['x'], ap['y'], ap.get('z')), arrive_m=2.0, max_legs=3, timeout=45.0, stop=stop, log=log)
        if not r.get('ok') and (ap.get('d') or 99) <= 15.0:
            r = motion.walk_to(ap['x'], ap['y'], timeout=15.0, stop=stop)        # pas de chemin (mur, estrade) : tout droit
        if not r.get('ok'):
            st = motion.read_state() or {}
            if st.get('x') is None or math.hypot(st['x'] - ap['x'], st['y'] - ap['y']) > 4.0:
                log(f"  [terminal] injoignable ({r.get('reason')}) : ecarte 1 jour")
                mark(ap, f"injoignable : {r.get('reason')}", 1.0, log=log)
                return 'failed'
    # a pied : face au terminal, invite « Se connecter » (balayage haut / bas : panneaux muraux)
    st = motion.read_state() or {}
    dist = math.hypot(st['x'] - ap['x'], st['y'] - ap['y']) if st.get('x') is not None else 3.0
    motion.approach_machine(ap['x'], ap['y'], dist, log, stop)
    # le mini-jeu s ouvre ? (etat 1 = en cours) ; le cerveau le resout des sa prochaine iteration (breach.run)
    t1 = time.perf_counter()
    while time.perf_counter() - t1 < 5.0:
        if stop is not None and stop.is_set():
            return 'failed'
        b = (motion.read_state() or {}).get('breach') or {}
        if int(b.get('state') or 0) == 1:
            log(f"  [terminal] connecte a « {ap.get('name')} » : Breach Protocol ouvert ({time.perf_counter() - t0:.0f} s)")
            mark(ap, 'pirate', 7.0, log=log)
            return 'minigame'
        time.sleep(0.25)
    # un dernier essai par script une fois a cote (la portee du Breach a distance peut jouer)
    if _script_dive(ap, log=log):
        mark(ap, 'pirate', 7.0, log=log)
        return 'minigame'
    log("  [terminal] pas de mini-jeu apres l invite : ecarte 1 jour")
    mark(ap, 'pas de mini-jeu', 1.0, log=log)
    return 'failed'
