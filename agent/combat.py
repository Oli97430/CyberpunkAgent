"""
combat.py -- competence "combattre et se soigner", MELEE + GRENADES + POUVOIRS, v2 mobile.

Entrees (mod CET, 20 Hz) : state.hp (%), state.combat, state.enemies = 6 hostiles les plus
proches {x,y,z,d, sx,sy (projection ecran NORMALISEE ~[-1,1]), dead}.
Touches lues dans les mappings du jeu : attaque = clic gauche (maintenu = attaque chargee),
parade = clic droit, GRENADE = clic milieu, soin = X, cyberware iconique = E, quick melee = Q,
saut = Espace, accroupi/glissade = C, sprint = Maj gauche, esquive = double appui direction.

Bilan v1 (2026-09-11 06:10) : 94 coups, 47 quick melee, 0 grenade, 0 esquive, mort en 85 s
contre 6 hostiles -> V restait plante. v2 : machine a etats avec REPLI, STRAFE, ESQUIVE,
attaque CHARGEE, GLISSADE d approche, FRAPPE SAUTEE, grenade sur l ennemi ELOIGNE.
"""
from __future__ import annotations

import math
import random
import time

from . import input_kbm as kbm, motion, nav

HEAL_BELOW, HEAL_CD = 45.0, 5.0
RETREAT_HP, RETREAT_PRESSURE, RETREAT_S = 35.0, 2, 2.5
RETREAT_COOLDOWN = 10.0         # pas deux replis a moins de 10 s : 25 replis/combat = V fuyait au lieu de frapper
OUTNUMBERED = 4                 # a partir de 4 hostiles : decrocher plutot que tenir
GRENADE_MIN_M, GRENADE_MAX_M, GRENADE_CD = 11.0, 28.0, 20.0
MELEE_RANGE = 2.4
MELEE_SLOT = '3'                          # fixe par inventory.manage() : emplacement au meilleur DPS de melee
RANGED_SLOT = None                        # fixe par inventory.manage() si une arme a distance est equipee
RANGED_MIN_M, RANGED_MAX_M = 12.0, 45.0   # on passe a l arme a feu au-dela de 12 m (ou cible en hauteur) et on revient
RANGED_BACK_M = 7.0                       # au corps a corps sous 7 m : au CAC, c est le katana (Olivier, 13/09)
CYBERWARE_CD = 18.0                       # touche : kbm.K('iconic') (F chez ce joueur)
# quick melee : kbm.K('quickmelee') (~ chez ce joueur)
QUICKHACK_CD = 4.0
KNIFE_CD = 4.0
HACK_ROTATION = (0, 1, 2)     # on alterne les 3 premiers hacks du panneau (le meilleur n est pas forcement le 1er)
DODGE_CD, STRAFE_CD, JUMP_ATTACK_CD, SLIDE_CD = 1.4, 1.6, 6.0, 8.0   # esquive/dash toutes les 1,4 s max (Olivier : « plus d esquives »)


def quickhack_first(slot: int = 0) -> None:
    """Scanner (Tab maintenu) -> panneau sur la cible visee -> molette pour descendre de `slot`
    crans -> F applique. Fermeture par RETOUR ARRIERE (CloseQuickHackPanel), JAMAIS Echap :
    Echap ouvrait le menu pause quand le panneau n etait pas la (bilan 06:39)."""
    kbm.act_hold('scanner'); time.sleep(0.45)
    kbm.act('ui_confirm', 0.1); time.sleep(0.35)
    for _ in range(slot):
        kbm.wheel(-1); time.sleep(0.12)
    kbm.act('ui_confirm', 0.1); time.sleep(0.25)
    kbm.act_release('scanner'); time.sleep(0.15)
    kbm.tap('BACKSPACE', 0.08)


# Priorite des hacks, du plus decisif au moins. Chaque rang : identifiants TweakDB (ex. QuickHack.EMPOverloadLvl2Hack,
# independants de la langue) + titres francais du jeu. 24/09 : court-circuit s appelle EMPOverload et surcharge
# synaptique BrainMelt dans le TweakDB -- sans eux, Ping ou Redemarrage optique passaient devant.
HACK_PRIORITY = (('cyberpsycho', 'madness'), ('suicide',), ('systemcollapse', 'systemreset', 'reinitialisation'),
                 ('overheat', 'surchauffe'), ('shortcircuit', 'empoverload', 'courtcircuit'), ('contagion',),
                 ('synapse', 'brainmelt', 'synaptique'), ('detonate', 'grenadeexplode', 'grenade'),
                 ('weaponmalfunction', 'malfunction', 'defaillance', 'enrayage'), ('cripple', 'locomotion', 'paralysie'),
                 ('rebootoptics', 'blind', 'redemarrage'), ('memorywipe', 'effacement'), ('whistle', 'sifflement'),
                 ('ping',))


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii').lower()
    return ''.join(c for c in s if c.isalnum())


def _hack_score(h: dict) -> int:
    t = _norm(h.get('action') or '') + ' ' + _norm(h.get('title') or '')
    for rank, kws in enumerate(HACK_PRIORITY):
        if any(kw in t for kw in kws):
            return len(HACK_PRIORITY) - rank
    return 0


_min_cost = [None, -1e9]   # (cout du hack le moins cher du dernier panneau lisible, heure) : RAM trop basse -> on n ouvre pas


def _readable(h: dict) -> bool:
    a, t = str(h.get('action') or ''), str(h.get('title') or '')
    return bool((a and not a.startswith('userdata')) or (t and not t.startswith('userdata')))


def quickhack_best(log=print) -> str | None:
    """Ouvre le panneau sur la cible visee, lit la liste (blackboard), choisit le meilleur hack
    utilisable (non verrouille, RAM suffisante), fait defiler jusqu a ce qu il soit SURLIGNE
    (boucle fermee sur qh.sel), applique (F), ferme (Retour arriere). Renvoie le titre applique.
    24/09 : liste REELLEMENT affichee (nom, cout, verrouille) ; rien d utilisable -> panneau referme sans hacker au
    hasard, renvoie 'ram' (RAM connue trop basse : on n ouvre meme pas) ; surlignage illisible -> rang dans la liste."""
    ram0 = (motion.read_state() or {}).get('ram')
    if ram0 is not None and _min_cost[0] is not None and ram0 < _min_cost[0] and time.perf_counter() - _min_cost[1] < 30.0:
        return 'ram'
    kbm.act_hold('scanner'); time.sleep(0.45)
    kbm.act('ui_confirm', 0.1)
    qh = None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.2:
        st = motion.read_state()
        qh = (st or {}).get('qh')
        if qh and qh.get('open') and qh.get('list'):
            break
        time.sleep(0.05)
    chosen = None
    readable = [h for h in ((qh or {}).get('list') or []) if _readable(h)]
    if readable:
        ram = qh.get('ram')
        # toute la liste : un hack trop cher en RAM est justement « verrouille », et les verrous dependent de la cible
        costs = [h['cost'] for h in readable if isinstance(h.get('cost'), (int, float)) and h['cost'] > 0]
        _min_cost[0], _min_cost[1] = (min(costs) if costs else None), time.perf_counter()
        usable = [h for h in readable if not h.get('locked') and (ram is None or (h.get('cost') or 0) <= ram)]
        if not usable:
            log(f"  [combat] quickhacks : rien d utilisable (RAM {ram}, {len(readable)} hack(s) lus) : panneau referme")
            kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
            return 'ram'
        chosen = max(usable, key=_hack_score)
    if chosen is None:
        # liste indisponible : hack surligne par defaut (comportement a l aveugle)
        kbm.act('ui_confirm', 0.1); time.sleep(0.25)
        kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
        return None
    # amener la selection sur le hack choisi (molette) : sens choisi d apres le rang du hack surligne
    want = str(chosen.get('action') or '').strip().lower()
    target_i = int(chosen.get('i') or 1)
    rank_of = {str(h.get('action') or '').strip().lower(): int(h.get('i') or 0) for h in readable if h.get('action')}
    stepped, matched = 0, False
    for _ in range(len(readable) + 2):
        st = motion.read_state()
        sel = str(((st or {}).get('qh') or {}).get('sel') or '')
        if want and sel.strip().lower() == want:
            matched = True; break
        if not sel or sel.startswith('userdata'):
            # surlignage illisible : on descend jusqu au rang du hack (le panneau s ouvre sur le premier)
            for _ in range(max(0, target_i - 1 - stepped)):
                kbm.wheel(-1); time.sleep(0.12)
            matched = True; break
        cur_i = rank_of.get(sel.strip().lower())
        kbm.wheel(1 if (cur_i is not None and cur_i > target_i) else -1); stepped += 1; time.sleep(0.12)
    if not matched:
        # jamais surligne (hack absent du panneau...) : on referme SANS valider plutot que lancer un autre hack
        log(f"  [combat] quickhack « {chosen.get('title') or chosen.get('action')} » jamais surligne : panneau referme sans hacker")
        kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
        return None
    kbm.act('ui_confirm', 0.1); time.sleep(0.25)
    kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
    return chosen.get('title') or chosen.get('action')


