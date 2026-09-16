"""
driving.py -- competence "conduire" v1, par AUTODRIVE (patch 2.3 : touche vehicleAutodrive = G chez
ce joueur) : la voiture roule seule vers l objectif suivi.

Sequence : appeler le vehicule (callVehicle = V) -> attendre qu il arrive (< 25 m) -> aller a
1-2 m du centre -> poses (face au vehicule, regard vers la selle 30-75 deg) + E -> etat
vehicle=true -> G maintenu 1,5 s (autodrive) -> surveiller la distance a l objectif -> a l arrivee (< 60 m)
ou au bout de MAX_DRIVE_S, descendre (exitVehicle = F en vehicule chez ce joueur).
Les COURSES (competition) ne sont pas couvertes : l autodrive ne fait pas la course.
"""
from __future__ import annotations

import math
import time

from . import input_kbm as kbm, motion

MAX_DRIVE_S = 300.0
ARRIVE_M = 60.0


def look_smooth(dx: float, dy: float, step: int = 60, dt: float = 0.012) -> None:
    """Deplacement camera par petits paquets : un seul gros MouseMove est ecrete par le jeu
    (V « ne baisse pas assez le regard » avec un -1500 d un coup)."""
    n = max(1, int(max(abs(dx), abs(dy)) // step) + 1)
    for _ in range(n):
        kbm.look(dx / n, dy / n); time.sleep(dt)


def autodrive_toggle() -> None:
    """Lance l autodrive : G MAINTENU 1,5 s (verifie en jeu le 12/09 : la moto part en < 5 s ;
    un appui simple ne fait rien, et un appui simple AVANT le maintien annule l effet)."""
    kbm.act('autodrive', 1.5)


def _dist(st, tx, ty):
    return math.hypot(st['x'] - tx, st['y'] - ty)


def summon_and_board(stop=None, log=print) -> bool:
    """Appelle le vehicule et monte dedans. True si V est en vehicule a la fin."""
    st = motion.read_state()
    if not st:
        return False
    if st.get('vehicle'):
        return True
    if not kbm.ACTIONS.get('callvehicle'):
        log('  [conduite] pas de touche « appeler le vehicule »'); return False
    # au hasard : une voiture ou une moto parmi celles de V (VehicleSystem) ; a defaut la touche d appel
    import random
    from . import nav
    want = random.choice((0, 1, 2))                      # 0 = n importe lequel, 1 = voiture, 2 = moto
    # VOIE LIBRE : on n appelle pas le vehicule au milieu de la circulation (il arrive sur la route la plus proche et
    # se fait bloquer / percuter) : on attend que plus aucun vehicule d inconnu ne roule a moins de 30 m (12 s max),
    # et si ca ne se calme pas, V s ecarte de quelques metres avant d appeler
    t_wait = time.perf_counter(); waited = False
    while time.perf_counter() - t_wait < 12.0:
        if stop is not None and stop.is_set():
            return False
        s_t = motion.read_state() or {}
        if int(s_t.get('traffic') or 0) == 0:
            break
        if not waited:
            waited = True; log(f"  [conduite] circulation ({s_t.get('traffic')} vehicule(s) en mouvement a < 30 m) : on attend une voie libre")
        time.sleep(0.5)
    else:
        log('  [conduite] circulation persistante : V s ecarte de la voie avant d appeler')
        motion.turn_by(90.0, timeout=1.5, stop=stop)
        kbm.hold('W'); time.sleep(1.6); kbm.release('W')
    rv = nav._wait(nav._send({'cmd': 'vehicle_call', 'x': want}), timeout=5.0)
    if rv and rv.get('ok') and rv.get('spawned', True):
        log(f"  [conduite] V appelle « {rv.get('name')} » ({rv.get('vtype')}) parmi ses {rv.get('total')} vehicules")
    elif rv and rv.get('ok'):
        # le systeme a refuse le spawn (cooldown, zone sans route, restriction de scene) : inutile d attendre 30 s
        log(f"  [conduite] vehicule « {rv.get('name')} » : {rv.get('reason')} -> touche d appel en secours")
        kbm.act('callvehicle', 0.15)
        if rv.get('cooldown') or rv.get('restricted'):
            time.sleep(1.5)
            s0 = motion.read_state() or {}
            if not [v for v in (s0.get('vehicles') or []) if v.get('player')]:
                return False
    else:
        log(f"  [conduite] appel du vehicule (touche) : {(rv or {}).get('reason', 'mod muet')}")
        kbm.act('callvehicle', 0.15)
    # attendre la voiture (jusqu a 30 s) : le vehicule du joueur arrive sur la ROUTE la plus proche (jusqu a 150 m) ;
    # on ira le rejoindre par le maillage
    car = None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 30.0:
        if stop is not None and stop.is_set():
            return False
        s2 = motion.read_state() or {}
        vs = s2.get('vehicles') or []
        mine = [v for v in vs if v.get('player')]
        if mine:
            car = mine[0]; break
        # (pas de repli sur une voiture garee d un inconnu : il faudrait la forcer, et c est long)
        time.sleep(0.5)
    if not car:
        log('  [conduite] aucun vehicule du joueur arrive en 30 s (zone sans route proche ?)'); return False
    log(f"  [conduite] vehicule « {car.get('name', '?')} » a {car['d']:.0f} m")
    t_stop = time.perf_counter()
    while time.perf_counter() - t_stop < 8.0 and (car.get('speed') or 0) > 0.5:      # il finit sa manoeuvre : on ne court pas apres
        time.sleep(0.4)
        s_c = motion.read_state() or {}
        car = next((v for v in (s_c.get('vehicles') or []) if v.get('player')), car)
    if car['d'] > 2.5:                                    # deja a portee sinon (les poses gerent 1-2 m)
        # le vehicule arrive sur la ROUTE la plus proche : V y va par le maillage (nav.goto), puis tout droit
        from . import nav
        r = nav.goto(lambda: nav.request_path_to(car['x'], car['y'], car.get('z')), arrive_m=2.0, max_legs=4, timeout=40.0, stop=stop, log=log)
        s1 = motion.read_state() or {}
        if s1 and math.hypot(s1['x'] - car['x'], s1['y'] - car['y']) > 2.5:
            old = motion.ARRIVE_M; motion.ARRIVE_M = 1.3  # une moto est fine : l invite « Enfourcher » exige ~1 m
            try:
                r = motion.walk_to(car['x'], car['y'], timeout=20.0, stop=stop)
            finally:
                motion.ARRIVE_M = old
        if not r.get('ok'):
            log(f"  [conduite] marche vers le vehicule : {r.get('reason')} (on tente les poses quand meme)")
    # Embarquement par POSES : face au centre du vehicule, regard en bas (la selle d une moto est
    # ~0,8 m sous les yeux a 1,5 m), E a chaque pose. Le blackboard `interact` et `lookat` ne sont
    # pas fiables a cette distance, donc on ne s y fie pas : seul `vehicle` fait foi.
    name = str(car.get('name') or '').lower()
    poses = ((0, 45), (0, 30), (0, 60), (-12, 45), (12, 45), (-12, 30), (12, 60), (0, 75), (-25, 45), (25, 45), (0, 15), (0, 50))
    t1 = time.perf_counter()
    for k, (dyaw, pitch) in enumerate(poses):
        if stop is not None and stop.is_set():
            return False
        s2 = motion.read_state() or {}
        if s2.get('vehicle'):
            time.sleep(3.0); log('  [conduite] V est au volant'); return True
        if s2.get('x') is None:
            time.sleep(0.3); continue
        d = math.hypot(s2['x'] - car['x'], s2['y'] - car['y'])
        if d < 1.0:                                      # trop colle : un pas en arriere
            kbm.act('back', 0.25); time.sleep(0.4)
            s2 = motion.read_state() or s2
        elif d > 2.2 and k % 4 == 3:                     # trop loin : un pas en avant
            motion.turn_to(motion.bearing_to(s2['x'], s2['y'], car['x'], car['y']), timeout=2.0, stop=stop)
            kbm.act('forward', 0.25); time.sleep(0.4)
            s2 = motion.read_state() or s2
        motion.turn_to(motion.wrap(motion.bearing_to(s2['x'], s2['y'], car['x'], car['y']) + dyaw), timeout=2.0, stop=stop, tol=3.0)
        look_smooth(0, -2400); time.sleep(0.1)           # butee haute (regard au ciel), par paquets
        look_smooth(0, 1800 + int(pitch * 20)); time.sleep(0.25)   # ~90 deg vers l horizon + inclinaison voulue
        inter = (motion.read_state() or {}).get('interact') or {}
        choice = str((inter.get('choices') or [''])[0])
        if any(w in choice.lower() for w in ('saisir', 'porter', 'contr', 'pirater')):
            continue                                      # pas E sur « saisir » / « prendre le controle »
        kbm.act('interact', 0.4)
        t_e = time.perf_counter()
        while time.perf_counter() - t_e < 1.6:
            time.sleep(0.2)
            if (motion.read_state() or {}).get('vehicle'):
                time.sleep(3.0)
                log(f"  [conduite] V est au volant (pose {k + 1} : yaw {dyaw:+d}, inclinaison {pitch} deg, d={d:.1f} m)")
                return True
    look_smooth(0, -2400); time.sleep(0.1); look_smooth(0, 1900)   # regard a peu pres a l horizon
    log('  [conduite] aucune invite pour monter'); return False


def autodrive_to(tx: float, ty: float, stop=None, log=print) -> dict:
    """En vehicule : enclenche l autodrive (G) et surveille l approche de (tx, ty)."""
    t0 = time.perf_counter()
    st = motion.read_state()
    if not st or not st.get('vehicle'):
        return {'ok': False, 'reason': 'pas en vehicule'}
    if not kbm.ACTIONS.get('autodrive'):
        return {'ok': False, 'reason': 'pas de touche autodrive'}
    autodrive_toggle()
    log('  [conduite] autodrive enclenche (G maintenu 1,5 s)')
    d0 = _dist(st, tx, ty)
    x0, y0 = st['x'], st['y']
    last_move_t, last_pos = time.perf_counter(), (st['x'], st['y'])
    relaunched = False
    last_log = time.perf_counter()
    # G est une BASCULE : un 2e appui coupe l autodrive. On ne relance qu une fois, apres 20 s
    # d immobilite (feu rouge, embouteillage...). Un arret prolonge ensuite = destination atteinte
    # par la route (le marqueur est hors route) ou blocage : on descend et on finit a pied.
    while time.perf_counter() - t0 < MAX_DRIVE_S:
        if stop is not None and stop.is_set():
            break
        s2 = motion.read_state()
        if not s2:
            time.sleep(0.3); continue
        d = _dist(s2, tx, ty)
        if not s2.get('vehicle'):
            # a l arrivee l autodrive fait descendre V (vu en jeu : 2787 m -> 118 m puis vehicle=false)
            if d < 250.0:
                log(f'  [conduite] arrive (descendu a {d:.0f} m de l objectif)')
                return {'ok': True, 'seconds': time.perf_counter() - t0, 'dist': d}
            return {'ok': False, 'reason': 'ejecte du vehicule', 'seconds': time.perf_counter() - t0, 'dist': d}
        if d < ARRIVE_M:
            log(f'  [conduite] arrive a {d:.0f} m de l objectif')
            return {'ok': True, 'seconds': time.perf_counter() - t0, 'dist': d}
        if time.perf_counter() - last_log > 20.0:
            last_log = time.perf_counter()
            log(f'  [conduite] {time.perf_counter() - t0:.0f} s : a {d:.0f} m de l objectif, {math.hypot(s2["x"] - x0, s2["y"] - y0):.0f} m du depart')
        if d > d0 + 150.0 + 0.3 * d0:
            # l autodrive s eloigne franchement : pas d itineraire routier vers ce marqueur (zone
            # pietonne) -> le jeu « croise » au hasard. On s arrete la et on finit a pied.
            log(f'  [conduite] autodrive s eloigne ({d0:.0f} -> {d:.0f} m) : pas d itineraire, on descend')
            return {'ok': False, 'reason': 'autodrive sans itineraire', 'seconds': time.perf_counter() - t0, 'dist': d}
        if math.hypot(s2['x'] - last_pos[0], s2['y'] - last_pos[1]) > 2.0:
            last_move_t, last_pos = time.perf_counter(), (s2['x'], s2['y'])
        elif time.perf_counter() - last_move_t > 20.0:
            traveled = math.hypot(s2['x'] - x0, s2['y'] - y0)
            # arret pres de l objectif = fin de l itineraire routier (le marqueur est dans une zone
            # pietonne) : succes, on finit a pied. Un 2e G ici lancerait la « croisiere » au hasard.
            if d < 250.0:
                log(f'  [conduite] autodrive arrete a {d:.0f} m de l objectif ({traveled:.0f} m parcourus) : fin du trajet routier')
                return {'ok': True, 'seconds': time.perf_counter() - t0, 'dist': d}
            if not relaunched:
                relaunched = True; last_move_t = time.perf_counter()
                log(f'  [conduite] immobile depuis 20 s a {d:.0f} m ({traveled:.0f} m parcourus) : relance autodrive')
                autodrive_toggle()
                continue
            ok = False
            log(f"  [conduite] autodrive arrete a {d:.0f} m de l objectif ({traveled:.0f} m parcourus) : {'fin du trajet routier' if ok else 'bloque'}")
            return {'ok': ok, 'reason': None if ok else 'autodrive bloque', 'seconds': time.perf_counter() - t0, 'dist': d}
        time.sleep(0.5)
    s2 = motion.read_state() or st
    return {'ok': False, 'reason': 'temps ecoule', 'seconds': time.perf_counter() - t0, 'dist': _dist(s2, tx, ty)}


def exit_vehicle(log=print) -> bool:
    st = motion.read_state()
    if not st or not st.get('vehicle'):
        return True
    key = 'exitvehicle' if kbm.ACTIONS.get('exitvehicle') else 'interact'
    for _ in range(3):
        kbm.act(key, 1.0); time.sleep(2.0)
        s2 = motion.read_state() or {}
        if not s2.get('vehicle'):
            log('  [conduite] descendu du vehicule'); return True
    log('  [conduite] impossible de descendre'); return False


def drive_to(tx: float, ty: float, stop=None, log=print) -> dict:
    if not summon_and_board(stop=stop, log=log):
        return {'ok': False, 'reason': 'embarquement echoue'}
    r = autodrive_to(tx, ty, stop=stop, log=log)
    exit_vehicle(log=log)
    return r
