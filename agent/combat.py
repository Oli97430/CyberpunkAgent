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

from . import input_kbm as kbm, motion

HEAL_BELOW, HEAL_CD = 45.0, 5.0
RETREAT_HP, RETREAT_PRESSURE, RETREAT_S = 35.0, 2, 2.5
RETREAT_COOLDOWN = 10.0         # pas deux replis a moins de 10 s : 25 replis/combat = V fuyait au lieu de frapper
OUTNUMBERED = 4                 # a partir de 4 hostiles : decrocher plutot que tenir
GRENADE_MIN_M, GRENADE_MAX_M, GRENADE_CD = 11.0, 28.0, 20.0
MELEE_RANGE = 2.4
MELEE_SLOT = '3'                          # fixe par inventory.manage() : emplacement au meilleur DPS de melee
RANGED_SLOT = None                        # fixe par inventory.manage() si une arme a distance est equipee
RANGED_MIN_M, RANGED_MAX_M = 14.0, 45.0   # au-dela de 14 m (ou cible en hauteur), on tire si on a une arme
CYBERWARE_CD = 18.0                       # touche : kbm.K('iconic') (F chez ce joueur)
# quick melee : kbm.K('quickmelee') (~ chez ce joueur)
QUICKHACK_CD = 5.0
KNIFE_CD = 4.0
HACK_ROTATION = (0, 1, 2)     # on alterne les 3 premiers hacks du panneau (le meilleur n est pas forcement le 1er)
DODGE_CD, STRAFE_CD, JUMP_ATTACK_CD, SLIDE_CD = 3.0, 1.6, 6.0, 8.0


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


# Priorite des hacks (mots-cles francais, insensibles a la casse) : du plus decisif au moins.
# Cles d action (TweakDB, ex. QuickHack.OverheatHack) : independantes de la langue du jeu.
HACK_PRIORITY = ('cyberpsycho', 'suicide', 'overheat', 'shortcircuit', 'contagion', 'synapse', 'detonate',
                 'grenade', 'weaponmalfunction', 'malfunction', 'cripple', 'rebootoptics', 'blind', 'memorywipe',
                 'whistle', 'ping')


def _hack_score(h: dict) -> int:
    t = ((h.get('action') or '') + ' ' + (h.get('title') or '')).lower().replace('_', '')
    for rank, kw in enumerate(HACK_PRIORITY):
        if kw in t:
            return len(HACK_PRIORITY) - rank
    return 0


def quickhack_best(log=print) -> str | None:
    """Ouvre le panneau sur la cible visee, lit la liste (blackboard), choisit le meilleur hack
    utilisable (non verrouille, RAM suffisante), fait defiler jusqu a ce qu il soit SURLIGNE
    (boucle fermee sur qh.sel), applique (F), ferme (Retour arriere). Renvoie le titre applique."""
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
    if qh and qh.get('list'):
        ram = qh.get('ram')
        usable = [h for h in qh['list'] if not h.get('locked') and (ram is None or (h.get('cost') or 0) <= ram)]
        if usable:
            chosen = max(usable, key=_hack_score)
    if chosen is None:
        # liste indisponible : hack surligne par defaut (comportement a l aveugle)
        kbm.act('ui_confirm', 0.1); time.sleep(0.25)
        kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
        return None
    # amener la selection sur le hack choisi (molette), 8 crans max
    for _ in range(8):
        st = motion.read_state()
        sel = ((st or {}).get('qh') or {}).get('sel') or ''
        if chosen.get('action') and sel.strip().lower() == chosen['action'].strip().lower():
            break
        kbm.wheel(-1); time.sleep(0.12)
    kbm.act('ui_confirm', 0.1); time.sleep(0.25)
    kbm.act_release('scanner'); time.sleep(0.15); kbm.tap('BACKSPACE', 0.08)
    return chosen.get('title') or chosen.get('action')


def knife_throw() -> None:
    """Couteau (Nehan = arme active relevee) : viser (clic droit maintenu) + clic gauche = lancer."""
    kbm.mouse('right', True); time.sleep(0.35)
    kbm.mouse_tap('left', 0.1); time.sleep(0.15)
    kbm.mouse('right', False)


