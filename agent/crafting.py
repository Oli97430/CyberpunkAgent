"""
crafting.py -- competence "crafter" via CraftingSystem (CraftItem / CanItemBeCrafted), sans le menu.

Politique v2 ("V doit pouvoir tout crafter selon son niveau") :
  1. CONSOMMABLES d abord : soins (MaxDoc, Bounce Back), grenades, munitions -> stock cible WANT.
  2. puis TOUT ce qui est faisable parmi les recettes connues (armes, vetements, cyberware, mods,
     quickhacks) : le jeu gere le niveau (CanItemBeCrafted tient compte des perks, composants et
     du niveau de V ; l objet fabrique est au niveau de V). On ne fabrique qu UN exemplaire par
     recette, seulement si V n en possede pas deja, en commencant par la meilleure qualite.
     Les armes/vetements fabriques sont ensuite tries par inventory.manage (equipe le meilleur,
     vend/demonte le reste), donc la boucle converge vers le meilleur equipement craftable.
"""
from __future__ import annotations

import time

from . import nav

WANT = {                    # type d objet -> stock souhaite
    'Con_Inhaler': 5,       # soins (MaxDoc)
    'Con_Injector': 3,      # Bounce Back
}
# 12/10 : en 2.x une grenade lancee n est pas consommee (reserve commune) et le jeu ne garde qu un exemplaire par famille
# (player.script:2651, LeaveTheBestQualityConsumable) : fabriquer 5 grenades identiques gaspillait des composants.
# Un exemplaire par FAMILLE (pour adapter la grenade a l ennemi), puis la meilleure qualite.
GREN_FAMILIES = ('frag', 'emp', 'fire', 'bio', 'flash')
AMMO_WANT = 80              # par type de munition faisable (armes de reserve)
HEAL_TYPES = ('Con_Inhaler', 'Con_Injector')
MAX_CRAFT_PER_PASS = 10
MAX_GEAR_PER_PASS = 3       # armes / vetements / cyberware par passe
QR = {'Legendary': 5, 'Epic': 4, 'Rare': 3, 'Uncommon': 2, 'Common': 1}


def _q(q) -> float:                                          # 'RarePlus' etc. valaient 0 dans QR
    q = str(q or '')
    return QR.get(q.replace('PlusPlus', '').replace('Plus', ''), 0) + (0.5 if 'Plus' in q else 0.0)


def recipes() -> list[dict]:
    r = nav._wait(nav._send({'cmd': 'recipes'}), timeout=6.0)
    return (r or {}).get('recipes') or []


LAST = {}   # 12/10 : derniere reponse du mod (gain MESURE, raison d un refus)


def craft(index: int, qty: int = 1) -> bool:
    r = nav._wait(nav._send({'cmd': 'craft', 'x': index, 'y': qty}), timeout=6.0) or {}
    LAST.clear(); LAST.update(r)
    return bool(r.get('ok'))


def heal_stock(inventory_items: list[dict]) -> int:
    return sum(int(it.get('qty') or 1) for it in inventory_items if (it.get('type') or '') in HEAL_TYPES)


_made_gear: set[str] = set()    # equipement fabrique dans la session (evite la boucle craft -> vente -> craft)


