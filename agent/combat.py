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
# 12/10 ARME A DISTANCE ADAPTEE (Olivier : « de meme pour l arme a distance ») : V n avait qu un fusil a pompe (portee
# efficace 10 m) dans l emplacement 3, utilise de 15 a 42 m. L emplacement 3 recoit la meilleure arme pour les ennemis
# JUSTE AVANT de degainer (echange propre d un emplacement non actif, commande equip_ws du mod).
ARSENAL: dict = {}          # cle 'hash:seed' -> fiche d arme a distance (inventory.manage)
SMARTLINK = None            # stat joueur HasSmartLink
RANGED_KEY = None           # arme REELLEMENT dans RANGED_SLOT (verifiee par wslots)
WSWAP_FAILS = 0             # echanges sans effet ; >= 2 -> on n echange plus pendant 15 min (comme inventory.EQUIP_BROKEN)
_WSWAP_T = [0.0]            # heure du dernier echange sans effet
FAM_RNG = {'shotgun': 10.0, 'smg': 20.0, 'pistol': 20.0, 'ar': 30.0, 'lmg': 30.0, 'precision': 50.0, 'sniper': 60.0}
LAST_PICK = {'sig': None, 't': -1e9}
FAMILY = {'Wea_Handgun': 'pistol', 'Wea_Revolver': 'pistol', 'Wea_SubmachineGun': 'smg', 'Wea_Rifle': 'ar',
          'Wea_AssaultRifle': 'ar', 'Wea_Shotgun': 'shotgun', 'Wea_ShotgunDual': 'shotgun',
          'Wea_PrecisionRifle': 'precision', 'Wea_SniperRifle': 'sniper', 'Wea_LightMachineGun': 'lmg'}
KIND_W = {'mech': 3.0, 'drone': 1.5, 'android': 1.2, 'robot': 1.2, 'turret': 1.5, 'human': 1.0}
MECHANICAL = ('mech', 'drone', 'android', 'robot', 'turret')   # immunites poison + pas de bonus de tir a la tete
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
                 ('weaponmalfunction', 'weaponglitch', 'defaillance', 'enrayage'), ('cripple', 'locomotion', 'paralysie'),
                 ('rebootoptics', 'blind', 'redemarrage'), ('memorywipe', 'effacement'), ('whistle', 'sifflement'),
                 ('ping',))


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii').lower()
    return ''.join(c for c in s if c.isalnum())


def _hack_score(h: dict) -> int:
    # 12/10 : hack d APPAREIL (DeviceAction.*, ex. « Distraire les ennemis » d un distributeur) = sans interet en
    # combat ; il etait choisi 26 fois sur 27 (mot-cle « malfunction » de DeviceAction.MalfunctionClassHack)
    if 'device' in _norm(h.get('kind') or '') or str(h.get('action') or '').startswith('DeviceAction.'):
        return -1
    t = _norm(h.get('action') or '') + ' ' + _norm(h.get('title') or '')
    for rank, kws in enumerate(HACK_PRIORITY):
        if any(kw in t for kw in kws):
            return len(HACK_PRIORITY) - rank
    return 0


_min_cost = [None, -1e9]   # (cout du hack le moins cher du dernier panneau lisible, heure) : RAM trop basse -> on n ouvre pas


def _readable(h: dict) -> bool:
    a, t = str(h.get('action') or ''), str(h.get('title') or '')
    return bool((a and not a.startswith('userdata')) or (t and not t.startswith('userdata')))


def quickhack_best(log=print, want=None) -> str | None:
    """Ouvre le panneau sur la cible visee, lit la liste (blackboard), choisit le meilleur hack
    utilisable (non verrouille, RAM suffisante), fait defiler jusqu a ce qu il soit SURLIGNE
    (boucle fermee sur qh.sel), applique (F), ferme (Retour arriere). Renvoie le titre applique.
    24/09 : liste REELLEMENT affichee (nom, cout, verrouille) ; rien d utilisable -> panneau referme sans hacker au
    hasard, renvoie 'ram' (RAM connue trop basse : on n ouvre meme pas) ; surlignage illisible -> rang dans la liste."""
    ram0 = (motion.read_state() or {}).get('ram')
    if ram0 is not None and _min_cost[0] is not None and ram0 < _min_cost[0] and time.perf_counter() - _min_cost[1] < 30.0:
        return 'ram'
    kbm.act_hold('scanner'); time.sleep(0.45)
    # 12/10 : en 3.0 le panneau s ouvre seul en mode scanner sur la cible du reticule ; F ici = appliquer le hack surligne
    # / cyberware iconique chez ce joueur -> plus de F d ouverture. Repli : une fois la touche d interaction (selectChoice).
    qh, st_q, nudged = None, None, False
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.4:
        st_q = motion.read_state() or st_q
        qh = (st_q or {}).get('qh')
        if qh and qh.get('open') and qh.get('list'):
            break
        if not nudged and time.perf_counter() - t0 > 0.6 and not (qh and qh.get('open')):
            nudged = True; kbm.act('interact', 0.1)
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
        if want is not None:
            # 12/10 : hack PRECIS demande (« Distraire les ennemis » sur un appareil-leurre)
            pool = [h for h in usable if want(h)]
            if not pool:
                log(f"  [combat] quickhacks : hack voulu absent ({len(usable)} utilisable(s)) : panneau referme")
                kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
                return 'aucun'
            chosen = pool[0]
        else:
            chosen = max(usable, key=_hack_score)
        if want is None and _hack_score(chosen) < 0:
            # 12/10 : rien que des hacks d APPAREIL (« Distraire les ennemis » d un distributeur...) : inutile en combat
            log(f"  [combat] quickhacks : seulement des hacks d appareil ({chosen.get('title') or chosen.get('action')}) : panneau referme")
            kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
            return 'aucun'
    if chosen is None:
        # 12/10 : plus de hack a l aveugle (F = cyberware iconique) : on dit pourquoi et on referme
        la = (st_q or {}).get('lookat') or {}
        why = 'panneau jamais ouvert' if not qh else ('panneau ouvert sans liste' if not qh.get('list') else f"liste illisible ({len(qh['list'])} entrees)")
        log(f"  [combat] quickhacks : {why} ; reticule {la.get('cls')} a {float(la.get('d') or 0):.0f} m : referme sans hacker")
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