def knife_throw() -> None:
    """Couteau (Nehan = arme active relevee) : viser (clic droit maintenu) + clic gauche = lancer."""
    kbm.mouse('right', True); time.sleep(0.35)
    kbm.mouse_tap('left', 0.1); time.sleep(0.15)
    kbm.mouse('right', False)


def charges(st: dict, kind: str) -> bool:
    """24/09 : soin / grenade seulement s il reste une charge (le mod exporte res.heal / res.gren ; inconnu = on
    essaie, comme avant). 13:29-13:30 : 3 soins appuyes a vide a 40-44 % de vie, puis mort."""
    v = ((st or {}).get('res') or {}).get(kind)
    return not isinstance(v, (int, float)) or v > 0


def n_foes(st: dict, alive: list) -> int:
    """Vrai nombre d hostiles vivants : la liste exportee est plafonnee a 6, le mod exporte aussi le total (foes.n,
    police a part dans foes.np : en combat elle compte, c est de la legitime defense)."""
    f = (st or {}).get('foes') or {}
    try:
        return max(len(alive), int(f.get('n') or 0) + int(f.get('np') or 0))
    except (TypeError, ValueError):
        return len(alive)


def _alive(enemies, allow_police: bool = True):
    """Hostiles vivants. allow_police=False exclut la police (engagement proactif interdit).
    En combat (fight), la police reste une cible : c est de la legitime defense."""
    return [e for e in (enemies or []) if not e.get('dead') and not e.get('down') and (allow_police or not e.get('police'))]


# ---- visee --------------------------------------------------------------------------
_vsign = [1.0]
_last_sy = [None]


def aim_bearing(target_yaw: float, st: dict, gain: float = 0.5) -> float:
    err = motion.wrap(target_yaw - st['yaw'])
    dx = max(-motion.MAX_STEP, min(motion.MAX_STEP, motion.SIGN * err * motion.K_COUNTS_PER_DEG * gain))
    kbm.look(dx, 0)
    return abs(err)


def aim_at(e: dict, st: dict) -> float:
    """Horizontal par cap (maths validees), vertical sur sy normalise (signe appris).
    Renvoie l ecart de cap en degres."""
    err = motion.wrap(motion.bearing_to(st['x'], st['y'], e['x'], e['y']) - st['yaw'])
    dx = max(-motion.MAX_STEP, min(motion.MAX_STEP, motion.SIGN * err * motion.K_COUNTS_PER_DEG * 0.5))
    dy = 0.0
    sy = e.get('sy')
    if sy is not None and abs(sy) < 1.5:
        if _last_sy[0] is not None and abs(sy) > abs(_last_sy[0]) + 0.05:
            _vsign[0] = -_vsign[0]
        _last_sy[0] = sy
        dy = max(-120.0, min(120.0, _vsign[0] * sy * 400.0))
    kbm.look(dx, dy)
    return abs(err)


# ---- micro-mouvements ---------------------------------------------------------------
def dodge(side: str) -> None:
    """Esquive : touche dodgeDash du joueur (Ctrl gauche) en tenant la direction ; a defaut,
    double appui direction (reglage par defaut du jeu)."""
    if kbm.ACTIONS.get('dodge'):
        kbm.hold(side); time.sleep(0.05); kbm.act('dodge', 0.1); time.sleep(0.15); kbm.release(side)
    else:
        kbm.tap(side, 0.07); time.sleep(0.07); kbm.tap(side, 0.07)


def sprint_pass(e: dict, st: dict, side_sign: float, dur: float = 0.8) -> None:
    """Passe en SPRINT de biais (75 degres) autour de la cible : V regenere 60 % plus vite en sprintant (perk).
    On tourne d abord (boucle fermee sur le cap, 0,5 s max), puis W + sprint pendant dur, puis on relache."""
    want = motion.wrap(motion.bearing_to(st['x'], st['y'], e['x'], e['y']) + side_sign * 75.0)
    t_turn = time.perf_counter()
    s_t = st
    while time.perf_counter() - t_turn < 0.5:
        if aim_bearing(want, s_t, gain=0.8) < 12.0:
            break
        time.sleep(0.04)
        s_t = motion.read_state() or s_t
    kbm.hold('W'); kbm.act_hold('sprint'); time.sleep(dur)
    kbm.act_release('sprint'); kbm.release('W')


def strafe(side: str, dur: float = 0.4) -> None:
    kbm.hold(side); time.sleep(dur); kbm.release(side)


def heavy_attack() -> None:
    kbm.mouse('left', True); time.sleep(0.55); kbm.mouse('left', False)


def light_attack() -> None:
    kbm.mouse_tap('left', 0.09)


def block(dur: float = 0.4) -> None:
    kbm.mouse('right', True); time.sleep(dur); kbm.mouse('right', False)


def jump_attack() -> None:
    kbm.act('jump', 0.1); time.sleep(0.25); light_attack()


def slide() -> None:
    """Glissade d approche : sprint deja tenu, un appui accroupi."""
    kbm.act('crouch', 0.1)


def centroid(enemies) -> tuple[float, float]:
    return (sum(e['x'] for e in enemies) / len(enemies), sum(e['y'] for e in enemies) / len(enemies))


LAST_CALIB = {'t': -1e9, 'map': {}, 'sig': None}


