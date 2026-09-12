"""
test_loot.py -- test cible du LOOT : V va ramasser les objets/conteneurs lootables autour de lui
(< 20 m) et affiche ce qu il voit (classe, distance, tooltip de loot, invite) a chaque etape.
Place V pres de quelques objets au sol / conteneurs / corps, jeu au premier plan.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import combat, input_kbm as kbm, motion


LOG = Path(__file__).with_name('loot_test_log.txt')
_print = print


def print(*a, **k):                      # journal fichier + ecran (pour lecture a distance)
    line = ' '.join(str(x) for x in a)
    _print(line, **k)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")


def main():
    LOG.write_text('', encoding='utf-8')
    print(f"touche interagir = {kbm.K('interact')}  |  touche UI = {kbm.K('ui_confirm')}")
    print('Bascule vers Cyberpunk. Test dans 5 s... (F11 = arret)')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...'); time.sleep(1)
    if not kbm.game_focused():
        print('ABANDON : le jeu n est pas au premier plan.'); return
    ks = kbm.KillSwitch()
    st = motion.read_state()
    if not st:
        print('etat illisible'); return
    objs = st.get('loot') or []
    print(f"objets lootables vus par le mod : {len(objs)}")
    for o in objs:
        print(f"   {o.get('cls', '?'):24s} a {o['d']:5.1f} m  sx={o.get('sx')} sy={o.get('sy')}")
    print(f"lootCount actuel (tooltip) : {st.get('lootCount')}")
    print(f"etats : porte un corps={st.get('carrying')} locomotion={st.get('locomotion')} upperBody={st.get('upperBody')} vehicule={st.get('vehicle')} lookat={st.get('lookat')}")
    r = combat.loot_around(stop=ks.halt, log=print, radius_m=20.0, max_items=6, seen=set())
    print('\nresultat :', r)
    kbm.release_all()


if __name__ == '__main__':
    main()
