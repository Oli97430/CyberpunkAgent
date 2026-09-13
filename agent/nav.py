"""
nav.py -- competence aller_a : navigation sur le navmesh du jeu, par troncons.

Python ne calcule aucun chemin : il le DEMANDE au mod CET (cmd.json), qui interroge
le pathfinding du moteur (AINavigationSystem:CalculatePathForCharacter) et repond
dans path.json. Le navmesh etant streame par secteurs, un chemin peut etre
partiel : on le suit, puis on redemande depuis la nouvelle position, jusqu'a
l'arrivee.

Verifie en jeu (2026-09-10) : chemin de 12 waypoints sur 12 m, marqueur de quete
resolu par GetMappinFromObjective.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from . import motion
from .config import CFG

MOD_DIR = CFG.mod_dir or Path('.')      # <jeu>/bin/x64/plugins/cyber_engine_tweaks/mods/AgentProbe
CMD_FILE = MOD_DIR / 'cmd.json'
PATH_FILE = MOD_DIR / 'path.json'

_seq = int(time.time()) % 100000


DB_FILE = MOD_DIR / 'db.sqlite3'


def _send(cmd: dict) -> int:
    """
    Canal principal : SQLite (CET pre-ouvre `db` sur db.sqlite3 et sait le lire).
    Le fichier cmd.json est ecrit aussi, en repli : dans le sandbox CET, f:read est nil,
    donc ce repli ne sert que si une version future de CET le permet.
    """
    global _seq
    _seq += 1
    cmd['seq'] = _seq
    try:
        import sqlite3
        con = sqlite3.connect(str(DB_FILE), timeout=1.0)
        con.execute('CREATE TABLE IF NOT EXISTS cmd (seq INTEGER PRIMARY KEY, cmd TEXT, x REAL, y REAL, z REAL, hash INTEGER)')
        try:
            con.execute('ALTER TABLE cmd ADD COLUMN hash INTEGER')
        except Exception:
            pass
        con.execute('DELETE FROM cmd')
        con.execute('INSERT INTO cmd (seq, cmd, x, y, z, hash) VALUES (?,?,?,?,?,?)',
                    (_seq, cmd['cmd'], cmd.get('x'), cmd.get('y'), cmd.get('z'), cmd.get('hash')))
        con.commit(); con.close()
    except Exception as e:
        print(f'  [nav] sqlite indisponible ({e}), repli fichier')
    tmp = CMD_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(cmd), encoding='utf-8')
    os.replace(tmp, CMD_FILE)
    return _seq


def _wait(seq: int, timeout: float = 3.0) -> dict | None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            d = json.loads(PATH_FILE.read_text(encoding='utf-8'))
            if d.get('seq') == seq and d.get('seqEnd') == seq:
                return d
        except Exception:
            pass
        time.sleep(0.05)
    return None


_avoid: list[tuple[float, float]] = []   # zones ou V s est coince : le mod evite d y renvoyer


def add_avoid(x: float, y: float) -> None:
    _avoid.append((x, y))
    del _avoid[:-6]


def request_path_to_quest() -> dict | None:
    c = {'cmd': 'path_to_quest'}
    if _avoid:
        c['x'], c['y'] = _avoid[-1]        # dernier point bloquant (le mod en garde 1 : suffisant)
    return _wait(_send(c))


def request_path_to(x: float, y: float, z: float | None = None) -> dict | None:
    c = {'cmd': 'path_to', 'x': x, 'y': y}
    if z is not None:
        c['z'] = z
    return _wait(_send(c))


def fast_travel_to(tx: float, ty: float, log=print, min_gain_m: float = 800.0) -> dict:
    """Voyage rapide vers le point connu le plus proche de (tx,ty) si cela rapproche V d au moins min_gain_m.
    Le jeu exige normalement d etre a une borne : on essaie, le resultat fait foi (position avant/apres)."""
    r = _wait(_send({'cmd': 'fast_travel_points'}), timeout=6.0)
    pts = [p for p in ((r or {}).get('points') or []) if p.get('x') is not None]
    if not pts:
        return {'ok': False, 'reason': (r or {}).get('reason', 'aucun point')}
    st = motion.read_state() or {}
    d_now = math.hypot(st.get('x', 0) - tx, st.get('y', 0) - ty)
    best = min(pts, key=lambda p: math.hypot(p['x'] - tx, p['y'] - ty))
    d_after = math.hypot(best['x'] - tx, best['y'] - ty)
    if d_now - d_after < min_gain_m:
        return {'ok': False, 'reason': f'pas de point utile (gain {d_now - d_after:.0f} m)'}
    log(f"  [voyage] « {best.get('name')} » ({best.get('district')}) a {d_after:.0f} m de l objectif (gain {d_now - d_after:.0f} m)")
    # 1. tentative directe par script ; 2. sinon, marcher jusqu a la BORNE la plus proche et l utiliser (invite), puis re-essayer
    r2 = _wait(_send({'cmd': 'fast_travel', 'x': best['i']}), timeout=8.0)
    time.sleep(4.0)
    s_chk = motion.read_state()
    if not (s_chk and math.hypot(s_chk['x'] - best['x'], s_chk['y'] - best['y']) < 60.0):
        near = min(pts, key=lambda p: p.get('d', 1e9))
        if near.get('d', 1e9) < 400.0:
            log(f"  [voyage] borne la plus proche « {near.get('name')} » a {near['d']:.0f} m : V y va")
            from . import input_kbm as kbm
            rg = goto(lambda: request_path_to(near['x'], near['y'], near.get('z')), arrive_m=2.0, max_legs=8, timeout=150.0, log=log)
            if not rg.get('ok'):
                old = motion.ARRIVE_M; motion.ARRIVE_M = 2.0
                try:
                    motion.walk_to(near['x'], near['y'], timeout=25.0)
                finally:
                    motion.ARRIVE_M = old
            s3 = motion.read_state() or {}
            if s3 and math.hypot(s3['x'] - near['x'], s3['y'] - near['y']) < 4.0:
                motion.turn_to(motion.bearing_to(s3['x'], s3['y'], near['x'], near['y']), timeout=2.0)
                kbm.act('interact', 0.2); time.sleep(2.5)            # la borne ouvre la carte des voyages rapides
                r2 = _wait(_send({'cmd': 'fast_travel', 'x': best['i']}), timeout=8.0)
                time.sleep(2.0)
                kbm.tap('ESC', 0.08)                                  # refermer la carte si le voyage n a pas eu lieu
    time.sleep(6.0)                                      # ecran de chargement
    for _ in range(40):
        s2 = motion.read_state()
        if s2 and math.hypot(s2['x'] - best['x'], s2['y'] - best['y']) < 60.0:
            return {'ok': True, 'point': best.get('name'), 'methodes': (r2 or {}).get('methodes')}
        time.sleep(0.5)
    return {'ok': False, 'reason': f"voyage sans effet ({(r2 or {}).get('reason') or (r2 or {}).get('methodes')})"}


def goto(request, arrive_m: float = 3.0, max_legs: int = 10, timeout: float = 180.0, stop=None, log=print, interrupt=None) -> dict:
    """Boucle troncon par troncon. `request` = fonction qui renvoie un path.json."""
    t0 = time.perf_counter()
    legs = 0
    target = None
    while legs < max_legs and time.perf_counter() - t0 < timeout:
        if stop is not None and stop.is_set():
            return {'ok': False, 'reason': 'arret d urgence', 'legs': legs, 'seconds': time.perf_counter() - t0}
        resp = request()
        if resp is None:
            return {'ok': False, 'reason': 'mod muet (jeu pret ? 20 s apres chargement)', 'legs': legs, 'seconds': time.perf_counter() - t0}
        if not resp.get('ok'):
            return {'ok': False, 'reason': resp.get('reason'), 'legs': legs, 'seconds': time.perf_counter() - t0}
        target = resp['target']
        pts = [(p[0], p[1]) for p in resp['points']]
        log(f"  troncon {legs + 1}: {len(pts)} pts, {resp['length']:.0f} m{' (partiel)' if resp.get('partial') else ''}")
        r = motion.follow_path(pts, stop=stop, interrupt=interrupt)
        legs += 1
        if r.get('reason') == 'interrompu':
            return {'ok': False, 'reason': 'interrompu', 'legs': legs, 'seconds': time.perf_counter() - t0}
        st = motion.read_state()
        if st is None:
            return {'ok': False, 'reason': 'etat illisible', 'legs': legs, 'seconds': time.perf_counter() - t0}
        d = math.hypot(st['x'] - target['x'], st['y'] - target['y'])
        log(f"    -> {'ok' if r['ok'] else r.get('reason')}, reste {d:.1f} m")
        if d < arrive_m:
            return {'ok': True, 'legs': legs, 'final_dist': d, 'seconds': time.perf_counter() - t0}
        if not r['ok'] and r.get('reason') not in ('bloque',):
            return {'ok': False, 'reason': r.get('reason'), 'legs': legs, 'final_dist': d, 'seconds': time.perf_counter() - t0}
        # bloque ou troncon partiel : on redemande un chemin depuis ici
    return {'ok': False, 'reason': 'trop de troncons / timeout', 'legs': legs, 'seconds': time.perf_counter() - t0}


def goto_quest(**kw) -> dict:
    return goto(request_path_to_quest, **kw)