_seen_pos: set = set()      # compteurs (heal, gren) deja vus > 0 : leur 0 veut vraiment dire « plus de charge »


def charges(st: dict, kind: str) -> bool:
    """24/09 : soin / grenade seulement s il reste une charge (le mod exporte res.heal / res.gren ; inconnu = on
    essaie, comme avant). 13:29-13:30 : 3 soins appuyes a vide a 40-44 % de vie, puis mort.
    24/09, journal 06:26 : le jeu a exporte heal=0 alors que V avait 3 soins -- un 0 ne bloque donc que si ce
    compteur a deja ete vu > 0 (sa semantique est alors prouvee) ; sinon V essaie, comme avant."""
    res = (st or {}).get('res') or {}
    if kind == 'gren' and res.get('gk') == 'launcher':
        kind = 'proj'                                   # molette = lance-projectiles : ses charges a lui
    n = res.get(kind + 'N')                             # 12/10 : VRAIES charges (GetGrenadeCharges...), pas des points de pool
    if isinstance(n, (int, float)):
        return n >= 1
    v = res.get(kind)                                   # ancien mod : comportement d avant
    if not isinstance(v, (int, float)):
        return True
    if v > 0:
        _seen_pos.add(kind)
        return True
    return kind not in _seen_pos


def n_foes(st: dict, alive: list) -> int:
    """Vrai nombre d hostiles vivants : la liste exportee est plafonnee a 6, le mod exporte aussi le total (foes.n,
    police a part dans foes.np : en combat elle compte, c est de la legitime defense)."""
    f = (st or {}).get('foes') or {}
    try:
        return max(len(alive), int(f.get('n') or 0) + int(f.get('np') or 0))
    except (TypeError, ValueError):
        return len(alive)


def _xhair_blocked(st: dict, e: dict) -> bool:
    """12/10 : un objet (voiture, caisse, porte...) plus proche que la cible est sous le reticule -> on tirerait dedans.
    Un PNJ sous le reticule ne bloque pas (souvent la cible elle-meme, ou un autre hostile)."""
    la = (st or {}).get('lookat') or {}
    d = la.get('d')
    if not isinstance(d, (int, float)) or la.get('cls') in (None, 'gamePuppet'):
        return False
    return d < 15.0 and d < float(e.get('d') or 0) - 3.0


def _alive(enemies, allow_police: bool = True):
    """Hostiles vivants. allow_police=False exclut la police (engagement proactif interdit).
    En combat (fight), la police reste une cible : c est de la legitime defense."""
    return [e for e in (enemies or []) if not e.get('dead') and not e.get('down') and (allow_police or not e.get('police'))]


# ---- grenade ADAPTEE a l ennemi (12/10, Olivier : « V doit adapter les projectiles qu il lance a l ennemi ») -------------
GREN_CODE = {'frag': 1, 'emp': 2, 'fire': 3, 'bio': 4, 'flash': 5, 'smoke': 6, 'cut': 7, 'recon': 8}   # = table fams du mod
GREN_HAVE: set | None = None        # familles possedees (inventory.manage / gren_refresh) ; vide = on garde l equipee
GREN_SWITCH_CD, GREN_FAIL_CD, GREN_GROUP_M = 3.0, 30.0, 5.0     # rayon frag ~4 m, IEM ~3,5 m
_gren_fail: dict = {}
_gren_last = [-99.0]


def _gren_parse(have) -> set:
    return {p.split(':')[0] for p in str(have or '').split(',') if p}


def gren_refresh(log=print) -> None:
    global GREN_HAVE
    r = nav._wait(nav._send({'cmd': 'grenade_select', 'x': 0}), timeout=1.5)
    if r and r.get('ok'):
        GREN_HAVE = _gren_parse(r.get('have'))
        log(f"  [combat] grenades possedees : {sorted(GREN_HAVE)} (molette : {r.get('prevk')})")


def _emp_ok(e: dict) -> bool:
    """Bonus IEM reel (emp.tweak:373-405 : androide / drone / mech, netrunner, exo) ou tourelle ; pas d immunite EMP."""
    return bool(e.get('emp') or e.get('kind') == 'turret') and 'E' not in (e.get('imm') or '')


