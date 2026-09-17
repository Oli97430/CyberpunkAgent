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

import json
from . import input_kbm as kbm, motion, nav
from .config import CFG

MIN_SELLABLE = 8
MAX_VENDOR_M = 250.0
HEAL_WANT = 6               # soins souhaites apres passage chez un marchand
MONEY_RESERVE = 300         # eddies que V garde toujours
HEAL_WORDS = ('maxdoc', 'bounce', 'soin', 'inhal', 'inject')


def list_vendors() -> list[dict]:
    r = nav._wait(nav._send({'cmd': 'list_vendors'}), timeout=4.0)
    return (r or {}).get('vendors') or []


_failed: dict[tuple, float] = {}     # (x,y arrondis) -> heure de l echec (marchand injoignable : boutique hors maillage...)
_fail_n: dict[tuple, int] = {}
SKIP_FILE = CFG.log_file.parent / 'vendors_skip.json'   # marchands INUTILES, memorises d une session a l autre
_skip: dict[str, dict] = {}
try:
    _skip = json.loads(SKIP_FILE.read_text(encoding='utf-8')) if SKIP_FILE.exists() else {}
except Exception:
    _skip = {}


def _key(v: dict) -> str:
    return f"{round(v.get('x', 0))},{round(v.get('y', 0))}"


def is_skipped(v: dict) -> bool:
    e = _skip.get(_key(v))
    return bool(e) and time.time() - float(e.get('t', 0)) < float(e.get('days', 7)) * 86400.0


