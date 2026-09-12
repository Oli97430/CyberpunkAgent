"""
test_turn.py (v2) -- rotation en boucle fermee via agent/motion.py.
v1 : 3/3 convergees, erreur finale <= 0.25 deg, 1.0-1.4 s, mais oscillation +-10 deg
(deux corrections par mesure). v2 n'agit que sur une mesure neuve, gain 0.45.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import input_kbm as kbm, motion

RESULT_FILE = Path(__file__).resolve().parent / 'turn_result.json'


def main():
    print('Bascule vers Cyberpunk. Test dans 5 s...')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...'); time.sleep(1)
    if not kbm.game_focused():
        print('ABANDON : le jeu n est pas au premier plan.'); return
    ks = kbm.KillSwitch()

    res = {'steps': []}
    for delta in (90.0, -90.0, 180.0, 45.0):
        if ks.triggered.is_set():
            break
        r = motion.turn_by(delta, stop=ks.triggered)
        print(f"tourner {delta:+.0f} deg -> {'OK' if r['ok'] else 'TIMEOUT'}  "
              f"erreur {r['final_err']:+.2f} deg  {r['seconds']:.2f} s  {len(r['trace'])} mesures  "
              f"depassement max {max((abs(x) for x in r['trace'][1:] if (x > 0) != (r['trace'][0] > 0)), default=0):.1f} deg")
        r['delta'] = delta
        res['steps'].append(r)
        time.sleep(0.4)

    kbm.release_all()
    RESULT_FILE.write_text(json.dumps(res, indent=2))
    print(f'\nResultats : {RESULT_FILE}')


if __name__ == '__main__':
    main()
