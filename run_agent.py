"""
run_agent.py -- lanceur de l agent Cyberpunk 2077 (V joue seul).

    CyberpunkAgent.exe                 V joue 20 minutes (F11 = pause/reprise, F12 = arret)
    CyberpunkAgent.exe 45              V joue 45 minutes
    CyberpunkAgent.exe --check         verifie l installation (jeu, mod, CET, Ollama, modele)
    CyberpunkAgent.exe --test keys     test des touches (lit UserSettings.json du joueur)
    CyberpunkAgent.exe --test loot     test du loot autour de V
    CyberpunkAgent.exe --test drive    test de conduite (appel du vehicule, autodrive vers la quete)
    CyberpunkAgent.exe --test walk     test de marche / rotation

Le jeu doit tourner, une partie chargee, V a pied. Le lanceur attend que le jeu soit au
premier plan puis prend la main ; F11 met en pause / reprend (le joueur reprend la main), F12 arrete. Journal : %APPDATA%\\CyberpunkAgent.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.config import CFG, DATA_DIR  # noqa: E402


def check(verbose: bool = True) -> bool:
    from agent import llm
    ok = True
    lines = []
    if CFG.game_dir and (CFG.game_dir / 'bin' / 'x64' / 'Cyberpunk2077.exe').exists():
        lines.append(f'[OK]  jeu : {CFG.game_dir}')
    else:
        lines.append('[!!]  jeu introuvable : renseigne "game_dir" dans config.json'); ok = False
    cet = CFG.game_dir / 'bin' / 'x64' / 'plugins' / 'cyber_engine_tweaks' if CFG.game_dir else None
    if cet and cet.exists():
        lines.append('[OK]  Cyber Engine Tweaks present')
    else:
        lines.append('[!!]  Cyber Engine Tweaks absent : installe-le (https://www.nexusmods.com/cyberpunk2077/mods/107)'); ok = False
    if CFG.mod_dir and (CFG.mod_dir / 'init.lua').exists():
        lines.append(f'[OK]  mod AgentProbe : {CFG.mod_dir}')
    else:
        lines.append('[!!]  mod AgentProbe absent : relance l installateur'); ok = False
    if CFG.user_settings.exists():
        lines.append(f'[OK]  reglages du joueur : {CFG.user_settings}')
    else:
        lines.append('[..]  UserSettings.json introuvable : touches par defaut (F interagir, E cyberware...)')
    from agent import llm_client
    if llm_client.provider() == 'ollama':
        if not llm.alive():
            print('Ollama ne repond pas : demarrage de `ollama serve`...', flush=True)
            llm.ensure(log=print, wait_s=40.0)
        if llm.alive():
            lines.append(f'[OK]  Ollama repond ({CFG.ollama_url})')
            try:
                import json, urllib.request
                tags = json.loads(urllib.request.urlopen(CFG.ollama_url + '/api/tags', timeout=3).read())
                names = [m.get('name') for m in tags.get('models', [])]
                if any(n and n.split(':')[0] == CFG.model.split(':')[0] for n in names):
                    lines.append(f'[OK]  modele {CFG.model} disponible')
                else:
                    lines.append(f'[!!]  modele {CFG.model} absent : `ollama pull {CFG.model}`'); ok = False
            except Exception:
                pass
        else:
            lines.append('[!!]  Ollama ne repond pas' + (f' (exe : {CFG.ollama_exe})' if CFG.ollama_exe else ' (non installe ? https://ollama.com)'))
            ok = False
    else:
        good, msg = llm_client.check()
        lines.append(('[OK]  ' if good else '[!!]  ') + 'modele de decision : ' + msg)
        ok = ok and good
    lines.append(f'[..]  journaux : {DATA_DIR}')
    if verbose:
        print('\n'.join(lines))
    return ok


def mod_is_stale() -> str | None:
    """Le jeu a-t-il charge la DERNIERE version du mod ?"""
    try:
        if not (CFG.mod_dir and CFG.cet_log):
            return None
        lua_t = os.path.getmtime(CFG.mod_dir / 'init.lua')
        last = None
        for line in open(CFG.cet_log, encoding='utf-8', errors='ignore'):
            if f'Mod {CFG.mod_dir.name} loaded' in line:
                last = line
        if not last:
            return 'le mod ne semble pas charge par le jeu (relance le jeu apres l installation)'
        m = re.match(r'\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})', last)
        if not m:
            return None
        y, mo, d, h, mi, s = map(int, m.groups())
        if lua_t > time.mktime((y, mo, d, h, mi, s, 0, 0, -1)):
            return 'le mod a ete mis a jour depuis le lancement du jeu : relance le jeu'
    except Exception:
        return None
    return None


def play(minutes: float) -> None:
    from agent import brain, dialog, input_kbm as kbm
    stale = mod_is_stale()
    if stale:
        print(f'\n  !!! {stale}\n'); time.sleep(4)
    t0 = time.time()
    print('prechauffage du modele de decision (10-30 s la premiere fois)...', flush=True)
    dialog.llm_choice('test', ['a', 'b'], timeout=120)
    print(f'  modele pret en {time.time() - t0:.0f} s', flush=True)
    print(f'Bascule vers Cyberpunk : V joue seul pendant {minutes:.0f} min des que le jeu est au premier plan (F11 = pause/reprise, F12 = arret)', flush=True)
    t1 = time.time()
    while not kbm.game_focused():
        if time.time() - t1 > 120:
            print('ABANDON : le jeu n est pas passe au premier plan en 2 min.'); return
        time.sleep(0.25)
    print('  jeu au premier plan : depart dans 2 s', flush=True); time.sleep(2)
    ks = kbm.KillSwitch()
    brain.run(duration_s=minutes * 60.0, stop=ks.triggered, pause=ks.paused)
    print(f'\njournal : {brain.LOG_FILE}')


def run_test(name: str) -> None:
    from agent import input_kbm as kbm, motion
    print('Bascule vers le jeu (F11 = pause, F12 = arret)...', flush=True)
    t = time.time()
    while not kbm.game_focused() and time.time() - t < 120:
        time.sleep(0.25)
    time.sleep(1.5)
    ks = kbm.KillSwitch(); stop = ks.halt
    if name == 'keys':
        for action in ('forward', 'interact', 'ui_confirm', 'iconic', 'quickmelee', 'dodge', 'sprint', 'jump', 'crouch', 'consumable', 'scanner'):
            print(f'  {action:12s} -> {kbm.K(action)}')
        print('Appui de chaque touche dans 3 s (observe V)...'); time.sleep(3)
        for action in ('forward', 'jump', 'crouch', 'crouch', 'interact'):
            kbm.act(action, 0.3); time.sleep(1.0)
        r = motion.turn_by(90.0); print(f'  rotation +90 : {r.get("ok")}')
    elif name == 'loot':
        from agent import combat
        r = combat.loot_around(stop=stop, log=print, radius_m=20.0, max_items=8, seen=set())
        print(f'resultat : {r}')
    elif name == 'drive':
        from agent import driving
        st = motion.read_state() or {}
        q = st.get('quest') or {}
        if not q.get('hasMappin'):
            print('aucun objectif de quete suivi'); return
        print(f'resultat : {driving.drive_to(q["mx"], q["my"], stop=stop, log=print)}')
    elif name == 'walk':
        r = motion.turn_by(180.0); print(f'  demi-tour : {r.get("ok")}')
        st = motion.read_state()
        if st:
            print(f'  marche 5 m : {motion.walk_to(st["x"] + 5 * 0, st["y"] + 5, timeout=10.0, stop=stop)}')
    kbm.release_all()


def single_instance() -> bool:
    """Un seul agent a la fois : deux instances enverraient des touches en meme temps (vu le 13/09)."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.CreateMutexW(None, True, 'CyberpunkAgent.single')
        if k32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
            return False
        globals()['_mutex'] = h
    except Exception:
        pass
    return True


