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
import os
import sys
import time
from pathlib import Path

from . import appearance, braindance, breach, buffs, combat, dialog, driving, escape, explore, input_kbm as kbm, inventory, motion, nav, planner, quests, radio, remote, sms, terminals, vendor

from .config import CFG
LOG_FILE = CFG.log_file                 # %APPDATA%/CyberpunkAgent/brain_log.txt
ARRIVE_M = 3.0

# mots-cles exacts de la boucle de directives (0a0) : tout ce qui ne matche pas passe par remote.classify()
_DIRECTIVE_KEYWORDS = frozenset({
    'stop', 'arret', 'arrete-toi', 'arrete toi', 'pause', 'reprendre', 'resume', 'continue',
    'attaque', 'attaquer', 'combat', 'objectif', 'quete', 'marchand', 'vendre', 'boutique',
    'charcudoc', 'ripperdoc', 'implant', 'explore', 'explorer', 'balade', 'changer_quete', 'autre_quete',
    'status', 'etat', 'etat?', 'photo', 'screenshot', 'capture', 'niveau', 'soigne', 'stats',
})
_DIRECTIVE_PREFIXES = ('va_a ', 'va a ', 'courage ', 'style ', 'aggro ')


_LOG_STATE = {'file': LOG_FILE, 'err': None}