def choose_grenade(far: list, alive: list, flee: bool = False) -> tuple:
    """-> (cible, famille voulue ou None = garder l equipee, raison)."""
    have = GREN_HAVE or set()

    def grp(t):
        return [x for x in alive if (x['x'] - t['x']) ** 2 + (x['y'] - t['y']) ** 2 <= GREN_GROUP_M ** 2]
    tgt = max(far, key=lambda t: (len(grp(t)), t['d']))              # amas le plus dense, puis le plus loin (ancien far[-1])
    g = grp(tgt)
    te = max(far, key=lambda t: sum(1 for x in grp(t) if _emp_ok(x)))
    ge = grp(te)
    ne = sum(1 for x in ge if _emp_ok(x))
    if 'emp' in have and ne and ne * 2 >= len(ge):                    # majorite sensible a l IEM autour de la cible
        return te, 'emp', f"{te.get('kind') or '?'}{' netrunner' if te.get('net') else ''} x{len(ge)}"
    if flee:
        if 'flash' in have and not any('F' in (x.get('imm') or '') or x.get('kind') == 'android' for x in g):
            return tgt, 'flash', 'fuite : aveugler'
        if 'smoke' in have:
            return tgt, 'smoke', 'fuite : ecran'
    humans = [x for x in g if x.get('kind', 'human') == 'human' and not _emp_ok(x)]
    if len(humans) >= 2:
        imm = ''.join(x.get('imm') or '' for x in humans)
        if 'fire' in have and imm.count('B') * 2 < len(humans):
            return tgt, 'fire', f'groupe de {len(humans)} humains'
        if 'bio' in have and imm.count('P') * 2 < len(humans):
            return tgt, 'bio', f'groupe de {len(humans)} humains'
    if 'frag' in have:
        return tgt, 'frag', 'defaut'
    return tgt, None, 'garder l equipee'


def select_grenade(kind, st: dict, log=print) -> bool:
    """Met `kind` sur la molette (raccourci RB). Vrai si c est (ou va etre) la grenade active."""
    global GREN_HAVE
    res = (st or {}).get('res') or {}
    if not kind:
        return False
    if res.get('gk') == kind:
        return True
    if res.get('gk') == 'launcher' or kind not in (GREN_HAVE or set()):
        return False
    now = time.perf_counter()
    if (st.get('cg') or 0) not in (0, -1) or now - _gren_last[0] < GREN_SWITCH_CD or now - _gren_fail.get(kind, -99.0) < GREN_FAIL_CD:
        return False
    _gren_last[0] = now
    r = nav._wait(nav._send({'cmd': 'grenade_select', 'x': GREN_CODE[kind]}), timeout=1.0) or {}
    ok = bool(r.get('ok') or r.get('pending'))
    if not ok:
        _gren_fail[kind] = now
        if r.get('have') is not None:
            GREN_HAVE = _gren_parse(r.get('have'))
    log(f"  [combat] molette : {r.get('prevk')} -> {kind} {'OK' if ok else 'ECHEC'} "
        f"({r.get('method') or r.get('reason') or 'mod muet'}, {r.get('charges')} charge(s), {time.perf_counter() - now:.2f} s)")
    return ok


# ---- visee --------------------------------------------------------------------------
_vsign = [1.0]
_last_sy = [None]


def aim_bearing(target_yaw: float, st: dict, gain: float = 0.5) -> float:
    err = motion.wrap(target_yaw - st['yaw'])
    dx = max(-motion.MAX_STEP, min(motion.MAX_STEP, motion.SIGN * err * motion.K_COUNTS_PER_DEG * gain))
    kbm.look(dx, 0)
    return abs(err)


def _ang_tol(d: float) -> float:
    """Demi-largeur d un torse vue de V (plancher 1,5 deg : 2-3 images de retard, cible en mouvement)."""
    return max(1.5, math.degrees(math.atan2(0.3, max(1.0, d))))


def _same_foe(tgt: dict, st: dict):
    """Le meme ennemi dans un etat plus recent (le mod n exporte pas d identifiant) : le plus proche a < 3 m."""
    best, bd = None, 3.0
    for x in _alive((st or {}).get('enemies')):
        if x.get('x') is None or tgt.get('x') is None:
            continue
        dd = math.hypot(x['x'] - tgt['x'], x['y'] - tgt['y'])
        if dd < bd:
            best, bd = x, dd
    return best


def _aim_lock(tgt: dict, st: dict, timeout: float = 0.8):
    """12/10 : vise la cible du quickhack sur des mesures FRAICHES jusqu a l avoir sous le reticule (le panneau ne s ouvre
    que sur la cible du reticule). (ok, st, raison)."""
    kbm.release('W'); kbm.act_release('sprint')
    t0, seq, cur, err, la = time.perf_counter(), (st or {}).get('seq'), tgt, 99.0, {}
    _last_sy[0] = None                          # nouvelle cible : ne pas inverser le sens vertical appris
    while time.perf_counter() - t0 < timeout:
        s2 = motion.read_state(seq, timeout=0.12)
        if s2 is None:
            continue
        st, seq = s2, s2.get('seq')
        nxt = _same_foe(cur, st)
        if nxt is None:
            return False, st, 'cible perdue'
        cur = nxt
        err = abs(motion.wrap(motion.bearing_to(st['x'], st['y'], cur['x'], cur['y']) - st['yaw']))
        la = st.get('lookat') or {}
        cls_ok = la.get('cls') == 'gamePuppet' or (la.get('cls') == 'gameDevice' and cur.get('kind') == 'turret')
        on = cls_ok and not la.get('dead') and abs(float(la.get('d') or 0) - float(cur.get('d') or 0)) < 3.0
        if on and err < 2.0 * _ang_tol(cur['d']):
            return True, st, 'ok'
        aim_at(cur, st)
    return False, st, f"ecart {err:.1f} deg, reticule {la.get('cls')} a {float(la.get('d') or 0):.0f} m"


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