def main() -> None:
    if not single_instance():
        print('Un agent CyberpunkAgent tourne deja (F12 pour l arreter). Cette instance se ferme.')
        if getattr(sys, 'frozen', False):
            time.sleep(4)
        return
    ap = argparse.ArgumentParser(description='Agent IA autonome pour Cyberpunk 2077 (V joue seul).')
    ap.add_argument('minutes', nargs='?', type=float, default=20.0, help='duree de jeu en minutes (defaut 20)')
    ap.add_argument('--check', action='store_true', help='verifier l installation et quitter')
    ap.add_argument('--test', choices=['keys', 'loot', 'drive', 'walk'], help='lancer un test unitaire en jeu')
    ap.add_argument('--config', action='store_true', help='afficher la configuration detectee')
    ap.add_argument('--loop', action='store_true', help='enchainer les sessions sans fin (F12 pour arreter)')
    a = ap.parse_args()
    if a.config:
        import json
        print(json.dumps(CFG.as_dict(), indent=2, ensure_ascii=False)); return
    if a.check:
        sys.exit(0 if check() else 1)
    if not check(verbose=True):
        print('\nInstallation incomplete : corrige les lignes [!!] ci-dessus.')
        if getattr(sys, 'frozen', False):
            input('\nEntree pour fermer...')
        sys.exit(1)
    if a.test:
        run_test(a.test); return
    if a.loop:
        from agent import input_kbm as kbm
        n = 0
        while True:
            n += 1
            print(f'\n===== session {n} =====', flush=True)
            play(a.minutes)
            import ctypes
            if ctypes.windll.user32.GetAsyncKeyState(0x7B) & 0x8000:   # F12 enfonce : on s arrete
                break
            time.sleep(5.0)
    else:
        play(a.minutes)
    if getattr(sys, 'frozen', False):
        input('\nEntree pour fermer...')


if __name__ == '__main__':
    main()
