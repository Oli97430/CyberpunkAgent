"""
dialog_watch.py -- observe en direct ce que le mod voit : vie, combat, choix de dialogue.
Aucune entree envoyee au jeu. Ctrl+C pour quitter. Sert a valider la lecture des
dialogues AVANT d'ecrire la competence qui y repond.
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import motion

last = None
print('observation... (Ctrl+C pour quitter)')
try:
    while True:
        st = motion.read_state()
        if st:
            d = st.get('dialog')
            sig = json.dumps(d, ensure_ascii=False, sort_keys=True) if d else f"hp={st.get('hp')} combat={st.get('combat')}"
            if sig != last:
                last = sig
                t = time.strftime('%H:%M:%S')
                if d:
                    print(f"[{t}] DIALOGUE « {d.get('title')} »  selection={d.get('sel')}")
                    for i, c in enumerate(d.get('choices', [])):
                        print(f"      {'>' if i == d.get('sel') else ' '} {i}: {c}")
                else:
                    print(f"[{t}] pas de dialogue | hp={st.get('hp')} combat={st.get('combat')}")
        time.sleep(0.1)
except KeyboardInterrupt:
    pass
