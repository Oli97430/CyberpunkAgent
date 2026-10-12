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

from . import input_kbm as kbm, motion, nav   # nav promu au niveau module : _mount_script l utilise sans import local (NameError garanti sinon)

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


def _refresh(car: dict, vs: list, strict: bool = False) -> dict:
    """Le meme vehicule dans un export plus recent (par identite, sinon par position a moins de 6 m), sinon l ancien.
    strict (vol) : par identite SEULEMENT -- la voiture d a cote n est pas celle qui a ete jugee (revue)."""
    if car.get('h'):
        same = next((v for v in vs if v.get('h') == car['h']), None)     # 12/10 : identite exportee par le mod
        if same:
            return dict(car, **same, lost=False)
        if strict:
            return dict(car, lost=True)
    best = None
    for v in vs:
        dd = math.hypot((v.get('x') or 0) - car['x'], (v.get('y') or 0) - car['y'])
        if dd < 6.0 and (best is None or dd < best[0]):
            best = (dd, v)
    return dict(car, **best[1]) if best else car


def _police_near(st: dict, max_d: float = 30.0) -> bool:
    if any(e.get('police') and not e.get('dead') and (e.get('d') or 99) < max_d for e in (st.get('enemies') or [])):
        return True
    return any(any(p in (n.get('aff') or '').lower() for p in ('ncpd', 'police', 'maxtac')) and (n.get('d') or 99) < max_d
               for n in (st.get('npcs') or []))


def steal_candidate(st: dict, max_d: float = 30.0) -> dict | None:
    """Voiture / moto d inconnu ARRETEE la plus proche (< max_d), hors combat et sans police a moins de 30 m."""
    from .config import CFG as _C
    if not _C.features.get('steal', True) or st.get('combat') or st.get('vehicle') or _police_near(st):
        return None
    from . import tastes
    if tastes.police_veto(st):
        return None
    cands = [v for v in (st.get('vehicles') or []) if not v.get('player') and (v.get('d') or 99) <= max_d and (v.get('speed') or 0) <= 0.5
             and tastes.fallback_ok(v)]           # 12/10 (revue) : ni police, ni quete, ni verrou, ni occupe
    return min(cands, key=lambda v: v['d']) if cands else None


_STOLEN_AD = {'ok': None}   # 12/10 : l autodrive (mode Delamain) roule-t-il dans un vehicule vole ? None = pas encore essaye
_STOLEN = {'car': None}     # vehicule vole pour le trajet en cours (summon_and_board -> drive_to)


def _stolen_ad_known() -> bool | None:
    """Resultat memorise (travel_stats.json, cle steal) : True si un vol a deja roule, False si 3 refus de suite."""
    if _STOLEN_AD['ok'] is not None:
        return _STOLEN_AD['ok']
    try:
        from . import travel
        runs = list(travel._load().get('steal') or [])
    except Exception:
        return None
    if any(r.get('ok') for r in runs):
        return True
    return None


def steal_nearby(stop=None, log=print, max_d: float = 30.0) -> bool:
    """V vole le vehicule arrete le plus proche : marche jusqu a lui, force l ouverture (F maintenu), monte.
    12/10 : seulement si l autodrive a deja roule dans un vehicule vole (sinon 0 m parcouru, 42 s perdues -- 4 vols sur 4)."""
    if _stolen_ad_known() is not True:
        return False
    st = motion.read_state() or {}
    car = steal_candidate(st, max_d=max_d)
    if not car:
        return False
    log(f"  [conduite] V VOLE « {car.get('name', '?')} » a {car['d']:.0f} m")
    if _board(dict(car, mode='gare'), stop=stop, log=log, steal=True):
        _STOLEN['car'] = dict(car, mode='gare')
        return True
    return False


def _mount_script(log=print, any_vehicle: bool = False, stop=None, aim: dict | None = None) -> bool:
    """Monte par script (commande mount du mod, MountingFacility). Vrai si V est en vehicule dans les 3 s.
    aim : le vehicule PRECIS a prendre (12/10 : le mount visait le vehicule appele -- cas Galena)."""
    if stop is not None and stop.is_set():
        return False
    if aim is not None and aim.get('x') is not None:
        r = nav._wait(nav._send({'cmd': 'mount', 'x': 2, 'y': float(aim['x']), 'z': float(aim['y'])}), timeout=3.0)
    else:
        r = nav._wait(nav._send({'cmd': 'mount', 'x': 1 if any_vehicle else 0}), timeout=3.0)
    if not (r and r.get('ok')):
        log(f"  [conduite] montee par script refusee : {(r or {}).get('reason', 'mod muet')}")
        return False
    t = time.perf_counter()
    while time.perf_counter() - t < 3.0:
        if (motion.read_state() or {}).get('vehicle'):
            time.sleep(2.0); log(f"  [conduite] V est au volant (montee par script, vehicule {r.get('how')})"); return True
        time.sleep(0.2)
    return False