# ---- arme a distance ADAPTEE (12/10) -------------------------------------------------------------------------------------
def _kind(e) -> str:
    k = str(e.get('kind') or 'human').lower()
    return k if k in KIND_W else 'human'


def _rng(w: dict) -> float:
    """Portee efficace (stat du mod), sinon une valeur par famille (revue : 30 m pour un pompe sans stat lue)."""
    r = w.get('rng') or 0
    return float(r) if r > 0 else FAM_RNG.get(FAMILY.get(w.get('type') or '') or '', 30.0)


def _falloff(d: float, rng: float) -> float:
    """APPROXIMATION : les vraies courbes de chute de degats ne sont pas lues."""
    return 1.0 if (not rng or d <= rng) else max(0.25, rng / d)


def weapon_score(w: dict, kind: str, d: float, high: bool, foe: dict | None = None) -> float:
    """Score d une arme contre UN ennemi. L element d une arme a feu n agit que par ses effets d etat (damageSystem.script:546) ;
    immunites lues par le mod (foe['imm'] : E=EMP, B=brulure, P=poison) ; foe['emp'] = mecanique / netrunner / exo."""
    fam = FAMILY.get(w.get('type') or '')
    if not fam or (w.get('ammo') is not None and w['ammo'] <= 0) or w.get('eq_ok') is False:
        return 0.0
    rng = _rng(w)
    s = (w.get('dps') or 0.0) * _falloff(d, rng)
    mech = kind in MECHANICAL
    imm = str((foe or {}).get('imm') or '')
    if (w.get('elem') == 'Electric' or (w.get('shock') or 0) > 0 or w.get('emp')) and 'E' not in imm:
        s *= 1.35 if (mech or (foe or {}).get('emp')) else 1.05
    if w.get('elem') == 'Chemical' or (w.get('poison') or 0) > 0:
        s *= 0.95 if (mech or 'P' in imm) else 1.10
    if (w.get('elem') == 'Thermal' or (w.get('burn') or 0) > 0) and not mech and 'B' not in imm:
        s *= 1.10
    if mech and fam in ('sniper', 'precision', 'pistol'):
        s *= 0.85
    if w.get('smart'):
        s *= (1.4 if (kind == 'drone' or high) else 1.0) if SMARTLINK else 0.6
    if fam == 'shotgun' and d > 1.2 * rng:
        s *= 0.3
    if fam == 'sniper':
        s *= 0.5 if d < 15 else 0.6 if kind == 'drone' else 1.15 if d > 35 else 0.8   # visee a 4 deg : un coup rate coute cher
    if fam == 'precision' and kind == 'human' and d > 30:
        s *= 1.10
    if kind == 'drone' and fam in ('smg', 'ar', 'lmg'):
        s *= 1.15
    if kind == 'mech' and fam in ('lmg', 'ar'):
        s *= 1.15
    return s


def _group_score(w, foes, z0) -> float:
    return sum(KIND_W[_kind(x)] * weapon_score(w, _kind(x), x['d'], (x.get('z', z0) - z0) > 2.5, x) for x in foes)


def _slot_key(slot: int):
    """Cle de l arme REELLEMENT dans l emplacement (roue = touche) ; '' si vide, None si illisible."""
    ws = nav._wait(nav._send({'cmd': 'wslots'}), timeout=0.8) or {}
    for sl in ws.get('slots') or []:
        if int(sl.get('slot') or 0) == int(slot):
            return sl.get('wkey') or sl.get('key') or ''
    return None


def swap_ranged(key: str, log=print, why: str = '') -> bool:
    global RANGED_KEY, WSWAP_FAILS
    if WSWAP_FAILS and time.perf_counter() - _WSWAP_T[0] > 900.0:
        WSWAP_FAILS = 0                                   # revue : un incident ne coupe pas la fonction pour toute la session
    w = ARSENAL.get(key)
    if not w or not RANGED_SLOT or WSWAP_FAILS >= 2 or str(RANGED_SLOT) == str(MELEE_SLOT):
        return False
    slot, t0 = int(RANGED_SLOT), time.perf_counter()
    r = nav._wait(nav._send({'cmd': 'equip_ws', 'x': w.get('i'), 'y': slot, 'z': float(w.get('seed') or 0), 'hash': int(w.get('h') or 0)}), timeout=1.0)
    if not (r and r.get('ok')):
        if (r or {}).get('absent'):
            ARSENAL.pop(key, None)                        # vendue / demontee depuis l inventaire : on l oublie
        log(f"  [armes] echange refuse : « {w.get('name')} » ({(r or {}).get('reason') or 'mod muet'})")
        return False
    if not r.get('already'):
        time.sleep(0.15)
        got = _slot_key(slot)
        if not got:
            log(f"  [armes] emplacement {slot} illisible apres l echange vers « {w.get('name')} »")
            return False                                  # pas une preuve d echec : pas de compteur
        if got != key:
            WSWAP_FAILS += 1; _WSWAP_T[0] = time.perf_counter()
            log(f"  [armes] emplacement {slot} : echange SANS EFFET vers « {w.get('name')} » (lu {got}, echec {WSWAP_FAILS}/2)")
            return False
    old = (ARSENAL.get(RANGED_KEY) or {}).get('name')
    RANGED_KEY, WSWAP_FAILS = key, 0
    log(f"  [armes] emplacement {slot} : « {old} » -> « {w.get('name')} » ({time.perf_counter() - t0:.2f} s){why}")
    return True


