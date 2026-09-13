"""
appearance.py -- une fois par mois, V passe devant le miroir de son appartement et change un detail
de son apparence (coiffure, maquillage, lunettes...). Purement cosmetique, donc rare (30 jours, memorise) et seulement quand un appartement de V est a moins de 150 m.

Deroule : aller au marqueur d appartement -> entrer (porte : invite « Ouvrir ») -> chercher l invite
du miroir (« miroir », « apparence », « regarder ») en tournant sur soi dans l appartement -> E ->
dans l ecran de personnalisation, quelques touches au hasard (categorie suivante, option suivante),
puis validation ; Echap referme tout si l ecran ne repond pas.
"""
from __future__ import annotations

import random
import time

from . import input_kbm as kbm, motion, nav

PERIOD_S = 30 * 86400.0          # une fois par mois (temps reel), memorise entre les sessions (Olivier, 13/09)
import json as _json
from .config import DATA_DIR as _DATA
_FILE = _DATA / 'appearance.json'
try:
    _last = {'t': float(_json.loads(_FILE.read_text(encoding='utf-8')).get('last_epoch', 0))}
except Exception:
    _last = {'t': 0.0}


def _save() -> None:
    try:
        _FILE.write_text(_json.dumps({'last_epoch': _last['t']}), encoding='utf-8')
    except Exception:
        pass
MIRROR_WORDS = ('miroir', 'apparence', 'regarder', 'mirror', 'appearance')


def due() -> bool:
    return time.time() - _last['t'] > PERIOD_S


def nearest_apartment(log=print) -> dict | None:
    """Marqueur d appartement de V (variante contenant « Apartment ») a moins de 150 m."""
    r = nav._wait(nav._send({'cmd': 'list_vendors'}), timeout=4.0)          # meme liste de marqueurs de service
    cands = [v for v in ((r or {}).get('vendors') or []) if 'apartment' in str(v.get('variant') or '').lower() and v.get('dist', 1e9) < 150]
    return min(cands, key=lambda v: v['dist']) if cands else None


def visit_mirror(stop=None, log=print) -> dict:
    _last['t'] = time.time(); _save()
    apt = nearest_apartment(log)
    if not apt:
        return {'ok': False, 'reason': 'aucun appartement a moins de 150 m'}
    log(f"  [apparence] V passe chez lui ({apt.get('variant')} a {apt['dist']:.0f} m) pour se refaire une beaute")
    r = nav.goto(lambda: nav.request_path_to(apt['x'], apt['y'], apt.get('z')), arrive_m=2.0, max_legs=6, timeout=150.0, stop=stop, log=log)
    if not r.get('ok'):
        return {'ok': False, 'reason': f"appartement non atteint ({r.get('reason')})"}
    # dans l appartement : chercher le miroir en tournant (12 x 30 deg), puis avancer un peu et recommencer (3 fois)
    for attempt in range(3):
        for _ in range(12):
            if stop is not None and stop.is_set():
                return {'ok': False, 'reason': 'arret'}
            motion.turn_by(30.0, timeout=1.2, stop=stop)
            time.sleep(0.2)
            st = motion.read_state() or {}
            inter = st.get('interact') or {}
            ch = str((inter.get('choices') or [''])[0]).lower()
            if ch and any(w in ch for w in MIRROR_WORDS):
                log(f"  [apparence] miroir : « {inter['choices'][0]} » -> E")
                kbm.act('interact', 0.2); time.sleep(3.0)
                # ecran de personnalisation : changements au hasard, puis validation
                for _k in range(random.randint(2, 4)):
                    for _d in range(random.randint(1, 3)):
                        kbm.tap('DOWN', 0.08); time.sleep(0.25)           # categorie suivante
                    for _o in range(random.randint(1, 4)):
                        kbm.tap('RIGHT', 0.08); time.sleep(0.25)          # option suivante
                kbm.act('ui_confirm', 0.12); time.sleep(1.0)
                kbm.tap('ENTER', 0.08); time.sleep(2.0)
                kbm.tap('ESC', 0.08); time.sleep(1.0)
                log('  [apparence] nouveau look valide (au hasard)')
                return {'ok': True}
            if ch and any(w in ch for w in ('ouvrir', 'entrer')):
                kbm.act('interact', 0.15); time.sleep(2.0)               # porte de l appartement
        kbm.hold('W'); time.sleep(1.2); kbm.release('W')
    return {'ok': False, 'reason': 'miroir introuvable'}