def _board(car: dict, stop=None, log=print, steal: bool = False) -> bool:
    """Rejoint le vehicule (maillage puis ligne droite) et monte : par script d abord (MountingFacility), puis par
    poses (F ; F maintenu pour un vol) si le script echoue."""
    sm = (motion.read_state() or {}).get('summon') or {}
    if not steal and sm.get('x') is not None and math.hypot(sm['x'] - car['x'], sm['y'] - car['y']) < 25.0:
        car = dict(car, x=sm['x'], y=sm['y'], z=sm.get('z', car.get('z')), d=float(sm.get('d') or car.get('d') or 0))   # position VIVE du vehicule appele
    log(f"  [conduite] vehicule « {car.get('name', '?')} » a {car['d']:.0f} m")
    t_stop = time.perf_counter()
    while time.perf_counter() - t_stop < 8.0 and (car.get('speed') or 0) > 0.5:      # il finit sa manoeuvre : on ne court pas apres
        time.sleep(0.4)
        s_c = motion.read_state() or {}
        car = _refresh(car, s_c.get('vehicles') or [], strict=steal)
    for attempt in range(3):
        if car['d'] <= 2.5:                               # deja a portee (les poses gerent 1-2 m)
            break
        if stop is not None and stop.is_set():
            return False
        # le vehicule arrive sur la ROUTE la plus proche : V y va par le maillage (nav.goto), puis tout droit
        r = nav.goto(lambda c=car: nav.request_path_to(c['x'], c['y'], c.get('z')), arrive_m=2.0, max_legs=4, timeout=40.0, stop=stop, log=log)
        s1 = motion.read_state() or {}
        if s1 and math.hypot(s1['x'] - car['x'], s1['y'] - car['y']) > 2.5 and not (stop is not None and stop.is_set()):
            old = motion.ARRIVE_M; motion.ARRIVE_M = 1.3  # une moto est fine : l invite « Enfourcher » exige ~1 m
            try:
                r = motion.walk_to(car['x'], car['y'], timeout=20.0, stop=stop)
            finally:
                motion.ARRIVE_M = old
        if not r.get('ok'):
            log(f"  [conduite] marche vers le vehicule : {r.get('reason')} (on tente les poses quand meme)")
        # 12/10 : le vehicule appele a-t-il bouge pendant la marche ? (09:20 : V l avait rate de 35 m) -> position VIVE
        s_v = motion.read_state() or {}
        if steal:                                         # 12/10 : vehicule choisi : position vive par son identite
            car = _refresh(car, s_v.get('vehicles') or [], strict=True)
            if car.get('lost') and car.get('mode') == 'carjack':
                log(f"  [vol] « {car.get('name')} » n est plus en vue : V renonce"); return False
            if s_v.get('x') is not None:
                car['d'] = math.hypot(car['x'] - s_v['x'], car['y'] - s_v['y'])
            if car.get('mode') == 'carjack' and float(car.get('speed') or 0) > 1.0:
                log(f"  [vol] « {car.get('name')} » repart : V renonce"); return False
            break
        sm_v = s_v.get('summon') or {}
        if sm_v.get('x') is None or s_v.get('x') is None or math.hypot(sm_v['x'] - car['x'], sm_v['y'] - car['y']) <= 3.0:
            break
        d_v = math.hypot(sm_v['x'] - s_v['x'], sm_v['y'] - s_v['y'])
        if d_v > 80.0:
            break
        log(f"  [conduite] le vehicule a bouge pendant la marche : nouvelle position a {d_v:.0f} m (recalage {attempt + 1}/2)")
        car = dict(car, x=sm_v['x'], y=sm_v['y'], z=sm_v.get('z', car.get('z')), d=d_v)
        if attempt >= 2:
            break
    # d abord PAR SCRIPT (le siege conducteur, sans invite) : c est le plus fiable
    if stop is not None and stop.is_set():
        return False
    s_pre = motion.read_state() or {}
    aim = car if (steal and car.get('mode')) else None    # 12/10 : vol par gout -> CE vehicule
    if aim is not None and car.get('mode') == 'carjack':
        pass                                            # carjacking : par l invite du jeu seulement (le conducteur est sorti)
    elif s_pre.get('x') is not None and math.hypot(s_pre['x'] - car['x'], s_pre['y'] - car['y']) <= 8.0 and \
            car.get('lock') != 1 and _mount_script(log=log, any_vehicle=steal, stop=stop, aim=aim):
        return True
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
            if stop is not None and stop.is_set():
                return False
            kbm.act('forward', 0.25); time.sleep(0.4)
            s2 = motion.read_state() or s2
        motion.turn_to(motion.wrap(motion.bearing_to(s2['x'], s2['y'], car['x'], car['y']) + dyaw), timeout=2.0, stop=stop, tol=3.0)
        if stop is not None and stop.is_set():
            return False
        look_smooth(0, -2400); time.sleep(0.1)           # butee haute (regard au ciel), par paquets
        look_smooth(0, 1800 + int(pitch * 20)); time.sleep(0.25)   # ~90 deg vers l horizon + inclinaison voulue
        inter = (motion.read_state() or {}).get('interact') or {}
        choice = str((inter.get('choices') or [''])[0])
        cj = car.get('mode') == 'carjack' and 'contr' in choice.lower()
        if steal and 1 in (inter.get('off') or []) and (cj or any(w in choice.lower() for w in ('forc', 'ouvr', 'entrer', 'monter', 'enfourch'))):
            log(f"  [vol] « {choice} » grise (test d attribut rate) : V renonce"); return False
        if car.get('mode') == 'carjack':
            # 12/10 (revue) : cible relue a chaque pose (feu vert, autre voiture), police / etoiles, 15 s au plus ; F
            # seulement sur « Prendre le controle »
            s_cj = motion.read_state() or {}
            car = _refresh(car, s_cj.get('vehicles') or [], strict=True)
            if car.get('lost') or float(car.get('speed') or 0) > 1.0 or time.perf_counter() - t1 > 15.0:
                log(f"  [vol] « {car.get('name')} » repart ou n est plus a portee : V renonce"); return False
            from . import tastes as _tp
            if _police_near(s_cj) or _tp.police_veto(s_cj):
                log('  [vol] police ou etoiles : V renonce au carjacking'); return False
            if not cj:
                continue
        if not cj and any(w in choice.lower() for w in ('saisir', 'porter', 'contr', 'pirater')):
            continue                                      # pas E sur « saisir » / « prendre le controle »
        if stop is not None and stop.is_set():
            return False
        if cj:
            log(f"  [vol] V prend le controle de « {car.get('name')} » (conducteur au volant, {d:.0f} m)")
        kbm.act('interact', 1.4 if (steal or inter.get('hold')) else 0.4)   # voler = « Forcer » / « Ouvrir » : F maintenu
        t_e = time.perf_counter()
        while time.perf_counter() - t_e < (3.0 if steal else 1.6):
            time.sleep(0.2)
            if (motion.read_state() or {}).get('vehicle'):
                time.sleep(3.0)
                log(f"  [conduite] V est au volant (pose {k + 1} : yaw {dyaw:+d}, inclinaison {pitch} deg, d={d:.1f} m)")
                return True
    if stop is not None and stop.is_set():
        return False
    look_smooth(0, -2400); time.sleep(0.1); look_smooth(0, 1900)   # regard a peu pres a l horizon
    if car.get('mode') == 'carjack' or car.get('lock') == 1:
        log('  [vol] pas d invite utilisable : V renonce'); return False   # 12/10 : pas de montee par script dans ces cas
    if _mount_script(log=log, any_vehicle=steal, stop=stop, aim=car if (steal and car.get('mode')) else None):   # dernier recours
        return True
    log('  [conduite] aucune invite pour monter'); return False


