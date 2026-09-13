"""
buffs.py -- competence "se buffer" : avant un combat (et toutes les 60 s pendant), V consomme ce qui
le renforce : nourriture (Nourri = regeneration de vie), boisson (Hydrate = regeneration
d endurance), booster (Con_LongLasting : endurance / revitalisant) et drogue de combat (Black
Lace...). L alcool est exclu (malus). Dans ce jeu, nourriture ET boissons sont du type Con_Edible :
on les distingue par des mots du nom, et on verifie l effet via state.buffs (n = nourri, h = hydrate)
quand le mod l exporte. Commande Lua `use` sur les indices de la derniere liste d inventaire (cache).
"""
from __future__ import annotations

import time

from . import inventory, motion, nav

ALCOHOL = ('bière', 'biere', 'lager', 'whisky', 'vodka', 'tequila', 'vin ', 'château', 'chateau', 'cocktail', 'rhum', 'gin ',
           'alcool', 'beer', 'wine', 'saké', 'sake', 'donaghy', 'calavera', 'broseph')
DRINK_WORDS = ('water', 'eau', 'cola', 'jus', 'juice', 'beans', 'tiancha', 'naranjita', 'shwabshwab', 'cirrus', 'nicola',
               'ruisseau', 'tea', 'thé', 'café', 'coffee', 'soda', 'boisson', 'drink', 'latte', 'smash')
DRUG_WORDS = ('black lace', 'blacklace', 'omega', 'pseudo', 'synthcoke', 'glitter', 'booster', 'revitalisant', 'bounce', 'stamina')

_cache = {'t': 0.0, 'food': [], 'drink': [], 'drug': []}
_last_use = {'food': -999.0, 'drink': -999.0, 'drug': -999.0}
PERIOD = {'food': 300.0, 'drink': 300.0, 'drug': 150.0}      # duree typique des effets, quand le mod n exporte pas les buffs


def refresh(items: list[dict] | None = None) -> None:
    """Repere nourriture / boissons / boosters dans l inventaire (indices valables jusqu au prochain `inventory`)."""
    if items is None:
        inv = inventory.fetch()
        items = (inv or {}).get('items') or []
    food, drink, drug = [], [], []
    for it in items:
        t = it.get('type') or ''
        name = str(it.get('name') or '').lower()
        if not t.startswith('Con_') or t in ('Con_Ammo', 'Con_Inhaler', 'Con_Injector', 'Con_Skillbook'):
            continue
        if any(w in name for w in ALCOHOL):
            continue
        if t == 'Con_LongLasting' or any(w in name for w in DRUG_WORDS):
            drug.append(it)
        elif any(w in name for w in DRINK_WORDS):
            drink.append(it)
        elif t == 'Con_Edible':
            food.append(it)
    _cache.update(t=time.perf_counter(), food=food, drink=drink, drug=drug)


def _use(it: dict) -> bool:
    r = nav._wait(nav._send({'cmd': 'use', 'x': it['i']}), timeout=3.0)
    return bool(r and r.get('ok'))


def apply(st: dict | None, log=print, in_combat: bool = False) -> int:
    """Consomme ce qui manque : nourriture si pas Nourri, boisson si pas Hydrate, booster pour un combat."""
    from .config import CFG
    if not CFG.features.get('buffs', True):
        return 0
    if time.perf_counter() - _cache['t'] > 600.0 and not in_combat:
        refresh()
    b = (st or {}).get('buffs') or {}
    now = time.perf_counter()
    used = 0
    plan = [('food', b.get('n')), ('drink', b.get('h'))]
    if in_combat:
        plan.append(('drug', b.get('d')))
    for kind, active in plan:
        if active == 1:
            continue
        if now - _last_use[kind] < (PERIOD[kind] if active is None else 20.0):
            continue
        cands = _cache.get(kind) or []
        if not cands:
            continue
        it = cands[0]
        if _use(it):
            _last_use[kind] = now; used += 1
            log(f"  [buff] {kind} : « {it.get('name')} » consomme")
            if int(it.get('qty') or 1) <= 1:
                cands.pop(0)
            else:
                it['qty'] = int(it['qty']) - 1
            time.sleep(0.3)
            if active is not None and not in_combat:    # verifier l effet (mod recent) : sinon l objet n etait pas le bon type
                time.sleep(1.5)
                s2 = motion.read_state() or {}
                b2 = (s2.get('buffs') or {})
                if kind == 'food' and b2.get('n') == 0 and cands:
                    log(f"  [buff]   « {it.get('name')} » n a pas nourri : reclasse en boisson"); _cache['drink'].insert(0, cands.pop(0)) if cands and cands[0] is it else None
    return used
