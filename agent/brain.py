"""
brain.py -- boucle autonome v1 : enchaine les competences validees.

Priorites, a chaque tour :
  1. etat perime (menu, chargement, jeu ferme)  -> attendre
  2. dialogue ouvert                            -> repondre (dialog.answer_once)
  3. en combat                                  -> v1 : se mettre a l abri = rien de plus que
                                                   relacher les touches et attendre (competence a venir)
  4. invite d interaction devant nous ET objectif proche -> F (parler / ouvrir)
  5. objectif suivi avec marqueur, a > 3 m      -> aller_a (nav.goto_quest, un troncon)
  6. sinon                                       -> attendre que la quete avance

Tout est journalise dans brain_log.txt. F11 arrete tout (KillSwitch).
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from . import buffs, combat, dialog, driving, escape, input_kbm as kbm, inventory, motion, nav, planner, quests, radio, vendor

from .config import CFG
LOG_FILE = CFG.log_file                 # %APPDATA%/CyberpunkAgent/brain_log.txt
ARRIVE_M = 3.0


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


ENGAGE_M = 25.0


def _threat_near(st: dict) -> bool:
    """Interrompt un trajet : combat, ou hostile vivant a moins de ENGAGE_M."""
    if st.get('combat'):
        return True
    return any((not e.get('dead')) and (not e.get('police')) and e['d'] < ENGAGE_M
               and (e.get('z') is None or abs(e['z'] - st.get('z', e['z'])) < 3.5) for e in (st.get('enemies') or []))


def _dist_to_mappin(st: dict) -> float | None:
    q = st.get('quest')
    if not q or not q.get('hasMappin') or q.get('mx') is None:
        return None
    return math.hypot(st['x'] - q['mx'], st['y'] - q['my'])


def run(duration_s: float = 300.0, stop=None, pause=None) -> dict:
    hard_stop = stop
    if pause is not None:
        class _Halt:                                   # pour les competences : pause OU arret = rendre la main
            def is_set(self_): return (hard_stop is not None and hard_stop.is_set()) or pause.is_set()
        stop = _Halt()
    paused_logged = False
    t0 = time.perf_counter()
    stats = {'dialogues': 0, 'interactions': 0, 'trajets': 0, 'attentes': 0}
    last_quest_text = None
    idle_since = None
    same_hub, last_hub_sig = 0, None
    no_mappin_since = None
    path_failures = 0
    rot_failures = 0
    straight_tried = False
    obj_inter_tries = {}
    unfocused = False
    frozen_seq, frozen_t, frozen_logged = None, 0.0, False
    dead_since = None
    last_block_pos = None
    last_heal_t = -99.0
    plan = planner.Planner()
    last_inventory_t = -999.0
    inventory_done_once = False
    last_levelup_t = -999.0
    last_sell_t = -999.0
    last_overlevel_t = -999.0
    mute_hostiles = {}
    last_ripper_t = -999.0
    money_start = None
    vendor_fail_streak = 0
    last_drive_t = -999.0
    inter_tries: dict = {}          # (titre, choix, zone) -> (essais, ignore_jusqu_a)
    alt_target = None               # marqueur de quete choisi par V (x, y, texte, t0) quand l objectif suivi est bloque
    approached: dict = {}           # (nom, zone) -> heure : PNJ deja abordes (pas de harcelement pendant 5 min)
    looted: set = set()             # objets/conteneurs deja traites (position arrondie)
    rescued: dict = {}              # zone d agression -> heure (pas de retour sur la meme agression pendant 5 min)
    def _was_rescued(aggr, crimes):
        pts = [(a['x'], a['y']) for a in aggr] + [(c['x'], c['y']) for c in crimes]
        if not pts:
            return True
        k = (round(pts[0][0] / 15), round(pts[0][1] / 15))
        return time.perf_counter() - rescued.get(k, -9999.0) < 300.0
    def _was_approached(n):
        k = (n.get('name'), round(n['x'] / 8), round(n['y'] / 8))
        return time.perf_counter() - approached.get(k, -9999.0) < 300.0
    # version du code reellement chargee (evite de diagnostiquer un run perime)
    import os
    def _mt(m):
        try:
            return time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(Path(__file__).with_name(m + '.py'))))
        except OSError:
            return 'exe'
    stamp = ' '.join(f"{m}={_mt(m)}"
                     for m in ('brain', 'combat', 'planner', 'motion'))
    _log(f'=== cerveau v1 demarre ({duration_s:.0f} s max) | code : {stamp} ===')
    # auto-test : V bouge-t-il ? (touche avant 0,6 s) -> detecte tout de suite un blocage d entree
    s0 = motion.read_state()
    if s0 and kbm.game_focused():
        kbm.hold('W'); time.sleep(0.6); kbm.release('W'); time.sleep(0.3)
        s1 = motion.read_state() or s0
        moved = math.hypot(s1['x'] - s0['x'], s1['y'] - s0['y'])
        _log(f"auto-test deplacement : {moved:.2f} m en 0,6 s -> {'OK' if moved > 0.3 else 'V NE BOUGE PAS (menu ? animation ? touche ?)'}")

    while time.perf_counter() - t0 < duration_s:
        if hard_stop is not None and hard_stop.is_set():
            _log('arret d urgence'); break
        if pause is not None and pause.is_set():
            kbm.release_all()
            if not paused_logged:
                paused_logged = True; _log('PAUSE (F11) : le joueur a la main ; F11 pour reprendre')
            time.sleep(0.3); continue
        if paused_logged:
            paused_logged = False; _log('REPRISE (F11) : V rejoue'); plan.last_t = -99.0
        # V aime ecouter la radio de temps a autre (hors combat, dialogue, vehicule) : c est lui qui choisit la station
        try:
            _st_r = motion.read_state() or {}
            radio.tick(_st_r, log=_log, busy=bool(_st_r.get('combat') or (_st_r.get('dialog') or {}).get('choices') or _st_r.get('vehicle')))
        except Exception as _e:
            _log(f'  [radio] erreur : {_e}')
        # 0. jeu au premier plan ? sinon on ne touche a RIEN (les entrees iraient ailleurs)
        if not kbm.game_focused():
            kbm.release_all()
            if not unfocused:
                unfocused = True
                _log('jeu hors premier plan : entrees suspendues')
            time.sleep(0.3); continue
        if unfocused:
            unfocused = False
            _log('jeu de retour au premier plan : reprise')
        st = motion.read_state()
        if st is None:
            stats['attentes'] += 1; time.sleep(0.5); continue

        # 0b. etat FIGE (pause, menu, carte, chargement) : le mod ne tourne plus -> on ne touche a rien
        if st.get('seq') != frozen_seq:
            frozen_seq, frozen_t = st.get('seq'), time.perf_counter()
            if frozen_logged:
                frozen_logged = False; _log('etat a nouveau vivant : reprise')
        elif time.perf_counter() - frozen_t > 2.0:
            kbm.release_all()
            if not frozen_logged:
                frozen_logged = True; _log('etat fige (pause, menu ou chargement) : attente')
            time.sleep(0.5); continue

        if st.get('carrying') and kbm.ACTIONS.get('dropbody'):
            _log('V porte un corps : il le lache (il ne peut ni courir ni se battre ainsi)')
            kbm.act('dropbody', 0.2); time.sleep(1.0); continue

        if st.get('vehicle'):
            qv = st.get('quest') or {}
            dv = math.hypot(st['x'] - qv['mx'], st['y'] - qv['my']) if qv.get('mx') is not None else None
            if dv is not None and dv > 150.0 and kbm.ACTIONS.get('autodrive'):
                _log(f'V est deja en vehicule et l objectif est a {dv:.0f} m : autodrive')
                r = driving.autodrive_to(qv['mx'], qv['my'], stop=stop, log=_log)
                _log(f"conduite : {'arrive' if r.get('ok') else r.get('reason')}")
                driving.exit_vehicle(log=_log)
            else:
                _log('V est dans un vehicule et l objectif est proche : il descend')
                driving.exit_vehicle(log=_log)
            continue

        hp_now = st.get('hp')
        if hp_now is not None and hp_now <= 0.5:      # NB : `hp or 100` transformait 0 en 100 -> boucle de mort
            dead_since = dead_since or time.perf_counter()
            if time.perf_counter() - dead_since > 4.0:  # 4 s a 0 sur un etat vivant (pas un chargement)
                quests.mark_death(st['x'], st['y'])
                _log('V EST MORT : arret (recharge une sauvegarde) ; lieu memorise, les objectifs a < 80 m seront evites'); break
            time.sleep(0.3); continue
        dead_since = None

        q = st.get('quest') or {}
        if q.get('lvl') and st.get('level') and q['lvl'] > st['level'] + 3 and alt_target is None \
                and time.perf_counter() - last_overlevel_t > 300.0:
            last_overlevel_t = time.perf_counter()
            _log(f"quete suivie « {q.get('text')} » de niveau {q['lvl']} pour V niveau {st['level']} : trop haute, on en cherche une de son niveau")
            nxt = quests.switch(q.get('hash'), log=_log)
            if nxt:
                alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                continue
        if q.get('text') and q['text'] != last_quest_text:
            last_quest_text = q['text']
            _log(f"objectif : {q['text']}  (marqueur : {'oui' if q.get('hasMappin') else 'non'})")

        # 2. dialogue (avec detection de boucle : meme hub re-propose N fois = bloque)
        d = st.get('dialog')
        if d and d.get('choices'):
            sig = dialog.hub_signature(d)
            same_hub = same_hub + 1 if sig == last_hub_sig else 1
            last_hub_sig = sig
            if same_hub > 6:
                _log(f'BLOQUE : le hub "{d.get("title")}" revient sans cesse ({same_hub}x) ; '
                     f'choix grises = {[c for c, g in zip(d["choices"], d.get("inactive") or []) if g]}.')
                # sortir du dialogue : choix de sortie s il existe, sinon Echap
                quit_idx = next((i for i, c in enumerate(d['choices'])
                                 if any(w in c.lower() for w in dialog.QUIT_WORDS)), None)
                if quit_idx is not None:
                    dialog.select_index(quit_idx); dialog.confirm()
                else:
                    kbm.tap('ESC', 0.09)
                time.sleep(1.5)
                nxt = quests.switch(q.get('hash'), log=_log)
                if nxt:
                    alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                    stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                    same_hub, last_hub_sig = 0, None
                    time.sleep(1.0); continue
                _log('aucune autre quete accessible : arret.')
                break
            r = dialog.answer_once(stop=stop, log=_log)
            if r:
                stats['dialogues'] += 1
            time.sleep(0.8); continue
        same_hub, last_hub_sig = 0, None

        # 3. combat : melee + grenades + soin (agent/combat.py)
        if st.get('combat'):
            _near_h = min([e['d'] for e in (st.get('enemies') or []) if not e.get('dead')] or [99.0])
            if _near_h > 12.0 and (st.get('hp') or 100) >= 50:
                buffs.apply(st, log=_log, in_combat=True)  # se buffer AVANT de frapper, seulement si on a 3 s devant soi
            _log(f"combat detecte : {len(st.get('enemies') or [])} hostile(s), vie {st.get('hp', 0):.0f} %")
            r = combat.fight(stop=stop, log=_log)
            stats['combats'] = stats.get('combats', 0) + 1
            if not r.get('mort'):
                lr = combat.loot_around(stop=stop, log=_log, seen=looted)
                _log(f"loot : {lr.get('ramasses', 0)}/{lr.get('objets', 0)} objets ramasses")
                stats['loot'] = stats.get('loot', 0) + lr.get('ramasses', 0)
                ir = inventory.manage(log=_log); last_inventory_t = time.perf_counter()
                buffs.refresh()
                stats['equipes'] = stats.get('equipes', 0) + ir.get('equipes', 0)
                stats['demontes'] = stats.get('demontes', 0) + ir.get('demontes', 0)
            time.sleep(0.5); continue

        # 3a. gestion d inventaire : une fois au depart (choix de l arme), puis toutes les 5 min
        if time.perf_counter() - last_inventory_t > 300.0 or not inventory_done_once:
            inventory_done_once = True
            ir = inventory.manage(log=_log); last_inventory_t = time.perf_counter()
            buffs.refresh()
            if money_start is None:
                money_start = inventory.MONEY
            stats['equipes'] = stats.get('equipes', 0) + ir.get('equipes', 0)
            stats['demontes'] = stats.get('demontes', 0) + ir.get('demontes', 0)

        # 3a-bis. montee de niveau : points d attribut depenses (build melee) a chaque passe d inventaire
        if inventory_done_once and time.perf_counter() - last_levelup_t > 300.0:
            last_levelup_t = time.perf_counter()
            lr2 = nav._wait(nav._send({'cmd': 'levelup'}), timeout=6.0)
            if lr2 and lr2.get('ok'):
                _log(f"niveau : {lr2.get('bought', 0)} point(s) d attribut depenses ({lr2.get('attribute_points_before')} -> {lr2.get('attribute_points_after')}), perks dispo {lr2.get('perk_points')}")
            else:
                _log(f"niveau : echec ({(lr2 or {}).get('reason', 'mod muet')})")

        # 3a-ter. COURSES : assez d objets a vendre OU soins bas (craft insuffisant) -> marchand a portee
        need_heals = inventory.HEALS < 2
        if (len(inventory.SELLABLE) >= 8 or need_heals) and time.perf_counter() - last_sell_t > 600.0:
            last_sell_t = time.perf_counter()
            vendor.MAX_VENDOR_M = 700.0 if (len(inventory.SELLABLE) >= 20 or need_heals) else 250.0   # urgent -> on accepte d aller plus loin
            vend = vendor.pick_vendor(vendor.list_vendors())
            if vend:
                _log(f"courses : {len(inventory.SELLABLE)} objets a vendre, soins {inventory.HEALS}, marchand a {vend['dist']:.0f} m")
                tr = vendor.sell_trip(vend, stop=stop, log=_log)
                if not tr.get('ok'):
                    _log(f"courses : marchand non atteint ({tr.get('reason')}) : ecarte 15 min, on essaiera un autre")
                    vendor.mark_failed(vend); vendor_fail_streak += 1
                    if vendor_fail_streak >= 3:                 # maillage local impraticable : on suspend les courses 30 min
                        _log('courses : 3 marchands injoignables d affilee, courses suspendues 30 min')
                        last_sell_t = time.perf_counter() + 1200.0; vendor_fail_streak = 0
                    else:
                        last_sell_t = time.perf_counter() - 480.0   # nouvel essai (autre marchand) dans 2 min
                if tr.get('ok'):
                    vendor_fail_streak = 0
                    sr = inventory.sell_all(log=_log)
                    _log(f"vente : {sr.get('vendus', 0)} objet(s) pour {sr.get('eddies', 0)} eddies")
                    stats['ventes'] = stats.get('ventes', 0) + sr.get('vendus', 0)
                    br = vendor.buy_heals(inventory.HEALS, log=_log)
                    if br.get('achetes'):
                        inventory.HEALS += br['achetes']
                        _log(f"achat : {br['achetes']} soin(s) pour {br.get('eddies', 0)} eddies")
                        stats['achats'] = stats.get('achats', 0) + br['achetes']
                continue

        # 3a-quater. CHARCUDOC : assez d eddies -> V s optimise lui-meme (meilleur cyberware abordable, pose par script)
        if inventory.MONEY >= 6000 and time.perf_counter() - last_ripper_t > 1800.0 and not st.get('combat'):
            last_ripper_t = time.perf_counter()
            vendor.MAX_VENDOR_M = 700.0
            rip = vendor.pick_vendor(vendor.list_vendors(), prefer='ripper')
            if rip and 'ripper' in (rip.get('variant') or '').lower():
                _log(f"charcudoc : {inventory.MONEY} eddies, « {rip.get('variant')} » a {rip['dist']:.0f} m : V va s optimiser")
                tr = vendor.sell_trip(rip, stop=stop, log=_log)
                if tr.get('ok'):
                    rr = vendor.ripperdoc_shop(log=_log)
                    _log(f"charcudoc : {rr.get('poses', 0)} implant(s) pose(s) pour {rr.get('eddies', 0)} eddies ({rr.get('reason') or 'ok'})")
                    stats['implants'] = stats.get('implants', 0) + rr.get('poses', 0)
                else:
                    _log(f"charcudoc : non atteint ({tr.get('reason')})"); vendor.mark_failed(rip)
                continue

        # 3b. soin hors combat si la vie est basse
        if (st.get('hp') or 100) < 40 and time.perf_counter() - last_heal_t > 8.0:
            kbm.act('consumable', 0.1); last_heal_t = time.perf_counter()
            _log(f"soin hors combat (vie {st.get('hp'):.0f} %)")

        # 4. DECISION de V (modele local) : objectif / parler / attaquer / changer_quete / attendre
        if alt_target is not None:
            dist = math.hypot(st['x'] - alt_target['x'], st['y'] - alt_target['y'])
            if dist < ARRIVE_M or time.perf_counter() - alt_target['t0'] > 900:
                _log('cible alternative atteinte ou expiree : retour a la quete suivie')
                quests.mark_visited(alt_target.get('hash'))
                alt_target = None
                dist = _dist_to_mappin(st)
        else:
            dist = _dist_to_mappin(st)
        inter = st.get('interact')
        action = plan.maybe_decide(st, {'dist_m': dist, 'approached': _was_approached, 'rescued': _was_rescued}, log=_log)

        if action == 'eviter':
            hs = [e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('police')]
            if hs:
                cx = sum(e['x'] for e in hs) / len(hs); cy = sum(e['y'] for e in hs) / len(hs)
                nav.add_avoid(cx, cy)
                quests.mark_danger(cx, cy)
                _log(f'ZONE DANGEREUSE ({len(hs)} hostiles) : V s eloigne et change d objectif')
                away = motion.wrap(motion.bearing_to(cx, cy, st['x'], st['y']))
                motion.turn_to(away, timeout=2.0, stop=stop)
                kbm.hold('W'); kbm.act_hold('sprint'); time.sleep(4.0); kbm.act_release('sprint'); kbm.release('W')
                stats['evitements'] = stats.get('evitements', 0) + 1
            nxt = quests.switch(alt_target.get('hash') if alt_target else q.get('hash'), log=_log)
            if nxt:
                alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
            plan.last_t = -99.0; plan.action = 'objectif'
            continue

        if action == 'secourir':
            aggr = planner.aggressors(st)
            crimes = st.get('crimes') or []
            tgt = aggr[0] if aggr else (crimes[0] if crimes else None)
            if tgt:
                k = (round(tgt['x'] / 15), round(tgt['y'] / 15)); rescued[k] = time.perf_counter()
                _log(f"V JOUE LES SAUVEURS : {len(aggr)} agresseur(s), a {tgt['d']:.0f} m")
                plan.note('a secouru quelqu un')
                if tgt['d'] > 12:
                    nav.goto(lambda t=tgt: nav.request_path_to(t['x'], t['y'], t.get('z')), arrive_m=10.0, max_legs=3,
                             timeout=60.0, stop=stop, log=_log, interrupt=_threat_near)
                s2 = motion.read_state() or st
                aggr2 = planner.aggressors(s2)
                if aggr2:
                    a = aggr2[0]
                    combat.engage({'x': a['x'], 'y': a['y'], 'd': a['d'], 'sy': None}, stop=stop, log=_log)
                    r = combat.fight(stop=stop, log=_log)
                    stats['combats'] = stats.get('combats', 0) + 1
                    stats['sauvetages'] = stats.get('sauvetages', 0) + 1
                    if not r.get('mort'):
                        lr = combat.loot_around(stop=stop, log=_log, seen=looted)
                        stats['loot'] = stats.get('loot', 0) + lr.get('ramasses', 0)
                else:
                    _log('  agression terminee ou hors de vue a l arrivee')
                plan.last_t = -99.0
                continue
            action = 'objectif'

        # 4a. environnement : objets lootables non traites a < 12 m -> on ramasse d abord
        pending = [o for o in (st.get('loot') or []) if o['d'] < 12 and (round(o['x']), round(o['y'])) not in looted]
        if pending and action in ('objectif', 'aborder', 'attendre'):
            _log(f"environnement : {len(pending)} objet(s) lootable(s) proche(s) ({pending[0].get('cls', '?')} a {pending[0]['d']:.0f} m)")
            lr = combat.loot_around(stop=stop, log=_log, radius_m=12.0, max_items=4, seen=looted)
            stats['loot'] = stats.get('loot', 0) + lr.get('ramasses', 0)
            continue

        if action == 'aborder':
            cands = [n for n in planner.named_npcs(st) if n['d'] < 12 and not _was_approached(n)]
            if cands:
                n = cands[0]
                approached[(n.get('name'), round(n['x'] / 8), round(n['y'] / 8))] = time.perf_counter()
                _log(f"V aborde « {n.get('name')} » ({n['d']:.0f} m)")
                plan.note(f"a aborde {n.get('name')}")
                old_arrive = motion.ARRIVE_M; motion.ARRIVE_M = 1.8
                try:
                    r = motion.walk_to(n['x'], n['y'], timeout=15.0, stop=stop, interrupt=_threat_near)
                finally:
                    motion.ARRIVE_M = old_arrive
                if r.get('ok'):
                    # le regarder jusqu a ce que l invite « Parler » apparaisse (3 s max), puis F
                    t1 = time.perf_counter(); spoke = False
                    while time.perf_counter() - t1 < 3.0:
                        s2 = motion.read_state()
                        if not s2: break
                        target = next((m for m in (s2.get('npcs') or []) if m.get('name') == n.get('name')), n)
                        combat.aim_at({'x': target['x'], 'y': target['y'], 'sy': None}, s2)
                        inter = s2.get('interact')
                        if inter and inter.get('choices'):
                            kbm.act('interact', 0.45); spoke = True
                            _log(f"  -> « {inter['choices'][0]} » avec {n.get('name')}")
                            stats['interactions'] += 1
                            break
                        time.sleep(0.1)
                    if not spoke:
                        _log('  -> pas d invite de dialogue : on passe')
                else:
                    _log(f"  -> impossible de l atteindre ({r.get('reason')})")
                time.sleep(0.8); continue
            action = 'objectif'

        if action == 'parler' and inter and inter.get('choices') and any(w in inter['choices'][0].lower() for w in ('saisir', 'porter', 'soulever')):
            action = 'objectif'           # porter un corps n est jamais utile a V
        if action == 'parler':
            if inter and inter.get('choices'):
                # anti-boucle : la meme invite au meme endroit n est tentee que 2 fois, puis
                # ignoree 90 s (porte verrouillee, PNJ muet... V appuyait sur F sans fin)
                # cle SANS position : une invite qui suit V (« Activer ») ne doit pas etre retentee a chaque arret
                isig = (inter.get('title'), inter['choices'][0])
                tries, until = inter_tries.get(isig, (0, 0.0))
                if time.perf_counter() < until:
                    action = 'objectif'
                elif tries >= 2:
                    inter_tries[isig] = (0, time.perf_counter() + 600.0)
                    _log(f"invite « {inter['choices'][0]} » sans effet apres 2 essais : ignoree 10 min")
                    action = 'objectif'
                else:
                    inter_tries[isig] = (tries + 1, 0.0)
                    _log(f"V engage la conversation : « {inter['choices'][0]} » -> F (essai {tries + 1})")
                    plan.note('a engage une conversation')
                    kbm.act('interact', 1.0)   # maintien : certaines invites exigent un appui long
                    stats['interactions'] += 1
                    time.sleep(1.5); continue
            else:
                # rien a qui parler ici : on retombe sur l objectif
                action = 'objectif'

        if action == 'attaquer':
            hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('police')
                        and (e.get('z') is None or abs(e['z'] - st.get('z', e['z'])) < 3.5)]
            # hostiles « muets » : cibles qui n entrent jamais en combat (PNJ fuyards, tourelles hors portee...) ->
            # ignorees 3 min apres un engagement sans combat, pour ne pas perdre 25 s a chaque fois
            hostiles = [e for e in hostiles if time.perf_counter() - mute_hostiles.get((round(e['x']), round(e['y'])), -1e9) > 180.0]
            if hostiles:
                _log(f"V engage le combat : {len(hostiles)} hostile(s), le plus proche a {hostiles[0]['d']:.0f} m")
                plan.note('a engage un combat')
                engaged = combat.engage(hostiles[0], stop=stop, log=_log, max_s=14.0)
                s_now = motion.read_state() or {}
                if not engaged and not s_now.get('combat'):
                    mute_hostiles[(round(hostiles[0]['x']), round(hostiles[0]['y']))] = time.perf_counter()
                    _log('  cible sans reaction : ignoree 3 min')
                    plan.last_t = -99.0; plan.action = 'objectif'
                    continue
                r = combat.fight(stop=stop, log=_log)
                stats['combats'] = stats.get('combats', 0) + 1
                if r.get('sterile'):
                    for e in (motion.read_state() or {}).get('enemies') or []:
                        mute_hostiles[(round(e['x']), round(e['y']))] = time.perf_counter()
                if not r.get('mort'):
                    lr = combat.loot_around(stop=stop, log=_log, seen=looted)
                    _log(f"loot : {lr.get('ramasses', 0)}/{lr.get('objets', 0)} objets ramasses")
                    stats['loot'] = stats.get('loot', 0) + lr.get('ramasses', 0)
                continue
            action = 'objectif'

        if action == 'changer_quete':
            nxt = quests.switch(q.get('hash') if alt_target is None else alt_target.get('hash'), log=_log)
            if nxt:
                alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                plan.note('a change de cible')
                plan.last_t = -99.0; plan.action = 'objectif'     # forcer une nouvelle decision (3 changements en 3 s sinon)
                time.sleep(1.0); continue
            action = 'attendre'

        if action == 'attendre':
            stats['attentes'] += 1
            time.sleep(1.0); continue

        # 5. aller vers l objectif (action == 'objectif')
        if inter and inter.get('choices') and dist is not None and dist < ARRIVE_M + 2.0:
            # arrive au marqueur et une interaction est proposee : c est l objectif lui-meme... sauf les invites
            # de vehicule (« Prendre le controle » = piratage d une voiture, « Enfourcher ») et les invites PERIMEES
            # (le blackboard garde la derniere) : 3 essais max par invite, puis on l ignore 3 min
            ch0 = inter['choices'][0].lower()
            ikey = (str(inter.get('title') or ''), ch0)
            tries, t_first = obj_inter_tries.get(ikey, (0, time.perf_counter()))
            if time.perf_counter() - t_first > 180.0:
                tries, t_first = 0, time.perf_counter()
            vehicle_prompt = any(w in ch0 for w in ('prendre le contr', 'enfourcher', 'monter dans', 'monter a bord'))
            if not vehicle_prompt and tries < 3:
                obj_inter_tries[ikey] = (tries + 1, t_first)
                _log(f"interaction d objectif « {inter['choices'][0]} » -> E (maintenu) (essai {tries + 1}/3)")
                kbm.act('interact', 1.0)   # « Ouvrir », « Activer »... exigent parfois un appui long
                stats['interactions'] += 1
                time.sleep(1.5); continue
            if tries == 3 or (vehicle_prompt and ikey not in obj_inter_tries):
                obj_inter_tries[ikey] = (4, t_first)
                _log(f"invite « {inter['choices'][0]} » ignoree ({'vehicule' if vehicle_prompt else 'sans effet apres 3 essais'})")
        if dist is not None and dist > 500.0 and alt_target is None and kbm.ACTIONS.get('autodrive') \
                and time.perf_counter() - last_drive_t > 240.0:
            last_drive_t = time.perf_counter()
            _log(f'objectif a {dist:.0f} m : V prend la voiture (autodrive)')
            plan.note('a pris la voiture')
            q0 = st.get('quest') or {}
            r = driving.drive_to(q0.get('mx'), q0.get('my'), stop=stop, log=_log)
            stats['conduites'] = stats.get('conduites', 0) + 1
            _log(f"conduite : {'arrive' if r.get('ok') else r.get('reason')}")
            if not r.get('ok') and r.get('reason') == 'embarquement echoue':
                last_drive_t = time.perf_counter() + 360.0      # ici la moto ne vient pas / ne se monte pas : pas avant 10 min
            continue

        if dist is not None and dist > ARRIVE_M:
            no_mappin_since = None
            _log(f'objectif a {dist:.0f} m : aller_a')
            req = (lambda t=alt_target: nav.request_path_to(t['x'], t['y'])) if alt_target is not None else nav.request_path_to_quest
            r = nav.goto(req, arrive_m=ARRIVE_M, max_legs=1, timeout=60.0, stop=stop, log=_log,
                         interrupt=_threat_near)
            if r.get('reason') == 'interrompu':
                _log('trajet interrompu : menace proche -> decision immediate')
                plan.last_t = -99.0
                continue
            stats['trajets'] += 1
            # coince au meme endroit deux fois : on memorise la zone, le mod evitera d y renvoyer
            here = (st['x'], st['y'])
            if not r.get('ok') and last_block_pos and math.hypot(here[0] - last_block_pos[0], here[1] - last_block_pos[1]) < 3.0:
                nav.add_avoid(*here)
                _log(f'  zone bloquante memorisee ({here[0]:.0f},{here[1]:.0f}) : prochain troncon ailleurs')
                last_block_pos = None
            elif not r.get('ok'):
                last_block_pos = here
            else:
                last_block_pos = None
            if 'rotation initiale' in str(r.get('reason') or ''):
                rot_failures += 1
                if rot_failures >= 2:
                    _log('la souris est sans effet depuis 2 trajets : V est sans doute dans une scene (assis, stand) -> on en sort')
                    kbm.tap('ESC', 0.09); time.sleep(0.8)
                    s_e = motion.read_state() or {}
                    if s_e.get('seq') == st.get('seq'):            # menu pause ouvert par Echap -> on le referme
                        kbm.tap('ESC', 0.09); time.sleep(0.6)
                    kbm.hold('S'); time.sleep(1.5); kbm.release('S')
                    kbm.act('jump', 0.1); time.sleep(0.8)
                    rot_failures = 0
                    continue
            else:
                rot_failures = 0
            if r.get('ok') or r.get('legs', 0) > 0:
                path_failures = 0; straight_tried = False
            else:
                path_failures += 1
                _log(f"  trajet : {r.get('reason')} (echec {path_failures}/5)")
                if path_failures >= 5 and dist is not None and dist < 300 and not straight_tried:
                    # le maillage ne repond pas (interieur, escalier, ring...) : on marche EN LIGNE DROITE vers
                    # la cible 20 s (les portes s ouvrent en passant) ; si V a avance, le maillage se recalcule
                    straight_tried = True
                    _log(f'aucun chemin : marche en ligne droite vers la cible ({dist:.0f} m) pour sortir d ici')
                    tx, ty = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                    rw = motion.walk_to(tx, ty, timeout=20.0, stop=stop, interrupt=_threat_near, sprint=True)
                    s2 = motion.read_state() or st
                    moved = math.hypot(s2['x'] - st['x'], s2['y'] - st['y'])
                    _log(f"  ligne droite : {'ok' if rw.get('ok') else rw.get('reason')}, {moved:.0f} m parcourus")
                    if moved > 8.0:
                        path_failures = 0
                        continue
                    # toujours coince : V cherche une SORTIE (portes, invites, sondes dans 8 directions)
                    _log('ilot ferme : recherche d une sortie (portes, invites, sondes)')
                    re = escape.escape((tx, ty), stop=stop, log=_log)
                    _log(f"  sortie : {'trouvee par ' + re.get('moyen', '?') if re.get('ok') else 'introuvable (' + re.get('moyen', '?') + ')'}")
                    stats['sorties'] = stats.get('sorties', 0) + (1 if re.get('ok') else 0)
                    if re.get('ok'):
                        path_failures = 0
                    continue
                if path_failures >= 5:
                    _log('objectif inaccessible a pied depuis ici : changement de quete')
                    path_failures = 0
                    straight_tried = False
                    nxt = quests.switch(alt_target.get('hash') if alt_target else q.get('hash'), log=_log)
                    if nxt:
                        alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        time.sleep(1.0); continue
                    _log('aucune autre quete accessible : arret.')
                    break
                time.sleep(3.0)
            continue

        # 6. rien a faire : l objectif n a pas de marqueur, ou on y est sans interaction
        stats['attentes'] += 1
        if no_mappin_since is None:
            no_mappin_since = time.perf_counter()
        elif time.perf_counter() - no_mappin_since > 20.0:
            _log(f"objectif sans marqueur depuis 20 s ({q.get('text')}) : changement de quete")
            no_mappin_since = None
            nxt = quests.switch(q.get('hash'), log=_log)
            if nxt:
                alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                time.sleep(1.0); continue
            _log('aucune autre quete accessible : arret.')
            break
        time.sleep(0.5)

    kbm.release_all()
    dialog.unload_model()
    stats['seconds'] = time.perf_counter() - t0
    if money_start is not None:
        stats['eddies_gagnes'] = inventory.MONEY - money_start
    radio.turn_off(log=_log)
    _log(f'=== fin : {stats} ===')
    return stats