def _summon_evidence(s: dict, sm_pre: dict, mine_pre: list) -> str | None:
    """Preuve NEUVE qu un vehicule de V arrive apres l appel (12/10), sinon None."""
    sm = s.get('summon') or {}
    st_now, st_pre = int(sm.get('state') or 0), int(sm_pre.get('state') or 0)
    moved = (sm.get('x') is not None and sm_pre.get('x') is not None
             and math.hypot(sm['x'] - sm_pre['x'], sm['y'] - sm_pre['y']) > 3.0)
    if st_now in (1, 4) and (st_now != st_pre or moved or (sm_pre.get('x') is None and sm.get('x') is not None)):
        return f"etat d appel {st_pre} -> {st_now}" + (', vehicule en mouvement' if moved else '')
    if st_now == 1 and float(sm.get('speed') or 0) > 1.0:
        return 'vehicule appele en route'
    for v in (s.get('vehicles') or []):
        if v.get('player') and v.get('x') is not None and not any(
                px is not None and math.hypot(v['x'] - px, v['y'] - py) < 6.0 for px, py in mine_pre):
            return f"nouveau vehicule de V a {float(v.get('d') or 0):.0f} m"
    return None


def _record_steal(ok: bool, reason, stop=None) -> None:
    if stop is not None and stop.is_set():
        return
    try:
        from . import travel
        travel.record('steal', ok, reason)
    except Exception:
        pass