def manage(inventory_items: list[dict], log=print) -> dict:
    """Consommables jusqu aux cibles WANT, puis tout l equipement faisable (1 par recette)."""
    t0 = time.perf_counter()
    stock, names = {}, set()
    for it in inventory_items:
        t = it.get('type') or ''
        stock[t] = stock.get(t, 0) + int(it.get('qty') or 1)
        if it.get('name'):
            names.add(str(it['name']).lower())
    recs = recipes()
    if not recs:
        log('  [craft] aucune recette lisible')
        return {'ok': False}
    visible = [r for r in recs if not r.get('hidden')]
    doable = [r for r in visible if r.get('can')]
    log(f'  [craft] {len(recs)} recettes connues, {len(visible)} visibles, {len(doable)} faisables')
    made = 0
    # 1. consommables (soins en premier)
    for typ, want in WANT.items():
        have = stock.get(typ, 0)
        if have >= want:
            continue
        cands = sorted([r for r in doable if r.get('type') == typ], key=lambda r: -QR.get(str(r.get('quality')), 0))
        if not cands:
            continue
        r = cands[0]
        n = min(want - have, MAX_CRAFT_PER_PASS - made)
        if n <= 0:
            break
        if craft(r['i'], n):
            made += n
            log(f"  [craft] fabrique {n} x « {r.get('name')} » ({typ}, stock {have} -> {have + n})")
            time.sleep(0.4)
    # 1c. grenades (12/10) : famille MANQUANTE d abord, puis remplacement par une qualite superieure
    best_have = {}
    for it in inventory_items:
        if it.get('type') == 'Gad_Grenade' and it.get('gk'):
            best_have[it['gk']] = max(best_have.get(it['gk'], -1), _q(it.get('quality')))
    for fam in GREN_FAMILIES:
        if made >= MAX_CRAFT_PER_PASS:
            break
        cands = sorted([r for r in doable if r.get('type') == 'Gad_Grenade' and r.get('gk') == fam],
                       key=lambda r: -_q(r.get('quality')))
        if not cands or _q(cands[0].get('quality')) <= best_have.get(fam, -1):
            continue
        if craft(cands[0]['i'], 1):
            made += 1
            log(f"  [craft] grenade {fam} : « {cands[0].get('name')} » ({cands[0].get('quality')}) -- "
                f"{'nouvelle famille' if fam not in best_have else 'meilleure qualite'}")
            time.sleep(0.4)
    # 1b. munitions : par recette de munition faisable, jusqu a AMMO_WANT (par nom)
    for r in [r for r in doable if (r.get('type') or '').startswith('Con_Ammo')]:
        if made >= MAX_CRAFT_PER_PASS:
            break
        have = sum(int(it.get('qty') or 1) for it in inventory_items if str(it.get('name', '')).lower() == str(r.get('name', '')).lower())
        if have >= AMMO_WANT:
            continue
        lot = max(1, int(r.get('amount') or 1))
        n = max(1, min(3, -(-(AMMO_WANT - have) // lot)))
        if craft(r['i'], n):
            made += 1
            log(f"  [craft] « {r.get('name')} » : {LAST.get('qty', n)} lot(s) de {LAST.get('lot')} -> stock {LAST.get('before')} -> {LAST.get('after')}")
            time.sleep(0.3)
        else:
            log(f"  [craft] « {r.get('name')} » non fabrique : {LAST.get('reason') or 'mod muet'} (stock {have})")
    # 2. equipement : tout ce qui est faisable, meilleure qualite d abord, 1 exemplaire si V n en a pas
    gear = [r for r in doable if not (r.get('type') or '').startswith('Con_') and not (r.get('type') or '').startswith('Gad_')
            and str(r.get('name', '')).lower() not in names
            and (QR.get(str(r.get('quality')), 0) >= 3 or (r.get('type') or '').startswith('Gen_'))]   # Rare+ (ou composants)
    gear.sort(key=lambda r: -QR.get(str(r.get('quality')), 0))
    gear_made = 0
    for r in gear:
        if made >= MAX_CRAFT_PER_PASS or gear_made >= MAX_GEAR_PER_PASS:
            break
        if str(r.get('name', '')).lower() in _made_gear:
            continue                 # deja fabrique cette session (et sans doute vendu) : on ne refait pas fondre les composants
        if craft(r['i'], 1):
            _made_gear.add(str(r.get('name', '')).lower())
            made += 1; gear_made += 1
            log(f"  [craft] fabrique « {r.get('name')} » ({r.get('type')}, {r.get('quality')})")
            time.sleep(0.4)
    return {'ok': True, 'fabriques': made, 'equipement': gear_made, 'soins': heal_stock(inventory_items), 'seconds': time.perf_counter() - t0}