def calibrate_slots(log=print, listing_sig=None, force: bool = False) -> dict:
    """VERITE DES TOUCHES : appuie sur 1, 2, 3 (hors combat) et lit le type d arme reellement en main apres chacune.
    Le listing des emplacements du mod ne correspond pas aux touches (17/09 : la touche 3 sortait le fusil que le
    listing placait en 2). Fixe MELEE_SLOT / RANGED_SLOT. Au plus toutes les 10 min, ou si le listing change."""
    global MELEE_SLOT, RANGED_SLOT
    from .inventory import MELEE_TYPES as _MT
    now = time.perf_counter()
    if not force and now - LAST_CALIB['t'] < 600.0 and (listing_sig is None or listing_sig == LAST_CALIB['sig']):
        return LAST_CALIB['map']
    st0 = motion.read_state() or {}
    # NB : pas de condition sur `scene` (souvent vrai en exterieur, 17/09 11:40 : calibrage jamais lance)
    if st0.get('combat') or st0.get('menu') or st0.get('vehicle') or st0.get('swim') or (st0.get('hp') or 100) <= 0.5:
        log('  [armes] calibrage reporte (combat / menu / vehicule)')
        return LAST_CALIB['map']
    found = {}
    for k in ('1', '2', '3'):
        kbm.tap(k, 0.1); time.sleep(0.9)
        wt = (motion.read_state() or {}).get('weapon') or ''
        if wt in ('', 'Wea_Fists', 'None'):                 # la touche a RENGAINE (arme deja en main) : on la ressort
            kbm.tap(k, 0.1); time.sleep(0.7)
            wt = (motion.read_state() or {}).get('weapon') or ''
        found[k] = wt
    melee_keys = [k for k, wt in found.items() if wt in _MT and wt != 'Wea_Fists']
    ranged_keys = [k for k, wt in found.items() if wt.startswith('Wea_') and wt not in _MT]
    if len(set(found.values())) <= 1:
        # les trois touches donnent la meme chose : zone sans armes, scene, ou lecture figee -> NON CONCLUANT, on garde
        # les emplacements actuels et on reessaie dans 5 min (17/09 12:21 : 1=2=3=matraque -> distance None, faux)
        LAST_CALIB.update(t=now - 300.0, map=found, sig=listing_sig)
        log(f"  [armes] calibrage non concluant ({found.get('1') or 'rien'} pour les trois touches) : on garde melee {MELEE_SLOT}, distance {RANGED_SLOT}")
        return found
    if melee_keys:
        MELEE_SLOT = melee_keys[0]
    if ranged_keys:
        RANGED_SLOT = ranged_keys[0]
    LAST_CALIB.update(t=now, map=found, sig=listing_sig)
    log('  [armes] calibrage des touches : ' + ', '.join(f'{k}={wt or "rien"}' for k, wt in found.items())
        + f' -> melee {MELEE_SLOT}, distance {RANGED_SLOT}')
    if melee_keys and found.get(melee_keys[0]) != ((motion.read_state() or {}).get('weapon') or ''):
        kbm.tap(MELEE_SLOT, 0.1); time.sleep(0.4)             # on repart la melee en main
    return found


def _holding(melee: bool) -> bool:
    """V tient-il une arme de la categorie demandee (melee / a feu) ?"""
    from .inventory import MELEE_TYPES as _MT
    wt = (motion.read_state() or {}).get('weapon') or ''
    if melee:
        return wt in _MT and wt != 'Wea_Fists'
    return wt.startswith('Wea_') and wt not in _MT


def draw_slot(slot, melee: bool | None = None) -> bool:
    """Degaine l arme de la touche `slot` : d abord par la requete du jeu (mod, commande weapon_slot : le chemin exact des
    touches, sans clavier ni focus), puis la TOUCHE si l arme en main n est pas de la categorie voulue (17/09 : la
    requete laissait la matraque en main et la touche ne partait que les mains vides). Vrai si la categorie est bonne."""
    if not slot:
        return False
    r = None
    try:
        r = nav._wait(nav._send({'cmd': 'weapon_slot', 'x': int(slot)}), timeout=0.8)
    except Exception:
        r = None
    if r and r.get('ok'):
        time.sleep(0.5)
        if melee is None or _holding(melee):
            return True
    kbm.tap(str(slot), 0.08); time.sleep(0.6)
    return melee is None or _holding(melee)


def _ensure_weapon(slot, melee: bool) -> None:
    """Degaine l arme de l emplacement demande SEULEMENT si elle n est pas deja en main : la touche est une
    bascule, un appui de trop RENGAINE (engage() puis fight() appuyaient tous les deux). Poings = rien en main."""
    from .inventory import MELEE_TYPES as _MT
    st = motion.read_state() or {}
    wt = st.get('weapon') or ''
    holding_melee = wt in _MT and wt != 'Wea_Fists'
    holding_ranged = wt.startswith('Wea_') and wt not in _MT
    if (melee and holding_melee) or (not melee and holding_ranged):
        return
    if slot:
        draw_slot(slot, melee); time.sleep(0.2)


# ---- engagement (avant que le jeu ne passe en combat) -----------------------------------
LAST_ENGAGE = {'d0': None, 'd1': None}          # distance a la cible au debut / a la fin du dernier engage (pour le cerveau)
STEALTH_MAX_M = 12.0                           # accroupi seulement a moins de 12 m : plus loin on court (V n est pas une limace)


def _rescue_target(st: dict, last: dict) -> dict | None:
    """Cible de SECOURS rafraichie : le PNJ agressif / en combat le plus proche de la derniere position connue."""
    from . import planner as _pl
    aggr = _pl.aggressors(st)
    if not aggr:
        return None
    return min(aggr, key=lambda n: math.hypot(n['x'] - last['x'], n['y'] - last['y']))