def gunfu_weapon(alive, st) -> str | None:
    """12/10 succes « Gun Fu » (dataTrackingSystem.script:644) : 3 eliminations au pistolet / revolver en 3,5 s, chacune a
    < 7,5 m. Si 3 humains visibles sont a < 7 m et V a au moins 50 % de vie : la cle du meilleur pistolet / revolver."""
    try:
        from . import achievements as _ach
        if 'GunKata' in (_ach._STATE.get('unlocked') or set()):
            return None
    except Exception:
        pass
    hp = (st or {}).get('hp')
    if isinstance(hp, (int, float)) and hp < 50:
        return None
    close = [x for x in alive if _kind(x) == 'human' and not x.get('police') and x.get('d') is not None and x['d'] < 7.0
             and x.get('los') is not False]
    if len(close) < 3:
        return None
    guns = [((w.get('dps') or 0), k) for k, w in ARSENAL.items()
            if FAMILY.get(w.get('type') or '') == 'pistol' and (w.get('ammo') is None or w['ammo'] > 0) and w.get('eq_ok') is not False]
    return max(guns)[1] if guns else None


def _is_distract(h: dict) -> bool:
    t = _norm(h.get('action') or '') + ' ' + _norm(h.get('title') or '')
    return 'distract' in t or 'distraire' in t


def rats_decoy(e: dict, st: dict, log=print):
    """12/10 succes « Surement des rats » (dataTrackingSystem : +1 quand un PNJ enquete sur un appareil distrait, 30) :
    appareil piratable a < 20 m de V et < 12 m de la cible inconsciente -> « Distraire les ennemis ». Vrai si lance."""
    try:
        from . import achievements as _ach
        if 'MustBeTheRats' in (_ach._STATE.get('unlocked') or set()):
            return False
    except Exception:
        pass
    r = nav._wait(nav._send({'cmd': 'devices_near', 'x': 20}), timeout=1.5) or {}
    devs = [dv for dv in (r.get('devices') or []) if 3.0 < float(dv.get('d') or 0) <= 20.0
            and math.hypot(dv['x'] - e['x'], dv['y'] - e['y']) <= 12.0]
    if not devs:
        return False
    dv = min(devs, key=lambda x: math.hypot(x['x'] - e['x'], x['y'] - e['y']))
    for _ in range(3):
        s2 = motion.read_state(timeout=0.2) or st
        r2 = nav._wait(nav._send({'cmd': 'devices_near', 'x': 20}), timeout=1.0) or {}
        cur = min((x for x in (r2.get('devices') or []) if math.hypot(x['x'] - dv['x'], x['y'] - dv['y']) < 1.0),
                  key=lambda x: x['d'], default=dv)
        if aim_at(cur, s2) < 2.0:
            break
        time.sleep(0.1)
    title = quickhack_best(log=log, want=_is_distract)
    if title and title not in ('ram', 'aucun'):
        log(f"  [succes] Surement des rats : « {title} » sur « {dv.get('name')} » a {dv['d']:.0f} m (cible a "
            f"{math.hypot(dv['x'] - e['x'], dv['y'] - e['y']):.0f} m du leurre)")
        return True
    return False


