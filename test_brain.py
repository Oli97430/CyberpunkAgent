"""
test_brain.py -- V joue seul pendant N minutes (defaut 5) : va a l objectif, interagit,
repond aux dialogues, recommence quand la quete avance. F11 = reprendre la main.

    python test_brain.py          5 minutes
    python test_brain.py 10       10 minutes
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import brain, dialog, input_kbm as kbm


def mod_is_stale() -> str | None:
    """Le jeu a-t-il charge la DERNIERE version du mod ? (3 runs analyses a tort sur un mod perime)"""
    import os, re
    try:
        lua = r'F:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\AgentProbe\init.lua'
        logp = r'F:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\scripting.log'
        lua_t = os.path.getmtime(lua)
        last = None
        for line in open(logp, encoding='utf-8', errors='ignore'):
            if 'Mod AgentProbe loaded' in line:
                last = line
        if not last:
            return 'le mod ne semble pas charge'
        m = re.match(r'\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})', last)
        if not m:
            return None
        y, mo, d, h, mi, s = map(int, m.groups())
        load_t = time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
        if lua_t > load_t:
            return (f"le mod en jeu date de {h:02d}:{mi:02d}:{s:02d} mais init.lua a change a "
                    f"{time.strftime('%H:%M:%S', time.localtime(lua_t))} -> RELANCE LE JEU avant ce test")
    except Exception:
        return None
    return None


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    stale = mod_is_stale()
    if stale:
        print(f'\n  !!! MOD PERIME : {stale}\n'); time.sleep(4)
    t0 = time.time()
    print('prechauffage du modele de decision (10-30 s la premiere fois)...', flush=True)
    dialog.llm_choice('test', ['a', 'b'], timeout=120)
    print(f'  modele pret en {time.time() - t0:.0f} s', flush=True)
    print(f'Bascule vers Cyberpunk : V joue seul pendant {minutes:.0f} min des que le jeu est au premier plan (F11 = arret)', flush=True)
    t1 = time.time()
    while not kbm.game_focused():
        if time.time() - t1 > 60:
            print('ABANDON : le jeu n est pas passe au premier plan en 60 s.'); return
        time.sleep(0.25)
    print('  jeu au premier plan : depart dans 2 s', flush=True); time.sleep(2)
    ks = kbm.KillSwitch()
    brain.run(duration_s=minutes * 60.0, stop=ks.triggered, pause=ks.paused)
    print(f'\njournal : {brain.LOG_FILE}')


if __name__ == '__main__':
    main()
