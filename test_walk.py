"""
test_walk.py -- primitive walk_to : aller a un point, revenir.
Etape 1 : avance de 8 m dans la direction actuelle (valide la convention de cap : si V
recule ou part de cote, YAW_SIGN est faux). Etape 2 : retour au point de depart
(valide la rotation + marche vers un point arbitraire).
Terrain requis : 10 m degages devant V.
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import input_kbm as kbm, motion

RESULT_FILE = Path(__file__).resolve().parent / 'walk_result.json'


def main():
    print('Bascule vers Cyberpunk (10 m degages devant V). Test dans 5 s...')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...'); time.sleep(1)
    if not kbm.game_focused():
        print('ABANDON : le jeu n est pas au premier plan.'); return
    ks = kbm.KillSwitch()

    start = motion.read_state()
    res = {'start': {k: start[k] for k in ('x', 'y', 'yaw')}, 'steps': []}

    # cible 8 m devant, selon la convention de cap supposee
    yaw = math.radians(start['yaw'] * motion.YAW_SIGN)
    tx, ty = start['x'] - 8.0 * math.sin(yaw), start['y'] + 8.0 * math.cos(yaw)
    print(f"depart ({start['x']:.1f}, {start['y']:.1f}) cap {start['yaw']:.0f} -> cible ({tx:.1f}, {ty:.1f})")
    r = motion.walk_to(tx, ty, timeout=12.0, stop=ks.triggered)
    after = motion.read_state()
    moved = motion.dist2d(start, after)
    # si la convention est fausse, V s'eloigne de la cible au lieu de s'en approcher
    d_target = math.hypot(after['x'] - tx, after['y'] - ty)
    r.update({'step': 'aller', 'moved_m': moved, 'dist_to_target_m': d_target})
    print(f"aller : {'OK' if r['ok'] else r.get('reason')}  parcouru {moved:.1f} m, reste {d_target:.1f} m, {r['seconds']:.1f} s")
    res['steps'].append(r)
    time.sleep(0.5)

    if not ks.triggered.is_set():
        r2 = motion.walk_to(start['x'], start['y'], timeout=12.0, stop=ks.triggered)
        back = motion.read_state()
        r2.update({'step': 'retour', 'dist_to_start_m': motion.dist2d(start, back)})
        print(f"retour : {'OK' if r2['ok'] else r2.get('reason')}  a {r2['dist_to_start_m']:.1f} m du depart, {r2['seconds']:.1f} s")
        res['steps'].append(r2)

    kbm.release_all()
    RESULT_FILE.write_text(json.dumps(res, indent=2))
    print(f'\nResultats : {RESULT_FILE}')


if __name__ == '__main__':
    main()