def _alive(enemies, allow_police: bool = True):
    """Hostiles vivants. allow_police=False exclut la police (engagement proactif interdit).
    En combat (fight), la police reste une cible : c est de la legitime defense."""
    return [e for e in (enemies or []) if not e.get('dead') and (allow_police or not e.get('police'))]


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


# ---- engagement (avant que le jeu ne passe en combat) -----------------------------------
def engage(target: dict, stop=None, log=print, max_s: float = 25.0) -> bool:
    t0 = time.perf_counter()
    kbm.tap(MELEE_SLOT, 0.08)
    seq = None
    hacked = False
    try:
        while time.perf_counter() - t0 < max_s:
            if stop is not None and stop.is_set():
                return False
            st = motion.read_state(seq, timeout=0.3)
            if st is None:
                continue
            seq = st['seq']
            if st.get('combat'):
                return True
            alive = _alive(st.get('enemies'), allow_police=False)
            if not alive:
                # cible imposee (agresseur pas encore hostile a V) : on la suit par sa position initiale
                if target and target.get('x') is not None and time.perf_counter() - t0 < 12.0:
                    dd = math.hypot(target['x'] - st['x'], target['y'] - st['y'])
                    alive = [{'x': target['x'], 'y': target['y'], 'd': dd, 'sy': None}]
                else:
                    return False
            e = alive[0]
            gap = aim_at(e, st)
            if not hacked and e['d'] > 6.0 and gap < 8 and time.perf_counter() - t0 > 1.0:
                quickhack_first(); hacked = True; seq = None; continue
            if e['d'] > MELEE_RANGE:
                kbm.hold('W')
                if e['d'] > 8.0: kbm.act_hold('sprint')
                else: kbm.act_release('sprint')
            else:
                kbm.release('W'); kbm.act_release('sprint')
                if gap < 25:
                    heavy_attack(); time.sleep(0.2)
    finally:
        kbm.release_all()
    return False


