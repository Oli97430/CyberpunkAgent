"""
test_keys.py -- VERIFICATION VISUELLE des touches d action, une par une.
Jeu au premier plan, V debout en exterieur, face a un conteneur ou un objet (pour « interagir »).
Le script annonce chaque touche 2 s avant de l appuyer : regarde ce que fait V a l ecran.
A la fin, compare avec ce qui est attendu et dis-moi ce qui differe.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import input_kbm as kbm, motion

LOG = Path(__file__).with_name('keys_test_log.txt')
_print = print


def print(*a, **k):
    line = ' '.join(str(x) for x in a)
    _print(line, **k)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")


def pos():
    s = motion.read_state() or {}
    return s.get('x', 0), s.get('y', 0), s.get('yaw', 0)


STEPS = [
    ('forward',    0.8,  'AVANCER   (Z)        : V doit avancer'),
    ('back',       0.6,  'RECULER   (S)        : V doit reculer'),
    ('left',       0.6,  'GAUCHE    (Q)        : pas de cote gauche'),
    ('right',      0.6,  'DROITE    (D)        : pas de cote droit'),
    ('jump',       0.1,  'SAUT      (Espace)   : V saute'),
    ('crouch',     0.1,  'ACCROUPI  (C)        : V s accroupit (puis se releve a l appui suivant)'),
    ('crouch',     0.1,  'ACCROUPI  (C)        : V se releve'),
    ('interact',   0.4,  'INTERAGIR (E)        : ouvre/ramasse/parle si une invite est a l ecran'),
    ('iconic',     0.1,  'CYBERWARE (F)        : active le cyberware iconique (Sandevistan...)'),
    ('quickmelee', 0.1,  'COUP RAPIDE (~)      : coup de pied / quick melee'),
    ('dodge',      0.1,  'ESQUIVE   (Ctrl g.)  : V esquive (avec Z tenu)'),
    ('consumable', 0.1,  'SOIN      (X)        : utilise un inhalateur si V en a'),
    ('weapon2',    0.1,  'ARME 2    (2)        : degaine l emplacement 2 (Errata)'),
    ('holster',    0.1,  'RENGAINER (B)        : range l arme'),
    ('scanner',    1.2,  'SCANNER   (Tab tenu) : le scanner s affiche pendant 1,2 s'),
]


def main():
    LOG.write_text('', encoding='utf-8')
    print('touches resolues :', {k: kbm.ACTIONS.get(k) for k, _, _ in STEPS})
    print('Bascule vers Cyberpunk. Debut dans 5 s... (F11 = arret)')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...'); time.sleep(1)
    if not kbm.game_focused():
        print('ABANDON : le jeu n est pas au premier plan.'); return
    ks = kbm.KillSwitch()
    for action, dur, label in STEPS:
        if ks.triggered.is_set():
            break
        if not kbm.ACTIONS.get(action):
            print(f'-- {label}  -> AUCUNE TOUCHE CONFIGUREE'); continue
        print(f'-- dans 2 s : {label}  [touche {kbm.ACTIONS.get(action)}]')
        time.sleep(2.0)
        x0, y0, yaw0 = pos()
        if action == 'dodge':
            kbm.hold('W'); time.sleep(0.1); kbm.act('dodge', 0.1); time.sleep(0.3); kbm.release('W')
        elif action == 'scanner':
            kbm.act_hold('scanner'); time.sleep(dur); kbm.act_release('scanner')
        else:
            kbm.act(action, dur)
        time.sleep(0.8)
        x1, y1, yaw1 = pos()
        print(f'   -> deplacement {((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5:.2f} m, rotation {motion.wrap(yaw1 - yaw0):+.0f} deg')
    # test cle : tourner a la souris PUIS avancer (c est la sequence de walk_to qui donnait 0,00 m)
    if not ks.triggered.is_set():
        print('-- dans 2 s : ROTATION souris +90 deg puis AVANCER 1 s (sequence de la marche)')
        time.sleep(2.0)
        motion.turn_by(90.0)
        x0, y0, _ = pos()
        kbm.hold('W'); time.sleep(1.0); kbm.release('W'); time.sleep(0.5)
        x1, y1, _ = pos()
        print(f'   -> deplacement apres rotation : {((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5:.2f} m (attendu ~3 m)')
    kbm.release_all()
    print('fin du test de touches.')


if __name__ == '__main__':
    main()
