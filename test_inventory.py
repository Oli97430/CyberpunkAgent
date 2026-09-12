"""
test_inventory.py -- LECTURE SEULE : affiche ce que le mod voit de l inventaire de V
(nom, type, qualite, DPS, quantite, equipe). Ne modifie rien. A lancer jeu ouvert, en jeu
depuis >= 30 s. Sert a verifier la liste AVANT de laisser V equiper / demonter tout seul.

    python test_inventory.py            lecture seule
    python test_inventory.py --gerer    lecture puis gestion (equipe les 3 meilleures armes
                                        de melee, demonte la camelote)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import inventory

inv = inventory.fetch()
if not inv or not inv.get('ok'):
    print('mod muet ou erreur :', inv and inv.get('reason'))
    sys.exit(1)
items = inv.get('items') or []
print(f"{len(items)} objets | {inv.get('money')} eddies | poids {inv.get('weight')}/{inv.get('carry')}\n")
items_sorted = sorted(items, key=lambda it: ((it.get('type') or ''), -(it.get('dps') or 0)))
for it in items_sorted:
    flags = ''.join(['E' if it.get('equipped') else '-', 'I' if it.get('iconic') else '-', 'Q' if it.get('quest') else '-'])
    print(f"  [{flags}] {it.get('type', '?'):22s} {str(it.get('quality', '?')):10s} "
          f"dps={it.get('dps') or 0:6.0f} arm={it.get('armor') or 0:4.0f} x{int(it.get('qty') or 1):<3d} {it.get('name', '?')}")
print('\nE = equipe, I = iconique, Q = quete')
Path(__file__).with_name('inventory_dump.json').write_text(json.dumps(inv, indent=1, ensure_ascii=False), encoding='utf-8')

if '--gerer' in sys.argv:
    print('\n--- gestion ---')
    print(inventory.manage())
