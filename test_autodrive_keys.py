"""Experience : en vehicule, quelle action de G lance l autodrive ? (tap / maintien / double tap)."""
import sys, time, math
sys.path.insert(0, '.')
from agent import driving, motion, input_kbm as kbm
ks = kbm.KillSwitch(); stop = ks.halt
def log(m):
    line = time.strftime('%H:%M:%S ') + m; print(line)
    open('drive_test_log.txt', 'a', encoding='utf-8').write(line + '\n')
t = time.perf_counter()
while not kbm.game_focused() and time.perf_counter() - t < 60: time.sleep(0.3)
if not driving.summon_and_board(stop=stop, log=log):
    log('embarquement echoue'); sys.exit(1)
def pos():
    s = motion.read_state(); return (s['x'], s['y'], s.get('vehicle'))
time.sleep(3)
for label, fn in (('G maintenu 1.5 s', lambda: kbm.act('autodrive', 1.5)),
                  ('G double tap', lambda: (kbm.act('autodrive', 0.08), time.sleep(0.15), kbm.act('autodrive', 0.08))),
                  ('G tap 0.08', lambda: kbm.act('autodrive', 0.08))):
    if stop.is_set(): break
    p0 = pos(); fn()
    moved = 0.0
    for k in range(5):                       # 25 s d observation, par tranches de 5 s
        time.sleep(5); p1 = pos()
        moved = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        log(f'{label}: +{(k+1)*5} s -> {moved:.1f} m, vehicle={p1[2]}')
        if moved > 15: break
    if moved > 15:
        log(f'-> « {label} » lance l autodrive'); time.sleep(15); break
    time.sleep(3)
driving.exit_vehicle(log=log)
kbm.release_all()