def summon_and_board(stop=None, log=print, want_taste: bool = True) -> bool:
    """Appelle le vehicule et monte dedans. True si V est en vehicule a la fin."""
    st = motion.read_state()
    if not st:
        return False
    if st.get('vehicle'):
        return True
    if st.get('combat'):
        # en combat le mod ne scanne pas les vehicules (export vide) et V a mieux a faire
        log('  [conduite] en combat : pas d appel de vehicule'); return False
    _STOLEN['car'] = None
    if want_taste:                                       # rythme : tastes.pace_ok (10 min ; refus repetes -> 1 / h)
        # 12/10 (Olivier : « jamais vu V prendre le controle d un vehicule qui lui plairait ») : gouts de V
        from . import tastes
        from .config import CFG as _C
        pk = None
        if _C.features.get('steal', True):
            pk = tastes.pick(st, carjack_on=_C.features.get('carjack', True), log=log, police_near=_police_near(st))
        if pk:
            how = 'arretee au feu, conducteur au volant' if pk['mode'] == 'carjack' else 'garee'
            log(f"  [gouts] V repere « {pk.get('name')} » ({pk.get('mk')}, {pk.get('rid')}, score {pk['score']}, {how}, {float(pk.get('d') or 0):.0f} m) : elle lui plait")
            ok_b = _board(pk, stop=stop, log=log, steal=True)
            if not ok_b and not (stop is not None and stop.is_set()) and (motion.read_state() or {}).get('vehicle'):
                ok_b = True; log('  [vol] V est finalement au volant')
            if ok_b:
                s_b = motion.read_state() or {}
                rid = (s_b.get('dx') or {}).get('rid')
                if rid and pk.get('rid') and rid != pk.get('rid'):
                    log(f"  [vol] mauvais vehicule : V est dans « {rid} » (vise {pk.get('rid')})")
                _STOLEN['car'] = pk if (not rid or not pk.get('rid') or rid == pk.get('rid') or not (s_b.get('dx') or {}).get('pv')) else None
                return True
            if stop is not None and stop.is_set():
                return False
            _record_steal(False, 'embarquement echoue', stop)
    if not kbm.ACTIONS.get('callvehicle'):
        log('  [conduite] pas de touche « appeler le vehicule »')
        return steal_nearby(stop=stop, log=log)
    # au hasard : une voiture ou une moto parmi celles de V (VehicleSystem) ; a defaut la touche d appel
    import random
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
        if stop is not None and stop.is_set():
            return False
        log('  [conduite] circulation persistante : V s ecarte de la voie avant d appeler')
        motion.turn_by(90.0, timeout=1.5, stop=stop)
        kbm.hold('W'); time.sleep(1.6); kbm.release('W')
    if stop is not None and stop.is_set():
        return False
    # 12/10 : etat de l appel et vehicules de V AVANT l appel (un etat « en route » / un vehicule vu peuvent etre ceux
    # d un appel precedent : pris pour une arrivee, ils empechaient la touche d appel -- 4 echecs sur 4 depuis le 24/09)
    s_pre = motion.read_state() or {}
    sm_pre = s_pre.get('summon') or {}
    mine_pre = [(v.get('x'), v.get('y')) for v in (s_pre.get('vehicles') or []) if v.get('player') and v.get('x') is not None]
    seq_v = nav._send({'cmd': 'vehicle_call', 'x': want})
    rv = nav._wait(seq_v, timeout=5.0)
    if rv is None:
        # le mod ne repond pas en 5 s : souvent un menu / une scene qui suspend ses mises a jour ; il traitera l appel
        # des la reprise (vu le 17/09 : « mod muet » puis spawn reel 20 s plus tard) -> on attend encore avant la touche
        s_m = motion.read_state() or {}
        log(f"  [conduite] le mod ne repond pas (menu={s_m.get('menu')}, scene={s_m.get('scene')}) : on lui laisse 8 s de plus")
        rv = nav._wait(seq_v, timeout=8.0)
    # NB : rv['restrictions'] est le CATALOGUE statique des tags (m_restrictionTags), pas les restrictions actives :
    # seul rv['restricted'] (IsSummoningVehiclesRestricted) fait foi
    if rv and rv.get('ok') and rv.get('restricted') is True:
        log('  [conduite] appel de vehicule interdit ici par le jeu (IsSummoningVehiclesRestricted) : on n attend pas')
        return steal_nearby(stop=stop, log=log)
    if rv and rv.get('ok') and rv.get('existing'):
        log(f"  [conduite] « {rv.get('name')} » est deja la, a {float(rv.get('dist') or 0):.0f} m : V le rejoint")
    elif rv and rv.get('ok') and rv.get('spawned', True):
        log(f"  [conduite] V appelle « {rv.get('name')} » ({rv.get('vtype')}) parmi ses {rv.get('total')} vehicules")
    elif rv and rv.get('ok'):
        # le systeme a refuse le spawn (cooldown, zone sans route, restriction de scene) : inutile d attendre 30 s.
        # 24/09 : un « refus » peut quand meme faire apparaitre le vehicule (Olivier : 3 vehicules d un coup, embouteillage)
        # -> on regarde jusqu a 5 s, mais seulement des preuves NEUVES (12/10) avant d appuyer sur la touche d appel
        coming = None
        t_c = time.perf_counter()
        while time.perf_counter() - t_c < 5.0 and not coming:
            if stop is not None and stop.is_set():
                return False
            time.sleep(0.5)
            coming = _summon_evidence(motion.read_state() or {}, sm_pre, mine_pre)
        if coming:
            log(f"  [conduite] « {rv.get('name')} » arrive malgre le refus annonce ({coming}) : pas de second appel")
        else:
            if stop is not None and stop.is_set():
                return False
            log(f"  [conduite] vehicule « {rv.get('name')} » : {rv.get('reason')} -> touche d appel en secours")
            kbm.act('callvehicle', 0.15)
        if not coming and (rv.get('cooldown') or rv.get('restricted')):
            time.sleep(1.5)
            s0 = motion.read_state() or {}
            if not [v for v in (s0.get('vehicles') or []) if v.get('player')]:
                return steal_nearby(stop=stop, log=log)
    else:
        if stop is not None and stop.is_set():
            return False
        log(f"  [conduite] appel du vehicule (touche) : {(rv or {}).get('reason', 'mod muet')}")
        kbm.act('callvehicle', 0.15)
    # attendre la voiture (jusqu a 30 s) : le vehicule du joueur arrive sur la ROUTE la plus proche (jusqu a 150 m) ;
    # on ira le rejoindre par le maillage
    car = None
    t0 = time.perf_counter()
    SUMMON = {0: 'inactif', 1: 'en route', 2: 'deja appele', 3: 'ECHEC de trajet', 4: 'arrive'}
    last_log, last_state = -99.0, None
    fresh = None                                     # 12/10 : preuve NEUVE que l appel a abouti (pas un etat perime)
    still_since, last_sm_pos = None, None            # 12/10 : vehicule appele immobile depuis... (il est arrive)
    while time.perf_counter() - t0 < 60.0:
        if stop is not None and stop.is_set():
            return False
        s2 = motion.read_state() or {}
        sm = s2.get('summon') or {}
        s_state = int(sm.get('state') or 0)
        if s_state != last_state or time.perf_counter() - last_log > 10.0:
            last_state, last_log = s_state, time.perf_counter()
            log(f"  [conduite] appel : {SUMMON.get(s_state, s_state)}" + (f", « {sm.get('name')} » a {float(sm.get('d')):.0f} m" if sm.get('d') is not None else ''))
        if s_state == 3:
            log('  [conduite] le jeu ne trouve pas de trajet pour le vehicule : on fait sans'); break
        vs = s2.get('vehicles') or []
        mine = [v for v in vs if v.get('player')]
        fresh = fresh or _summon_evidence(s2, sm_pre, mine_pre)
        if sm.get('x') is not None:
            p_now = (sm['x'], sm['y'])
            if last_sm_pos is not None and math.hypot(p_now[0] - last_sm_pos[0], p_now[1] - last_sm_pos[1]) < 0.5 \
                    and float(sm.get('speed') or 0) <= 0.5:
                still_since = still_since or time.perf_counter()
            else:
                still_since = None
            last_sm_pos = p_now
        # 12/10 : « en route » = il roule vers V : on l attend (avant, V marchait vers sa position de depart et le ratait)
        arrived_car = s_state == 4 or (still_since is not None and time.perf_counter() - still_since >= 4.0)
        if fresh and sm.get('x') is not None and arrived_car and (s_state == 4 or (sm.get('d') or 999) <= 60.0):
            car = {'name': sm.get('name') or 'vehicule', 'x': sm['x'], 'y': sm['y'], 'z': sm.get('z'), 'd': float(sm.get('d') or 0), 'player': True, 'speed': sm.get('speed') or 0}
            break
        if mine and (mine[0].get('d') or 999) <= 60.0 and s_state != 1:
            car = mine[0]; break
        if s_state == 0 and time.perf_counter() - t0 > 40.0:
            break                                   # rien ne se passe : on ne s eternise pas
        time.sleep(0.5)
    if not car:
        seen = [(v.get('name'), round(v.get('d') or 0), v.get('player')) for v in ((motion.read_state() or {}).get('vehicles') or [])]
        log(f"  [conduite] aucun vehicule du joueur arrive en 60 s (methodes {(rv or {}).get('methodes')}, restrictions {(rv or {}).get('restrictions')}, cooldown {(rv or {}).get('vcooldown')}) ; vus : {seen}")
        return steal_nearby(stop=stop, log=log)                  # a defaut, V vole ce qui est gare a cote
    return _board(car, stop=stop, log=log)