def prepare_ranged(alive, st, log=print) -> None:
    """Avant de degainer RANGED_SLOT (V tient encore la melee) : y range la meilleure arme pour CES ennemis."""
    if not ARSENAL or not RANGED_SLOT:
        return
    foes = [x for x in alive if not x.get('police') and x.get('d') is not None and x['d'] < 70] or list(alive[:1])
    if not foes:
        return
    sig = tuple(sorted((_kind(x), int(x['d'] // 10)) for x in foes[:6]))
    now = time.perf_counter()
    if sig == LAST_PICK['sig'] and now - LAST_PICK['t'] < 20.0:
        return
    LAST_PICK.update(sig=sig, t=now)
    global RANGED_KEY
    real = _slot_key(int(RANGED_SLOT))                     # revue : ce qui est VRAIMENT dans l emplacement
    if real is not None:
        RANGED_KEY = real or None
    z0 = st.get('z', 0.0) if st else 0.0
    ranked = sorted(((_group_score(w, foes, z0), k) for k, w in ARSENAL.items()), reverse=True)
    if not ranked or ranked[0][0] <= 0:
        return
    s_best, k_best = ranked[0]
    s_cur = _group_score(ARSENAL[RANGED_KEY], foes, z0) if RANGED_KEY in ARSENAL else 0.0
    comp = ', '.join(f"{_kind(x)} {x['d']:.0f} m" + (' haut' if (x.get('z', z0) - z0) > 2.5 else '') for x in foes[:6])
    w = ARSENAL[k_best]
    log(f"  [armes] choix : [{comp}] -> « {w.get('name')} » ({FAMILY.get(w.get('type'))}/{w.get('evo')}/{w.get('elem')}, R{(w.get('rng') or 0):.0f}) "
        f"score {s_best:.0f}" + (f" ; 2e « {ARSENAL[ranked[1][1]].get('name')} » {ranked[1][0]:.0f}" if len(ranked) > 1 else '')
        + f" ; en place « {(ARSENAL.get(RANGED_KEY) or {}).get('name')} » {s_cur:.0f}")
    for s_k, k_k in ranked:                                          # revue : arme vendue / demontee -> la suivante
        if k_k == RANGED_KEY or s_k <= 0 or s_k <= 1.2 * s_cur:      # hysteresis : pas d echange pour +5 %
            break
        if swap_ranged(k_k, log) or k_k in ARSENAL:                  # echange fait, ou refus autre que « absente »
            break


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
    rats_n = 0                                     # 12/10 : leurres « Distraire les ennemis » (succes)
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
            # 12/10 succes « Surement des rats » : cible inconsciente, V accroupi -> un appareil-leurre pres d elle (2 max)
            if stealth and rats_n < 2 and not st.get('combat') and not rescue and 8.0 < e['d'] < 25.0 and gap < 30                     and e.get('x') is not None and time.perf_counter() - t_hack0 > 2.5:
                rats_n += 1; t_hack0 = time.perf_counter()
                if rats_decoy(e, st, log=log):
                    time.sleep(5.0)                         # l ennemi va enqueter (c est ce qui compte pour le succes)
                    seq = None; continue
            # HACKS D OUVERTURE : avant le contact, V pirate a distance jusqu a 3 hostiles differents (RAM permettant)
            if hacks_done < max_hacks and e['d'] > 12.0 and gap < 8 and time.perf_counter() - t0 > 1.0 and time.perf_counter() - t_hack0 > 2.5:
                vis = [x for x in (alive if len(alive) > 1 else [e])]
                tgt = vis[hacks_done % len(vis)]
                if tgt is not e:
                    aim_at(tgt, st)
                title = quickhack_best(log=log)
                if title in ('ram', 'aucun'):
                    hacks_done = max_hacks; t_hack0 = time.perf_counter(); seq = None
                    log('  [combat] ' + ('RAM insuffisante' if title == 'ram' else 'aucun hack offensif') + ' : pas de hack d ouverture, on engage')
                    continue
                hacks_done += 1; hacked = True; t_hack0 = time.perf_counter(); seq = None
                log(f"  [combat] hack d ouverture « {title or 'par defaut'} » sur cible a {tgt['d']:.0f} m ({hacks_done}/{max_hacks})")
                continue
            # DECLENCHER le combat : a distance, quelques tirs alignes (style mixte / distance) ; au contact, un coup
            if RANGED_SLOT and _C3.style != 'melee' and 12.0 < e['d'] < RANGED_MAX_M and gap < 6 and struck < 2 and time.perf_counter() - t0 > 1.0:
                kbm.release('W'); kbm.act_release('sprint')
                if struck == 0:
                    prepare_ranged([e], st, log)                   # 12/10 : la bonne arme dans l emplacement avant de degainer
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
    qh_skip_t, qh_fail = -99.0, 0                      # 12/10 : quickhack reporte (cible pas sous le reticule) : message / echecs
    mode = 'melee'; shots = 0
    blocked_t, blocked_log_t = None, -99.0   # 12/10 : pas de ligne de tir depuis... / dernier message
    adv_t0, adv_d0, adv_block = None, 0.0, -1.0   # 12/10 : pompe hors de portee (avance verifiee)
    gunfu_until, gunfu_t = -1.0, -99.0            # 12/10 : mode Gun Fu (succes) : jusqu a / dernier essai
    dry, dry_until, t_reload = 0, -1.0, -99.0     # 12/10 : chargeur vide sans reserve -> corps a corps
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
            if mode == 'ranged' and e.get('los') is False:
                vis = [x for x in alive if x.get('los') is True]       # 12/10 : l ennemi VISIBLE le plus proche d abord
                if vis:
                    e = vis[0]
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
                    # 12/10 : grenade de fuite lancee EN FACE du groupe, avant de tourner le dos (elle partait devant V)
                    if now - t_gren > 6.0 and charges(st, 'gren'):
                        cand = [x for x in alive if x['d'] > 8.0 and x.get('los') is not False]
                        if cand:
                            tgt_f, fam_f, why_f = choose_grenade(cand, alive, flee=True)
                            select_grenade(fam_f, st, log)
                            if abs(aim_at(tgt_f, st)) < 25:
                                kbm.mouse_tap('middle', 0.12); t_gren = now; stats['grenades'] += 1
                                log(f"  [combat] grenade de fuite ({fam_f or 'equipee'} : {why_f})"); time.sleep(0.25)
                away = motion.wrap(motion.bearing_to(cx, cy, st['x'], st['y']))
                aim_bearing(away, st, gain=0.7)
                kbm.hold('W'); kbm.act_hold('sprint')
                if now - t_heal > HEAL_CD and hp < 70 and now - t_gren > 0.8 and charges(st, 'heal'):
                    kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
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
            # -- grenade ADAPTEE (12/10) : cible = amas le plus dense ; famille selon la nature des ennemis
            far = [x for x in alive if GRENADE_MIN_M <= x['d'] <= GRENADE_MAX_M and x.get('los') is not False]   # pas dans un mur
            if far and (len(alive) >= 2 or any(_emp_ok(x) for x in far)) and now - t_gren > GRENADE_CD \
                    and now - t_heal > 0.8 and charges(st, 'gren'):
                tgt, fam, why = choose_grenade(far, alive)
                if fam and select_grenade(fam, st, log):
                    st2 = motion.read_state(seq, timeout=0.4)          # res.gk / grenN relus apres le changement
                    if st2:
                        st = st2; seq = st2['seq']
                    if not charges(st, 'gren'):
                        t_gren = now; continue                          # la nouvelle famille coute plus que la reserve
                gk = (st.get('res') or {}).get('gk') or '?'
                gap = aim_at(tgt, st)
                if gap < 10:
                    kbm.mouse_tap('middle', 0.12); t_gren = now; stats['grenades'] += 1
                    stats['gren_' + gk] = stats.get('gren_' + gk, 0) + 1
                    log(f"  [combat] grenade {gk} (voulue {fam or '-'} : {why}) sur {tgt.get('kind') or '?'} "
                        f"a {tgt['d']:.0f} m ({len(alive)} hostiles)")
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
            # 12/10 succes « Gun Fu » : 3 humains a < 7 m -> pistolet / revolver, 8 s de tirs rapproches (une fois / min)
            gf_key = None
            if RANGED_SLOT is not None and mode != 'ranged' and now >= gunfu_until and now - gunfu_t > 60.0 and now >= dry_until:
                gf_key = gunfu_weapon(alive, st)
                if gf_key:
                    gunfu_t = now
                    if gf_key == RANGED_KEY or swap_ranged(gf_key, log, why=' (Gun Fu)'):
                        gunfu_until = now + 8.0
                        log(f"  [succes] Gun Fu : {sum(1 for x in alive if x['d'] < 7.0)} ennemis a < 7 m -> « {(ARSENAL.get(gf_key) or {}).get('name')} » 8 s")
            gunfu = now < gunfu_until
            want_ranged = RANGED_SLOT is not None and now >= dry_until and (gunfu or high or rmin < e['d'] < RANGED_MAX_M)
            if want_ranged and mode != 'ranged':
                if not gunfu:
                    prepare_ranged(alive, st, log)                 # 12/10 : arme adaptee aux ennemis, avant de degainer
                if draw_slot(RANGED_SLOT, False):
                    mode = 'ranged'; shots = 0; time.sleep(0.3)
                else:
                    wsync_fail += 1                                    # l arme a feu ne sort pas : on reste melee et on fonce
                log(f"  [combat] arme a distance (cible a {e['d']:.0f} m{', en hauteur' if high else ''})")
            elif mode == 'ranged' and not gunfu and ((e['d'] <= rback and not high) or now < dry_until):
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
                if e['d'] > 3.0 and gap < 8 and pressure < 2 and now - t_hack > 2 * QUICKHACK_CD and (blind_hacks < 3 or now - blind_hacks_t > 20.0):   # vise d abord ; 8 s entre deux hacks
                    cands = [x for x in alive if x.get('los') is not False] or [e]   # 12/10 : cibles VISIBLES
                    tgt = cands[hack_i % len(cands)]; hack_i += 1
                    ok_aim, st, why_aim = _aim_lock(tgt, st)
                    if not ok_aim:
                        qh_fail += 1
                        t_hack = time.perf_counter() - 2 * QUICKHACK_CD + (2.0 if qh_fail < 3 else 15.0)   # vrai delai
                        stats['qh_sans_visee'] = stats.get('qh_sans_visee', 0) + 1
                        if now - qh_skip_t > 10.0:
                            qh_skip_t = now; log(f"  [combat] quickhack reporte : reticule pas sur la cible a {tgt['d']:.0f} m ({why_aim})")
                        seq = None; continue
                    qh_fail = 0
                    title = quickhack_best(log=log)
                    t_hack = time.perf_counter()                        # le delai court APRES le hack (il dure ~4 s)
                    if title in ('ram', 'aucun'):
                        log('  [combat] quickhack : ' + ('RAM insuffisante' if title == 'ram' else 'aucun hack offensif') + ', on tire'); seq = None; continue
                    stats['quickhacks'] += 1 if title else 0
                    if title and str(title).startswith('userdata'):
                        title = None
                    if title:
                        blind_hacks = 0
                    else:
                        blind_hacks = blind_hacks + 1 if now - blind_hacks_t < 20.0 else 1
                        blind_hacks_t = now
                    if title:
                        log(f"  [combat] quickhack « {title} » (a distance) sur cible a {tgt['d']:.0f} m")
                    if blind_hacks >= 3:
                        log('  [combat] 3 quickhacks non lances de suite : pause de 20 s avant de retenter, on tire')
                    seq = None; continue
                if _C2.features.get('sprint', True) and now - t_sprint > (2.5 if hp < 85 else 5.0):
                    side = 'D' if side == 'A' else 'A'
                    sprint_pass(e, st, 1.0 if side == 'D' else -1.0, dur=0.8)   # sprint lateral = regeneration + cible mouvante
                    t_sprint = now; stats['sprints'] = stats.get('sprints', 0) + 1
                    seq = None; continue
                if gap < 4 and holding_ranged and (e.get('los') is False or _xhair_blocked(st, e)):
                    # 12/10 (Olivier : « V tire trop souvent sur un mur ») : pas de ligne de tir -> on ne tire pas, on se
                    # decale (1,5 s), puis on avance vers la cible
                    if blocked_t is None:
                        blocked_t = now
                    if now - blocked_log_t > 10.0:
                        blocked_log_t = now
                        why_b = 'mur' if e.get('los') is False else f"objet a {((st.get('lookat') or {}).get('d') or 0):.0f} m sous le reticule"
                        log(f"  [combat] pas de ligne de tir vers la cible a {e['d']:.0f} m ({why_b}) : V se repositionne")
                    stats['sans_ligne_de_tir'] = stats.get('sans_ligne_de_tir', 0) + 1
                    if now - blocked_t < 1.5:
                        side = 'D' if side == 'A' else 'A'; strafe(side, 0.5)
                    else:
                        kbm.hold('W'); time.sleep(0.7); kbm.release('W')
                        if now - blocked_t > 4.0:
                            blocked_t = None                            # nouveau cycle : decalage de l autre cote
                    seq = None; continue
                blocked_t = None
                cur_w = ARSENAL.get(st.get('wk')) or {}
                sg_far = (gap < 4 and holding_ranged and FAMILY.get(cur_w.get('type') or '') == 'shotgun'
                          and e['d'] > 1.2 * _rng(cur_w) and not high and now >= adv_block
                          and (e.get('z', st.get('z', 0.0)) - st.get('z', 0.0)) > -2.0)
                if sg_far and adv_t0 is not None and now - adv_t0 > 3.0:
                    if e['d'] > adv_d0 - 1.0:                          # pas de progres (rambarde, eau, vide)
                        adv_block = now + 10.0; sg_far = False
                        log(f"  [armes] pompe a {e['d']:.0f} m : V ne se rapproche pas -> il tire quand meme")
                    adv_t0 = None
                if sg_far:
                    # 12/10 : fusil a pompe hors de sa portee (10 m) : les plombs ne font rien a 30 m -> on avance
                    if adv_t0 is None:
                        adv_t0, adv_d0 = now, e['d']
                    kbm.hold('W'); time.sleep(0.6); kbm.release('W')
                    seq = None; continue
                adv_t0 = None
                if gap < 4 and holding_ranged:
                    kbm.mouse('right', True); time.sleep(0.15)          # viser
                    kbm.mouse_tap('left', 0.12); shots += 1; stats['tirs'] = stats.get('tirs', 0) + 1
                    kbm.mouse('right', False)
                    wc = st.get('wclip')                                # munitions AVANT ce tir (12/10)
                    if isinstance(wc, (int, float)):
                        if wc == 0 and now - t_reload > 3.5:
                            dry += 1                                    # rechargement precedent : rien charge
                            if dry >= 2:
                                dry, dry_until = 0, now + 45.0
                                if cur_w:
                                    cur_w['ammo'] = 0
                                log(f"  [armes] plus de munitions pour « {cur_w.get('name') or st.get('weapon')} » : corps a corps 45 s")
                        elif wc > 0:
                            dry = 0
                        need_reload = wc <= 1
                    else:
                        need_reload = shots % 8 == 0
                    if need_reload and now - t_reload > 1.5:
                        kbm.act('reload', 0.1); time.sleep(1.2); t_reload = now   # recharger
                    if now - t_strafe > STRAFE_CD:
                        side = 'D' if side == 'A' else 'A'; strafe(side, 0.3); t_strafe = now
                time.sleep(0.15); continue

            # -- quickhack a distance
            if e['d'] > 3.0 and gap < 8 and pressure < 2 and now - t_hack > QUICKHACK_CD and (blind_hacks < 3 or now - blind_hacks_t > 20.0):
                cands = [x for x in alive if x.get('los') is not False] or [e]   # 12/10 : cibles VISIBLES
                tgt = cands[hack_i % len(cands)]; hack_i += 1
                ok_aim, st, why_aim = _aim_lock(tgt, st)
                if not ok_aim:
                    qh_fail += 1
                    t_hack = time.perf_counter() - QUICKHACK_CD + (2.0 if qh_fail < 3 else 15.0)   # vrai delai
                    stats['qh_sans_visee'] = stats.get('qh_sans_visee', 0) + 1
                    if now - qh_skip_t > 10.0:
                        qh_skip_t = now; log(f"  [combat] quickhack reporte : reticule pas sur la cible a {tgt['d']:.0f} m ({why_aim})")
                    seq = None; continue
                qh_fail = 0
                if tgt['d'] > 3.0:
                    e = tgt
                title = quickhack_best(log=log)
                t_hack = time.perf_counter()
                if title in ('ram', 'aucun'):
                    log('  [combat] quickhack : ' + ('RAM insuffisante' if title == 'ram' else 'aucun hack offensif')); seq = None; continue
                stats['quickhacks'] += 1 if title else 0
                if title and not str(title).startswith('userdata'):
                    blind_hacks = 0
                else:
                    blind_hacks = blind_hacks + 1 if now - blind_hacks_t < 20.0 else 1
                    blind_hacks_t = now
                if title and str(title).startswith('userdata'):
                    title = None
                if title:
                    log(f"  [combat] quickhack « {title} » sur cible a {e['d']:.0f} m")
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