def engage(target: dict, stop=None, log=print, max_s: float | None = None, rescue: bool = False) -> bool:
    """Va au contact d une cible et DECLENCHE le combat (premier coup / premiers tirs). True des que le jeu marque V
    en combat. max_s None = proportionnel a la distance (8 s + 1 s par 2,5 m). rescue : cible = PNJ agressif suivi
    en direct (il bouge), pas de discretion (il est deja en train de se battre)."""
    t0 = time.perf_counter()
    _ensure_weapon(MELEE_SLOT, melee=True)
    seq = None
    hacked = False
    st0 = motion.read_state() or {}
    from .config import CFG as _C3
    d_init = float(target.get('d') or 0.0)
    if max_s is None:
        max_s = 8.0 + d_init / 2.5
    LAST_ENGAGE['d0'], LAST_ENGAGE['d1'] = d_init, d_init
    stealth = _C3.features.get('stealth', True) and not rescue and not st0.get('combat') and 4.0 < d_init <= STEALTH_MAX_M and not st0.get('swim')
    crouched = False
    last_pos = {'x': target.get('x'), 'y': target.get('y')}
    struck = 0
    hacks_done, t_hack0 = 0, -99.0
    max_hacks = 1 if rescue else 2                      # en secours l agresseur se bat deja : un hack, puis on FONCE
    best_d, t_best = d_init, t0                         # progression : tant que V se rapproche, on ne renonce pas
    if stealth:
        kbm.act('crouch', 0.1); crouched = True; time.sleep(0.3)
        log('  [combat] approche en discretion (accroupi)')
    elif d_init > STEALTH_MAX_M:
        log(f"  [combat] cible a {d_init:.0f} m : V y va en courant")
    try:
        while True:
            _now = time.perf_counter()
            if _now - t0 > max_s and (_now - t_best > 5.0 or _now - t0 > 3.0 * max_s):
                log(f"  [combat] engagement expire ({_now - t0:.0f} s, cible a {LAST_ENGAGE['d1'] or 0:.0f} m)")
                break
            if stop is not None and stop.is_set():
                return False
            st = motion.read_state(seq, timeout=0.3)
            if st is None:
                continue
            seq = st['seq']
            if st.get('combat'):
                if crouched:
                    kbm.act('crouch', 0.1); crouched = False         # repere : on se releve et on se bat
                return True
            # elimination furtive : dans le dos d un ennemi inconscient du danger, l invite « Neutraliser / Tuer » apparait
            inter = st.get('interact') or {}
            ch0 = str((inter.get('choices') or [''])[0]).lower()
            if crouched and any(w in ch0 for w in ('neutraliser', 'tuer', 'eliminer', 'éliminer', 'takedown', 'assommer')):
                kbm.act('interact', 0.25); time.sleep(1.5)
                log(f'  [combat] elimination furtive : « {ch0} »')
                seq = None; continue
            alive = _alive(st.get('enemies'), allow_police=False)
            if not alive:
                # cible imposee (agresseur pas encore hostile a V) : suivie EN DIRECT parmi les PNJ agressifs, sinon
                # par sa derniere position connue
                live = _rescue_target(st, last_pos) if (rescue or target.get('x') is not None) else None
                if live is not None:
                    last_pos = {'x': live['x'], 'y': live['y']}
                    alive = [{'x': live['x'], 'y': live['y'], 'd': live.get('d') or math.hypot(live['x'] - st['x'], live['y'] - st['y']), 'sy': live.get('sy')}]
                elif last_pos.get('x') is not None:
                    dd = math.hypot(last_pos['x'] - st['x'], last_pos['y'] - st['y'])
                    if dd < 2.0 and time.perf_counter() - t0 > 4.0:
                        log('  [combat] personne a la derniere position connue de la cible'); return False
                    alive = [{'x': last_pos['x'], 'y': last_pos['y'], 'd': dd, 'sy': None}]
                else:
                    return False
            e = alive[0]
            LAST_ENGAGE['d1'] = e['d']
            if e['d'] < best_d - 1.0:
                best_d, t_best = e['d'], time.perf_counter()
            gap = aim_at(e, st)
            # discretion : on s accroupit seulement en arrivant a portee (< 12 m), jamais pour un secours
            if stealth is False and _C3.features.get('stealth', True) and not rescue and not crouched and e['d'] <= STEALTH_MAX_M and d_init > STEALTH_MAX_M and not st.get('swim'):
                kbm.act_release('sprint'); kbm.act('crouch', 0.1); crouched = True; stealth = True; time.sleep(0.2)
                log('  [combat] a portee : approche en discretion (accroupi)')
            # HACKS D OUVERTURE : avant le contact, V pirate a distance jusqu a 3 hostiles differents (RAM permettant)
            if hacks_done < max_hacks and e['d'] > 12.0 and gap < 8 and time.perf_counter() - t0 > 1.0 and time.perf_counter() - t_hack0 > 2.5:
                vis = [x for x in (alive if len(alive) > 1 else [e])]
                tgt = vis[hacks_done % len(vis)]
                if tgt is not e:
                    aim_at(tgt, st)
                title = quickhack_best(log=log)
                if title == 'ram':
                    hacks_done = max_hacks; t_hack0 = time.perf_counter(); seq = None
                    log('  [combat] RAM insuffisante : pas de hack d ouverture, on engage')
                    continue
                hacks_done += 1; hacked = True; t_hack0 = time.perf_counter(); seq = None
                log(f"  [combat] hack d ouverture « {title or 'par defaut'} » sur cible a {tgt['d']:.0f} m ({hacks_done}/{max_hacks})")
                continue
            # DECLENCHER le combat : a distance, quelques tirs alignes (style mixte / distance) ; au contact, un coup
            if RANGED_SLOT and _C3.style != 'melee' and 12.0 < e['d'] < RANGED_MAX_M and gap < 6 and struck < 2 and time.perf_counter() - t0 > 1.0:
                kbm.release('W'); kbm.act_release('sprint')
                _ensure_weapon(RANGED_SLOT, melee=False)
                kbm.mouse('right', True); time.sleep(0.2); kbm.mouse_tap('left', 0.35); kbm.mouse('right', False)   # rafale : un tir isole a 30 m rate
                struck += 1; seq = None
                if struck == 1: log(f"  [combat] ouverture du feu a {e['d']:.0f} m")
                continue
            if e['d'] > MELEE_RANGE:
                kbm.hold('W')
                if e['d'] > 8.0 and not crouched: kbm.act_hold('sprint')
                else: kbm.act_release('sprint')
            else:
                kbm.release('W'); kbm.act_release('sprint')
                if gap < 25:
                    _ensure_weapon(MELEE_SLOT, melee=True)
                    heavy_attack(); struck += 1; time.sleep(0.2)
                    if struck >= 5 and not rescue:
                        log(f"  [combat] {struck} coups au contact sans reaction : cible intouchable (vitre, autre niveau, scene)")
                        return False
    finally:
        kbm.release_all()
        if crouched:
            kbm.act('crouch', 0.1)                                   # ne jamais rester accroupi apres l approche
    return False


