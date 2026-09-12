"""
test_goto_quest.py -- premiere COMPETENCE complete : aller_a(objectif de quete suivi).
Demande le chemin navmesh au mod, le suit troncon par troncon, s'arrete a 3 m du marqueur.
Jeu lance depuis >= 30 s, V dans la rue, quete suivie avec marqueur visible.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import input_kbm as kbm, motion, nav

RESULT_FILE = Path(__file__).resolve().parent / 'goto_result.json'


def main():
    print('Bascule vers Cyberpunk. Depart dans 5 s... (F11 = arret)')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...'); time.sleep(1)
    if not kbm.game_focused():
        print('ABANDON : le jeu n est pas au premier plan.'); return
    ks = kbm.KillSwitch()
    start = motion.read_state()
    print(f"depart ({start['x']:.0f}, {start['y']:.0f})")
    r = nav.goto_quest(stop=ks.triggered)
    print(f"\nRESULTAT : {'ARRIVE' if r['ok'] else 'ECHEC : ' + str(r.get('reason'))}  "
          f"{r['legs']} troncon(s), {r['seconds']:.0f} s" + (f", a {r['final_dist']:.1f} m" if 'final_dist' in r else ''))
    kbm.release_all()
    r['start'] = {k: start[k] for k in ('x', 'y')}
    RESULT_FILE.write_text(json.dumps(r, indent=2))


if __name__ == '__main__':
    main()
