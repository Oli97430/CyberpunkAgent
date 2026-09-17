"""
inventory.py -- competence "gerer son equipement et son inventaire".

Sans passer par le menu (plein ecran, sans blackboard) : le mod liste les objets par
TransactionSystem, equipe par EquipmentSystem, demonte par ItemActionsHelper -- les memes
operations que le menu, sans l interface.

Politique v1, prudente :
  - ARMES : equiper les 3 meilleures armes de MELEE par DPS (V joue melee, couteau Nehan).
    Un objet iconique n est jamais demonte.
  - DEMONTAGE : uniquement les objets de type camelote (Gen_Junk) et les armes communes
    (qualite Common) en surnombre, jamais les objets de quete, jamais les iconiques.
  - Tout est journalise ; aucune vente (necessite un marchand + menu).
"""
from __future__ import annotations

import time

from . import nav

MELEE_TYPES = ('Wea_Knife', 'Wea_Katana', 'Wea_OneHandedClub', 'Wea_TwoHandedClub', 'Wea_Hammer',
               'Wea_LongBlade', 'Wea_ShortBlade', 'Wea_Machete', 'Wea_Axe', 'Wea_Chainsword', 'Wea_Fists')
JUNK_TYPES = ('Gen_Junk',)
BLUNT_TYPES = ('Wea_OneHandedClub', 'Wea_TwoHandedClub', 'Wea_Hammer')   # contondantes : preferees au corps a corps (Olivier)


def melee_score(it: dict) -> float:
    return (it.get('dps') or 0) * (1.25 if (it.get('type') or '') in BLUNT_TYPES else 1.0)
MAX_DISASSEMBLE = 12
equip_attempts: dict = {}      # index d objet -> tentatives d equipement (evite de re-equiper en boucle)
SELLABLE: list = []          # rempli par manage() ; consomme par sell_all() chez un marchand
SELL_VALUE: int = 0          # estimation des eddies a encaisser
MONEY: int = 0               # eddies a la derniere passe
HEALS: int = 99              # soins en stock (MaxDoc + Bounce Back) a la derniere passe manage()


LISTING_SEQ: int = 0         # incremente a chaque `inventory` : les indices `i` d une liste precedente sont PERIMES


def fetch() -> dict | None:
    global LISTING_SEQ
    r = nav._wait(nav._send({'cmd': 'inventory'}), timeout=6.0)
    if r:
        LISTING_SEQ += 1
    return r


def equip(index: int, slot: int) -> bool:
    r = nav._wait(nav._send({'cmd': 'equip', 'x': index, 'y': slot}), timeout=4.0)
    return bool(r and r.get('ok'))


def disassemble(index: int, qty: int = 1) -> bool:
    r = nav._wait(nav._send({'cmd': 'disassemble', 'x': index, 'y': qty}), timeout=4.0)
    return bool(r and r.get('ok'))


EQUIP_BROKEN = False              # vrai des qu une reaffectation d emplacement par script s avere sans effet


def sell_all(log=print) -> dict:
    """Chez un marchand (< 6 m) : vend les objets marques a vendre, un par un (commande Lua sell)."""
    global SELLABLE
    total, n, echecs, streak = 0, 0, 0, 0
    inv = fetch()                      # indices frais
    if not inv or not inv.get('ok'):
        return {'ok': False}
    fresh = {it.get('name'): it for it in (inv.get('items') or [])}
    for it in list(SELLABLE):
        if not str(it.get('name') or '').strip():
            continue                                   # objets sans nom (eclats, elements de quete) : le jeu les refuse
        cur = fresh.get(it.get('name'))
        if not cur or cur.get('equipped') or cur.get('iconic'):
            continue
        r = nav._wait(nav._send({'cmd': 'sell', 'x': cur['i'], 'y': int(cur.get('qty') or 1)}), timeout=5.0)
        if r and r.get('ok'):
            total += int(r.get('total') or 0); n += 1
            log(f"  [vente] « {cur.get('name')} » -> {r.get('total')} eddies")
            inv = fetch(); fresh = {i2.get('name'): i2 for i2 in ((inv or {}).get('items') or [])}
        else:
            # un objet refuse (absent pour le jeu, protege...) n arrete pas la vente des autres (17/09 : 16 objets, 0 vendu)
            echecs += 1; streak += 1
            log(f"  [vente] echec « {cur.get('name')} » : {(r or {}).get('reason')}")
            if streak >= 3:
                log('  [vente] 3 echecs de suite : on arrete la vente ici'); break
            continue
        streak = 0
    SELLABLE = []
    return {'ok': True, 'vendus': n, 'eddies': total, 'echecs': echecs}