# ---- combat -----------------------------------------------------------------------------
def fight(stop=None, log=print, max_s: float = 180.0) -> dict:
    t0 = time.perf_counter()
    stats = {'soins': 0, 'grenades': 0, 'coups': 0, 'charges': 0, 'esquives': 0, 'strafes': 0,
             'parades': 0, 'replis': 0, 'sauts': 0, 'glissades': 0, 'cyberware': 0, 'quickhacks': 0, 'quick_melee': 0}
    t_heal = t_gren = t_cyber = t_hack = t_dodge = t_strafe = t_jump = t_slide = t_knife = -99.0
    t_wsync = -99.0
    t_sprint = -99.0
    wsync_fail = 0                                     # degainages sans effet (zone a mains nues, arme restreinte)
    blind_hacks, blind_hacks_t = 0, -99.0              # hacks « par defaut » de suite (liste du panneau illisible) : pause, pas coupure
    mode = 'melee'; shots = 0
    hack_i = 0
    last_hp, last_hp_t = None, time.perf_counter()
    seq = None
    combo = 0
    side = 'A'
    retreat_until = 0.0
    flee_until, last_flee_end = 0.0, -999.0
    t_buff = -999.0
    sterile_n, sterile_hp, sterile_t = -1, 100.0, time.perf_counter()  # hysterese : pas de flip sur un simple flicker de vie
    sterile_charges = 0
    last_retreat_end = -99.0
    no_target_logged = False
    police_logged = False
    _ensure_weapon(MELEE_SLOT, melee=True)
    log(f'  [combat] v2 debut, arme emplacement {MELEE_SLOT}')
    try:
        while time.perf_counter() - t0 < max_s:
            if stop is not None and stop.is_set():
                break
            st = motion.read_state(seq, timeout=0.3)
            if st is None:
                continue
            seq = st['seq']
            now = time.perf_counter()
            hp = st.get('hp')
            hp = 100.0 if hp is None else float(hp)
            if hp <= 0.5:
                log('  [combat] V EST MORT : arret du combat'); stats['mort'] = True; break

            # -- soin
            if hp < HEAL_BELOW and now - t_heal > HEAL_CD and charges(st, 'heal'):
                kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
                log(f'  [combat] soin (vie {hp:.0f} %)')

            # -- chute brutale de vie -> esquive arriere immediate
            if last_hp is not None and last_hp - hp > 15 and now - last_hp_t < 1.5 and now - t_dodge > 0.8:
                kbm.release('W'); dodge('S'); t_dodge = now; stats['esquives'] += 1
            if last_hp is None or now - last_hp_t > 1.5:
                last_hp, last_hp_t = hp, now

            alive = _alive(st.get('enemies'))
            n_alive = n_foes(st, alive)             # 24/09 : total reel (la liste exportee est plafonnee a 6)
            if not st.get('combat'):
                # le jeu ne marque V « en combat » qu apres le premier echange : pendant 5 s, s il reste des hostiles
                # vivants a portee, on continue (et on frappe) au lieu de conclure que c est fini
                near_alive = [x for x in alive if x['d'] < 25.0]
                if now - t0 > 5.0 or not near_alive:
                    log('  [combat] fin : plus en combat'); break
            # combat STERILE : rien ne change depuis 75 s (meme nombre d hostiles, vie intacte) -> cibles
            # injoignables (vitre, autre etage, tourelle hors portee) : on arrete de taper dans le vide
            if len(alive) != sterile_n or abs(hp - sterile_hp) > 5.0:   # nombre d ennemis change, ou vie a vraiment bouge (pas un flicker de regen)
                sterile_n, sterile_hp, sterile_t = len(alive), hp, now
            elif (sterile_hp >= 90 or stats['coups'] + stats.get('tirs', 0) == 0) and alive and sterile_charges < 2 and now - sterile_t > 40.0:
                # rien ne bouge depuis 40 s : la cible est hors de portee (distance, etage, vitre). V n est pas un
                # couard : il CHARGE (sprint droit dessus, saut sur les obstacles) avant de conclure quoi que ce soit
                sterile_charges += 1; sterile_t = now
                log(f"  [combat] combat sterile depuis 40 s : V charge la cible a {alive[0]['d']:.0f} m ({sterile_charges}/2)")
                if mode == 'ranged':
                    draw_slot(MELEE_SLOT, True); mode = 'melee'; time.sleep(0.3)
                t_ch, t_j = time.perf_counter(), -99.0
                while time.perf_counter() - t_ch < 8.0 and not (stop is not None and stop.is_set()):
                    s_ch = motion.read_state() or st
                    a_ch = _alive(s_ch.get('enemies'))
                    if not a_ch or a_ch[0]['d'] < MELEE_RANGE or float(s_ch.get('hp') or 100) < 60:
                        break
                    aim_at(a_ch[0], s_ch); kbm.hold('W'); kbm.act_hold('sprint')
                    if time.perf_counter() - t_j > 2.0:
                        kbm.act('jump', 0.1); t_j = time.perf_counter()
                    time.sleep(0.15)
                kbm.release('W'); kbm.act_release('sprint')
                seq = None; continue
            elif (sterile_hp >= 90 or stats['coups'] + stats.get('tirs', 0) == 0) and now - sterile_t > 75.0:
                log(f'  [combat] combat sterile : {len(alive)} hostile(s) intouchable(s) depuis 75 s malgre {sterile_charges} charge(s) -> on laisse tomber')
                stats['sterile'] = True; break
            if not alive:
                kbm.release('W'); kbm.act_release('sprint')
                if not no_target_logged:
                    no_target_logged = True
                    log('  [combat] aucune cible exportee (voir probe_progress.txt : ENNEMIS)')
                time.sleep(0.2); continue
            no_target_logged = False

            e = alive[0]
            pressure = sum(1 for x in alive if x['d'] < 5.0)
            cx, cy = centroid(alive)
            only_police = all(x.get('police') for x in alive)
            if only_police and not police_logged:
                police_logged = True
                log('  [combat] la police attaque : legitime defense, mais on cherche a decrocher')

            # -- buffs (nourriture / boisson / booster) toutes les 60 s, via le mod (pas de menu)
            if now - t_buff > 60.0 and e['d'] > 4.0:
                t_buff = now
                try:
                    from . import buffs as _buffs
                    _buffs.apply(st, log=log, in_combat=True)
                except Exception as ex:
                    log(f'  [buff] erreur : {ex}')
            # -- cyberware iconique (Sandevistan / Berserk / camo) des qu on est engage
            if e['d'] < 15.0 and now - t_cyber > CYBERWARE_CD:
                kbm.act('iconic', 0.12); t_cyber = now; stats['cyberware'] += 1
                log(f"  [combat] cyberware iconique ({kbm.K('iconic')})")

            # -- FUITE : submerge (5+ hostiles, ou 4+ avec vie < 50 %, ou 3+ avec vie < 30 %) -> on decroche
            #    franchement : dos au groupe, sprint 8 s, soin, grenade derriere soi ; on recommence tant que
            #    le groupe suit. Mieux vaut perdre l engagement que la partie (mort 20:55 face a 6).
            # fuite = vraiment submerge OU vie basse face a un groupe ; jamais sur le seul nombre (V n est pas un couard)
            # 6+ hostiles = on decroche TOUT DE SUITE (3 morts face a des groupes de 6 : 100 -> 41 % de vie en 5 s)
            # la POLICE : on ne se bat pas, on file (les renforts sont sans fin). Seuils de fuite selon le courage.
            from .config import CFG as _C
            T_FLEE = _C.courage_t[2]
            if _C.courage == 'temeraire':
                want_flee = only_police or (n_alive >= T_FLEE and hp < 50) or (n_alive >= 3 and hp < 22) or hp < 12
            elif _C.courage == 'prudent':
                want_flee = only_police or n_alive >= T_FLEE or (n_alive >= 3 and hp < 45) or (n_alive >= 2 and hp < 30)
            else:
                want_flee = only_police or n_alive >= T_FLEE or (n_alive >= 4 and hp < 35) or (n_alive >= 2 and hp < 22)
            if now < flee_until or (want_flee and now - last_flee_end > 4.0):
                if now >= flee_until:
                    flee_until = now + 8.0; last_flee_end = flee_until; stats['fuites'] = stats.get('fuites', 0) + 1
                    log(f'  [combat] FUITE ({n_alive} hostiles, vie {hp:.0f} %) : on decroche')
                away = motion.wrap(motion.bearing_to(cx, cy, st['x'], st['y']))
                aim_bearing(away, st, gain=0.7)
                kbm.hold('W'); kbm.act_hold('sprint')
                if now - t_heal > HEAL_CD and hp < 70 and charges(st, 'heal'):
                    kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
                if now - t_gren > 6.0 and e['d'] > 6.0 and charges(st, 'gren'):
                    kbm.mouse_tap('middle', 0.12); t_gren = now; stats['grenades'] += 1   # grenade vers l arriere (on regarde devant : elle part devant... on ne vise pas)
                if now - t_dodge > 2.0:
                    dodge('W'); t_dodge = now; stats['esquives'] += 1
                continue
            # -- REPLI tactique : vie basse sous pression -> dos aux ennemis, sprint, soin
            outnumbered = n_alive >= OUTNUMBERED
            want_retreat = (hp < RETREAT_HP and pressure >= RETREAT_PRESSURE) or hp < 20 \
                or (outnumbered and hp < 45 and pressure >= 3) \
                or (only_police and (hp < 60 or pressure >= 2))
            if now < retreat_until or (want_retreat and now - last_retreat_end > RETREAT_COOLDOWN):
                if now >= retreat_until:
                    retreat_until = now + RETREAT_S; last_retreat_end = retreat_until; stats['replis'] += 1
                    log(f'  [combat] REPLI (vie {hp:.0f} %, {pressure} au contact)')
                away = motion.wrap(motion.bearing_to(cx, cy, st['x'], st['y']))   # cap opposé au centre des ennemis
                aim_bearing(away, st, gain=0.7)
                kbm.hold('W'); kbm.act_hold('sprint')
                if now - t_heal > HEAL_CD and hp < HEAL_BELOW and charges(st, 'heal'):
                    kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
                continue
            kbm.act_release('sprint')

            # -- grenade sur un ennemi ELOIGNE (pas forcement le plus proche), si groupe
            far = [x for x in alive if GRENADE_MIN_M <= x['d'] <= GRENADE_MAX_M]
            if far and len(alive) >= 2 and now - t_gren > GRENADE_CD and charges(st, 'gren'):
                tgt = far[-1]
                gap = aim_at(tgt, st)
                if gap < 10:
                    kbm.mouse_tap('middle', 0.12); t_gren = now; stats['grenades'] += 1
                    log(f"  [combat] grenade sur cible a {tgt['d']:.0f} m ({len(alive)} hostiles)")
                    time.sleep(0.4)
                continue

            # -- visee sur la cible principale
            gap = aim_at(e, st)

            # -- MODE A DISTANCE : cible loin ou en hauteur (drone/tourelle), si une arme a distance existe
            high = (e.get('z', st.get('z', 0.0)) - st.get('z', 0.0)) > 2.5
            from .config import CFG as _C2
            if _C2.style == 'distance':
                rmin, rback = 4.0, 2.5                    # arme a feu des 4 m : V est un tireur
            elif _C2.style == 'mixte':
                rmin, rback = 8.0, 5.0
            else:
                rmin, rback = RANGED_MIN_M, RANGED_BACK_M  # melee : arme a feu seulement loin / en hauteur
            want_ranged = RANGED_SLOT is not None and (high or rmin < e['d'] < RANGED_MAX_M)
            if want_ranged and mode != 'ranged':
                if draw_slot(RANGED_SLOT, False):
                    mode = 'ranged'; shots = 0; time.sleep(0.3)
                else:
                    wsync_fail += 1                                    # l arme a feu ne sort pas : on reste melee et on fonce
                log(f"  [combat] arme a distance (cible a {e['d']:.0f} m{', en hauteur' if high else ''})")
            elif mode == 'ranged' and e['d'] <= rback and not high:
                draw_slot(MELEE_SLOT, True); mode = 'melee'; time.sleep(0.4)
                log('  [combat] retour au corps a corps')
            # l arme REELLEMENT tenue (export du mod) : si elle ne correspond pas au mode, on corrige (la touche
            # d emplacement est une bascule : un appui de trop rengainait, ou laissait la mitrailleuse au CAC)
            wt = st.get('weapon') or ''
            from .inventory import MELEE_TYPES as _MT
            holding_ranged = wt.startswith('Wea_') and wt not in _MT
            holding_melee = wt in _MT and wt != 'Wea_Fists'         # poings = rien en main
            if mode == 'ranged' and not holding_ranged and wsync_fail >= 3:
                mode = 'melee'; log('  [combat] l arme a feu ne sort pas : retour au corps a corps, on fonce')
            if wt and now - t_wsync > 1.5:
                if (mode == 'melee' and holding_melee) or (mode == 'ranged' and holding_ranged):
                    wsync_fail = 0
                elif wsync_fail >= 3:
                    if wsync_fail == 3:
                        wsync_fail = 4; log(f'  [combat] arme indisponible ici (tenue : {wt}) : combat a mains nues / avec ce qu il y a')
                        if max_s < 600.0:
                            # MATCH A MAINS NUES (Vaincre X, boxe) : partir avant la fin laisse le jeu en « match en cours » et bloque
                            # les armes ensuite (« Action impossible », 17/09) : on va jusqu au bout, 10 min s il le faut
                            max_s = 600.0; log('  [combat] match a mains nues : on va jusqu au bout (10 min max)')
                elif mode == 'melee' and not holding_melee:
                    ok_d = draw_slot(MELEE_SLOT, True); t_wsync = now; wsync_fail = 0 if ok_d else wsync_fail + 1; log(f'  [combat] arme tenue {wt} : on degaine la melee (emplacement {MELEE_SLOT}) -> {"ok" if ok_d else "sans effet"}')
                elif mode == 'ranged' and RANGED_SLOT and not holding_ranged:
                    ok_d = draw_slot(RANGED_SLOT, False); t_wsync = now; wsync_fail = 0 if ok_d else wsync_fail + 1; log(f'  [combat] arme tenue {wt} : on degaine l arme a feu (emplacement {RANGED_SLOT}) -> {"ok" if ok_d else "sans effet"}')
            if mode == 'ranged':
                kbm.release('W'); kbm.act_release('sprint')
                # hacks A DISTANCE entre deux rafales : cible alternee (hack_i) pour ne pas empiler sur le meme
                if e['d'] > 3.0 and gap < 8 and now - t_hack > 2 * QUICKHACK_CD and (blind_hacks < 3 or now - blind_hacks_t > 20.0):   # vise d abord ; 8 s entre deux hacks
                    tgt = alive[hack_i % len(alive)]; hack_i += 1
                    if tgt is not e:
                        aim_at(tgt, st)
                    title = quickhack_best(log=log)
                    t_hack = time.perf_counter()                        # le delai court APRES le hack (il dure ~4 s)
                    if title == 'ram':
                        log('  [combat] quickhack : RAM insuffisante, on tire'); seq = None; continue
                    stats['quickhacks'] += 1
                    if title and str(title).startswith('userdata'):
                        title = None
                    if title:
                        blind_hacks = 0
                    else:
                        blind_hacks = blind_hacks + 1 if now - blind_hacks_t < 20.0 else 1
                        blind_hacks_t = now
                    log(f"  [combat] quickhack « {title or 'par defaut'} » (a distance) sur cible a {tgt['d']:.0f} m")
                    if blind_hacks >= 3:
                        log('  [combat] 3 hacks a l aveugle de suite (liste illisible) : pause de 20 s avant de retenter, on tire')
                    seq = None; continue
                if _C2.features.get('sprint', True) and now - t_sprint > (2.5 if hp < 85 else 5.0):
                    side = 'D' if side == 'A' else 'A'
                    sprint_pass(e, st, 1.0 if side == 'D' else -1.0, dur=0.8)   # sprint lateral = regeneration + cible mouvante
                    t_sprint = now; stats['sprints'] = stats.get('sprints', 0) + 1
                    seq = None; continue
                if gap < 4 and holding_ranged:
                    kbm.mouse('right', True); time.sleep(0.15)          # viser
                    kbm.mouse_tap('left', 0.12); shots += 1; stats['tirs'] = stats.get('tirs', 0) + 1
                    kbm.mouse('right', False)
                    if shots % 8 == 0:
                        kbm.act('reload', 0.1); time.sleep(1.2)               # recharger
                    if now - t_strafe > STRAFE_CD:
                        side = 'D' if side == 'A' else 'A'; strafe(side, 0.3); t_strafe = now
                time.sleep(0.15); continue

            # -- quickhack a distance
            if e['d'] > 3.0 and gap < 8 and now - t_hack > QUICKHACK_CD and (blind_hacks < 3 or now - blind_hacks_t > 20.0):
                tgt = alive[hack_i % len(alive)]; hack_i += 1
                if tgt is not e and tgt['d'] > 3.0:
                    aim_at(tgt, st); e = tgt
                title = quickhack_best(log=log)
                t_hack = time.perf_counter()
                if title == 'ram':
                    log('  [combat] quickhack : RAM insuffisante'); seq = None; continue
                stats['quickhacks'] += 1
                if title and not str(title).startswith('userdata'):
                    blind_hacks = 0
                else:
                    blind_hacks = blind_hacks + 1 if now - blind_hacks_t < 20.0 else 1
                    blind_hacks_t = now
                if title and str(title).startswith('userdata'):
                    title = None
                log(f"  [combat] quickhack « {title or 'par defaut'} » sur cible a {e['d']:.0f} m")
                seq = None; continue

            # -- lancer de couteau sur cible a moyenne distance (Nehan revient tout seul)
            if 4.0 < e['d'] < 14.0 and gap < 6 and now - t_knife > KNIFE_CD:
                knife_throw(); t_knife = now; stats['couteaux'] = stats.get('couteaux', 0) + 1
                log(f"  [combat] couteau lance ({e['d']:.0f} m)")
                continue

            # -- APPROCHE : sprint, glissade, frappe sautee, zigzag
            if e['d'] > MELEE_RANGE:
                kbm.hold('W')
                if 4.5 < e['d'] < 8.0 and now - t_dodge > DODGE_CD and gap < 20:
                    dodge('W'); t_dodge = now; stats['esquives'] += 1          # DASH d approche (dodgeDash vers l avant)
                if e['d'] > 4.5:
                    kbm.act_hold('sprint')                              # jusqu au contact : attaque de sprint + regeneration
                    if e['d'] < 9.0 and now - t_slide > SLIDE_CD:
                        slide(); t_slide = now; stats['glissades'] += 1
                elif 3.0 < e['d'] < 4.5 and now - t_jump > JUMP_ATTACK_CD and gap < 15:
                    jump_attack(); t_jump = now; stats['sauts'] += 1; stats['coups'] += 1
                elif now - t_strafe > STRAFE_CD:
                    side = 'D' if side == 'A' else 'A'
                    strafe(side, 0.25); t_strafe = now; stats['strafes'] += 1
                continue

            # -- CONTACT : combo mobile
            kbm.release('W')
            if gap >= 25:
                continue
            combo += 1
            # -- SPRINT de biais (perk : +60 % de regeneration en sprintant) : regulierement au contact, plus souvent
            #    quand la vie baisse ; pas quand 3+ ennemis collent (on ne tourne pas le dos a une meute)
            if _C2.features.get('sprint', True) and now - t_sprint > (1.8 if hp < 70 else 3.5) and pressure < 3:
                side = 'D' if side == 'A' else 'A'
                sprint_pass(e, st, 1.0 if side == 'D' else -1.0, dur=0.9 if hp < 70 else 0.7)
                t_sprint = now; stats['sprints'] = stats.get('sprints', 0) + 1
                seq = None; continue
            if pressure >= 2 and combo % 5 == 0:
                block(0.3); stats['parades'] += 1
            elif now - t_dodge > DODGE_CD and combo % 2 == 0:
                side = 'D' if side == 'A' else 'A'
                dodge(side); t_dodge = now; stats['esquives'] += 1
            elif combo % 5 == 0:
                heavy_attack(); stats['charges'] += 1; stats['coups'] += 1
            elif combo % 8 == 0:
                kbm.act('quickmelee', 0.1); stats['quick_melee'] += 1
            elif now - t_strafe > STRAFE_CD:
                side = 'D' if side == 'A' else 'A'
                kbm.hold(side); light_attack(); time.sleep(0.15); kbm.release(side)
                t_strafe = now; stats['strafes'] += 1; stats['coups'] += 1
            else:
                light_attack(); stats['coups'] += 1
            time.sleep(0.22)
    finally:
        kbm.release_all()
    stats['seconds'] = time.perf_counter() - t0
    log(f'  [combat] bilan : {stats}')
    return stats