_AUTODRIVE_ON = {'left_on': False}   # 12/10 : autodrive enclenche par V puis laisse actif sur F11/F12


def _stolen_stop() -> None:
    """Coupe l autodrive mode Delamain par le mod (jamais G : dans ce mode le joueur n a ni frein ni volant -- revue)."""
    nav._wait(nav._send({'cmd': 'autodrive', 'x': 0}), timeout=1.5)


def _stolen_start(log=print, stop=None) -> str:
    """12/10 : autodrive dans un vehicule qui n est pas a V : commande du mod (mode Delamain), verifiee 8 s sur un VRAI
    mouvement (revue : dx.on seul = demande acceptee, voiture parfois immobile). 'ok' | 'stop' | 'combat' | 'lane' | 'refuse'."""
    for attempt in range(2):
        s0 = motion.read_state() or {}
        if s0.get('combat') or (s0.get('dx') or {}).get('wl'):
            return 'combat'
        if stop is not None and stop.is_set():
            return 'stop'
        r = nav._wait(nav._send({'cmd': 'autodrive', 'x': 1}), timeout=2.0) or {}
        lane = r.get('lane')
        log(f"  [vol] autodrive mode Delamain : voie {lane}, destination {r.get('dest')}, envoye {r.get('sent')}" + (f" ({r.get('reason')})" if r.get('reason') else ''))
        if lane in (1, 2):                          # hors voie : le jeu refuse avant meme le mode Delamain (:318-330)
            if attempt == 0:
                log('  [vol] hors voie de circulation : V avance un peu')
                kbm.hold('W'); time.sleep(1.5); kbm.release('W'); time.sleep(0.5)
                continue
            return 'lane'
        p0 = (s0.get('x'), s0.get('y'))
        t = time.perf_counter()
        while time.perf_counter() - t < 8.0:
            if stop is not None and stop.is_set():
                return 'stop'
            s = motion.read_state() or {}
            dx = s.get('dx') or {}
            moved = None not in p0 and s.get('x') is not None and math.hypot(s['x'] - p0[0], s['y'] - p0[1]) > 3.0
            if float(dx.get('spd') or 0) > 2.0 or moved:
                return 'ok'
            if s.get('combat') or dx.get('wl'):
                return 'combat'
            time.sleep(0.4)
        return 'refuse'
    return 'lane'


