"""Va au dernier champ de bataille (corps des policiers) puis loot_around avec la commande Lua `loot`."""
import sys, time
sys.path.insert(0, '.')
from agent import combat, motion, nav, input_kbm as kbm
ks = kbm.KillSwitch(); stop = ks.halt
f = open('loot_test_log.txt', 'a', encoding='utf-8')
def log(m):
    line = time.strftime('%H:%M:%S ') + m; print(line); f.write(line + '\n'); f.flush()
t = time.perf_counter()
while not kbm.game_focused() and time.perf_counter() - t < 60: time.sleep(0.3)
X, Y, Z = -1243.0, 1941.0, 19.0
st = motion.read_state()
log(f"--- test loot (script) : V a ({st['x']:.0f},{st['y']:.0f}), cible ({X:.0f},{Y:.0f})")
r = nav.goto(lambda: nav.request_path_to(X, Y, Z), arrive_m=4.0, max_legs=6, timeout=90.0, stop=stop, log=log)
log(f'trajet : {r}')
st = motion.read_state()
log(f"objets lootables vus : {len(st.get('loot') or [])}, corps : {len(st.get('bodies') or [])}")
res = combat.loot_around(stop=stop, log=log, radius_m=20.0, max_items=8, seen=set())
log(f'resultat : {res}')
kbm.release_all()
