"""
test_dialog.py -- competence dialogue : V repond seul a une conversation entiere.
Place V devant le PNJ (El Cesar). Le script attend que TU ouvres le dialogue (F sur le
PNJ) : il ne le fait pas lui-meme pour ne pas parler a la mauvaise personne. Des que des
choix apparaissent, il decide et repond, jusqu'a la fin de la conversation.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import input_kbm as kbm, dialog

RESULT = Path(__file__).resolve().parent / 'dialog_result.json'


def main():
    print('prechauffage du modele de decision...')
    dialog.llm_choice('test', ['a', 'b'], timeout=120)   # charge llama3.2 en VRAM (~2,5 Go) avant le jeu
    print('Bascule vers Cyberpunk et LANCE le dialogue (F sur le PNJ). 5 s... (F11 = arret)')
    for n in (5, 4, 3, 2, 1):
        print(f'  {n}...'); time.sleep(1)
    if not kbm.game_focused():
        print('ABANDON : le jeu n est pas au premier plan.'); return
    ks = kbm.KillSwitch()
    print('en attente de choix de dialogue (60 s max)...')
    t0 = time.time()
    while time.time() - t0 < 60 and not dialog.current_dialog() and not ks.triggered.is_set():
        time.sleep(0.1)
    if not dialog.current_dialog():
        print('aucun dialogue detecte.'); return
    r = dialog.run_conversation(stop=ks.triggered)
    print(f"\nconversation terminee : {len(r['turns'])} reponse(s) en {r['seconds']:.0f} s")
    kbm.release_all()
    RESULT.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
