"""
test_phase0.py -- Phase 0 : prouve (ou infirme) que l'injection clavier/souris
fonctionne sur CETTE installation de Cyberpunk 2077, en mesurant l'effet REEL via
l'etat exporte par le mod CET AgentProbe (position/yaw), pas par supposition.

A lancer SOI-MEME (le focus de la fenetre ne peut pas etre pilote a distance) :
jeu deja lance, en mode normal/plat, V debout dans un endroit sur et degage
(aucun ennemi, aucun vehicule, pas en dialogue).

Le script laisse 5 s pour basculer sur le jeu, puis agit seul.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import input_kbm as kbm

STATE_FILE = Path(r'F:\SteamLibrary\steamapps\common\Cyberpunk 2077'
                   r'\bin\x64\plugins\cyber_engine_tweaks\mods\AgentProbe\state.bin')
RESULT_FILE = Path(__file__).resolve().parent / 'phase0_result.json'


def read_state():
    for _ in range(10):
        try:
            raw = STATE_FILE.read_bytes().decode('utf-8', 'ignore').strip()
            data = json.loads(raw)
            if data.get('seq') == data.get('seqEnd'):
                return data
        except Exception:
            pass
        time.sleep(0.05)
    return None


def dist(a, b):
    return ((a['x'] - b['x']) ** 2 + (a['y'] - b['y']) ** 2) ** 0.5


def main():
    result = {'admin': kbm.is_admin(), 'steps': []}
    print(f"Elevation administrateur : {'OUI' if result['admin'] else 'NON (risque connu sur ce jeu)'}")

    if not STATE_FILE.exists():
        print(f"ECHEC : {STATE_FILE} introuvable. Le mod AgentProbe tourne-t-il (jeu lance) ?")
        result['fatal'] = 'state.bin introuvable'
        RESULT_FILE.write_text(json.dumps(result, indent=2))
        return

    print('Bascule vers Cyberpunk 2077 maintenant. Test dans 5 secondes...')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...')
        time.sleep(1)

    if not kbm.game_focused():
        msg = f"ABANDON : Cyberpunk2077.exe n'est pas au premier plan (focus = {kbm.foreground_process_name()!r})."
        print(msg)
        result['fatal'] = msg
        RESULT_FILE.write_text(json.dumps(result, indent=2))
        return

    ks = kbm.KillSwitch()

    # --- Test 1 : deplacement (W, scancode) ---
    before = read_state()
    kbm.hold('W')
    time.sleep(1.2)
    kbm.release('W')
    time.sleep(0.3)
    after = read_state()
    if before and after:
        moved = dist(before, after)
        ok = moved > 0.3
        print(f"DEPLACEMENT (W 1.2s) : {moved:.2f} m  -> {'OK' if ok else 'ECHEC'}")
        result['steps'].append({'test': 'move_w', 'meters': moved, 'ok': ok})
    else:
        print('DEPLACEMENT : etat illisible avant/apres.')
        result['steps'].append({'test': 'move_w', 'ok': False, 'error': 'etat illisible'})

    time.sleep(0.5)

    # --- Test 2 : visee (souris relative) ---
    if not ks.triggered.is_set() and kbm.game_focused():
        before = read_state()
        kbm.look_primed(400, 0)
        time.sleep(0.3)
        after = read_state()
        if before and after:
            dyaw = after['yaw'] - before['yaw']
            dyaw = ((dyaw + 180) % 360) - 180  # normalise a [-180, 180]
            ok = abs(dyaw) > 1.0
            print(f"VISEE (souris dx=400) : delta yaw = {dyaw:.2f} deg -> {'OK' if ok else 'ECHEC'}")
            result['steps'].append({'test': 'look_mouse', 'delta_yaw_deg': dyaw, 'ok': ok})
        else:
            print('VISEE : etat illisible avant/apres.')
            result['steps'].append({'test': 'look_mouse', 'ok': False, 'error': 'etat illisible'})

    kbm.release_all()
    RESULT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nResultats ecrits dans {RESULT_FILE}")


if __name__ == '__main__':
    main()