def autodrive_to(tx: float, ty: float, stop=None, log=print, stolen: dict | None = None) -> dict:
    """En vehicule : enclenche l autodrive (G) et surveille l approche de (tx, ty).
    stolen : vehicule qui n est pas a V (12/10) -> commande autodrive du mod, refus constate en 8 s."""
    t0 = time.perf_counter()
    st = motion.read_state()
    if not st or not st.get('vehicle'):
        return {'ok': False, 'reason': 'pas en vehicule'}
    dx0 = st.get('dx') or {}
    if stolen is None and dx0.get('pv') is False and dx0.get('av') is False:
        stolen = {'name': dx0.get('rid') or 'vehicule'}   # revue : V est dans un vehicule qui n est pas a lui (reprise apres F11)
    if stolen is not None:
        if stop is not None and stop.is_set():
            return {'ok': False, 'reason': 'arret d urgence', 'seconds': 0.0}
        res = _stolen_start(log=log, stop=stop)
        if res != 'ok':
            _stolen_stop()
            why = {'stop': 'arret d urgence', 'combat': 'combat (vehicule vole)', 'lane': 'hors voie (vehicule vole)'}.get(res, 'autodrive refuse (vehicule vole)')
            return {'ok': False, 'reason': why, 'seconds': time.perf_counter() - t0}
        log('  [vol] la voiture volee roule')
        return _watch_drive(tx, ty, st, t0, stop=stop, log=log, relaunch=lambda: _stolen_start(log=log, stop=stop) == 'ok', relaunches=3, on_flag=True)
    if not kbm.ACTIONS.get('autodrive'):
        return {'ok': False, 'reason': 'pas de touche autodrive'}
    if stop is not None and stop.is_set():
        return {'ok': False, 'reason': 'arret d urgence', 'seconds': 0.0}
    modd = 'on' in dx0                                   # 12/10 : mod recent -> commandes sans bascule
    if _AUTODRIVE_ON['left_on'] and _current_speed(st) > 3.0 or (modd and dx0.get('on')):
        # 12/10 : V avait enclenche l autodrive avant une pause F11 et la voiture roule encore : G est une BASCULE
        log('  [conduite] autodrive deja actif : on n y touche pas')
    elif modd and _own_start(log=log, stop=stop):
        log('  [conduite] autodrive enclenche (commande du mod)')
    else:
        autodrive_toggle()
        log('  [conduite] autodrive enclenche (G maintenu 1,5 s)')
    _AUTODRIVE_ON['left_on'] = True
    if modd:
        return _watch_drive(tx, ty, st, t0, stop=stop, log=log, relaunch=lambda: _own_start(log=log, stop=stop), relaunches=3, own_cmd=True)
    return _watch_drive(tx, ty, st, t0, stop=stop, log=log, relaunch=autodrive_toggle, relaunches=1)


def _own_start(log=print, stop=None) -> bool:
    """12/10 : autodrive NORMAL de la voiture de V par le mod (pas de bascule G) ; vrai si dx.on dans les 3 s."""
    r = nav._wait(nav._send({'cmd': 'autodrive', 'x': 3}), timeout=2.0) or {}
    t = time.perf_counter()
    while time.perf_counter() - t < 3.0:
        if stop is not None and stop.is_set():
            return False
        if ((motion.read_state() or {}).get('dx') or {}).get('on'):
            return True
        time.sleep(0.3)
    log(f"  [conduite] autodrive refuse par le jeu (voie {r.get('lane')}, disponible {r.get('avail')})")
    return False


