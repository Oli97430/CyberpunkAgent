"""
vendor.py -- competence "revendre" (v1, pilotee par l interface du marchand).

Aucun blackboard ne decrit l ecran du marchand (menu plein ecran). On s appuie donc sur les
touches lues dans les mappings : dans l ecran du marchand, G = « vendre la camelote »
(sell_junk), F = valider la transaction (vendor_checkout), Echap = sortir.
Pre-requis : les objets a vendre doivent etre marques CAMELOTE (fonction de marquage sondee
cote mod ; en attendant, seule la camelote native est vendue).

Politique : on ne va vendre que si >= MIN_SELLABLE objets sont marques, et si un marchand
est a moins de MAX_VENDOR_M. Les marchands d armes paient mieux les armes : on prefere une
variante « gun/weapon » pour les armes, « clothes » pour les vetements, sinon le plus proche.
"""
from __future__ import annotations

import math
import time

from . import input_kbm as kbm, motion, nav

MIN_SELLABLE = 8
MAX_VENDOR_M = 250.0
HEAL_WANT = 6               # soins souhaites apres passage chez un marchand
MONEY_RESERVE = 300         # eddies que V garde toujours
HEAL_WORDS = ('maxdoc', 'bounce', 'soin', 'inhal', 'inject')


def list_vendors() -> list[dict]:
    r = nav._wait(nav._send({'cmd': 'list_vendors'}), timeout=4.0)
    return (r or {}).get('vendors') or []


_failed: dict[tuple, float] = {}     # (x,y arrondis) -> heure de l echec (marchand injoignable : boutique hors maillage...)


def mark_failed(v: dict) -> None:
    _failed[(round(v.get('x', 0)), round(v.get('y', 0)))] = time.perf_counter()


def pick_vendor(vendors: list[dict], prefer: str | None = None) -> dict | None:
    if not vendors:
        return None
    near = [v for v in vendors if v.get('dist', 1e9) <= MAX_VENDOR_M and 'ripper' not in (v.get('variant') or '').lower()
            and time.perf_counter() - _failed.get((round(v.get('x', 0)), round(v.get('y', 0))), -1e9) > 900.0]
    if not near:
        return None
    if prefer:
        pref = [v for v in near if prefer in (v.get('variant') or '').lower()]
        if pref:
            return min(pref, key=lambda v: v['dist'])
    return min(near, key=lambda v: v['dist'])


def vendor_stock() -> dict | None:
    return nav._wait(nav._send({'cmd': 'vendor_stock'}), timeout=6.0)


def buy(index: int, qty: int = 1) -> dict | None:
    return nav._wait(nav._send({'cmd': 'buy', 'x': index, 'y': qty}), timeout=6.0)


def buy_heals(have: int, log=print, want: int = HEAL_WANT) -> dict:
    """Chez le marchand present : achete des soins (les moins chers d abord) jusqu a `want`,
    en gardant MONEY_RESERVE eddies. A appeler apres sell_all (l argent de la vente sert a l achat)."""
    if have >= want:
        return {'ok': True, 'achetes': 0}
    stock = vendor_stock()
    if not stock or not stock.get('ok'):
        log(f"  [achat] stock illisible : {(stock or {}).get('reason')}")
        return {'ok': False}
    money = int(stock.get('money') or 0)
    heals = [it for it in (stock.get('items') or [])
             if (it.get('type') or '') in ('Con_Inhaler', 'Con_Injector') or any(w in str(it.get('name', '')).lower() for w in HEAL_WORDS)]
    if not heals:
        log(f"  [achat] « {stock.get('vendor')} » ne vend pas de soins ({len(stock.get('items') or [])} articles)")
        return {'ok': True, 'achetes': 0}
    heals.sort(key=lambda it: int(it.get('price') or 1e9))
    bought, spent = 0, 0
    for it in heals:
        price = max(1, int(it.get('price') or 0))
        can_afford = (money - MONEY_RESERVE) // price
        n = min(want - have - bought, int(it.get('qty') or 1), can_afford)
        if n <= 0:
            continue
        r = buy(it['i'], n)
        if r and r.get('ok'):
            bought += n; spent += int(r.get('total') or 0); money -= int(r.get('total') or 0)
            log(f"  [achat] {n} x « {it.get('name')} » pour {r.get('total')} eddies (reste {money})")
        else:
            log(f"  [achat] echec « {it.get('name')} » : {(r or {}).get('reason')}")
        if have + bought >= want:
            break
    return {'ok': True, 'achetes': bought, 'eddies': spent}


def sell_trip(vendor: dict, stop=None, log=print) -> dict:
    """Va au marchand, ouvre la boutique (F maintenu), vend la camelote (G), valide (F), sort."""
    t0 = time.perf_counter()
    log(f"  [vente] direction marchand « {vendor.get('variant')} » a {vendor['dist']:.0f} m")
    r = nav.goto(lambda: nav.request_path_to(vendor['x'], vendor['y'], vendor.get('z')), arrive_m=2.5,
                 max_legs=8, timeout=240.0, stop=stop, log=log)
    if not r.get('ok'):
        st0 = motion.read_state() or {}
        d0 = math.hypot(vendor['x'] - st0.get('x', 1e9), vendor['y'] - st0.get('y', 1e9)) if st0 else 1e9
        if d0 < 40.0:                                   # boutique hors maillage : on y va tout droit
            log(f'  [vente] pas de chemin ({d0:.0f} m) : marche en ligne droite vers le marchand')
            old = motion.ARRIVE_M; motion.ARRIVE_M = 2.5
            try:
                r = motion.walk_to(vendor['x'], vendor['y'], timeout=30.0, stop=stop)
            finally:
                motion.ARRIVE_M = old
        if not r.get('ok'):
            return {'ok': False, 'reason': f"marchand non atteint ({r.get('reason')})", 'seconds': time.perf_counter() - t0}
    # trouver l invite du marchand : petit balayage du regard
    opened = False
    t1 = time.perf_counter()
    while time.perf_counter() - t1 < 6.0:
        st = motion.read_state()
        inter = (st or {}).get('interact')
        if inter and inter.get('choices'):
            log(f"  [vente] invite « {inter['choices'][0]} » -> F")
            kbm.act('interact', 1.0); opened = True
            break
        kbm.look(120, 0); time.sleep(0.15)
    if not opened:
        return {'ok': False, 'reason': 'aucune invite de marchand', 'seconds': time.perf_counter() - t0}
    # l ecran du marchand s ouvre : on le referme aussitot (Echap), la vente est realisee par la
    # commande Lua `sell` (transaction au prix du jeu avec le marchand present)
    time.sleep(1.5)
    kbm.tap('ESC', 0.1); time.sleep(0.8)
    log('  [vente] marchand atteint')
    return {'ok': True, 'seconds': time.perf_counter() - t0}
