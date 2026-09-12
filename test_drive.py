"""Test conduite : appelle la voiture, monte, autodrive vers l objectif de quete, descend.
Lancer en jeu (V a pied, en exterieur, quete suivie). F11 = arret d urgence."""
import sys, time, threading
sys.path.insert(0, '.')
from agent import driving, motion, input_kbm as kbm

ks = kbm.KillSwitch()
stop = ks.halt
log_f = open('drive_test_log.txt', 'a', encoding='utf-8')
def log(m):
    line = time.strftime('%H:%M:%S ') + m
    print(line); log_f.write(line + '\n'); log_f.flush()

log('--- test conduite : attente du focus sur le jeu (60 s max)')
t_f = time.perf_counter()
while not kbm.game_focused():
    if time.perf_counter() - t_f > 60:
        log('jeu jamais au premier plan'); sys.exit(1)
    time.sleep(0.5)
time.sleep(1.5)
st = motion.read_state()
if not st:
    log('pas d etat (mod charge ?)'); sys.exit(1)
q = st.get('quest') or {}
if not q.get('hasMappin'):
    log('aucun objectif de quete suivi'); sys.exit(1)
log(f"objectif « {q.get('text')} » a {motion.dist2d(st, {'x': q['mx'], 'y': q['my']}):.0f} m")
r = driving.drive_to(q['mx'], q['my'], stop=stop, log=log)
log(f'resultat : {r}')
kbm.release_all()