def _watch_drive(tx, ty, st, t0, stop=None, log=print, relaunch=None, relaunches: int = 1, on_flag: bool = False,
                 own_cmd: bool = False) -> dict:
    """Surveille l approche de (tx, ty) pendant l autodrive ; relance (G, ou commande du mod pour un vehicule vole)."""
    d0 = _dist(st, tx, ty)
    x0, y0 = st['x'], st['y']
    last_move_t, last_pos = time.perf_counter(), (st['x'], st['y'])
    relaunched = 0
    last_log = time.perf_counter()
    # G est une BASCULE : un 2e appui coupe l autodrive. On ne relance qu une fois, apres 20 s
    # d immobilite (feu rouge, embouteillage...). Un arret prolonge ensuite = destination atteinte
    # par la route (le marqueur est hors route) ou blocage : on descend et on finit a pied.
    while time.perf_counter() - t0 < MAX_DRIVE_S:
        if stop is not None and stop.is_set():
            if on_flag:
                _stolen_stop()                            # revue : mode Delamain = ni frein ni volant pour le joueur
            return {'ok': False, 'reason': 'arret d urgence', 'seconds': time.perf_counter() - t0}   # 12/10 : le joueur a la main (autodrive laisse actif)
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
            dxw = s2.get('dx') or {}
            dest = ''
            if dxw.get('ax') is not None:
                dest = f", autodrive on={dxw.get('on')} roue libre={dxw.get('fr')} type={dxw.get('dt')} destination a {math.hypot(dxw['ax'] - tx, dxw['ay'] - ty):.0f} m de la cible"
            log(f'  [conduite] {time.perf_counter() - t0:.0f} s : a {d:.0f} m de l objectif, {math.hypot(s2["x"] - x0, s2["y"] - y0):.0f} m du depart{dest}')
        if d > d0 + 150.0 + 0.3 * d0:
            # l autodrive s eloigne franchement : pas d itineraire routier vers ce marqueur (zone
            # pietonne) -> le jeu « croise » au hasard. On s arrete la et on finit a pied.
            log(f'  [conduite] autodrive s eloigne ({d0:.0f} -> {d:.0f} m) : pas d itineraire, on descend')
            return {'ok': False, 'reason': 'autodrive sans itineraire', 'seconds': time.perf_counter() - t0, 'dist': d}
        if on_flag and (s2.get('combat') or (s2.get('dx') or {}).get('wl')):
            _stolen_stop()                                # revue : pas de relance en combat / avec des etoiles
            log(f'  [vol] combat ou police pendant le trajet a {d:.0f} m : V arrete la voiture volee')
            return {'ok': False, 'reason': 'combat (vehicule vole)', 'seconds': time.perf_counter() - t0, 'dist': d}
        if on_flag and (s2.get('dx') or {}).get('on') is False and time.perf_counter() - last_move_t > 4.0 and d < 250.0:
            log(f'  [conduite] autodrive termine a {d:.0f} m de l objectif : fin du trajet routier')
            return {'ok': True, 'seconds': time.perf_counter() - t0, 'dist': d}
        if on_flag and (s2.get('dx') or {}).get('on') is False and time.perf_counter() - last_move_t > 4.0 and relaunched < relaunches:
            relaunched += 1; last_move_t = time.perf_counter()     # 12/10 : vehicule vole, le jeu coupe l autodrive au moindre choc
            log(f'  [vol] autodrive coupe par le jeu a {d:.0f} m : relance {relaunched}/{relaunches}')
            if relaunch is not None and not relaunch():
                _stolen_stop()
                why = 'arret d urgence' if (stop is not None and stop.is_set()) else 'autodrive coupe (vehicule vole)'
                return {'ok': False, 'reason': why, 'seconds': time.perf_counter() - t0, 'dist': d}
            continue
        if math.hypot(s2['x'] - last_pos[0], s2['y'] - last_pos[1]) > 2.0:
            last_move_t, last_pos = time.perf_counter(), (s2['x'], s2['y'])
        elif own_cmd and (s2.get('dx') or {}).get('on') and time.perf_counter() - last_move_t <= 60.0:
            pass                       # 12/10 : autodrive ACTIF et immobile (feu, bouchon) : on attend, aucune touche
        elif time.perf_counter() - last_move_t > 20.0:
            traveled = math.hypot(s2['x'] - x0, s2['y'] - y0)
            # arret pres de l objectif = fin de l itineraire routier (le marqueur est dans une zone
            # pietonne) : succes, on finit a pied. Un 2e G ici lancerait la « croisiere » au hasard.
            if d < 250.0:
                log(f'  [conduite] autodrive arrete a {d:.0f} m de l objectif ({traveled:.0f} m parcourus) : fin du trajet routier')
                return {'ok': True, 'seconds': time.perf_counter() - t0, 'dist': d}
            if relaunched < relaunches:
                relaunched += 1; last_move_t = time.perf_counter()
                log(f'  [conduite] immobile depuis 20 s a {d:.0f} m ({traveled:.0f} m parcourus) : relance autodrive ({relaunched}/{relaunches})')
                if relaunch is not None:
                    relaunch()
                continue
            ok = False
            log(f"  [conduite] autodrive arrete a {d:.0f} m de l objectif ({traveled:.0f} m parcourus) : {'fin du trajet routier' if ok else 'bloque'}")
            return {'ok': ok, 'reason': None if ok else 'autodrive bloque', 'seconds': time.perf_counter() - t0, 'dist': d}
        time.sleep(0.5)
    s2 = motion.read_state() or st
    return {'ok': False, 'reason': 'temps ecoule', 'seconds': time.perf_counter() - t0, 'dist': _dist(s2, tx, ty)}


def _current_speed(st: dict) -> float:
    dx = st.get('dx') or {}
    if st.get('vehicle') and dx.get('spd') is not None:
        return float(dx['spd'])                      # 12/10 : vitesse du vehicule MONTE (un vehicule vole n est pas dans summon)
    sm = st.get('summon') or {}
    if sm.get('speed') is not None:
        return float(sm['speed'])
    for v in (st.get('vehicles') or []):
        if v.get('player'):
            return float(v.get('speed') or 0)
    return 0.0