# ---- combat -----------------------------------------------------------------------------
def fight(stop=None, log=print, max_s: float = 180.0) -> dict:
    t0 = time.perf_counter()
    stats = {'soins': 0, 'grenades': 0, 'coups': 0, 'charges': 0, 'esquives': 0, 'strafes': 0,
             'parades': 0, 'replis': 0, 'sauts': 0, 'glissades': 0, 'cyberware': 0, 'quickhacks': 0, 'quick_melee': 0}
    t_heal = t_gren = t_cyber = t_hack = t_dodge = t_strafe = t_jump = t_slide = t_knife = -99.0
    mode = 'melee'; shots = 0
    hack_i = 0
    last_hp, last_hp_t = None, time.perf_counter()
    seq = None
    combo = 0
    side = 'A'
    retreat_until = 0.0
    flee_until, last_flee_end = 0.0, -999.0
    t_buff = -999.0
    sterile_sig, sterile_t = None, time.perf_counter()
    last_retreat_end = -99.0
    no_target_logged = False
    police_logged = False
    kbm.tap(MELEE_SLOT, 0.08)
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
            if hp < HEAL_BELOW and now - t_heal > HEAL_CD:
                kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
                log(f'  [combat] soin (vie {hp:.0f} %)')

            # -- chute brutale de vie -> esquive arriere immediate
            if last_hp is not None and last_hp - hp > 15 and now - last_hp_t < 1.5 and now - t_dodge > 0.8:
                kbm.release('W'); dodge('S'); t_dodge = now; stats['esquives'] += 1
            if last_hp is None or now - last_hp_t > 1.5:
                last_hp, last_hp_t = hp, now

            alive = _alive(st.get('enemies'))
            if not st.get('combat'):
                log('  [combat] fin : plus en combat'); break
            # combat STERILE : rien ne change depuis 75 s (meme nombre d hostiles, vie intacte) -> cibles
            # injoignables (vitre, autre etage, tourelle hors portee) : on arrete de taper dans le vide
            sig = (len(alive), hp >= 90)
            if sig != sterile_sig:
                sterile_sig, sterile_t = sig, now
            elif hp >= 90 and now - sterile_t > 75.0:
                log(f'  [combat] combat sterile : {len(alive)} hostile(s) intouchable(s) depuis 75 s -> on laisse tomber')
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
            want_flee = len(alive) >= 6 or (len(alive) >= 4 and hp < 35) or (len(alive) >= 2 and hp < 22)
            if now < flee_until or (want_flee and now - last_flee_end > 4.0):
                if now >= flee_until:
                    flee_until = now + 8.0; last_flee_end = flee_until; stats['fuites'] = stats.get('fuites', 0) + 1
                    log(f'  [combat] FUITE ({len(alive)} hostiles, vie {hp:.0f} %) : on decroche')
                away = motion.wrap(motion.bearing_to(cx, cy, st['x'], st['y']))
                aim_bearing(away, st, gain=0.7)
                kbm.hold('W'); kbm.act_hold('sprint')
                if now - t_heal > HEAL_CD and hp < 70:
                    kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
                if now - t_gren > 6.0 and e['d'] > 6.0:
                    kbm.mouse_tap('middle', 0.12); t_gren = now; stats['grenades'] += 1   # grenade vers l arriere (on regarde devant : elle part devant... on ne vise pas)
                if now - t_dodge > 2.0:
                    dodge('W'); t_dodge = now; stats['esquives'] += 1
                continue
            # -- REPLI tactique : vie basse sous pression -> dos aux ennemis, sprint, soin
            outnumbered = len(alive) >= OUTNUMBERED
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
                if now - t_heal > HEAL_CD and hp < HEAL_BELOW:
                    kbm.act('consumable', 0.1); t_heal = now; stats['soins'] += 1
                continue
            kbm.act_release('sprint')

            # -- grenade sur un ennemi ELOIGNE (pas forcement le plus proche), si groupe
            far = [x for x in alive if GRENADE_MIN_M <= x['d'] <= GRENADE_MAX_M]
            if far and len(alive) >= 2 and now - t_gren > GRENADE_CD:
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
            high = (e.get('z', st['z']) - st['z']) > 2.5
            want_ranged = RANGED_SLOT is not None and (high or RANGED_MIN_M < e['d'] < RANGED_MAX_M)
            if want_ranged and mode != 'ranged':
                kbm.tap(RANGED_SLOT, 0.08); mode = 'ranged'; shots = 0; time.sleep(0.5)
                log(f"  [combat] arme a distance (cible a {e['d']:.0f} m{', en hauteur' if high else ''})")
            elif not want_ranged and mode == 'ranged' and e['d'] <= RANGED_MIN_M - 3 and not high:
                kbm.tap(MELEE_SLOT, 0.08); mode = 'melee'; time.sleep(0.4)
                log('  [combat] retour au corps a corps')
            if mode == 'ranged':
                kbm.release('W'); kbm.act_release('sprint')
                if gap < 4:
                    kbm.mouse('right', True); time.sleep(0.15)          # viser
                    kbm.mouse_tap('left', 0.12); shots += 1; stats['tirs'] = stats.get('tirs', 0) + 1
                    kbm.mouse('right', False)
                    if shots % 8 == 0:
                        kbm.act('reload', 0.1); time.sleep(1.2)               # recharger
                    if now - t_strafe > STRAFE_CD:
                        side = 'D' if side == 'A' else 'A'; strafe(side, 0.3); t_strafe = now
                time.sleep(0.15); continue

            # -- quickhack a distance
            if e['d'] > 3.0 and gap < 8 and now - t_hack > QUICKHACK_CD:
                title = quickhack_best(log=log); t_hack = now; stats['quickhacks'] += 1
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
                if e['d'] > 7.0:
                    kbm.act_hold('sprint')
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
            if pressure >= 2 and combo % 5 == 0:
                block(0.3); stats['parades'] += 1
            elif now - t_dodge > DODGE_CD and combo % 4 == 0:
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
    bodies += [e for e in (st.get('enemies') or []) if e.get('dead') and e['d'] <= radius_m]
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