def manage(log=print) -> dict:
    global HEALS, SELL_VALUE, MONEY, SELLABLE, EQUIP_BROKEN
    t0 = time.perf_counter()
    inv = fetch()
    if not inv or not inv.get('ok'):
        log('  [inventaire] mod muet ou erreur : ' + str(inv and inv.get('reason')))
        return {'ok': False}
    items = inv.get('items') or []
    MONEY = int(inv.get('money') or 0)
    log(f"  [inventaire] {len(items)} objets, {inv.get('money', '?')} eddies, poids {inv.get('weight', '?')}/{inv.get('carry', '?')}")
    # 0. meilleur emplacement d arme (par DPS) -> c est celui que le combat degainera
    slots = [s for s in (inv.get('slots') or []) if (s.get('dps') or 0) > 0]
    if slots:
        from . import combat
        melee_slots = [s for s in slots if (s.get('type') or '') in MELEE_TYPES]
        ranged_slots = [s for s in slots if (s.get('type') or '').startswith('Wea_') and (s.get('type') or '') not in MELEE_TYPES]
        best = max(melee_slots or slots, key=melee_score)
        combat.MELEE_SLOT = str(best['slot'])
        combat.RANGED_SLOT = str(max(ranged_slots, key=lambda s: s.get('dps') or 0)['slot']) if ranged_slots else None
        log(f"  [inventaire] emplacements : " + ' | '.join(f"{s['slot']}:{s.get('name', '?')} dps {s.get('dps', 0):.0f}" for s in slots)
            + f"  -> melee au {best['slot']} ({best.get('name')})" + (f", distance au {combat.RANGED_SLOT}" if combat.RANGED_SLOT else ''))

    # 1. armes : 2 meilleures de MELEE (emplacements 1-2) + la meilleure A DISTANCE (emplacement 3)
    #    pour les drones, tourelles et cibles hors de portee ; V reste melee par defaut.
    melee = [it for it in items if (it.get('type') or '') in MELEE_TYPES and (it.get('dps') or 0) > 0]
    melee.sort(key=melee_score, reverse=True)          # contondantes favorisees (+25 %)
    ranged = [it for it in items if (it.get('type') or '').startswith('Wea_') and (it.get('type') or '') not in MELEE_TYPES and (it.get('dps') or 0) > 0]
    ranged.sort(key=lambda it: it.get('dps') or 0, reverse=True)
    equipped = 0
    # affectation STABLE : emplacement 1 = meilleure melee, 2 = deuxieme melee, 3 = meilleure arme a feu.
    # On n equipe que si l emplacement ne contient pas deja cet objet (9 re-equipements par session sinon :
    # les 3 melee et l arme a feu se chassaient l une l autre).
    in_slot = {int(sl.get('slot') or 0): str(sl.get('name') or '') for sl in (inv.get('slots') or [])}
    final = dict(in_slot)                      # contenu REEL des emplacements apres reaffectation
    wanted = {}
    for slot, it in enumerate(melee[:2]):
        wanted[slot] = it
    if ranged:
        wanted[2] = ranged[0]
    for slot, it in sorted(wanted.items()):
        if in_slot.get(slot + 1) == str(it.get('name')):
            continue
        if EQUIP_BROKEN:
            continue                                   # la reaffectation par script n a aucun effet dans ce jeu : on n insiste pas
        if equip(it['i'], slot):
            equipped += 1; final[slot + 1] = str(it.get('name'))
            kind = 'arme a feu' if slot == 2 else 'melee'
            log(f"  [inventaire] {kind} « {it.get('name')} » ({it.get('type')}, dps {it.get('dps', 0):.0f}) -> emplacement {slot + 1}")
            time.sleep(0.3)
    from . import combat
    if equipped:
        # VERITE DU JEU : on relit les emplacements apres la reaffectation (17/09 10:07 : equip() repondait ok mais le
        # jeu gardait 1:Mocassin 2:fusil 3:matraque -> le combat tapait 3 pour l arme a feu et tenait la matraque)
        inv2 = fetch()
        real = {int(sl.get('slot') or 0): str(sl.get('name') or '') for sl in ((inv2 or {}).get('slots') or [])} if inv2 and inv2.get('ok') else None
        if real is not None:
            if any(real.get(k) != v for k, v in final.items() if v):
                EQUIP_BROKEN = True
                log('  [inventaire] reaffectation SANS EFFET (le jeu garde : ' + ' | '.join(f'{k}:{v}' for k, v in sorted(real.items())) + ') : on garde les emplacements reels')
            final = dict(real)
    # emplacements a degainer = ceux REELS apres la reaffectation (16/09 : le combat tapait « 2 » pour l arme a feu
    # alors que le fusil venait de passer en 3 et le Mocassin en 2 -> V restait a 26 m avec une matraque, 0 coup)
    m_by_name = {str(it.get('name')): it for it in melee}
    r_by_name = {str(it.get('name')): it for it in ranged}
    m_final = [(s, m_by_name[n]) for s, n in final.items() if n in m_by_name]
    r_final = [(s, r_by_name[n]) for s, n in final.items() if n in r_by_name]
    if m_final:
        combat.MELEE_SLOT = str(max(m_final, key=lambda p: melee_score(p[1]))[0])
    if r_final:
        combat.RANGED_SLOT = str(max(r_final, key=lambda p: p[1].get('dps') or 0)[0])
    elif combat.RANGED_SLOT is not None and final.get(int(combat.RANGED_SLOT)) in m_by_name:
        combat.RANGED_SLOT = None                 # l ancien emplacement « distance » tient maintenant une melee
    if ranged and combat.RANGED_SLOT is None and final.get(3) not in m_by_name:
        combat.RANGED_SLOT = '3'
    if equipped:
        log(f"  [inventaire] apres reaffectation : melee au {combat.MELEE_SLOT}" + (f", distance au {combat.RANGED_SLOT}" if combat.RANGED_SLOT else ', pas d arme a feu'))
    top_idx ={it['i'] for it in melee[:2]} | ({ranged[0]['i']} if ranged else set())

    # 1b. vetements : dans chaque emplacement (tete, visage, torse int/ext, jambes, pieds), le meilleur
    #     par armure puis qualite. Un objet iconique/de quete n est jamais demonte, juste compare.
    QRANK = {'Legendary': 5, 'Epic': 4, 'Rare': 3, 'Uncommon': 2, 'Common': 1}
    worn = inv.get('worn') or {}
    if worn:
        log('  [inventaire] porte : ' + ', '.join(f'{k}={v}' for k, v in worn.items()))
    for area in ('Feet', 'Legs', 'InnerChest', 'OuterChest', 'Head', 'Face'):
        if isinstance(worn, dict) and worn and area not in worn:
            log(f'  [inventaire] RIEN de porte en {area} (V est nu a cet endroit) : on habille')
    clo_types = sorted({it.get('type') for it in items if (it.get('type') or '').startswith('Clo_') and it.get('type') != 'Clo_Outfit'})
    for ct in clo_types:
        cands = [it for it in items if it.get('type') == ct]
        cands.sort(key=lambda it: (QRANK.get(str(it.get('quality')), 0), (it.get('armor') or 0), (it.get('price') or 0)), reverse=True)
        best = cands[0]
        if best.get('equipped'):
            continue
        cur = next((it for it in cands if it.get('equipped')), None)
        if cur and (cur.get('armor') or 0) >= (best.get('armor') or 0) and QRANK.get(str(cur.get('quality')), 0) >= QRANK.get(str(best.get('quality')), 0):
            continue
        if equip_attempts.get(best['i'], 0) >= 2:          # deja tente deux fois sans que le jeu le montre porte
            continue
        equip_attempts[best['i']] = equip_attempts.get(best['i'], 0) + 1
        if equip(best['i'], 0):
            equipped += 1
            log(f"  [inventaire] vetement « {best.get('name')} » ({ct}, armure {best.get('armor') or 0:.0f}, {best.get('quality')}) equipe")
            time.sleep(0.3)

    # 2. demontage : camelote, armes de melee communes en surnombre, et armes-poubelle
    #    (communes, non iconiques, non equipees, DPS < 60 : Unity/Liberty/Copperhead en double...)
    #    Jamais un iconique, un objet de quete, un objet equipe.
    junk = [it for it in items if (it.get('type') or '') in JUNK_TYPES and not it.get('quest')]
    spare = [it for it in melee[2:] if (it.get('quality') or '') == 'Common' and not it.get('iconic')
             and not it.get('quest') and not it.get('equipped') and it['i'] not in top_idx]
    trash = [it for it in items if (it.get('type') or '').startswith('Wea_') and (it.get('type') or '') not in MELEE_TYPES
             and (it.get('quality') or '') == 'Common' and not it.get('iconic') and not it.get('quest')
             and not it.get('equipped') and 0 < (it.get('dps') or 0) < 60]
    spare = spare + trash
    w, cap = inv.get('weight') or 0, inv.get('carry') or 0
    if cap and w > 0.9 * cap:
        best_dps = max([it.get('dps') or 0 for it in items] or [1])
        heavy = [it for it in items if (it.get('type') or '').startswith('Wea_') and not it.get('iconic') and not it.get('quest')
                 and not it.get('equipped') and it['i'] not in top_idx and (it.get('dps') or 0) < 0.8 * best_dps]
        spare = spare + [it for it in heavy if it not in spare]
        log(f'  [inventaire] surcharge ({w:.0f}/{cap:.0f}) : demontage elargi')
    dis = 0
    for it in (junk + spare)[:MAX_DISASSEMBLE]:
        if disassemble(it['i'], int(it.get('qty') or 1)):
            dis += 1
            log(f"  [inventaire] demonte « {it.get('name')} » x{int(it.get('qty') or 1)}")
            time.sleep(0.2)
    # 2b. objets A VENDRE : armes/vetements non iconiques, non equipes, hors quete, qualite <= Rare,
    #     qui ne sont ni dans le top ni demontes. Vendus quand un marchand est a portee (brain).
    QR = {'Legendary': 5, 'Epic': 4, 'Rare': 3, 'Uncommon': 2, 'Common': 1}
    dis_idx = {it['i'] for it in (junk + spare)[:MAX_DISASSEMBLE]}
    global SELLABLE
    # MAXIMUM DE FRIC : tout ce qui ne sert pas part a la vente, quelle que soit la qualite : armes hors top 3,
    # vetements non portes hors meilleur par zone, mods d armes, babioles (Gen_Misc). Jamais un iconique, un
    # objet de quete, un objet porte. La camelote (Gen_Junk) est demontee (composants pour le craft).
    best_clo = set()
    for ct in clo_types:
        cc = sorted([it for it in items if it.get('type') == ct], key=lambda it: (QRANK.get(str(it.get('quality')), 0), it.get('price') or 0), reverse=True)
        if cc:
            best_clo.add(cc[0]['i'])
    SELLABLE = [it for it in items if (((it.get('type') or '').startswith('Wea_') and it['i'] not in top_idx)
                                       or ((it.get('type') or '').startswith('Clo_') and it['i'] not in best_clo)
                                       or (it.get('type') or '').startswith('Prt_')
                                       or (it.get('type') or '') == 'Gen_Misc')
                and not it.get('iconic') and not it.get('quest') and not it.get('equipped')
                and str(it.get('name') or '').strip() and it['i'] not in dis_idx]
    # valeur estimee : prix du jeu s il est connu (le stat Price est souvent nul), sinon par qualite
    QVAL = {'Legendary': 2500, 'Epic': 1000, 'Rare': 400, 'Uncommon': 150, 'Common': 50}
    def _val(it):
        p = it.get('price') or 0
        return (p * 0.15) if p > 0 else QVAL.get(str(it.get('quality')), 30) * (0.3 if ((it.get('type') or '') == 'Gen_Misc' or (it.get('type') or '').startswith('Prt_')) else 1.0)
    SELL_VALUE = int(sum(_val(it) * int(it.get('qty') or 1) for it in SELLABLE))
    if SELLABLE:
        log(f'  [inventaire] {len(SELLABLE)} objet(s) a vendre au prochain marchand (~{SELL_VALUE} eddies)')

    # 2c. ECLATS et livres de competence : on les lit (XP, eddies, infos) -> commande use
    readables = [it for it in items if (it.get('type') or '') in ('Con_Skillbook', 'Gen_Readable', 'Gen_Shard')
                 or 'shard' in str(it.get('name') or '').lower() or 'éclat' in str(it.get('name') or '').lower() or 'eclat' in str(it.get('name') or '').lower()]
    for it in readables[:6]:
        r = nav._wait(nav._send({'cmd': 'use', 'x': it['i']}), timeout=3.0)
        if r and r.get('ok'):
            log(f"  [inventaire] lit « {it.get('name')} »")
            time.sleep(0.3)

    # 3. craft : soins / grenades manquants (recettes connues et faisables)
    crafted = 0
    try:
        from . import crafting
        HEALS = crafting.heal_stock(items)
        cr = crafting.manage(items, log=log)
        crafted = cr.get('fabriques', 0)
        if HEALS < 2:
            log(f'  [inventaire] soins bas ({HEALS}) : achat au prochain marchand si le craft n a pas suffi')
    except Exception as e:
        log(f'  [craft] erreur : {e}')
    # indices perimes apres demontage : la prochaine passe refait inventory
    return {'ok': True, 'objets': len(items), 'equipes': equipped, 'demontes': dis, 'fabriques': crafted, 'seconds': time.perf_counter() - t0}