# ---- loot apres combat ------------------------------------------------------------------
def loot_bodies(stop=None, log=print, max_bodies: int = 5, radius_m: float = 25.0) -> dict:
    """Apres un combat : marche jusqu a chaque ennemi MORT exporte par le mod (dans radius_m),
    et appuie sur F des qu une invite d interaction (Fouiller / Ramasser) apparait."""
    from . import nav
    t0 = time.perf_counter()
    st = motion.read_state()
    if not st:
        return {'ok': False, 'reason': 'etat illisible'}
    # corps memorises par le mod (les requetes de ciblage excluent les morts) + morts encore listes
    bodies = [b for b in (st.get('bodies') or []) if b['d'] <= radius_m]
    bodies += [e for e in (st.get('enemies') or []) if (e.get('dead') or e.get('down')) and e['d'] <= radius_m]   # assommes : lootables aussi
    bodies.sort(key=lambda e: e['d'])
    looted = 0
    for b in bodies[:max_bodies]:
        if stop is not None and stop.is_set():
            break
        motion.ARRIVE_M, old_arrive = 1.2, motion.ARRIVE_M
        try:
            r = motion.walk_to(b['x'], b['y'], timeout=12.0, stop=stop)
        finally:
            motion.ARRIVE_M = old_arrive
        if not r.get('ok'):
            continue
        # le corps est au sol : regard ~45 deg vers le bas (900 counts), puis balayage lent
        # gauche-droite jusqu a l invite « Fouiller »
        kbm.look(0, 900)
        t1 = time.perf_counter()
        pressed = False
        sweep = 0
        while time.perf_counter() - t1 < 4.0:
            s2 = motion.read_state()
            inter = (s2 or {}).get('interact')
            if inter and inter.get('choices'):
                kbm.act('interact', 0.3); pressed = True
                log(f"  [loot] « {inter['choices'][0]} » sur un corps a ({b['x']:.0f},{b['y']:.0f})")
                time.sleep(0.7)
                s3 = motion.read_state()
                if (s3 or {}).get('interact'):
                    kbm.act('ui_confirm', 0.1); time.sleep(0.4)       # « Tout prendre » si un panneau s est ouvert
                break
            # balayage : -30 deg, +60 deg, -60 deg ... par petits pas
            sweep += 1
            kbm.look(60 if (sweep // 10) % 2 == 0 else -60, 0)
            time.sleep(0.1)
        kbm.look(0, -900)
        if pressed:
            looted += 1
    return {'ok': True, 'corps': len(bodies), 'fouilles': looted, 'seconds': time.perf_counter() - t0}

def _look_smooth(dx: float, dy: float, step: int = 60, dt: float = 0.012) -> None:
    """Camera par petits paquets : un seul gros MouseMove est ecrete par le jeu."""
    n = max(1, int(max(abs(dx), abs(dy)) // step) + 1)
    for _ in range(n):
        kbm.look(dx / n, dy / n); time.sleep(dt)


def loot_around(stop=None, log=print, radius_m: float = 20.0, max_items: int = 8, seen: set | None = None) -> dict:
    """Ramasse TOUT ce qui est lootable autour (conteneurs, objets au sol, corps).
    Pour chaque objet : marche jusqu a 1.3 m, se tourne vers lui, puis BALAIE la vue de haut en
    bas jusqu a ce que l objet soit SOUS LE RETICULE (state.lookat, GetLookAtObject) -> touche
    d interaction (E). Si un panneau de loot s ouvre (lootCount change), F repete pour le vider.
    Journal detaille a chaque etape : c est la competence la plus capricieuse."""
    t0 = time.perf_counter()
    seen = seen if seen is not None else set()
    st = motion.read_state()
    if not st:
        return {'ok': False, 'reason': 'etat illisible'}
    if st.get('swim'):
        return {'ok': False, 'reason': 'dans l eau'}
    LOOT_CLS = ('gameLootContainerBase', 'gameItemDropObject', 'gameLootBag', 'gameContainerObject')
    NO_GRAB = ('saisir', 'porter', 'soulever', 'grab', 'carry')    # invites a NE JAMAIS accepter : V porterait un corps
    if st.get('carrying') and kbm.ACTIONS.get('dropbody'):
        log('  [loot] V porte un corps : on le lache'); kbm.act('dropbody', 0.2); time.sleep(0.8)
    objs = [o for o in (st.get('loot') or []) if o['d'] <= radius_m]
    objs += [{'x': b['x'], 'y': b['y'], 'z': b.get('z', st['z']), 'd': b['d'], 'cls': 'corps'} for b in (st.get('bodies') or []) if b['d'] <= min(radius_m, 12.0)]
    objs.sort(key=lambda o: o['d'])
    done, tried = 0, 0
    for o in objs:
        key = (round(o['x']), round(o['y']))
        if key in seen:
            continue
        if (stop is not None and stop.is_set()) or tried >= max_items:
            break
        tried += 1
        seen.add(key)
        old = motion.ARRIVE_M; motion.ARRIVE_M = 1.3
        try:
            r = motion.walk_to(o['x'], o['y'], timeout=12.0, stop=stop)
        finally:
            motion.ARRIVE_M = old
        s1 = motion.read_state() or {}
        dnow = math.hypot(o['x'] - s1.get('x', 0), o['y'] - s1.get('y', 0))
        log(f"  [loot] {o.get('cls', '?')} : marche {'ok' if r.get('ok') else r.get('reason')}{(' (bouge ' + format(r.get('moved_m', 0), '.2f') + ' m)') if r.get('reason') == 'bloque' else ''}, a {dnow:.1f} m"
            f" | porte un corps={s1.get('carrying')} locomotion={s1.get('locomotion')} upperBody={s1.get('upperBody')} vehicule={s1.get('vehicle')}")
        if not r.get('ok') and dnow > 3.0:
            if dnow <= 5.5:                       # trop loin pour l interface, assez pres pour le loot par script (6 m)
                from . import nav
                rl = nav._wait(nav._send({'cmd': 'loot', 'x': o['x'], 'y': o['y'], 'z': o.get('z')}), timeout=4.0)
                if rl and rl.get('ok') and rl.get('transferes'):
                    done += 1
                    log(f"  [loot]   script (a {dnow:.1f} m) : {rl.get('cls')} -> {rl.get('transferes')} objet(s) {rl.get('noms') or ''}")
                else:
                    log(f"  [loot]   script (a {dnow:.1f} m) : {(rl or {}).get('reason', 'rien')}")
            continue
        # face a l objet, puis balayage vertical : du haut (-15 deg) vers le bas (+50 deg) par pas de 5 deg
        for _ in range(6):
            s2 = motion.read_state()
            if s2 and aim_at({'x': o['x'], 'y': o['y'], 'sy': None}, s2) < 3.0:
                break
            time.sleep(0.05)
        _look_smooth(0, -200)                   # partir de -10 deg (les corps et objets au sol sont bien plus bas)
        got = False
        base_count = (motion.read_state() or {}).get('lootCount') or 0
        pitch_steps = 0
        grab_seen = False
        for step in range(18):
            s2 = motion.read_state()
            if not s2:
                break
            if s2.get('carrying') and kbm.ACTIONS.get('dropbody'):
                log('  [loot]   V a saisi un corps par erreur : on le lache'); kbm.act('dropbody', 0.2); time.sleep(0.8); break
            la = s2.get('lookat') or {}
            inter = s2.get('interact')
            # tooltip de loot : le blackboard est PERIME (il garde le dernier tooltip) -> on ne le croit que
            # s il a CHANGE depuis le debut de cet objet
            panel = bool(s2.get('lootPanel')) and (s2.get('lootCount') or 0) > 0 and (s2.get('lootCount') or 0) != base_count
            # en mode loot, un PNJ au sol sous le reticule a < 2,5 m = le corps vise (IsDead peu fiable)
            corpse_under = o.get('cls') == 'corps' and la.get('cls') == 'gamePuppet' and (la.get('d') or 99) < 2.5
            under = la.get('cls') in LOOT_CLS or (la.get('cls') in ('gamePuppet', 'gameObject') and la.get('dead')) or corpse_under
            prompt = ((inter or {}).get('choices') or [''])[0].lower()
            # « Saisir » = l action E MAINTENU (porter le corps) affichee a cote du tooltip de loot : ce n est
            # pas un obstacle, c est meme le signe qu on regarde un corps. On ne fait JAMAIS d appui long ici.
            if prompt and any(w in prompt for w in NO_GRAB):
                grab_seen = True; inter = None
            # en mode loot, seules les invites de LOOT comptent (pas « Parler », « Activer »...)
            if prompt and not any(w in prompt for w in ('fouiller', 'ramasser', 'ouvrir', 'prendre', 'piller', 'loot')):
                inter = None
            if panel or under or (inter and inter.get('choices')):
                log(f"  [loot]   sous le reticule : {la.get('cls')} a {la.get('d', 0):.1f} m | tooltip={'oui' if panel else 'non'} ({s2.get('lootCount')} objet(s))"
                    + (f" | invite « {inter['choices'][0]} »" if inter and inter.get('choices') else '') + (' | « saisir » affiche' if grab_seen else ''))
                # appuis COURTS repetes : chaque E prend l objet surligne ; un E long porterait le corps
                taken = 0
                for _ in range(10):
                    kbm.act('interact', 0.08); time.sleep(0.45)
                    taken += 1
                    s3 = motion.read_state() or {}
                    if s3.get('carrying'):
                        log('  [loot]   corps saisi malgre tout : on le lache'); kbm.act('dropbody', 0.2); time.sleep(0.8); break
                    if not (s3.get('lootPanel') and (s3.get('lootCount') or 0) > 0):
                        break
                got = True
                log(f'  [loot]   {taken} appui(s) E ; tooltip ensuite : {"encore " + str((motion.read_state() or {}).get("lootCount")) + " objet(s)" if (motion.read_state() or {}).get("lootPanel") else "ferme"}')
                break
            _look_smooth(0, 100)                # 5 deg vers le bas
            pitch_steps += 1
            time.sleep(0.12)
        if not got:
            la = (motion.read_state() or {}).get('lookat') or {}
            log(f"  [loot]   rien sous le reticule apres balayage (dernier : {la.get('cls')} a {la.get('d', 0):.1f} m, lootCount={base_count})")
        # dans tous les cas : loot PAR SCRIPT de l objet vise (transfert direct du contenu vers V, comme « tout prendre »),
        # l interface de loot etant trop capricieuse pour « ne rien oublier »
        from . import nav
        rl = nav._wait(nav._send({'cmd': 'loot', 'x': o['x'], 'y': o['y'], 'z': o.get('z')}), timeout=4.0)
        if rl and rl.get('ok'):
            n = rl.get('transferes', 0)
            log(f"  [loot]   script : {rl.get('cls')} -> {n} objet(s) {rl.get('noms') or ''} [{','.join(rl.get('methodes') or [])}]")
            if n:
                got = True
        else:
            log(f"  [loot]   script : {(rl or {}).get('reason', 'mod muet')}")
        if got:
            done += 1
        _look_smooth(0, -100 * pitch_steps + 200)   # revenir a l horizontale
    return {'ok': True, 'objets': len(objs), 'ramasses': done, 'seconds': time.perf_counter() - t0}