def exit_vehicle(log=print, stop=None) -> bool:
    def _h():                                            # 12/10 : F11/F12 -> le joueur garde le vehicule
        return stop is not None and stop.is_set()
    st = motion.read_state()
    if not st or not st.get('vehicle'):
        return True
    if _h():
        return False
    sp = _current_speed(st)
    dxs = st.get('dx') or {}
    stolen_now = _STOLEN['car'] is not None or (dxs.get('pv') is False and dxs.get('av') is False)
    if stolen_now and not _h():
        # 12/10 (revue) : vehicule vole -> autodrive Delamain coupe par le mod AVANT de descendre, quelle que soit la vitesse
        _stolen_stop()
        t_o = time.perf_counter()
        while time.perf_counter() - t_o < 2.0 and ((motion.read_state() or {}).get('dx') or {}).get('on'):
            time.sleep(0.2)
        st = motion.read_state() or st
        sp = _current_speed(st)
    if sp > 2.0:
        # encore en mouvement (croisiere autodrive pas coupee) : on freine, sinon V sautait de la
        # voiture en marche (19/09) en descendant directement
        log(f'  [conduite] freinage avant de descendre ({sp:.0f} m/s)')
        if stolen_now:
            pass
        elif 'on' in dxs and not _h():
            _stolen_stop()       # 12/10 : arret par le mod (DisableAutoDriveRequest), pas de bascule G
            _AUTODRIVE_ON['left_on'] = False
        elif kbm.ACTIONS.get('autodrive') and not _h():
            autodrive_toggle()   # bascule : coupe la croisiere automatique
            _AUTODRIVE_ON['left_on'] = False
        t_b = time.perf_counter()
        while time.perf_counter() - t_b < 6.0 and sp > 2.0 and not _h():
            kbm.act('back', 0.3)
            st = motion.read_state() or st
            sp = _current_speed(st)
        log(f'  [conduite] vitesse {sp:.0f} m/s avant de descendre')
    key = 'exitvehicle' if kbm.ACTIONS.get('exitvehicle') else 'interact'
    for _ in range(3):
        if _h():
            log('  [conduite] arret d urgence : le joueur garde le vehicule'); return False
        kbm.act(key, 1.0); time.sleep(2.0)
        s2 = motion.read_state() or {}
        if not s2.get('vehicle'):
            _AUTODRIVE_ON['left_on'] = False
            log('  [conduite] descendu du vehicule'); return True
    log('  [conduite] impossible de descendre'); return False


def drive_to(tx: float, ty: float, stop=None, log=print) -> dict:
    try:
        return _drive_to(tx, ty, stop=stop, log=log)
    finally:
        _STOLEN['car'] = None                           # revue : jamais d etat de vol qui fuit d un trajet a l autre


def _drive_to(tx: float, ty: float, stop=None, log=print) -> dict:
    boarded = summon_and_board(stop=stop, log=log)
    stolen = _STOLEN['car'] if boarded else None
    if stolen is not None and not (stop is not None and stop.is_set()):
        # 12/10 : V est dans le vehicule qui lui plait -> trajet avec, si l autodrive veut bien rouler
        r = autodrive_to(tx, ty, stop=stop, log=log, stolen=stolen)
        r['boarded'], r['stolen'] = True, stolen.get('name')
        why_r = str(r.get('reason') or '')
        refused = 'autodrive refuse' in why_r or 'hors voie' in why_r
        if 'autodrive refuse' in why_r:
            _STOLEN_AD['ok'] = False
        if refused:
            log(f"  [vol] {why_r} pour « {stolen.get('name')} » : V la laisse et appelle sa voiture (gouts gardes pour plus tard)")
        elif r.get('ok'):
            _STOLEN_AD['ok'] = True                     # revue : seulement un trajet REUSSI prouve que ca roule
        exit_vehicle(log=log, stop=stop)
        if not (stop is not None and stop.is_set()):
            s_e = motion.read_state() or {}
            log(f"  [vol] V abandonne « {stolen.get('name')} » a {_dist(s_e, tx, ty):.0f} m de l objectif" if s_e.get('x') is not None else '  [vol] fin du trajet')
        _record_steal(bool(r.get('ok')), r.get('reason'), stop)
        _STOLEN['car'] = None
        s_x = motion.read_state() or {}
        if not refused or (stop is not None and stop.is_set()) or s_x.get('vehicle') or s_x.get('combat'):
            return r                                     # revue : pas de repli depuis le vehicule vole ni en combat
        boarded = summon_and_board(stop=stop, log=log, want_taste=False)   # la suite normale : sa voiture
        if boarded and _STOLEN['car'] is not None:
            _STOLEN['car'] = None
    if not boarded:
        r = {'ok': False, 'reason': 'arret d urgence' if (stop is not None and stop.is_set()) else 'embarquement echoue'}
    elif stop is not None and stop.is_set():             # monte pendant F11 : pas d autodrive, le joueur a la main
        r = {'ok': False, 'reason': 'arret d urgence', 'boarded': True}
    elif _STOLEN['car'] is not None:                     # vol de secours (sa voiture n est pas venue)
        stolen = _STOLEN['car']
        r = autodrive_to(tx, ty, stop=stop, log=log, stolen=stolen)
        r['boarded'], r['stolen'] = True, stolen.get('name')
        exit_vehicle(log=log, stop=stop)
        _record_steal(bool(r.get('ok')), r.get('reason'), stop)
        _STOLEN['car'] = None
        return r
    else:
        r = autodrive_to(tx, ty, stop=stop, log=log)
        r['boarded'] = True
        exit_vehicle(log=log, stop=stop)                 # 12/10 : ne fait rien apres F11 (le joueur garde la voiture)
    record_drive(r, stop)
    return r


def record_drive(r: dict, stop=None) -> None:
    """Statistiques de conduite (travel.py) ; un trajet coupe par pause / arret n est ni un succes ni un echec."""
    if stop is not None and stop.is_set():
        return
    try:
        from . import travel
        travel.record('drive', bool(r.get('ok')), r.get('reason'))
    except Exception:
        pass