def mark_useless(v: dict, reason: str, days: float = 7.0, log=print) -> None:
    """Marchand qui ne sert a rien (ne parle pas, rien en stock, rien a echanger) : V n y retourne pas pendant `days` jours."""
    _skip[_key(v)] = {'t': time.time(), 'days': days, 'name': v.get('name') or v.get('variant'), 'reason': reason}
    try:
        SKIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIP_FILE.write_text(json.dumps(_skip, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception as e:
        log(f'  [marchand] memoire non sauvee : {e}')
    log(f"  [marchand] « {v.get('name') or v.get('variant')} » ecarte {days:g} jour(s) : {reason}")


def mark_failed(v: dict, log=print) -> None:
    k = (round(v.get('x', 0)), round(v.get('y', 0)))
    _failed[k] = time.perf_counter()
    _fail_n[k] = _fail_n.get(k, 0) + 1
    if _fail_n[k] >= 2:
        mark_useless(v, 'injoignable deux fois', days=1.0, log=log)


def pick_vendor(vendors: list[dict], prefer: str | None = None) -> dict | None:
    if not vendors:
        return None
    near = [v for v in vendors if v.get('dist', 1e9) <= MAX_VENDOR_M and (prefer == 'ripper' or 'ripper' not in (v.get('variant') or '').lower())
            and not any(w in (v.get('variant') or '').lower() for w in ('apartment', 'wardrobe'))
            and time.perf_counter() - _failed.get((round(v.get('x', 0)), round(v.get('y', 0))), -1e9) > 900.0
            and not is_skipped(v)]
    if not near:
        return None
    if prefer:
        pref = [v for v in near if prefer in (v.get('variant') or '').lower()]
        if pref:
            return min(pref, key=lambda v: v['dist'])
    return min(near, key=lambda v: v['dist'])


def vendor_stock() -> dict | None:
    """Stock du marchand present. Garde-fou : si le mod a pris V lui-meme pour le marchand (vieux mod : le
    joueur n etait pas exclu de la recherche), on refuse -> sinon « vendre » donnait de l argent a V pour
    ses propres objets."""
    r = nav._wait(nav._send({'cmd': 'vendor_stock'}), timeout=6.0)
    if r and r.get('ok'):
        from . import inventory
        name = str(r.get('vendor') or '').strip()
        n_items = len(r.get('items') or [])
        inv = inventory.fetch() or {}
        if name in ('V', '') or n_items == len(inv.get('items') or []):
            return {'ok': False, 'reason': f'le « marchand » est V lui-meme ({name or "?"}, {n_items} articles) : recharge le mod'}
    return r


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
        price = int(it.get('price') or 0)
        if price <= 0:
            continue                                    # prix inconnu : on n achete pas a l aveugle
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


CYBER_PRIORITY = ('arms', 'mantis', 'gorilla', 'strongarms', 'sandevistan', 'berserk', 'systemreplacement', 'os',
                  'skeleton', 'musculoskeletal', 'integumentary', 'subdermal', 'nervous', 'kerenzikov', 'cardiovascular',
                  'biomonitor', 'immune', 'legs', 'frontalcortex', 'hands', 'eyes', 'kiroshi')
CYBER_RESERVE = 2000         # eddies que V garde apres le charcudoc
QR = {'Legendary': 5, 'Epic': 4, 'Rare': 3, 'Uncommon': 2, 'Common': 1}


def _cyber_score(it: dict) -> tuple:
    t = (str(it.get('type') or '') + ' ' + str(it.get('name') or '')).lower().replace('_', '')
    prio = next((len(CYBER_PRIORITY) - i for i, w in enumerate(CYBER_PRIORITY) if w in t), 0)
    return (QR.get(str(it.get('quality')), 0), prio, -(int(it.get('price') or 0)))


def ripperdoc_shop(log=print, max_buys: int = 3) -> dict:
    """Chez le charcudoc present (< 6 m) : achete et POSE le meilleur cyberware abordable (qualite d abord,
    puis priorite du build melee), en gardant CYBER_RESERVE eddies. Un implant qui ne se pose pas
    (capacite insuffisante, emplacement incompatible) est revendu aussitot au meme prix."""
    from . import inventory
    stock = vendor_stock()
    if not stock or not stock.get('ok'):
        return {'ok': False, 'reason': (stock or {}).get('reason', 'stock illisible')}
    money = int(stock.get('money') or 0)
    cyber = [it for it in (stock.get('items') or [])
             if (str(it.get('type') or '').startswith('Cyb') or 'cyberware' in str(it.get('type') or '').lower())]
    if not cyber:
        log(f"  [charcudoc] « {stock.get('vendor')} » : aucun cyberware en stock ({len(stock.get('items') or [])} articles)")
        return {'ok': True, 'poses': 0, 'useless': f"aucun cyberware en stock ({stock.get('vendor')})"}
    cyber.sort(key=_cyber_score, reverse=True)
    posed, spent, tried = 0, 0, 0
    for it in cyber:
        price = int(it.get('price') or 0)
        if tried >= max_buys or posed >= 2:
            break
        if price <= 0 or money - price < CYBER_RESERVE:
            continue
        tried += 1
        r = buy(it['i'], 1)
        if not (r and r.get('ok')):
            log(f"  [charcudoc] achat « {it.get('name')} » refuse : {(r or {}).get('reason')}")
            continue
        money -= int(r.get('total') or price)
        # retrouver l implant dans l inventaire (liste fraiche) et le poser
        inv = inventory.fetch() or {}
        mine = next((x for x in (inv.get('items') or []) if str(x.get('name')) == str(it.get('name')) and not x.get('equipped')), None)
        if not mine:
            log(f"  [charcudoc] « {it.get('name')} » achete mais introuvable dans l inventaire"); continue
        er = nav._wait(nav._send({'cmd': 'equip', 'x': mine['i'], 'y': 0}), timeout=6.0)
        if er and er.get('ok') and er.get('worn'):
            posed += 1; spent += int(r.get('total') or price)
            log(f"  [charcudoc] POSE « {it.get('name')} » ({it.get('quality')}) pour {r.get('total')} eddies [{er.get('method')}]")
        elif er is None:
            log(f"  [charcudoc] « {it.get('name')} » : pas de reponse du mod, implant conserve (peut-etre pose)")
            spent += int(r.get('total') or price)
        else:
            # ne se pose pas (capacite cyberware, emplacement occupe...) : on le GARDE (un marchand rachete a
            # une fraction du prix : la revente immediate perdait ~80 %), il servira plus tard
            spent += int(r.get('total') or price)
            log(f"  [charcudoc] « {it.get('name')} » ne se pose pas ({(er or {}).get('method')}) : conserve dans l inventaire")
    return {'ok': True, 'poses': posed, 'eddies': spent, 'reste': money}


def buy_supplies(items: list[dict], log=print) -> dict:
    """Chez le marchand present : grenades (jusqu a 4) et munitions de l arme a feu (jusqu a ~120 cartouches),
    les moins cheres d abord, en gardant MONEY_RESERVE eddies."""
    stock = vendor_stock()
    if not stock or not stock.get('ok'):
        return {'ok': False}
    money = int(stock.get('money') or 0)
    have_gren = sum(int(it.get('qty') or 1) for it in items if (it.get('type') or '') == 'Gad_Grenade')
    have_ammo = sum(int(it.get('qty') or 1) for it in items if (it.get('type') or '') == 'Con_Ammo')
    wants = []
    if have_gren < 4:
        wants.append(('Gad_Grenade', 4 - have_gren, 'grenade'))
    if have_ammo < 120:
        wants.append(('Con_Ammo', 2, 'munitions'))          # 2 lots
    bought = []
    for typ, n, label in wants:
        cands = sorted([it for it in (stock.get('items') or []) if (it.get('type') or '') == typ], key=lambda it: int(it.get('price') or 1e9))
        for it in cands:
            if n <= 0:
                break
            price = int(it.get('price') or 0)
            if price <= 0:
                continue
            k = min(n, int(it.get('qty') or 1), (money - MONEY_RESERVE) // price)
            if k <= 0:
                continue
            r = buy(it['i'], k)
            if r and r.get('ok'):
                n -= k; money -= int(r.get('total') or 0); bought.append(f"{k} x {it.get('name')}")
                log(f"  [achat] {label} : {k} x « {it.get('name')} » pour {r.get('total')} eddies")
    return {'ok': True, 'achats': bought, 'reste': money}


RECIPE_WORDS = ('plan', 'schéma', 'schema', 'recette', 'spec', 'blueprint', 'recipe')


def buy_recipes(items: list[dict], log=print, max_buys: int = 2) -> dict:
    """Plans de craft en vente chez le marchand present : achat (<= 20 % de la fortune, reserve gardee) puis
    apprentissage (commande use). Les plans deja connus (noms des recettes) sont ignores."""
    from .config import CFG
    if not CFG.features.get('recipes', True):
        return {'ok': True, 'appris': []}
    stock = vendor_stock()
    if not stock or not stock.get('ok'):
        return {'ok': False}
    from . import crafting, inventory
    known = {str(r.get('name') or '').lower() for r in crafting.recipes()}
    money = int(stock.get('money') or 0)
    cands = [it for it in (stock.get('items') or [])
             if ('recipe' in str(it.get('type') or '').lower() or 'spec' in str(it.get('type') or '').lower()
                 or any(w in str(it.get('name') or '').lower() for w in RECIPE_WORDS))
             and not any(str(it.get('name') or '').lower().replace('plan : ', '').replace('plan: ', '') in k or k in str(it.get('name') or '').lower() for k in known if k)]
    cands.sort(key=lambda it: -QR.get(str(it.get('quality')), 0))
    learned = []
    for it in cands[:max_buys]:
        price = int(it.get('price') or 0)
        if price <= 0 or price > money * 0.2 or money - price < MONEY_RESERVE:
            continue
        r = buy(it['i'], 1)
        if not (r and r.get('ok')):
            continue
        money -= int(r.get('total') or price)
        inv = inventory.fetch() or {}
        mine = next((x for x in (inv.get('items') or []) if str(x.get('name')) == str(it.get('name'))), None)
        if mine:
            u = nav._wait(nav._send({'cmd': 'use', 'x': mine['i']}), timeout=4.0)
            if u and u.get('ok'):
                learned.append(str(it.get('name')))
                log(f"  [achat] plan appris : « {it.get('name')} » ({it.get('quality')}) pour {r.get('total')} eddies")
    return {'ok': True, 'appris': learned, 'reste': money}


def sell_trip(vendor: dict, stop=None, log=print) -> dict:
    """Va au marchand, ouvre la boutique (F maintenu), vend la camelote (G), valide (F), sort."""
    t0 = time.perf_counter()
    log(f"  [vente] direction marchand « {vendor.get('variant')} » a {vendor['dist']:.0f} m")
    if vendor['dist'] > 300.0 and kbm.ACTIONS.get('autodrive'):
        from . import driving
        rd = driving.drive_to(vendor['x'], vendor['y'], stop=stop, log=log)
        log(f"  [vente] en vehicule : {'arrive' if rd.get('ok') else rd.get('reason')}")
    r = nav.goto(lambda: nav.request_path_to(vendor['x'], vendor['y'], vendor.get('z')), arrive_m=2.5,
                 max_legs=8, timeout=240.0, stop=stop, log=log)
    if not r.get('ok'):
        st0 = motion.read_state() or {}
        d0 = math.hypot(vendor['x'] - st0.get('x', 1e9), vendor['y'] - st0.get('y', 1e9)) if st0 else 1e9
        if d0 < 40.0:                                   # boutique hors maillage ou bloque pres du but : on y va tout droit
            log(f"  [vente] {r.get('reason')} a {d0:.0f} m : marche en ligne droite vers le marchand")
            old = motion.ARRIVE_M; motion.ARRIVE_M = 2.5
            try:
                r = motion.walk_to(vendor['x'], vendor['y'], timeout=30.0, stop=stop)
            finally:
                motion.ARRIVE_M = old
        if not r.get('ok') and d0 >= 40.0:
            # quartier sans maillage (Kabuki : 12 h d echecs) : voyage rapide vers le point le plus proche du marchand
            ft = nav.fast_travel_to(vendor['x'], vendor['y'], log=log, min_gain_m=150.0)
            log(f"  [vente] voyage rapide : {('arrive a ' + str(ft.get('point'))) if ft.get('ok') else ft.get('reason')}")
            if ft.get('ok'):
                r = nav.goto(lambda: nav.request_path_to(vendor['x'], vendor['y'], vendor.get('z')), arrive_m=2.5, max_legs=8, timeout=180.0, stop=stop, log=log)
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
        # pas d invite lisible (charcudoc : c est une scene, pas un menu) : si le marchand est a portee du
        # script (< 6 m), la transaction par script suffit, on n a pas besoin de son ecran
        st1 = motion.read_state() or {}
        d1 = math.hypot(vendor['x'] - st1.get('x', 1e9), vendor['y'] - st1.get('y', 1e9)) if st1 else 1e9
        if 6.0 < d1 < 40.0:                             # encore trop loin pour le script (6 m) : on s approche tout droit
            log(f'  [vente] marchand a {d1:.0f} m : on s approche en ligne droite')
            old_a = motion.ARRIVE_M; motion.ARRIVE_M = 2.5
            try:
                motion.walk_to(vendor['x'], vendor['y'], timeout=15.0, stop=stop)
            finally:
                motion.ARRIVE_M = old_a
        probe = vendor_stock()
        if probe and probe.get('ok'):
            log(f"  [vente] pas d invite, mais « {probe.get('vendor')} » est a portee ({len(probe.get('items') or [])} articles) : transactions par script")
            return {'ok': True, 'seconds': time.perf_counter() - t0, 'script_only': True}
        return {'ok': False, 'reason': 'aucune invite de marchand', 'seconds': time.perf_counter() - t0}
    # l ecran du marchand s ouvre : on le referme aussitot (Echap), la vente est realisee par la
    # commande Lua `sell` (transaction au prix du jeu avec le marchand present)
    time.sleep(1.5)
    kbm.tap('ESC', 0.1); time.sleep(0.8)
    log('  [vente] marchand atteint')
    return {'ok': True, 'seconds': time.perf_counter() - t0}