def _log(msg: str) -> None:
    """Journal : console + fichier. Jamais bloquant : une console qui refuse un caractere ou un fichier inaccessible
    ne doit pas tuer la session (16/09 : l exe lance depuis le panneau ne laissait AUCUN journal)."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            print(line.encode('ascii', 'replace').decode('ascii'), flush=True)
        except Exception:
            pass
    # miroir dans le dossier du mod (chemin dont l ecriture est prouvee : agent_diag.txt y arrive) : le journal
    # habituel restait vide pour l exe lance depuis le panneau (16/09), sans erreur rapportee
    try:
        if CFG.mod_dir:
            with (CFG.mod_dir / 'agent_log.txt').open('a', encoding='utf-8') as f:
                f.write(line + '\n')
    except Exception as e:
        _LOG_STATE['err_mod'] = repr(e)
    _LOG_STATE['n'] = _LOG_STATE.get('n', 0) + 1
    if _LOG_STATE['n'] % 50 == 0:
        _write_diag()
    fallback = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent) / 'brain_log.txt'
    for target in (_LOG_STATE['file'], fallback):
        try:
            with target.open('a', encoding='utf-8') as f:
                f.write(line + '\n')
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            _LOG_STATE['file'] = target
            _LOG_STATE['size'] = target.stat().st_size
            return
        except Exception as e:
            _LOG_STATE['err'] = f'{target}: {e!r}'


def _write_diag() -> None:
    """Fichier de diagnostic dans le dossier du mod (toujours lisible) : ou va le journal, quel exe, quel environnement."""
    try:
        d = (CFG.mod_dir / 'agent_diag.txt') if CFG.mod_dir else Path('agent_diag.txt')
        d.write_text('\n'.join([
            f"heure : {time.strftime('%d/%m %H:%M:%S')}",
            f"exe : {sys.executable}",
            f"frozen : {getattr(sys, 'frozen', False)}",
            f"cwd : {Path.cwd()}",
            f"APPDATA : {os.environ.get('APPDATA')}",
            f"journal : {_LOG_STATE['file']}",
            f"erreur journal : {_LOG_STATE['err']}",
            f"lignes ecrites : {_LOG_STATE.get('n', 0)} ; taille vue par l exe : {_LOG_STATE.get('size')} ; erreur miroir : {_LOG_STATE.get('err_mod')}",
            f"argv : {sys.argv}",
        ]) + '\n', encoding='utf-8')
    except Exception:
        pass


ENGAGE_M = 25.0


def _sleep(seconds: float, stop=None) -> bool:
    """Attente interruptible par F12 (stop) : renvoie False si on a ete interrompu."""
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        if stop is not None and stop.is_set():
            return False
        time.sleep(min(0.1, max(0.0, t_end - time.perf_counter())))
    return True


def _reload_last_save(log, stop=None) -> bool:
    """Ecran de mort : « Charger la derniere sauvegarde » est le choix par defaut. On attend l ecran (6 s),
    on valide (touche UI), puis on attend que V soit vivant et que l etat bouge (60 s max). Interruptible (F12)."""
    if not _sleep(6.0, stop):
        return False
    for attempt in range(3):
        # d abord la commande du jeu (celle du menu de mort : LoadLastCheckpoint), la touche de validation en secours
        rr = nav._wait(nav._send({'cmd': 'reload'}), timeout=5.0)
        if rr and rr.get('ok'):
            log('  rechargement demande au jeu (LoadLastCheckpoint)')
        else:
            log(f"  rechargement par la touche de validation ({(rr or {}).get('reason', 'mod muet')})")
            kbm.act('ui_confirm', 0.1)
        t0 = time.perf_counter(); last_seq = None
        while time.perf_counter() - t0 < 60.0:
            if stop is not None and stop.is_set():
                return False
            s = motion.read_state()
            if s and (s.get('hp') or 0) > 5 and s.get('seq') != last_seq:
                last_seq = s.get('seq')
                _sleep(2.0, stop)
                s2 = motion.read_state()
                if s2 and (s2.get('hp') or 0) > 5 and s2.get('seq') != last_seq:
                    log(f'  sauvegarde rechargee (vie {s2.get("hp"):.0f} %), V reprend')
                    _sleep(20.0, stop)                      # chauffe du mod apres chargement
                    return True
            time.sleep(0.5)
        log(f'  rechargement : pas de signe de vie (essai {attempt + 1}/3)')
    return False


def _approach_machine(tx: float, ty: float, dist: float, log, stop) -> bool:
    return motion.approach_machine(tx, ty, dist, log, stop)


MUTE_HOSTILES: dict = {}        # (x, y) arrondis -> instant ou un hostile a ete declare « sans reaction » (ignore 3 min)
RESCUED: dict = {}              # zone d agression (x/15, y/15) -> heure du dernier secours (pas de retour pendant 5 min)
RESCUE_INTERRUPT_M = 30.0       # une agression a moins de 30 m interrompt le trajet en cours : V s implique
MUTE_S, MUTE_CLOSE_M = 180.0, 12.0
MUTE_FAILS: dict = {}           # cellule -> (nombre d engagements sans reaction, heure) ; 34 assauts a 1 m sur un intouchable le 17/09
MUTE_CELL_M, MUTE_FAILS_S = 3.0, 600.0   # cellule de 3 m (l hostile bouge un peu) ; echecs oublies apres 10 min


def _mute_key(e: dict) -> tuple:
    return (round(e['x'] / MUTE_CELL_M), round(e['y'] / MUTE_CELL_M))


def _mute_fail(e: dict, n: int = 1) -> None:
    k = _mute_key(e)
    c, t = MUTE_FAILS.get(k, (0, 0.0))
    now = time.perf_counter()
    if now - t > MUTE_FAILS_S:
        c = 0
    MUTE_FAILS[k] = (c + n, now)
    MUTE_HOSTILES[k] = now
    if len(MUTE_FAILS) > 200:                     # purge : on garde les 100 plus recents
        for old in sorted(MUTE_FAILS, key=lambda kk: MUTE_FAILS[kk][1])[:100]:
            MUTE_FAILS.pop(old, None); MUTE_HOSTILES.pop(old, None)


def _muted(e: dict) -> bool:
    """Hostile ignore : declare sans reaction il y a moins de 3 min ET pas au contact (a moins de 12 m on se bat,
    sinon boucle « attaquer -> objectif -> trajet interrompu » 3 fois par seconde, vue le 16/09 20:34)."""
    k = _mute_key(e)
    c, t = MUTE_FAILS.get(k, (0, 0.0))
    if time.perf_counter() - t > MUTE_FAILS_S:
        c = 0
    if (e.get('d') or 0.0) < MUTE_CLOSE_M and c < 2:
        return False                                # au contact on retente une fois ; au 2e echec c est un intouchable
    return time.perf_counter() - MUTE_HOSTILES.get(k, -1e9) < MUTE_S


RESCUE_STATE = {'last_interrupt': -9999.0}


def _rescue_cell(a: dict) -> tuple:
    return (round(a['x'] / 15), round(a['y'] / 15))


def _rescue_pending(st: dict, max_d: float = RESCUE_INTERRUPT_M) -> bool:
    """Une agression (PNJ agressif / en combat, hors police) a moins de max_d, pas encore traitee.
    Au plus une interruption de trajet par minute (17/09 04:47 : boucle a 3 Hz quand le planificateur refusait)."""
    if not CFG.features.get('rescue', True):
        return False
    now = time.perf_counter()
    if now - RESCUE_STATE['last_interrupt'] < 60.0:
        return False
    for a in planner.aggressors(st):
        if (a.get('d') or 99.0) < max_d and now - RESCUED.get(_rescue_cell(a), -9999.0) > 300.0:
            RESCUE_STATE['last_interrupt'] = now
            return True
    return False


def _threat_near(st: dict) -> bool:
    """Interrompt un trajet : combat, hostile vivant (non ignore) a moins de ENGAGE_M, ou agression proche a secourir."""
    if st.get('combat'):
        return True
    if any((not e.get('dead')) and (not e.get('police')) and e['d'] < ENGAGE_M and not _muted(e)
           and (e.get('z') is None or abs(e['z'] - st.get('z', e['z'])) < 3.5) for e in (st.get('enemies') or [])):
        return True
    return _rescue_pending(st)


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
    focus_insist_n = 0              # « on insiste » consecutifs sur la quete assignee inaccessible
    rot_failures = 0
    swim_logged = False
    straight_tried = False
    obj_inter_tries = {}
    unfocused = False
    frozen_seq, frozen_t, frozen_logged = None, 0.0, False
    frozen_esc = 0
    frozen_notified = False
    dead_since = None
    consecutive_deaths, last_death_t = 0, -1e9   # 3 morts en moins de 5 min au meme endroit -> on laisse tomber la quete un moment
    last_block_pos = None
    escape_tries, escape_pos = 0, None   # essais d evasion (porte/sonde) au MEME endroit : 3 max avant d escalader
    shop_stuck_n, shop_stuck_pos, shop_stuck_t = 0, None, -1e9   # ecarts d un marchand/menu au MEME endroit (survit aux resets de menu_t/dialog)
    stuck_cycles, stuck_pos = 0, None    # cycles d evasion EPUISES (escape_tries >= 3) dans le meme SECTEUR (60 m) : 2 max
    last_heal_t = -99.0
    plan = planner.Planner()
    last_inventory_t = -999.0
    inventory_done_once = False
    last_levelup_t = -999.0
    last_sell_t = -999.0
    last_ap_scan_t = -999.0        # derniere recherche de points d acces (terminaux)
    last_phone_t = -999.0
    breach_t = None
    bd_t = None
    bd_ok, bd_tries = True, 0
    menu_t, menu_esc, scene_t = None, 0, None
    scene_pos, scene_wait = None, 25.0
    shop_back_t = -999.0
    last_close_t = -999.0
    last_ft_t = -999.0
    last_overlevel_t = -999.0
    mute_hostiles = MUTE_HOSTILES
    last_ripper_t = -999.0
    money_start = None
    vendor_fail_streak = 0
    last_drive_t = -999.0
    inter_tries: dict = {}          # (titre, choix, zone) -> (essais, ignore_jusqu_a)
    alt_target = None               # marqueur de quete choisi par V (x, y, texte, t0) quand l objectif suivi est bloque
    approached: dict = {}           # (nom, zone) -> heure : PNJ deja abordes (pas de harcelement pendant 5 min)
    looted: set = set()             # objets/conteneurs deja traites (position arrondie)
    rescued: dict = RESCUED         # zone d agression -> heure (pas de retour sur la meme agression pendant 5 min)
    def _was_rescued(aggr, crimes):
        # traitee = TOUTES les cellules des agresseurs / marqueurs vues il y a moins de 5 min (meme regle que _rescue_pending)
        pts = [(a['x'], a['y']) for a in aggr] + [(c['x'], c['y']) for c in crimes if c.get('x') is not None]
        if not pts:
            return True
        return all(time.perf_counter() - rescued.get((round(x / 15), round(y / 15)), -9999.0) < 300.0 for x, y in pts)
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
    _write_diag()
    _log(f'=== cerveau v1 demarre ({duration_s:.0f} s max) | code : {stamp} ===')
    remote.ensure_started(log=_log)
    # auto-test : V bouge-t-il ? (touche avant 0,6 s) -> detecte tout de suite un blocage d entree
    s0 = motion.read_state()
    if s0 and kbm.game_focused():
        kbm.hold('W'); time.sleep(0.6); kbm.release('W'); time.sleep(0.3)
        s1 = motion.read_state() or s0
        moved = math.hypot(s1['x'] - s0['x'], s1['y'] - s0['y'])
        _log(f"auto-test deplacement : {moved:.2f} m en 0,6 s -> {'OK' if moved > 0.3 else 'V NE BOUGE PAS (menu ? animation ? touche ?)'}")

    crashes = 0
    try:
        while time.perf_counter() - t0 < duration_s:
            try:
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

                # 0a0. DIRECTIVE recue (jeu ou Telegram) : V est redirige en direct, sans attendre qu il ait fini
                dtv = remote.poll(log=_log)
                if dtv:
                    low = dtv.lower().strip()
                    if low not in _DIRECTIVE_KEYWORDS and not low.startswith(_DIRECTIVE_PREFIXES):
                        # pas un mot-cle exact : le modele local traduit la phrase libre (20/09, « trop basique »)
                        cl = remote.classify(dtv)
                        if cl:
                            _log(f'DIRECTIVE « {dtv} » comprise comme : {cl}')
                            dtv, low = cl, cl.lower().strip()
                    stats['directives'] = stats.get('directives', 0) + 1
                    if low in ('stop', 'arret', 'arrete-toi', 'arrete toi'):
                        _log('DIRECTIVE : arret demande'); remote.notify('V s arrete.')
                        if stop is not None: stop.set()
                    elif low == 'pause':
                        if pause is not None: pause.set()
                        remote.notify('V est en pause.')
                    elif low in ('reprendre', 'resume', 'continue'):
                        if pause is not None: pause.clear()
                        remote.notify('V reprend.')
                    elif low in ('attaque', 'attaquer', 'combat'):
                        hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead')]
                        if hostiles:
                            _log(f'DIRECTIVE : attaque ({len(hostiles)} hostile(s) en vue)')
                            combat.fight(stop=stop, log=_log)
                            stats['combats'] = stats.get('combats', 0) + 1
                            remote.notify(f'V a engage le combat ({len(hostiles)} hostile(s)).')
                        else:
                            _log('DIRECTIVE : attaque demandee mais aucun hostile en vue')
                            remote.notify('Aucun hostile en vue pour l instant.')
                    elif low in ('objectif', 'quete'):
                        alt_target = None
                        _log('DIRECTIVE : retour a la quete suivie')
                        remote.notify('V reprend sa quete.')
                    elif low in ('marchand', 'vendre', 'boutique'):
                        last_sell_t = -999.0
                        _log('DIRECTIVE : course chez le marchand forcee')
                        remote.notify('V part faire ses courses.')
                    elif low in ('charcudoc', 'ripperdoc', 'implant'):
                        last_ripper_t = -999.0
                        _log('DIRECTIVE : passage charcudoc force')
                        remote.notify('V va chez le charcudoc.')
                    elif low in ('explore', 'explorer', 'balade'):
                        nxd = explore.pick(log=_log)
                        if nxd:
                            alt_target = {'x': nxd['x'], 'y': nxd['y'], 'text': nxd.get('text'), 'hash': nxd.get('hash'), 't0': time.perf_counter()}
                            stats['explorations'] = stats.get('explorations', 0) + 1
                        remote.notify('V part explorer' + (' : ' + nxd['text'] if nxd else ' (aucun point connu)'))
                    elif low in ('changer_quete', 'autre_quete'):
                        nxd = quests.switch(alt_target.get('hash') if alt_target else (st.get('quest') or {}).get('hash'), log=_log)
                        if nxd:
                            alt_target = {'x': nxd['x'], 'y': nxd['y'], 'text': nxd.get('text'), 'hash': nxd.get('hash'), 't0': time.perf_counter()}
                            stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        remote.notify('nouvelle cible : ' + (nxd['text'] if nxd else 'aucune trouvee'))
                    elif low.startswith('va_a ') or low.startswith('va a '):
                        lieu = dtv.split(' ', 2)[-1].strip().lower()
                        cands = [v for v in (vendor.list_vendors() or []) if lieu in str(v.get('variant') or '').lower()]
                        ftr = nav._wait(nav._send({'cmd': 'fast_travel_points'}), timeout=6.0)
                        cands += [p for p in ((ftr or {}).get('points') or []) if p.get('x') is not None
                                  and (lieu in str(p.get('name') or '').lower() or lieu in str(p.get('district') or '').lower())]
                        if cands:
                            c = min(cands, key=lambda c: c.get('dist', c.get('d', 1e9)))
                            label = c.get('name') or c.get('variant') or lieu
                            alt_target = {'x': c['x'], 'y': c['y'], 'text': f'directive : {label}', 'hash': -800_000_000 - round(c['x'] + c['y']), 't0': time.perf_counter()}
                            _log(f'DIRECTIVE : V part vers « {label} »')
                            remote.notify(f'V part vers {label}.')
                        else:
                            _log(f'DIRECTIVE : lieu « {lieu} » inconnu')
                            remote.notify(f'Lieu « {lieu} » inconnu (marchand/charcudoc/point de voyage rapide deja decouvert seulement).')
                    elif low in ('status', 'etat', 'etat?'):
                        q_s = st.get('quest') or {}
                        d_s = _dist_to_mappin(st)
                        txt = (f"V : niveau {st.get('level', '?')}, vie {st.get('hp', 0):.0f} %, {inventory.MONEY} eddies\n"
                               f"Quete : {q_s.get('text') or 'aucune'}" + (f' (a {d_s:.0f} m)' if d_s is not None else '') + '\n'
                               f"Session : {stats.get('combats', 0)} combat(s), {stats.get('morts', 0)} mort(s), "
                               f"{stats.get('loot', 0)} objet(s) loote(s), {stats.get('trajets', 0)} trajet(s), "
                               f"{int(time.perf_counter() - t0)} s ecoulees")
                        _log('DIRECTIVE : etat demande')
                        remote.notify(txt)
                    elif low in ('photo', 'screenshot', 'capture'):
                        _log('DIRECTIVE : capture d ecran demandee')
                        img = remote.screenshot_jpeg()
                        if img:
                            sent = remote.send_photo(img, caption=f"V - {st.get('hp', 0):.0f} % de vie, niveau {st.get('level', '?')}")
                            _log(f"  [telegram] photo {'envoyee' if sent else 'echec d envoi'} ({len(img)} octets)")
                        else:
                            _log('  [telegram] capture d ecran impossible (dxcam/cv2 indisponible ?)')
                            remote.notify('Capture d ecran impossible (dxcam indisponible ?).')
                    elif low == 'niveau':
                        last_levelup_t = -999.0
                        _log('DIRECTIVE : verification niveau/perks forcee')
                        remote.notify('V verifie son niveau et ses perks.')
                    elif low == 'soigne':
                        if kbm.ACTIONS.get('consumable'):
                            kbm.act('consumable', 0.1); last_heal_t = time.perf_counter()
                            _log('DIRECTIVE : soin force')
                            remote.notify(f"V se soigne (vie {st.get('hp', 0):.0f} %).")
                        else:
                            remote.notify('Pas de touche de soin configuree.')
                    elif low == 'stats':
                        _log('DIRECTIVE : bilan de session demande')
                        remote.notify('Bilan de session :\n' + '\n'.join(f'- {k} : {v}' for k, v in stats.items()))
                    elif low.startswith('courage '):
                        v_c = low.split(' ', 1)[1].strip()
                        if v_c in ('prudent', 'equilibre', 'temeraire'):
                            CFG.courage = v_c
                            CFG.courage_t = {'prudent': (5, 4, 5, 80, 25), 'equilibre': (6, 5, 6, 60, 25), 'temeraire': (8, 7, 8, 40, 35)}[v_c]
                            _log(f'DIRECTIVE : courage regle sur {v_c}')
                            remote.notify(f'V est desormais {v_c}.')
                        else:
                            remote.notify(f'Courage inconnu ({v_c}) : prudent, equilibre ou temeraire.')
                    elif low.startswith('style '):
                        v_s = low.split(' ', 1)[1].strip()
                        if v_s in ('melee', 'mixte', 'distance'):
                            CFG.style = v_s
                            _log(f'DIRECTIVE : style regle sur {v_s}')
                            remote.notify(f'V se bat desormais en style {v_s}.')
                        else:
                            remote.notify(f'Style inconnu ({v_s}) : melee, mixte ou distance.')
                    elif low.startswith('aggro '):
                        v_a = low.split(' ', 1)[1].strip()
                        if v_a in ('defensif', 'normal', 'chasseur'):
                            CFG.aggro = v_a
                            _log(f'DIRECTIVE : agressivite reglee sur {v_a}')
                            remote.notify(f'V est desormais {v_a}.')
                        else:
                            remote.notify(f'Agressivite inconnue ({v_a}) : defensif, normal ou chasseur.')
                    else:
                        _log(f'DIRECTIVE non reconnue : « {dtv} »')
                        remote.notify('Directive non reconnue. Essaie : stop, pause, reprendre, attaque, objectif, marchand, charcudoc, explore, changer_quete, va_a <lieu>, status, photo, niveau, soigne, stats, courage/style/aggro <valeur>.')
                    plan.last_t = -99.0; time.sleep(0.3); continue

                # 0a2. BREACH PROTOCOL ouvert (terminal de piratage / point d acces) : le jeu est en pause, le mod relaie
                # l etat par onDraw (paused=true). V lit la grille et les sequences, calcule la solution et clique.
                br = st.get('breach') or {}
                if int(br.get('state') or 0) == 1 and (st.get('paused') or br.get('ctrl')):
                    if breach_t is None or time.perf_counter() - breach_t > 45.0:
                        breach_t = time.perf_counter(); kbm.release_all()
                        stats['breach'] = stats.get('breach', 0) + 1
                        _log('BREACH PROTOCOL ouvert : V resout la grille')
                        rb = breach.run(stop=stop, log=_log)
                        if rb.get('ok'):
                            stats['breach_ok'] = stats.get('breach_ok', 0) + 1
                            _log(f"breach : REUSSI ({rb.get('clics')} selections, {rb.get('seconds', 0):.0f} s)")
                        else:
                            _log(f"breach : {rb.get('reason') or ('etat ' + str(rb.get('state')))} -> on quitte le terminal")
                            s_b = motion.read_state() or {}
                            if int((s_b.get('breach') or {}).get('state') or 0) == 1:
                                kbm.tap('ESC', 0.09); time.sleep(1.0)
                    time.sleep(0.5); continue
                elif not br:
                    breach_t = None

                # 0a3. DANSE SENSORIELLE (braindance) : V prend l editeur en main (indices, couches, timeline, sortie)
                bdst = st.get('bd') or {}
                if bdst.get('active') or bdst.get('rew'):
                    # 20/09 : run() pouvait echouer a sortir (bd toujours active) sans jamais etre rappelee -- bd_t
                    # restait fixe pour de bon. On retente (avec un delai) tant que la danse est encore active.
                    if bd_t is None or (not bd_ok and time.perf_counter() - bd_t > 15.0):
                        if bd_t is None:
                            bd_tries = 0
                        bd_t = time.perf_counter(); kbm.release_all()
                        stats['bd'] = stats.get('bd', 0) + 1
                        _log('DANSE SENSORIELLE : V prend l editeur en main')
                        rbd = braindance.run(stop=stop, log=_log)
                        bd_ok = bool(rbd.get('ok'))
                        _log(f"danse sensorielle : {rbd.get('reason')} ({rbd.get('scans', 0)} indice(s) scanne(s), {rbd.get('seconds', 0):.0f} s)")
                        stats['bd_scans'] = stats.get('bd_scans', 0) + int(rbd.get('scans') or 0)
                        if not bd_ok:
                            bd_tries += 1
                            if bd_tries == 3:
                                remote.notify(f"V semble coince dans une danse sensorielle depuis plusieurs tentatives ({rbd.get('reason')}).")
                    time.sleep(0.5); continue
                else:
                    bd_t, bd_ok, bd_tries = None, True, 0

                # 0a4. MENU OUVERT sans raison (ecran de marchand, inventaire, carte laisses ouverts) : le monde continue
                # mais la souris ne pilote plus la camera (« rotation initiale echouee » en boucle hier soir) -> Echap
                # 0a3b. MORT (vie a 0) : AVANT les menus, car l ecran de mort leve menu=true (16/09 21:28 : 11 min fige)
                hp_now = st.get('hp')
                if (hp_now is not None and hp_now <= 0.5) or st.get('dead'):      # NB : `hp or 100` transformait 0 en 100 -> boucle de mort
                    dead_since = dead_since or time.perf_counter()
                    if time.perf_counter() - dead_since > 4.0:  # 4 s a 0 sur un etat vivant (pas un chargement)
                        quests.mark_death(st['x'], st['y'])
                        _log('V EST MORT : lieu memorise (objectifs a < 80 m evites) ; rechargement de la derniere sauvegarde')
                        if _reload_last_save(_log, stop=stop):
                            stats['morts'] = stats.get('morts', 0) + 1
                            remote.notify(f"V est mort (mort n {stats['morts']} de la session) : rechargement de la derniere sauvegarde.")
                            if time.perf_counter() - last_death_t > 300.0:
                                consecutive_deaths = 0
                            consecutive_deaths += 1; last_death_t = time.perf_counter()
                            dead_since = None; plan.last_t = -99.0; path_failures = 0
                            if consecutive_deaths >= 3:
                                # meme combat perdu 3 fois de suite : on laisse tomber CETTE quete un moment (comme un objectif
                                # inaccessible), au lieu de foncer une 4e fois dans le meme groupe (19/09 : 5 morts avant de gagner)
                                consecutive_deaths = 0
                                s_d = motion.read_state() or st
                                q_d = s_d.get('quest') or {}
                                nxt = quests.switch(alt_target.get('hash') if alt_target else q_d.get('hash'), log=_log)
                                if nxt:
                                    _log(f"3 morts de suite au meme combat : V laisse tomber et fait autre chose (« {nxt.get('text')} »)")
                                    alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                                    stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                else:
                                    alt_target = None
                            else:
                                alt_target = None
                            time.sleep(3.0); continue
                        _log('rechargement impossible : arret'); break
                    time.sleep(0.3); continue
                dead_since = None
                if st.get('menu') and not (st.get('breach') or {}).get('state') == 1 and not (st.get('bd') or {}).get('active'):
                    if menu_t is None:
                        menu_t = time.perf_counter(); menu_esc = 0
                    elif time.perf_counter() - menu_t > 2.5 and menu_esc < 4:
                        menu_esc += 1; menu_t = time.perf_counter()
                        _log(f'menu ouvert (marchand / inventaire / carte) : Retour arriere puis Echap ({menu_esc}/4)')
                        kbm.release_all(); kbm.tap('BACKSPACE', 0.09); time.sleep(0.6)      # vues d appareil (longue-vue...) : Retour arriere
                        if (motion.read_state() or {}).get('menu'):
                            kbm.tap('ESC', 0.09)
                        if menu_esc >= 2:
                            # un editeur (apparence, personnalisation) repond a Echap par « quitter sans sauvegarder ? » :
                            # on confirme ; sur le menu pause la meme touche = « Reprendre »
                            time.sleep(0.8)
                            if (motion.read_state() or {}).get('menu'):
                                kbm.act('ui_confirm', 0.1); time.sleep(0.5)
                                if (motion.read_state() or {}).get('menu'):
                                    kbm.tap('ENTER', 0.08)
                        if st.get('scene'):
                            time.sleep(0.5); kbm.hold('S'); time.sleep(1.2); kbm.release('S')
                        s_ms = motion.read_state() or st
                        if s_ms.get('x') is not None:
                            if shop_stuck_pos is not None and time.perf_counter() - shop_stuck_t < 300.0 and math.hypot(s_ms['x'] - shop_stuck_pos[0], s_ms['y'] - shop_stuck_pos[1]) < 6.0:
                                shop_stuck_n += 1
                            else:
                                shop_stuck_n, shop_stuck_pos = 1, (s_ms['x'], s_ms['y'])
                            shop_stuck_t = time.perf_counter()
                            if shop_stuck_n >= 3:
                                # 20/09 : reculer (S) au meme endroit ne creait pas de distance reelle -- le stand
                                # rouvrait 1 s plus tard (V recule sans doute dans un mur, kiosque exigu) -> on
                                # se retourne et on COURT VERS L AVANT (deplacement libre, comme l ecart normal)
                                _log(f'  {shop_stuck_n}e ecart au meme endroit (menu) : V se retourne et court loin, vraiment cette fois')
                                nav.add_avoid(*shop_stuck_pos)
                                motion.turn_by(180.0, timeout=1.5, stop=stop)
                                kbm.hold('W'); kbm.act_hold('sprint'); time.sleep(6.0); kbm.act_release('sprint'); kbm.release('W')
                                shop_stuck_n = 0; alt_target = None; plan.last_t = -99.0
                    elif time.perf_counter() - menu_t > 30.0:
                        # jamais de boucle muette : on le dit et on recommence une serie (menu de mort, chargement, carte...)
                        _log(f"menu toujours ouvert 30 s apres 4 Echap (vie {st.get('hp')}, scene {st.get('scene')}) : nouvelle serie")
                        menu_t = time.perf_counter(); menu_esc = 0
                    time.sleep(0.4); continue
                menu_t = None
                # 0a5. SCENE sans choix de dialogue depuis 25 s (assis a un stand, conversation muette) : on en sort
                if st.get('scene') and not (st.get('dialog') or {}).get('choices') and not (st.get('bd') or {}).get('active'):
                    _hostile_near = _threat_near(st)          # meme regle que les trajets (police, autre niveau, intouchables exclus)
                    _moved = scene_pos is not None and st.get('x') is not None and math.hypot(st['x'] - scene_pos[0], st['y'] - scene_pos[1]) > 3.0
                    if scene_t is None or _moved or _hostile_near:
                        # V se deplace ou se bat : la « scene » ne le bloque pas (16/09 : chasse au cyberpsycho, 15 min
                        # d Echap / recul / saut toutes les 30 s en plein combat) -> on ne touche a rien
                        scene_t = time.perf_counter(); scene_pos = (st.get('x'), st.get('y'))
                    elif time.perf_counter() - scene_t > scene_wait:
                        scene_t = time.perf_counter(); scene_pos = (st.get('x'), st.get('y'))
                        _log(f'scene sans dialogue depuis {scene_wait:.0f} s, V immobile : il s en extrait (Retour arriere, Echap, recul, saut)')
                        scene_wait = min(300.0, scene_wait * 2)     # si ca ne marche pas, on insiste de moins en moins
                        # d abord RETOUR ARRIERE : c est la touche qui quitte les vues d appareil (longue-vue, cameras, scanner) ;
                        # Echap y ouvrirait le menu pause (Olivier, 17/09)
                        kbm.release_all(); kbm.tap('BACKSPACE', 0.09); time.sleep(0.8)
                        s_e = motion.read_state() or {}
                        if s_e.get('scene') and not (s_e.get('dialog') or {}).get('choices') and not s_e.get('menu'):
                            kbm.tap('ESC', 0.09); time.sleep(0.8)
                            s_e = motion.read_state() or {}
                        if s_e.get('menu'):
                            kbm.tap('ESC', 0.09); time.sleep(0.6)
                        kbm.hold('S'); time.sleep(1.5); kbm.release('S')
                        kbm.act('jump', 0.1); time.sleep(0.8)
                        stats['scenes_quittees'] = stats.get('scenes_quittees', 0) + 1
                        continue
                else:
                    scene_t, scene_pos, scene_wait = None, None, 25.0

                # 0b. etat FIGE (pause, menu, carte, chargement) : le mod ne tourne plus -> on ne touche a rien
                if st.get('seq') != frozen_seq:
                    frozen_seq, frozen_t = st.get('seq'), time.perf_counter()
                    if frozen_logged:
                        frozen_logged = False; _log('etat a nouveau vivant : reprise')
                elif time.perf_counter() - frozen_t > 2.0 and (st.get('hp') is None or st.get('hp') > 0.5):
                    kbm.release_all()
                    if not frozen_logged:
                        frozen_logged = True; frozen_esc = 0; frozen_notified = False; _log('etat fige (pause, menu ou chargement) : attente')
                    # un menu ouvert par accident (carte des voyages rapides, inventaire...) fige le jeu : apres 8 s, Echap,
                    # puis toutes les 20 s, 3 fois au plus (un chargement, lui, se termine tout seul)
                    if time.perf_counter() - frozen_t > 8.0 + 20.0 * frozen_esc and frozen_esc < 3:
                        frozen_esc += 1
                        _log(f'etat fige depuis {time.perf_counter() - frozen_t:.0f} s : Echap pour fermer un eventuel menu ({frozen_esc}/3)')
                        kbm.tap('ESC', 0.09)
                    elif frozen_esc >= 3 and time.perf_counter() - frozen_t > 90.0:
                        # 3 series d Echap sans effet : pas un simple menu -- on ne reste plus jamais inactif pour de bon
                        # (20/09 : le mod pouvait rester silencieusement bloque le reste de la session)
                        if not frozen_notified and time.perf_counter() - frozen_t > 300.0:
                            frozen_notified = True
                            _log(f"etat fige depuis {int(time.perf_counter() - frozen_t)} s malgre plusieurs series d Echap : tentative de rechargement de la derniere sauvegarde")
                            remote.notify(f"V semble bloque (etat fige depuis {int(time.perf_counter() - frozen_t)} s) : tentative de rechargement.")
                            if _reload_last_save(_log, stop=stop):
                                frozen_seq, frozen_t, frozen_logged, frozen_esc, frozen_notified = st.get('seq'), time.perf_counter(), False, 0, False
                                time.sleep(3.0); continue
                        else:
                            frozen_esc = 0   # nouvelle serie d Echap (au cas ou un nouveau menu serait apparu depuis)
                    time.sleep(0.5); continue

                # 0c. TELEPHONE : un appel entrant -> on repond (touche telephone maintenue), la conversation suit via le dialogue
                ph = st.get('phone') or {}
                if CFG.features.get('phone', True) and ph.get('incoming') and time.perf_counter() - last_phone_t > 8.0 and not st.get('combat'):
                    last_phone_t = time.perf_counter()
                    _log(f"TELEPHONE : appel entrant de « {ph.get('contact') or '?'} » -> V repond")
                    kbm.act('phone', 0.9)
                    stats['appels'] = stats.get('appels', 0) + 1
                    time.sleep(1.5); continue

                # 0d. SMS : lecture et reponse (periodique, ou des qu un message arrive), hors combat
                if not st.get('combat') and CFG.features.get('sms', True):
                    try:
                        n_sms = sms.check_and_reply(st, log=_log)
                        if n_sms:
                            stats['sms'] = stats.get('sms', 0) + n_sms
                    except Exception as _e:
                        _log(f'  [sms] erreur : {_e}')

                # 0e. NAGE : dans l eau, V ne plonge pas (pas de touche accroupi), remonte des que l oxygene baisse,
                #     et nage vers l objectif / la rive la plus proche ; pas de combat ni de loot dans l eau
                if st.get('swim'):
                    oxy = st.get('oxygen')
                    if oxy is not None and oxy < 70:
                        kbm.act('jump', 0.4); time.sleep(0.3)                 # remonter a la surface
                        if not swim_logged:
                            _log(f'NAGE : oxygene {oxy:.0f} %, V remonte a la surface')
                    if not swim_logged:
                        swim_logged = True; _log('NAGE : V est dans l eau, il nage vers l objectif (ni plongee, ni combat, ni loot)')
                    qs = st.get('quest') or {}
                    if qs.get('mx') is not None:
                        motion.walk_to(qs['mx'], qs['my'], timeout=8.0, stop=stop, sprint=False)
                    else:
                        kbm.hold('W'); time.sleep(3.0); kbm.release('W')
                    continue
                if swim_logged:
                    swim_logged = False; _log('NAGE : V est sorti de l eau')

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

                q = st.get('quest') or {}
                # FOCUS : la quete suivie (assignee par le joueur) passe avant tout le reste tant qu elle a un marqueur
                focus = CFG.focus_tracked and bool(q.get('hasMappin')) and alt_target is None
                if q.get('lvl') and st.get('level') and q['lvl'] > st['level'] + 3 and alt_target is None and not CFG.focus_tracked \
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
                    if dialog.is_shop_hub(d):
                        # ecran de commerce : inutile (ventes/achats par script) et il revient tant qu on regarde le marchand
                        if time.perf_counter() - shop_back_t > 6.0:
                            shop_back_t = time.perf_counter()
                            _log(f'ecran de commerce « {d.get("title")} » : on s ecarte du marchand')
                            kbm.tap('ESC', 0.09); time.sleep(0.5)
                            kbm.hold('S'); time.sleep(1.5); kbm.release('S')
                            motion.turn_by(150.0, timeout=1.5, stop=stop)
                            kbm.hold('W'); time.sleep(1.2); kbm.release('W')
                            s_ds = motion.read_state() or st
                            if s_ds.get('x') is not None:
                                if shop_stuck_pos is not None and time.perf_counter() - shop_stuck_t < 300.0 and math.hypot(s_ds['x'] - shop_stuck_pos[0], s_ds['y'] - shop_stuck_pos[1]) < 6.0:
                                    shop_stuck_n += 1
                                else:
                                    shop_stuck_n, shop_stuck_pos = 1, (s_ds['x'], s_ds['y'])
                                shop_stuck_t = time.perf_counter()
                                if shop_stuck_n >= 3:
                                    # 20/09 : reculer (S) au meme endroit ne creait pas de distance reelle -- le stand
                                    # rouvrait 1 s plus tard (V recule sans doute dans un mur, kiosque exigu) -> on
                                    # se retourne et on COURT VERS L AVANT (deplacement libre, comme l ecart normal)
                                    _log(f'  {shop_stuck_n}e ecart au meme endroit (commerce) : V se retourne et court loin, vraiment cette fois')
                                    nav.add_avoid(*shop_stuck_pos)
                                    motion.turn_by(180.0, timeout=1.5, stop=stop)
                                    kbm.hold('W'); kbm.act_hold('sprint'); time.sleep(6.0); kbm.act_release('sprint'); kbm.release('W')
                                    shop_stuck_n = 0; alt_target = None; plan.last_t = -99.0
                        time.sleep(0.5); continue
                    same_hub = same_hub + 1 if sig == last_hub_sig else 1
                    last_hub_sig = sig
                    if same_hub > 6:
                        _log(f'BLOQUE : le hub "{d.get("title")}" revient sans cesse ({same_hub}x) ; '
                             f'choix grises = {[c for c, g in zip(d["choices"], d.get("inactive") or []) if g]}.')
                        # sortir du dialogue : choix de sortie s il existe, sinon Echap
                        quit_idx = next((i for i, c in enumerate(d['choices'])
                                         if any(w in c.lower() for w in dialog.QUIT_WORDS)), None)
                        dialog._shop_hubs[sig] = time.perf_counter()          # on n y repondra plus pendant 10 min
                        if quit_idx is not None:
                            dialog.select_index(quit_idx); dialog.confirm()
                        else:
                            kbm.tap('ESC', 0.09)
                        time.sleep(1.0)
                        kbm.hold('S'); time.sleep(1.5); kbm.release('S')
                        motion.turn_by(150.0, timeout=1.5, stop=stop)
                        time.sleep(0.5)
                        nxt = quests.switch(q.get('hash'), log=_log)
                        if nxt:
                            alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                            stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                            same_hub, last_hub_sig = 0, None
                            time.sleep(1.0); continue
                        nxe = explore.pick(log=_log)
                        if nxe:
                            alt_target = {'x': nxe['x'], 'y': nxe['y'], 'text': nxe.get('text'), 'hash': nxe.get('hash'), 't0': time.perf_counter()}
                            stats['explorations'] = stats.get('explorations', 0) + 1
                            same_hub, last_hub_sig = 0, None
                            time.sleep(1.0); continue
                        _log('aucune autre quete accessible ni point d exploration connu : arret.')
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
                    remote.notify(f"Combat termine : {r.get('coups', 0)} coup(s), {r.get('tirs', 0)} tir(s), {r.get('quickhacks', 0)} hack(s), {r.get('seconds', 0):.0f} s" + (' -- V est mort' if r.get('mort') else ''))
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
                        if lr2.get('bought'):
                            remote.notify(f"V a monte en niveau : {lr2['bought']} point(s) d attribut depenses (niveau {st.get('level', '?')}).")
                        if (lr2.get('perk_points') or 0) > 0:
                            pr = nav._wait(nav._send({'cmd': 'perks'}), timeout=8.0)
                            if pr and pr.get('ok'):
                                _log(f"perks : {len(pr.get('achetes') or [])} achete(s) {pr.get('achetes')}, restants {pr.get('restants')}")
                                if pr.get('achetes'):
                                    remote.notify(f"V a achete {len(pr['achetes'])} perk(s) : {', '.join(pr['achetes'])}.")
                            else:
                                _log(f"perks : echec ({(pr or {}).get('reason', 'mod muet')})")
                    else:
                        _log(f"niveau : echec ({(lr2 or {}).get('reason', 'mod muet')})")

                # 3a-ter. COURSES : assez d objets a vendre OU soins bas (craft insuffisant) -> marchand a portee
                need_heals = inventory.HEALS < 2
                # PRAGMATIQUE (Olivier, 17/09) : on ne va chez le marchand que l inventaire PLEIN ou presque (>= 85 % du poids
                # portable), ou pour des soins (survie). Plus de detour pour 8 babioles ou 1000 eddies.
                inv_full = inventory.WEIGHT >= inventory.FULL_RATIO * inventory.CARRY and len(inventory.SELLABLE) > 0
                rich_sale = inv_full
                if CFG.features.get('sell', True) and not _threat_near(st) and (inv_full or need_heals) and time.perf_counter() - last_sell_t > 600.0 \
                        and (not focus or need_heals):    # en focus, seules les courses de soins passent avant la quete
                    last_sell_t = time.perf_counter()
                    vendor.MAX_VENDOR_M = 5000.0                       # course dediee : le marchand connu le plus proche fera l affaire,
                                                                       # sell_trip() sait deja s y rendre a toute distance (conduite/voyage rapide)
                    vend = vendor.pick_vendor(vendor.list_vendors())
                    if vend:
                        _log(f"courses : poids {inventory.WEIGHT:.0f}/{inventory.CARRY:.0f}, {len(inventory.SELLABLE)} objets a vendre, soins {inventory.HEALS}, marchand a {vend['dist']:.0f} m")
                        tr = vendor.sell_trip(vend, stop=stop, log=_log)
                        if not tr.get('ok'):
                            _log(f"courses : marchand non atteint ({tr.get('reason')}) : ecarte 15 min, on essaiera un autre")
                            vendor.mark_failed(vend, log=_log); vendor_fail_streak += 1
                            if vendor_fail_streak >= 3:                 # maillage local impraticable : on suspend les courses 30 min
                                _log('courses : 3 marchands injoignables d affilee, courses suspendues 30 min')
                                last_sell_t = time.perf_counter() + 1200.0; vendor_fail_streak = 0
                            else:
                                last_sell_t = time.perf_counter() - 480.0   # nouvel essai (autre marchand) dans 2 min
                        if tr.get('ok'):
                            vendor_fail_streak = 0
                            had_sellable = len(inventory.SELLABLE) > 0
                            sr = inventory.sell_all(log=_log)
                            _log(f"vente : {sr.get('vendus', 0)} objet(s) pour {sr.get('eddies', 0)} eddies")
                            stats['ventes'] = stats.get('ventes', 0) + sr.get('vendus', 0)
                            br = vendor.buy_heals(inventory.HEALS, log=_log)
                            sp = vendor.buy_supplies((inventory.fetch() or {}).get('items') or [], log=_log)
                            if sp.get('achats'):
                                _log(f"achat : {', '.join(sp['achats'])}")
                            rp = vendor.buy_recipes((inventory.fetch() or {}).get('items') or [], log=_log)
                            if rp.get('appris'):
                                stats['plans'] = stats.get('plans', 0) + len(rp['appris'])
                            if br.get('achetes'):
                                inventory.HEALS += br['achetes']
                                _log(f"achat : {br['achetes']} soin(s) pour {br.get('eddies', 0)} eddies")
                                stats['achats'] = stats.get('achats', 0) + br['achetes']
                            if sr.get('ok') and br.get('ok') and had_sellable and not sr.get('vendus') and not sr.get('echecs') and not br.get('achetes') and not sp.get('achats') and not rp.get('appris'):
                                vendor.mark_useless(vend, 'rien vendu ni achete', days=1.0, log=_log)
                        continue

                # 3a-quater. CHARCUDOC : assez d eddies -> V s optimise lui-meme (meilleur cyberware abordable, pose par script)
                if CFG.features.get('ripperdoc', True) and inventory.MONEY >= 6000 and time.perf_counter() - last_ripper_t > 1800.0 and not st.get('combat') and not _threat_near(st) and not focus:
                    last_ripper_t = time.perf_counter()
                    vendor.MAX_VENDOR_M = 5000.0
                    rip = vendor.pick_vendor(vendor.list_vendors(), prefer='ripper')
                    if rip and 'ripper' in (rip.get('variant') or '').lower():
                        _log(f"charcudoc : {inventory.MONEY} eddies, « {rip.get('variant')} » a {rip['dist']:.0f} m : V va s optimiser")
                        tr = vendor.sell_trip(rip, stop=stop, log=_log)
                        if tr.get('ok'):
                            rr = vendor.ripperdoc_shop(log=_log)
                            _log(f"charcudoc : {rr.get('poses', 0)} implant(s) pose(s) pour {rr.get('eddies', 0)} eddies ({rr.get('reason') or 'ok'})")
                            stats['implants'] = stats.get('implants', 0) + rr.get('poses', 0)
                            if rr.get('useless'):
                                # rien a poser ici (pas de stock, ne parle pas, stock illisible) : on n y revient pas de sitot
                                vendor.mark_useless(rip, rr['useless'], days=7.0, log=_log)
                        else:
                            _log(f"charcudoc : non atteint ({tr.get('reason')})"); vendor.mark_failed(rip, log=_log)
                        continue

                # 3a-quinquies. APPARENCE : une fois par mois, si un appartement de V est proche, passage au miroir
                if CFG.features.get('appearance', True) and appearance.due() and not st.get('combat') and inventory.MONEY > 0 and not focus:
                    apt = appearance.nearest_apartment(_log)
                    if apt:
                        ra = appearance.visit_mirror(stop=stop, log=_log)
                        _log(f"apparence : {'nouveau look' if ra.get('ok') else ra.get('reason')}")
                        continue
                    appearance._last['t'] = time.time() - appearance.PERIOD_S + 900.0   # pas d appartement : on reverra dans 15 min (sans l enregistrer)

                # 3b. soin hors combat si la vie est basse
                if (st.get('hp') or 100) < 40 and time.perf_counter() - last_heal_t > 8.0:
                    kbm.act('consumable', 0.1); last_heal_t = time.perf_counter()
                    _log(f"soin hors combat (vie {st.get('hp'):.0f} %)")

                # 4. DECISION de V (modele local) : objectif / parler / attaquer / changer_quete / attendre
                if alt_target is not None:
                    dist = math.hypot(st['x'] - alt_target['x'], st['y'] - alt_target['y'])
                    if dist < ARRIVE_M or time.perf_counter() - alt_target['t0'] > 900:
                        if dist < ARRIVE_M and not st.get('combat') and not ((st.get('interact') or {}).get('choices')):
                            if _approach_machine(alt_target['x'], alt_target['y'], dist, _log, stop):   # console / donneur de quete
                                stats['interactions'] += 1
                                last_close_t = time.perf_counter()
                                time.sleep(1.5)
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

                if action == 'secourir' and not CFG.features.get('rescue', True):
                    action = 'objectif'
                if action == 'secourir':
                    aggr = planner.aggressors(st)
                    crimes = st.get('crimes') or []
                    tgt = aggr[0] if aggr else (crimes[0] if crimes else None)
                    if tgt:
                        for _a in aggr + [c for c in crimes if c.get('x') is not None]:
                            rescued[_rescue_cell(_a)] = time.perf_counter()
                        _log(f"V JOUE LES SAUVEURS : {len(aggr)} agresseur(s), a {tgt['d']:.0f} m")
                        plan.note('a secouru quelqu un')
                        if tgt['d'] > 12:
                            nav.goto(lambda t=tgt: nav.request_path_to(t['x'], t['y'], t.get('z')), arrive_m=10.0, max_legs=3,
                                     timeout=60.0, stop=stop, log=_log, interrupt=_threat_near)
                        s2 = motion.read_state() or st
                        aggr2 = planner.aggressors(s2)
                        t_look = time.perf_counter()
                        while not aggr2 and time.perf_counter() - t_look < 8.0 and not (stop is not None and stop.is_set()):
                            # personne en vue : tour d horizon (l export ne liste que les PNJ cibles) et on attend un peu
                            motion.turn_by(90.0, timeout=1.2, stop=stop); time.sleep(0.6)
                            s2 = motion.read_state() or s2
                            aggr2 = planner.aggressors(s2)
                        if aggr2:
                            a = aggr2[0]
                            engaged = combat.engage({'x': a['x'], 'y': a['y'], 'd': a['d'], 'sy': a.get('sy')}, stop=stop, log=_log, rescue=True)
                            s3 = motion.read_state() or {}
                            if not engaged and not s3.get('combat'):
                                # l agresseur n a pas reagi (ou V n est pas arrive) : second assaut sur le plus proche encore visible
                                aggr3 = planner.aggressors(s3)
                                if aggr3:
                                    a3 = aggr3[0]
                                    _log(f"  agresseur sans reaction a {a3['d']:.0f} m : second assaut")
                                    engaged = combat.engage({'x': a3['x'], 'y': a3['y'], 'd': a3['d'], 'sy': a3.get('sy')}, stop=stop, log=_log, rescue=True, max_s=15.0)
                                    for _a in aggr3:
                                        rescued[_rescue_cell(_a)] = time.perf_counter()
                            s3 = motion.read_state() or {}
                            if not engaged and not s3.get('combat'):
                                _log('  les agresseurs ne reagissent pas : V passe son chemin (zone ignoree 5 min)')
                                plan.last_t = -99.0
                                continue
                            r = combat.fight(stop=stop, log=_log)
                            stats['combats'] = stats.get('combats', 0) + 1
                            stats['sauvetages'] = stats.get('sauvetages', 0) + 1
                            remote.notify(f"V a joue les sauveurs : combat termine, {r.get('coups', 0)} coup(s), {r.get('seconds', 0):.0f} s" + (' -- V est mort' if r.get('mort') else ''))
                            if not r.get('mort'):
                                lr = combat.loot_around(stop=stop, log=_log, seen=looted)
                                stats['loot'] = stats.get('loot', 0) + lr.get('ramasses', 0)
                        else:
                            _log('  agression terminee ou hors de vue a l arrivee (8 s de tour d horizon)')
                        plan.last_t = -99.0
                        continue
                    action = 'objectif'

                # 4b. TERMINAL : point d acces non pirate a portee -> V s y connecte (Breach Protocol : eddies, composants) ;
                #     le mini-jeu ouvert est resolu par le traitement 0a2 a l iteration suivante
                if CFG.features.get('terminals', True) and action in ('objectif', 'aborder', 'attendre') and not st.get('combat') \
                        and not _threat_near(st) and time.perf_counter() - last_ap_scan_t > 20.0:
                    last_ap_scan_t = time.perf_counter()
                    ap = terminals.pick(log=_log)
                    if ap:
                        _log(f"terminal : point d acces « {ap.get('name')} » a {ap['d']:.0f} m : V va s y connecter")
                        r_ap = terminals.jack_in(ap, stop=stop, log=_log)
                        if r_ap == 'minigame':
                            stats['terminaux'] = stats.get('terminaux', 0) + 1
                            breach_t = None                          # pas de delai avant la resolution
                        plan.last_t = -99.0
                        continue
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
                    hostiles = [e for e in hostiles if not _muted(e)]
                    if hostiles:
                        _log(f"V engage le combat : {len(hostiles)} hostile(s), le plus proche a {hostiles[0]['d']:.0f} m")
                        plan.note('a engage un combat')
                        engaged = combat.engage(hostiles[0], stop=stop, log=_log)
                        s_now = motion.read_state() or {}
                        d0, d1 = combat.LAST_ENGAGE.get('d0') or 0.0, combat.LAST_ENGAGE.get('d1') or 0.0
                        if not engaged and not s_now.get('combat') and d0 - d1 > 6.0 and d1 > 4.0:
                            _log(f"  engagement inacheve ({d0:.0f} -> {d1:.0f} m) : on y retourne")
                            plan.last_t = -99.0; plan.action = 'attaquer'
                            continue
                        if not engaged and not s_now.get('combat'):
                            _mute_fail(hostiles[0])
                            _log('  cible sans reaction : ignoree 3 min')
                            plan.last_t = -99.0; plan.action = 'objectif'
                            continue
                        r = combat.fight(stop=stop, log=_log)
                        stats['combats'] = stats.get('combats', 0) + 1
                        if r.get('sterile'):
                            # intouchables (vitre, autre niveau) : ignores 3 min meme a 1 m (2 echecs d un coup)
                            for e in (motion.read_state() or {}).get('enemies') or []:
                                if not e.get('dead'):
                                    _mute_fail(e, n=2)
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
                # 5a. ARRIVE au marqueur (< 7 m) sans invite : machines a quetes (bornes, terminaux, distributeurs) -> on s approche a 1 m
                if dist is not None and dist < ARRIVE_M + 4.0 and not (inter and inter.get('choices'))                 and time.perf_counter() - last_close_t > 30.0 and not st.get('combat'):
                    last_close_t = time.perf_counter()
                    tx, ty = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                    if tx is not None:
                        if _approach_machine(tx, ty, dist, _log, stop):
                            stats['interactions'] += 1
                        continue
                if dist is not None and dist > 500.0 and kbm.ACTIONS.get('autodrive') and CFG.features.get('driving', True) \
                        and time.perf_counter() - last_drive_t > 240.0:
                    last_drive_t = time.perf_counter()
                    _log(f'objectif a {dist:.0f} m : V prend la voiture (autodrive)')
                    plan.note('a pris la voiture')
                    # aussi vers une cible alternative (19/09 : 2 200 m a pied parce que la quete etait « alternative »)
                    q0 = {'mx': alt_target['x'], 'my': alt_target['y']} if alt_target is not None else (st.get('quest') or {})
                    r = driving.drive_to(q0.get('mx'), q0.get('my'), stop=stop, log=_log)
                    stats['conduites'] = stats.get('conduites', 0) + 1
                    _log(f"conduite : {'arrive' if r.get('ok') else r.get('reason')}")
                    if not r.get('ok') and r.get('reason') == 'embarquement echoue':
                        last_drive_t = time.perf_counter() + 360.0      # ici la moto ne vient pas / ne se monte pas : pas avant 10 min
                        if dist > 1500.0 and time.perf_counter() - last_ft_t > 600.0 and CFG.features.get('fasttravel', True):
                            last_ft_t = time.perf_counter()
                            ft = nav.fast_travel_to(q0.get('mx'), q0.get('my'), log=_log)
                            _log(f"voyage rapide : {('arrive a ' + str(ft.get('point'))) if ft.get('ok') else ft.get('reason')}")
                            if ft.get('ok'):
                                stats['voyages'] = stats.get('voyages', 0) + 1
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
                    s_h = motion.read_state() or st
                    here = (s_h['x'], s_h['y'])
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
                            s_b = motion.read_state() or st
                            kbm.tap('ESC', 0.09); time.sleep(0.8)
                            s_e = motion.read_state() or {}
                            if s_e.get('seq') == s_b.get('seq'):            # menu pause ouvert par Echap -> on le referme
                                kbm.tap('ESC', 0.09); time.sleep(0.6)
                            kbm.hold('S'); time.sleep(1.5); kbm.release('S')
                            kbm.act('jump', 0.1); time.sleep(0.8)
                            rot_failures = 0
                            continue
                    else:
                        rot_failures = 0
                    if r.get('ok') or r.get('real_legs', 0) > 0:      # 'legs' seul comptait un troncon bloque des le 1er pas comme un progres
                        path_failures = 0; straight_tried = False
                    else:
                        path_failures += 1
                        _log(f"  trajet : {r.get('reason')} (echec {path_failures}/5)")
                        if path_failures >= 5 and dist is not None and not straight_tried and escape_tries < 3:      # meme pour une cible lointaine : d abord SORTIR d ici
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
                                path_failures = 0; focus_insist_n = 0
                                continue
                            # toujours coince : V cherche une SORTIE (portes, invites, sondes dans 8 directions)
                            here_e = (s2['x'], s2['y'])
                            if escape_pos is not None and math.hypot(here_e[0] - escape_pos[0], here_e[1] - escape_pos[1]) < 4.0:
                                escape_tries += 1                        # meme point qu au dernier essai : pas de vrai progres
                            else:
                                escape_tries, escape_pos = 1, here_e
                            _log(f'ilot ferme : recherche d une sortie (portes, invites, sondes) [{escape_tries}/3]')
                            re = escape.escape((tx, ty), stop=stop, log=_log)
                            _log(f"  sortie : {'trouvee par ' + re.get('moyen', '?') if re.get('ok') else 'introuvable (' + re.get('moyen', '?') + ')'}")
                            stats['sorties'] = stats.get('sorties', 0) + (1 if re.get('ok') else 0)
                            if re.get('ok'):
                                path_failures = 0
                            if escape_tries >= 3:
                                _log('  meme point de blocage 3 fois de suite : V arrete d essayer cette porte, on cherche une autre solution')
                                escape_tries, escape_pos = 0, None
                                if stuck_pos is not None and math.hypot(here_e[0] - stuck_pos[0], here_e[1] - stuck_pos[1]) < 60.0:
                                    stuck_cycles += 1
                                else:
                                    stuck_cycles, stuck_pos = 1, here_e
                                if stuck_cycles >= 2:
                                    # 19-20/09 : un cycle d evasion epuise laissait juste path_failures=5 pour forcer les branches
                                    # suivantes, mais le troncon partiel qui suit presque toujours remet path_failures a 0 avant
                                    # qu elles ne soient evaluees (9 min bloque au meme secteur, en boucle) -> on escalade ICI, tout de suite.
                                    _log(f'  bloque dans ce secteur depuis {stuck_cycles} cycles d evasion epuises : voyage rapide direct')
                                    remote.notify('V etait bloque dans un secteur depuis plusieurs minutes : depart en voyage rapide ailleurs.')
                                    stuck_cycles = 0
                                    tx2, ty2 = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                                    rft2 = nav.fast_travel_to(tx2, ty2, log=_log, min_gain_m=50.0)
                                    if rft2.get('ok'):
                                        stats['voyages'] = stats.get('voyages', 0) + 1; path_failures = 0; straight_tried = False
                                        continue
                                    nxts = quests.switch(alt_target.get('hash') if alt_target else q.get('hash'), log=_log)
                                    if nxts:
                                        alt_target = {'x': nxts['x'], 'y': nxts['y'], 'text': nxts.get('text'), 'hash': nxts.get('hash'), 't0': time.perf_counter()}
                                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                        path_failures = 0; straight_tried = False
                                        time.sleep(1.0); continue
                                    nxes = explore.pick(log=_log)
                                    if nxes:
                                        alt_target = {'x': nxes['x'], 'y': nxes['y'], 'text': nxes.get('text'), 'hash': nxes.get('hash'), 't0': time.perf_counter()}
                                        stats['explorations'] = stats.get('explorations', 0) + 1
                                        path_failures = 0; straight_tried = False
                                        time.sleep(1.0); continue
                                    _log('secteur bloque, aucun voyage/quete/exploration possible : arret.')
                                    break
                                path_failures = 5; straight_tried = True   # force le passage aux branches suivantes (insister / voyage rapide / quete)
                                continue
                            continue
                        if path_failures >= 5 and focus and focus_insist_n >= 3:
                            # 3 fois de suite sans progres (17/09 : 40 min coince pres de « Parler a Johnny ») : V fait autre chose un moment
                            focus_insist_n = 0; path_failures = 0; straight_tried = False
                            nxt = quests.switch(q.get('hash'), log=_log)
                            if nxt:
                                _log(f"quete assignee inaccessible 3 fois de suite : V fait autre chose (« {nxt.get('text')} ») avant d y revenir")
                                alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                continue
                        if path_failures >= 5 and focus:
                            focus_insist_n += 1
                            _log(f'objectif inaccessible pour l instant, mais c est la quete assignee : on insiste ({focus_insist_n}/3 ; pause 45 s, puis vehicule / voyage rapide)')
                            path_failures = 0; straight_tried = False
                            last_drive_t = -999.0; last_ft_t = -999.0          # debloque les tentatives de vehicule et de voyage rapide
                            t_w = time.perf_counter()
                            while time.perf_counter() - t_w < 45.0 and not (stop is not None and stop.is_set()):
                                time.sleep(1.0)
                            continue
                        if path_failures >= 5 and CFG.features.get('fasttravel', True) and time.perf_counter() - last_ft_t > 120.0:
                            # aucun chemin d ici et la sortie a echoue : le voyage rapide (par requete au systeme du jeu) peut nous sortir
                            last_ft_t = time.perf_counter()
                            tx, ty = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                            _log('aucun chemin d ici : tentative de voyage rapide vers l objectif')
                            rft = nav.fast_travel_to(tx, ty, log=_log, min_gain_m=100.0)
                            if rft.get('ok'):
                                stats['voyages'] = stats.get('voyages', 0) + 1; path_failures = 0; straight_tried = False
                                continue
                            _log(f"  voyage rapide : {rft.get('reason')}")
                        if path_failures >= 5:
                            _log('objectif inaccessible a pied depuis ici : changement de quete')
                            path_failures = 0
                            straight_tried = False
                            escape_tries, escape_pos = 0, None
                            nxt = quests.switch(alt_target.get('hash') if alt_target else q.get('hash'), log=_log)
                            if nxt:
                                alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                time.sleep(1.0); continue
                            nxe = explore.pick(log=_log)
                            if nxe:
                                alt_target = {'x': nxe['x'], 'y': nxe['y'], 'text': nxe.get('text'), 'hash': nxe.get('hash'), 't0': time.perf_counter()}
                                stats['explorations'] = stats.get('explorations', 0) + 1
                                time.sleep(1.0); continue
                            _log('aucune autre quete accessible ni point d exploration connu : arret.')
                            break
                        time.sleep(3.0)
                    continue

                # 6. rien a faire : l objectif n a pas de marqueur, ou on y est sans interaction
                stats['attentes'] += 1
                if no_mappin_since is None:
                    no_mappin_since = time.perf_counter()
                elif time.perf_counter() - no_mappin_since > 45.0:      # > 30 s (cooldown 5a) : V a eu au moins un essai d approche avant d abandonner
                    _log(f"objectif sans marqueur ou invite en retard depuis 45 s ({q.get('text')}) : V cherche une quete (donneur de quete de son niveau, ou marqueur)")
                    no_mappin_since = None
                    nxt = quests.switch(q.get('hash'), log=_log, prefer_givers=True)
                    if nxt:
                        alt_target = {'x': nxt['x'], 'y': nxt['y'], 'text': nxt.get('text'), 'hash': nxt.get('hash'), 't0': time.perf_counter()}
                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        time.sleep(1.0); continue
                    nxe = explore.pick(log=_log)
                    if nxe:
                        alt_target = {'x': nxe['x'], 'y': nxe['y'], 'text': nxe.get('text'), 'hash': nxe.get('hash'), 't0': time.perf_counter()}
                        stats['explorations'] = stats.get('explorations', 0) + 1
                        time.sleep(1.0); continue
                    _log('aucune autre quete accessible ni point d exploration connu : arret.')
                    break
                time.sleep(0.5)

            except Exception as _e:
                crashes += 1
                import traceback as _tb
                _log(f'ERREUR dans la boucle ({crashes}/5) : {_e!r}')
                _log(_tb.format_exc())
                kbm.release_all()
                if crashes >= 5:
                    _log('trop d erreurs : arret de la session'); break
                time.sleep(1.0)
    finally:
        kbm.release_all()
        try:
            dialog.unload_model()
            radio.turn_off(log=_log)
        except Exception as _e:
            _log(f'  [fin] nettoyage : {_e}')
        stats['seconds'] = time.perf_counter() - t0
        if money_start is not None:
            stats['eddies_gagnes'] = inventory.MONEY - money_start
        _log(f'=== fin : {stats} ===')
        remote.notify(f"Session terminee ({stats['seconds'] / 60:.0f} min) : {stats.get('combats', 0)} combat(s), "
                      f"{stats.get('morts', 0)} mort(s), {stats.get('loot', 0)} objet(s) loote(s), "
                      f"{stats.get('trajets', 0)} trajet(s), {stats.get('eddies_gagnes', 0)} eddies gagnes.")
    return stats
