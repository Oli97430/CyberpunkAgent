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

from . import achievements, appearance, braindance, breach, buffs, combat, dialog, driving, escape, explore, hud, narrator, relaunch, input_kbm as kbm, inventory, llm, motion, nav, navprog, planner, quests, radio, remote, sms, terminals, psycho, travel, vendor, youtube_chat

from .config import CFG
LOG_FILE = CFG.log_file                 # %APPDATA%/CyberpunkAgent/brain_log.txt
ARRIVE_M = 3.0

# mots-cles exacts de la boucle de directives (0a0) : tout ce qui ne matche pas passe par remote.classify()
_DIRECTIVE_KEYWORDS = frozenset({
    'stop', 'arret', 'arrete-toi', 'arrete toi', 'pause', 'reprendre', 'resume', 'continue',
    'attaque', 'attaquer', 'combat', 'objectif', 'quete', 'marchand', 'vendre', 'boutique',
    'charcudoc', 'ripperdoc', 'implant', 'explore', 'explorer', 'balade', 'changer_quete', 'autre_quete',
    'status', 'etat', 'etat?', 'photo', 'screenshot', 'capture', 'niveau', 'soigne', 'stats',
    'modeles', 'liste_modeles', 'modele_liste', 'hud', 'config', 'reglages', 'mission', 'missions', 'bilan', 'celebre',
    'vole', 'voiture', 'fete', 'loot', 'radio', 'cyberpsycho', 'etat',
})
_DIRECTIVE_PREFIXES = ('va_a ', 'va a ', 'courage ', 'style ', 'aggro ', 'modele ', 'mission_choix ')
_STOP_WORDS = ('stop', 'arret', 'arrete-toi', 'arrete toi')
_RESUME_WORDS = ('reprendre', 'resume', 'continue')


def _fold(t: str) -> str:
    """minuscules, sans accents ni ponctuation finale (« Change de quête ! » -> « change de quete »)."""
    import unicodedata
    t = ''.join(c for c in unicodedata.normalize('NFD', t.lower()) if unicodedata.category(c) != 'Mn')
    return ' '.join(t.replace('\u2019', ' ').replace("'", ' ').replace('-', ' ').strip(' .!?').split())


# 12/10 (journal 04:55 : « Change de quête » -> DIRECTIVE non reconnue) : formulations courantes, avant le modele local
_PHRASES = (
    (('change de quete', 'changer de quete', 'change quete', 'changer quete', 'change de mission', 'changer de mission',
      'autre quete', 'autre mission', 'change d objectif', 'changer d objectif', 'autre objectif', '=laisse tomber',
      'laisse tomber ta quete', 'laisse tomber la quete', 'change ta quete', 'abandonne ta quete', 'oublie ta quete',
      'lache ta quete', 'quitte ta quete', 'passe a autre chose', 'fais autre chose', 'quete suivante'), 'changer_quete'),
    (('reprends ta quete', 'reprend ta quete', 'continue ta quete', 'suis ta quete', 'retourne a ta quete',
      'retour a la quete', 'suis la quete'), 'objectif'),
    (('va explorer', 'explore la ville', 'balade toi', 'promene toi'), 'explore'),
    (('va vendre', 'va au marchand', 'fais tes courses', 'vends ton'), 'marchand'),
    (('soigne toi', 'heal'), 'soigne'),
    (('ou es tu', 'ou t es', 'tu fais quoi', 'que fais tu', 'statut'), 'status'),
)


def _understand(dtv: str) -> tuple[str, str]:
    """Directive -> (texte, commande) : mot-cle exact, sinon le modele local traduit la phrase libre vers la
    commande la plus proche (20/09, « trop basique »)."""
    low = dtv.lower().strip()
    f = _fold(dtv)
    if low.startswith(('courage ', 'style ', 'aggro ')):
        return f, f                                     # valeurs enumerees : « téméraire » = « temeraire »
    if low not in _DIRECTIVE_KEYWORDS and not low.startswith(_DIRECTIVE_PREFIXES):
        if f in _DIRECTIVE_KEYWORDS:
            return f, f
        for p in _DIRECTIVE_PREFIXES:
            if f.startswith(p) and p not in ('va a ',):
                # commande a parametre (« Modèle qwen3-vl:8b ») : seul le mot-cle est plie, pas le parametre
                raw = dtv.strip().split()
                c = p + ' '.join(raw[len(p.split()):]).lower()
                return (_fold(c) if p in ('courage ', 'style ', 'aggro ') else c), (_fold(c) if p in ('courage ', 'style ', 'aggro ') else c)
        neg = any((' ' + w + ' ') in (' ' + f + ' ') for w in ('ne', 'n', 'pas', 'sans', 'jamais', 'plus de', 'arrete de', 'arretes de'))
        hits = set()
        for words, cmd in _PHRASES:
            for w in words:
                if w.startswith('='):
                    if f == w[1:]:
                        hits.add(cmd)
                elif (' ' + w + ' ') in (' ' + f + ' '):
                    hits.add(cmd)
        if len(hits) == 1 and not neg:                  # sens unique, sans negation : pas besoin du modele
            cmd = hits.pop()
            _log(f'DIRECTIVE « {dtv} » comprise comme : {cmd}')
            return cmd, cmd
        if f.startswith('va a ') and not hits:
            c = 'va a ' + ' '.join(dtv.strip().split()[2:]).lower()   # lieu tel qu ecrit (accents : « Mégabuilding »)
            return c, c
        cl = remote.classify(dtv)
        if cl:
            _log(f'DIRECTIVE « {dtv} » comprise comme : {cl}')
            dtv, low = cl, cl.lower().strip()
    return dtv, low

# HUD : libelle du mode selon la decision du planificateur (le mod affiche lui-meme combat/conduite/danse/dialogue)
_HUD_MODES = {'objectif': 'EN MISSION', 'attaquer': 'ENGAGEMENT', 'eviter': 'REPLI TACTIQUE', 'secourir': 'INTERVENTION',
              'parler': 'INTERACTION', 'aborder': 'PRISE DE CONTACT', 'changer_quete': 'REAFFECTATION', 'attendre': 'VEILLE'}


def _hud_directive(st: dict, alt_target: dict | None, dist: float | None) -> str:
    if alt_target is not None:
        t = str(alt_target.get('text') or '')
        label = 'objectif secondaire (marqueur)' if (not t or t.endswith('Variant')) else t
    else:
        label = (st.get('quest') or {}).get('text') or 'aucune quete suivie'
    if dist is not None:
        label = f'{label} [{dist:.0f} m]'
    return hud.ascii_up(label)[:70]


def _hud_cfg() -> dict:
    """Config EFFECTIVE pour le HUD et le panneau CET : resume lisible + valeurs (le panneau y aligne les champs que
    l utilisateur n a pas touches -- sinon il montrait ses propres defauts). Jamais la cle API."""
    return {'cfg': hud.ascii_plain(CFG.summary()), 'model': _hud_model(),
            'temper': hud.ascii_up(f'{CFG.courage} / {CFG.style} / {CFG.aggro}'),
            'cfgv': {'provider': CFG.provider, 'model': CFG.model, 'openai_model': CFG.openai_model,
                     'anthropic_model': CFG.anthropic_model, 'courage': CFG.courage, 'style': CFG.style,
                     'aggro': CFG.aggro, 'minutes': CFG.minutes, 'features': dict(CFG.features)}}


def _hud_model() -> str:
    if CFG.provider == 'ollama':
        return hud.ascii_up(CFG.model)
    return hud.ascii_up(f"{CFG.provider} {CFG.openai_model if CFG.provider == 'openai' else CFG.anthropic_model}")


_LOG_STATE = {'file': LOG_FILE, 'err': None}


def _log(msg: str) -> None:
    """Journal : console + fichier. Jamais bloquant : une console qui refuse un caractere ou un fichier inaccessible
    ne doit pas tuer la session (16/09 : l exe lance depuis le panneau ne laissait AUCUN journal)."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        from . import narrator                     # 19/10 : V commente ses decisions en grand sur le HUD
        narrator.feed(str(msg).strip())
        from . import clips, features              # 19/10 : moments forts -> clip OBS ; fonctions vues en jeu
        clips.feed(str(msg).strip(), log=_log)
        features.feed(str(msg).strip())
    except Exception:
        pass
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


_AUTOTEST = {'alert_t': -1e9}     # derniere alerte « V ne bouge pas » (le processus survit aux sessions --loop)


def _busy_state(s: dict) -> bool:
    """Etat ou l immobilite de V est normale (dialogue, scene, vehicule, menu, chargement, mort, danse, breach)."""
    hp = s.get('hp')
    return bool((s.get('dialog') or {}).get('choices') or motion.game_lock(s) or s.get('vehicle') or s.get('menu')
                or s.get('ftLoading') or s.get('dead') or (isinstance(hp, (int, float)) and hp <= 0.5)
                or (s.get('bd') or {}).get('active') or (s.get('breach') or {}).get('state') == 1)


def _tid(alt_target, q) -> tuple:
    """Identite de la cible de navigation (12/10) : marqueur choisi par V ou objectif suivi -- pas sa cellule (un
    marqueur de PNJ bouge)."""
    if alt_target is not None:
        return ('alt', alt_target.get('hash'), str(alt_target.get('text') or ''))
    return ('q', (q or {}).get('hash'), str((q or {}).get('text') or ''))


def _celebrate() -> None:
    """Petite fete sur place (aucune arme : pas de tirs en l air en ville -> police / passants) : 2 sauts, un tour
    complet en deux demi-tours, accroupi-debout deux fois (le « salut » des joueurs)."""
    for _ in range(2):
        kbm.act('jump', 0.1); time.sleep(0.9)
    st = motion.read_state() or {}
    if st.get('yaw') is not None:
        motion.turn_by(180.0, timeout=2.0); motion.turn_by(180.0, timeout=2.0)
    for _ in range(2):
        kbm.act('crouch', 0.08); time.sleep(0.45); kbm.act('crouch', 0.08); time.sleep(0.45)


def _session_report(stats: dict, t0: float, money_start, ach_start: int | None = None, final: bool = False) -> str:
    """19/10 (Olivier : « tout » -> 3) : bilan de session lisible (Telegram + journal)."""
    from . import clips, youtube_chat
    mins = (time.perf_counter() - t0) / 60.0
    eddies = (inventory.MONEY - money_start) if money_start is not None else None
    ach = achievements._STATE.get('unlocked')
    n0 = achievements._STATE.get('n0', ach_start)   # 19/10 : ach_start etait pris AVANT la lecture Steam -> « +7 » faux
    lines = [f'BILAN DE SESSION ({mins:.0f} min)',
             f"- combats : {stats.get('combats', 0)} ; ennemis neutralises : ~{combat.KILLS[0]}",
             f"- morts : {stats.get('morts', 0)}" + (f" ; revanches : {stats['revanches']}" if stats.get('revanches') else '')
             + (f" ; crashs du jeu relances : {stats['crashs']}" if stats.get('crashs') else ''),
             f"- objets lootes : {stats.get('loot', 0)}" + (f' ; eddies gagnes : {eddies:+}' if eddies is not None else ''),
             f"- distance parcourue : {stats.get('km', 0.0):.1f} km",
             (f"- succes Steam : {len(ach)}" + (f' (+{len(ach) - n0} cette session)' if n0 is not None else ''))
             if ach is not None else '- succes Steam : inconnus']
    if youtube_chat.CHAT_STATS['orders'] or stats.get('missions_du_chat'):
        lines.append(f"- chat : {youtube_chat.CHAT_STATS['orders']} ordre(s) suivi(s), {stats.get('missions_du_chat', 0)} mission(s) choisie(s)")
    if clips.STATUS['clips']:
        lines.append(f"- clips enregistres : {clips.STATUS['clips']}" + (f" ; Shorts : {clips.STATUS['shorts']}" if clips.STATUS.get('shorts') else ''))
    try:
        from . import sounds
        lines.append(f"- chat YouTube : " + (f"direct {youtube_chat.STATUS['video_id']}, {youtube_chat.STATUS['messages']} message(s) lu(s)"
                                             if youtube_chat.STATUS.get('video_id') else f"pas de direct trouve ({youtube_chat.STATUS.get('error') or 'non configure'})")
                     + f" ; sons du HUD joues : {sounds.STATUS['played']}" + (f" (erreur : {sounds.STATUS['error']})" if sounds.STATUS.get('error') else ''))
    except Exception:
        pass
    if youtube_chat.SESSION_PTS:
        lines.append('- points du chat : ' + ', '.join(f'{i + 1}. {a} {p}' for i, (a, p) in enumerate(youtube_chat.top(3))))
    if stats.get('fetes') or youtube_chat.STATUS.get('soutiens'):
        lines.append(f"- soutiens du chat : {youtube_chat.STATUS.get('soutiens', 0)} ; fetes de V : {stats.get('fetes', 0)}")
    try:
        from . import features
        lines.append(features.report(features.save() if final else None))
    except Exception:
        pass
    return '\n'.join(lines)


def _alt(n: dict, text=None, hash_=None) -> dict:
    """12/10 : cible alternative, z TOUJOURS recopie (la zone d arrivee et la requete de chemin visent son etage)."""
    return {'x': n['x'], 'y': n['y'], 'z': n.get('z'), 'text': n.get('text') if text is None else text,
            'hash': n.get('hash') if hash_ is None else hash_, 't0': time.perf_counter(),
            'jph': n.get('jph')}          # 19/10 : entree de journal du marqueur (destination de l autodrive)


def _path_alt(t: dict):
    """Chemin vers une cible alternative, a son etage si connu ; sinon (ou sans chemin) a l etage de V, comme avant."""
    if isinstance(t.get('z'), (int, float)):
        r = nav.request_path_to(t['x'], t['y'], t['z'])
        if r and r.get('ok'):
            return r
    return nav.request_path_to(t['x'], t['y'])


def _dz(st: dict, tz) -> float | None:
    z = st.get('z') if st else None
    return abs(z - tz) if isinstance(z, (int, float)) and isinstance(tz, (int, float)) else None


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
    if any((not e.get('dead')) and (not e.get('down')) and (not e.get('police')) and e['d'] < ENGAGE_M and not _muted(e)
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
    pause_since = 0.0               # debut de la pause en cours (heure murale, comme remote.LAST_T)
    paused_at_start = pause is not None and pause.is_set()   # 12/10 : pause heritee de la session precedente (--loop)
    pause_pos = None                # position de V au debut de la pause (le joueur a pu le deplacer)
    t0 = time.perf_counter()
    stats = {'dialogues': 0, 'interactions': 0, 'trajets': 0, 'attentes': 0}

    def _halted(rr=None) -> bool:
        """12/10 : F11 (pause) / F12 (arret) pendant une competence, ou competence rendue sur « arret d urgence »."""
        return (rr or {}).get('reason') == 'arret d urgence' or (stop is not None and stop.is_set())
    last_quest_text = None
    idle_since = None
    same_hub, last_hub_sig = 0, None
    no_mappin_since = None
    path_failures = 0
    navp = navprog.Tracker()        # 12/10 : progres REEL vers la cible + echelle de deblocage (navprog.py)
    focus_skip_t = -1e9             # derniere bascule hors de la quete suivie mise de cote
    race_skip_t, race_told = -1e9, set()   # 12/10 : quetes de course (V ne sait pas piloter une course)
    escape_fail = (None, -1e9)      # (cible, heure) : sortie introuvable -> voyage rapide d ilot permis
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
    lock_t, lock_step, lock_told = None, 0, False     # 12/10 : V fige par le jeu (workspot de scene)
    scene_pos, scene_wait = None, 25.0
    shop_back_t = -999.0
    last_close_t = -999.0
    last_overlevel_t = -999.0
    mute_hostiles = MUTE_HOSTILES
    last_ripper_t = -999.0
    ripper_forced = False           # directive « charcudoc » : passe outre la suspension et le delai
    money_start = None
    last_poll_t = time.time()       # 19/10 : dernier sondage de mission du chat (le premier 15 min apres le depart)
    km_last = None                  # 19/10 : derniere position (distance parcourue, bilan de session)
    revenge_done: set = set()       # 19/10 : lieux de mort deja « prepares » (soin + annonce) pour la revanche
    combat.KILLS[0] = 0
    from . import features as _features
    _features.reset()
    ach_start = len(achievements._STATE.get('unlocked') or ())
    vendor_fail_streak = 0
    last_drive_t = -999.0
    ft_tried: dict = {}             # cible (cellule de 50 m) -> heure du dernier voyage rapide tente vers elle
    marker_watch = None             # arrivee sur un marqueur choisi par V : observee 60 s (utile ou sterile ?)
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
        if time.perf_counter() - approached.get(k, -9999.0) < 300.0:
            return True
        # 12/10 : un PNJ qui MARCHE (Panam, « Suivre Panam ») changeait de cellule et redevenait « nouveau » : 20 abordages
        # en 4 min -> sans invite de dialogue, son NOM est ecarte 3 min
        return time.perf_counter() - approached.get(('nom', n.get('name')), -9999.0) < 180.0
    deferred: list = []             # ordres recus en pause / hors premier plan : (texte, commande), executes a la reprise
    idle_poll_t = [-99.0]
    def _status_text(st):
        q_s = st.get('quest') or {}
        d_s = _dist_to_mappin(st) if st.get('x') is not None else None
        return (f"V : niveau {st.get('level', '?')}, vie {st.get('hp', 0):.0f} %, {inventory.MONEY} eddies\n"
                f"Quete : {q_s.get('text') or 'aucune'}" + (f' (a {d_s:.0f} m)' if d_s is not None else '') + '\n'
                f"Session : {stats.get('combats', 0)} combat(s), {stats.get('morts', 0)} mort(s), "
                f"{stats.get('loot', 0)} objet(s) loote(s), {stats.get('trajets', 0)} trajet(s), "
                f"{int(time.perf_counter() - t0)} s ecoulees")
    def _send_photo(st):
        img = remote.screenshot_jpeg()
        if img:
            sent = remote.send_photo(img, caption=f"V - {st.get('hp', 0):.0f} % de vie, niveau {st.get('level', '?')}")
            _log(f"  [telegram] photo {'envoyee' if sent else 'echec d envoi'} ({len(img)} octets)")
        else:
            _log('  [telegram] capture d ecran impossible (dxcam/cv2 indisponible ?)')
            remote.notify('Capture d ecran impossible (dxcam indisponible ?).')
    def _idle_directive(why):
        """23/09 : en pause (F11 ou directive) et hors premier plan, la boucle ne lisait AUCUN ordre --
        « reprendre » envoye par Telegram ne pouvait jamais etre lu (piege). File Telegram lue 1 fois/s (pas le
        panneau CET : sa lecture passe par le mod, jusqu a 3 s d attente si le jeu est dans un menu) ; arret,
        reprise, etat et photo traites ici, le reste note et execute a la reprise. True = il faut s arreter."""
        now = time.perf_counter()
        if now - idle_poll_t[0] < 1.0:
            return False
        idle_poll_t[0] = now
        dtv = remote.poll(log=_log, cet=False)
        if not dtv:
            return False
        dtv, low = _understand(dtv)
        where = 'en pause' if why == 'pause' else 'jeu hors premier plan'
        if low in _STOP_WORDS:
            _log(f'DIRECTIVE ({where}) : arret demande'); remote.notify('V s arrete.')
            if hard_stop is not None and hasattr(hard_stop, 'set'): hard_stop.set()
            return True
        if low in _RESUME_WORDS:
            if why == 'pause' and pause is not None and remote.LAST_T < pause_since:
                # recue AVANT cette pause (restee en file) : l appliquer rendrait la main a V malgre F11 / « pause »
                _log('DIRECTIVE : reprise recue avant la pause, ignoree')
                remote.notify('« reprendre » recu avant la pause : ignore. Renvoie-le pour relancer V.')
            elif why == 'pause' and pause is not None:
                pause.clear(); _log('DIRECTIVE : reprise'); remote.notify('V reprend.')
            else:
                remote.notify('Le jeu n est pas au premier plan : V reprendra des qu il y sera.')
            return False
        if low == 'pause':
            if pause is not None: pause.set()
            remote.notify('V est deja en pause.' if why == 'pause' else 'V est en pause.')
            return False
        st_i = motion.read_state() or {}
        if low in ('status', 'etat', 'etat?'):
            _log(f'DIRECTIVE ({where}) : etat demande')
            remote.notify(f'[{where.upper()}] ' + _status_text(st_i))
        elif low in ('photo', 'screenshot', 'capture'):
            _log(f'DIRECTIVE ({where}) : capture d ecran demandee')
            _send_photo(st_i)
        elif len(deferred) < 5:
            deferred.append((dtv, low))
            _log(f'DIRECTIVE ({where}) : « {dtv} » notee pour la reprise')
            remote.notify(f'Ordre note ({where}) : execute a la reprise.' + (' « reprendre » ou F11 pour relancer V.' if why == 'pause' else ''))
        else:
            remote.notify(f'Deja 5 ordres en attente ({where}) : celui-ci est ignore.')
        return False
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
    try:
        from . import sounds                       # 19/10 : sons du HUD T-800 (demarrage + generation en fond)
        sounds.prewarm(); sounds.play('boot')
    except Exception:
        pass
    hp_alarm_t = -99.0
    crash_check_t, game_gone = -99.0, 0     # 19/10 : relance automatique apres un crash du jeu
    _vr = CFG.vr_mode()
    if _vr:
        _log(f'MODE VR ACTIF ({_vr}) : l agent joue a plat seulement -- VR-OFF.bat puis relancer V. Session annulee.')
        stats['vr'] = True
        return stats
    remote.ensure_started(log=_log)
    remote.bind(hard_stop, pause)          # stop / pause / reprendre Telegram appliques des reception
    youtube_chat.ensure_started(log=_log)  # 18/10 : commandes « ! » du chat YouTube en direct (youtube.json)
    # 23/09 : config EFFECTIVE et source de chaque reglage (le panneau CET ecrasait le panneau Windows en silence)
    _log(f'config : {CFG.summary()}')
    _log(f'transport : {travel.summary()}')
    achievements.start(log=_log)                    # 12/10 : succes Steam (lecture seule du cache local)
    for w in CFG.warnings:
        _log(f'  [config] ATTENTION : {w}')
    if CFG.provider == 'ollama':
        sz = next((m['size_gb'] for m in llm.list_models(timeout=2.0) if m['name'] == CFG.model), None)
        if sz is not None and sz >= 6.0:
            w = (f'modele {CFG.model} ~{sz:.1f} Go de VRAM : le jeu en occupe deja ~14 Go sur 24, risque de plantage '
                 f'(un modele de 9,5 Go residant faisait planter le jeu). Modele leger conseille : llama3.2:latest (~2 Go).')
            _log(f'  [config] ATTENTION : {w}')
            remote.notify('Attention : ' + w)
    hud.update(t0=time.time(), mode='INITIALISATION', paused=False, ended=False, stats={}, **_hud_cfg())
    hud.start()
    # auto-test : V bouge-t-il ? -> detecte tout de suite un blocage d entree
    # 12/10 (revue) : 3 essais DIFFERENTS (avant, arriere, demi-tour + avant) sur des etats NEUFS. Pause seulement si
    # rien ne bouge, que la souris est sans effet elle aussi, et que rien n explique l immobilite (chargement, menu,
    # scene, mort...) : face a un mur, V tourne -> pas de pause ; pendant un chargement l etat ne bouge pas -> non concluant.
    s0 = motion.read_state()
    if s0 and kbm.game_focused() and not (pause is not None and pause.is_set()):
        moved, fresh, busy, turned = 0.0, False, False, False
        for _try in range(3):
            if stop is not None and stop.is_set():
                break
            a = motion.read_state()
            if not a:
                break
            if _try == 2:                              # 3e essai : demi-tour (un mur devant V ?) puis avant
                y0 = a.get('yaw')
                motion.turn_by(180.0, timeout=1.5, stop=stop)
                a2 = motion.read_state(last_seq=a.get('seq'), timeout=1.0)
                if a2 is not None and isinstance(y0, (int, float)) and abs(motion.wrap(a2['yaw'] - y0)) > 20.0:
                    turned = True
                a = a2 or a
            key = 'S' if _try == 1 else 'W'          # scancodes physiques, comme motion.walk_to
            kbm.hold(key); time.sleep(0.6); kbm.release(key); time.sleep(0.3)
            b = motion.read_state(last_seq=a.get('seq'), timeout=1.0)
            if b is not None:
                fresh = True
                busy = busy or _busy_state(a) or _busy_state(b)
                moved = max(moved, math.hypot(b['x'] - a['x'], b['y'] - a['y']))
                if moved > 0.3:
                    break
            if _try < 2:
                time.sleep(2.0)
        if not fresh:
            _log('auto-test deplacement : etat du jeu fige (chargement ? menu ?) -> non concluant, pas de pause')
        else:
            _log(f"auto-test deplacement : {moved:.2f} m -> {'OK' if moved > 0.3 else 'V NE BOUGE PAS'}"
                 + ('' if moved > 0.3 else (' (normal : dialogue, scene, vehicule, menu, chargement ou mort)' if busy
                                              else ' mais la souris tourne V (obstacle devant lui ?)' if turned else '')))
        if fresh and moved > 0.3:
            _AUTOTEST['alert_t'] = -1e9
        if fresh and moved <= 0.3 and not busy and not turned and pause is not None \
                and not (stop is not None and stop.is_set()):
            # 30/09 16:21 : la session a tourne 12 min a vide (V immobile) avant un F12
            _log('auto-test : ni clavier ni souris n ont d effet apres 3 essais -> PAUSE ; debloque le jeu puis F11 ou « reprendre »')
            if time.time() - _AUTOTEST['alert_t'] > 1800.0:     # un seul message par 30 min (--loop), la pause, elle, toujours
                _AUTOTEST['alert_t'] = time.time()
                remote.notify('V ne bouge pas au demarrage (clavier et souris sans effet) : mis en pause. Debloque le jeu, puis F11 ou « reprendre ».')
            pause.set()

    crashes = 0
    try:
        while time.perf_counter() - t0 < duration_s:
            try:
                if hard_stop is not None and hard_stop.is_set():
                    _log('arret d urgence'); break
                if pause is not None and pause.is_set():
                    kbm.release_all()
                    if not paused_logged:
                        paused_logged = True; pause_since = 0.0 if paused_at_start else time.time()
                        paused_at_start = False
                        _sp = motion.read_state() or {}
                        pause_pos = (_sp['x'], _sp['y'], _sp.get('z')) if _sp.get('x') is not None else None
                        _log('PAUSE : le joueur a la main ; F11 (ou « reprendre » sur Telegram) pour reprendre')
                        hud.update(paused=True)
                    if _idle_directive('pause'):
                        break
                    time.sleep(0.3); continue
                paused_at_start = False                          # la pause heritee a ete levee (F11 avant le 1er tour)
                if paused_logged:
                    paused_logged = False; _log('REPRISE : V rejoue'); plan.last_t = -99.0
                    _sr = motion.read_state() or {}
                    if pause_pos and _sr.get('x') is not None and (
                            math.hypot(_sr['x'] - pause_pos[0], _sr['y'] - pause_pos[1]) > navprog.MOVED_M
                            or (isinstance(pause_pos[2], (int, float)) and isinstance(_sr.get('z'), (int, float))
                                and abs(_sr['z'] - pause_pos[2]) > navprog.NOVEL_DZ)):
                        # 12/10 : le joueur a deplace V pendant la pause (ascenseur compris) : on repart d ici
                        navp.displaced(); path_failures = 0; straight_tried = False
                        escape_tries, escape_pos = 0, None; escape_fail = (None, -1e9); last_block_pos = None
                    hud.update(paused=False)
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
                    # 19/10 (Olivier : « 1 », DLSS 5) : le jeu a DISPARU (crash) 2 controles de suite (~20 s) -> relance
                    if time.perf_counter() - crash_check_t > 10.0:
                        crash_check_t = time.perf_counter()
                        game_gone = 0 if relaunch.game_running() else game_gone + 1
                    if game_gone >= 2:
                        game_gone = 0
                        if not relaunch.allowed():
                            _log(f'[relance] {relaunch.MAX_PER_HOUR} crashs en moins d 1 h : V s arrete (relance automatique suspendue)')
                            remote.notify(f'Le jeu a plante {relaunch.MAX_PER_HOUR} fois en moins d 1 h : V s arrete. Relance-le toi-meme.')
                            break
                        stats['crashs'] = stats.get('crashs', 0) + 1
                        remote.notify('Le jeu a plante : relance automatique en cours (Steam puis « Continuer »).')
                        if relaunch.relaunch(log=_log, stop=stop):
                            navp.displaced(); plan.last_t = -99.0; km_last = None
                            remote.notify('V est de retour en jeu apres le crash.')
                            continue
                        remote.notify('Relance automatique ratee : V s arrete.')
                        break
                    if _idle_directive('focus'):
                        break
                    time.sleep(0.3); continue
                if unfocused:
                    unfocused = False
                    _log('jeu de retour au premier plan : reprise')
                st = motion.read_state()
                if st is None:
                    stats['attentes'] += 1; time.sleep(0.5); continue
                # 24/09 : bilan d une arrivee sur un marqueur choisi par V (memoire persistante des marqueurs steriles).
                # Evenement compte seulement pres du marqueur ; temps observe cumule (pas le temps mural : pauses et
                # competences longues ne comptent pas) ; V reparti loin sans resultat = sterile.
                if marker_watch is not None:
                    _mw = marker_watch
                    _now = time.perf_counter()
                    _mw['seen'] += min(5.0, max(0.0, _now - _mw['last'])); _mw['last'] = _now
                    _dm = math.hypot(st['x'] - _mw['x'], st['y'] - _mw['y'])
                    _ch = (st.get('dialog') or {}).get('choices') or []
                    _why = ''
                    if _dm < 20.0:
                        _why = ('dialogue' if (len(_ch) >= 2 or (_ch and _mw['used'])) else 'combat' if st.get('combat')
                                else 'objectif change' if (st.get('quest') or {}).get('text') != _mw['quest'] else '')
                    if _why or _dm > 30.0 or _mw['seen'] > 60.0:
                        quests.marker_result(_mw['text'], _mw['x'], _mw['y'], bool(_why),
                                             _why or ('reparti sans resultat' if _dm > 30.0 else 'rien en 60 s'), z=_mw.get('z'), log=_log)
                        marker_watch = None

                # 19/10 : alarme du HUD T-800 quand la vie passe sous 25 % (toutes les 15 s au plus)
                if (st.get('hp') or 100) < 25 and (st.get('hp') or 0) > 0.5 and time.time() - hp_alarm_t > 15.0:
                    hp_alarm_t = time.time()
                    try:
                        from . import sounds
                        sounds.play('alarm')
                    except Exception:
                        pass
                # 19/10 : distance parcourue (sauts > 300 m = voyage rapide / rechargement, non comptes)
                if st.get('x') is not None:
                    if km_last is not None:
                        _dd = math.hypot(st['x'] - km_last[0], st['y'] - km_last[1])
                        if _dd < 300.0:
                            stats['km'] = stats.get('km', 0.0) + _dd / 1000.0
                    km_last = (st['x'], st['y'])
                # 19/10 : en direct, le chat choisit la prochaine mission toutes les 15 min (hors combat)
                if youtube_chat.live() and time.time() - last_poll_t > 900.0 and not st.get('combat') and not youtube_chat.POLL['t0']:
                    last_poll_t = time.time()
                    _opts = quests.options(3, (alt_target or st.get('quest') or {}).get('hash'), st.get('level'))
                    if youtube_chat.open_poll(_opts):
                        _log('[youtube] sondage de mission ouvert : ' + ' / '.join(str(o.get('text')) for o in _opts))

                # 0a0. DIRECTIVE recue (jeu ou Telegram) : V est redirige en direct, sans attendre qu il ait fini
                # (ordres notes pendant la pause / hors premier plan d abord : deja compris)
                dtv, low = deferred.pop(0) if deferred else (remote.poll(log=_log), None)
                if dtv:
                    if low is None:
                        dtv, low = _understand(dtv)
                    stats['directives'] = stats.get('directives', 0) + 1
                    hud.update(order='"' + hud.ascii_up(dtv)[:40] + '"', order_t=time.time())
                    hud.event(f'ordre recu : {dtv}')
                    if low in _STOP_WORDS:
                        # 23/09 : `stop` est l enveloppe _Halt (sans set) des qu une pause est fournie -> AttributeError,
                        # V continuait. On arme le VRAI arret (F12) et on sort tout de suite.
                        _log('DIRECTIVE : arret demande'); remote.notify('V s arrete.')
                        if hard_stop is not None and hasattr(hard_stop, 'set'): hard_stop.set()
                        break
                    elif low == 'pause':
                        if pause is not None: pause.set()
                        remote.notify('V est en pause.')
                    elif low in _RESUME_WORDS:
                        # hors pause : rien a reprendre. Si une pause vient d etre posee (F11 entre le haut de boucle et
                        # ici), ce « reprendre » est plus ancien qu elle : on ne la leve pas.
                        remote.notify('Pause posee entre-temps : « reprendre » ignore, renvoie-le pour relancer V.'
                                      if pause is not None and pause.is_set() else 'V n est pas en pause : il continue.')
                    elif low in ('attaque', 'attaquer', 'combat'):
                        hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('down')]
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
                        navp.fresh_start(); focus_skip_t = -1e9      # 12/10 : echelle neuve, mises de cote levees
                        _log('DIRECTIVE : retour a la quete suivie')
                        remote.notify('V reprend sa quete.')
                    elif low in ('marchand', 'vendre', 'boutique'):
                        last_sell_t = -999.0
                        _log('DIRECTIVE : course chez le marchand forcee')
                        remote.notify('V part faire ses courses.')
                    elif low in ('charcudoc', 'ripperdoc', 'implant'):
                        last_ripper_t = -999.0; ripper_forced = True
                        _log('DIRECTIVE : passage charcudoc force')
                        remote.notify('V va chez le charcudoc.')
                    elif low in ('explore', 'explorer', 'balade'):
                        nxd = explore.pick(log=_log)
                        if nxd:
                            alt_target = _alt(nxd)
                            stats['explorations'] = stats.get('explorations', 0) + 1
                        remote.notify('V part explorer' + (' : ' + nxd['text'] if nxd else ' (aucun point connu)'))
                    elif low in ('mission', 'missions'):
                        # 19/10 : le chat choisit la mission parmi 3 (!1 !2 !3, 45 s) ; hors direct -> liste sur Telegram
                        opts = quests.options(3, (alt_target or st.get('quest') or {}).get('hash'), st.get('level'))
                        if youtube_chat.live() and youtube_chat.open_poll(opts):
                            last_poll_t = time.time()
                            _log('[youtube] sondage de mission ouvert : ' + ' / '.join(str(o.get('text')) for o in opts))
                        else:
                            remote.notify('Missions possibles :\n' + '\n'.join(f"- {o.get('text')} ({o['dist']:.0f} m)" for o in opts)
                                          if opts else 'Aucune autre mission avec marqueur.')
                    elif low.startswith('mission_choix '):
                        try:
                            h_m = int(low.split()[1])
                        except (ValueError, IndexError):
                            h_m = None
                        q_m = next((q for q in quests.list_active() if h_m is not None and int(q.get('hash') or -1) == h_m
                                    and q.get('x') is not None), None)
                        if q_m:
                            alt_target = _alt(q_m)
                            plan.last_t = -99.0; plan.action = 'objectif'
                            stats['missions_du_chat'] = stats.get('missions_du_chat', 0) + 1
                            _log(f"[quetes] mission choisie par le chat : « {q_m.get('text')} » a {q_m.get('dist') or 0:.0f} m")
                        else:
                            _log('[quetes] mission choisie par le chat introuvable (terminee entre-temps ?)')
                    elif low == 'vole':
                        # 19/10 (chat) : vole la voiture la plus proche (pas pres de la police : driving.steal_candidate)
                        if st.get('combat') or st.get('vehicle'):
                            _log('[ordre] vol de voiture refuse : V est en combat ou deja au volant')
                        else:
                            ok_v = driving.steal_nearby(stop=stop, log=_log, max_d=40.0)
                            _log(f"[ordre] vol de voiture : {'reussi' if ok_v else 'aucune voiture prenable a 40 m'}")
                    elif low == 'voiture':
                        if st.get('combat') or st.get('vehicle'):
                            _log('[ordre] voiture refusee : V est en combat ou deja au volant')
                        else:
                            ok_c = driving.summon_and_board(stop=stop, log=_log)
                            _log(f"[ordre] voiture appelee : {'V monte' if ok_c else 'pas venue'}")
                    elif low == 'loot':
                        if st.get('combat'):
                            _log('[ordre] fouille refusee : combat en cours')
                        else:
                            lr = combat.loot_around(stop=stop, log=_log, seen=looted)
                            stats['loot'] = stats.get('loot', 0) + lr.get('ramasses', 0)
                            _log(f"[ordre] fouille : {lr.get('ramasses', 0)}/{lr.get('objets', 0)} objet(s) ramasse(s)")
                    elif low == 'radio':
                        radio.on_or_next(log=_log)
                    elif low == 'cyberpsycho':
                        _ps = [q_p for q_p in quests.list_active() if q_p.get('text') == 'HuntForPsychoVariant' and q_p.get('x') is not None]
                        if _ps:
                            _p = min(_ps, key=lambda q_p: q_p.get('dist') or 1e9)
                            alt_target = _alt(_p)
                            plan.last_t = -99.0; plan.action = 'objectif'
                            _log(f"[ordre] chasse au cyberpsycho : zone a {_p.get('dist') or 0:.0f} m")
                        else:
                            _log('[ordre] chasse au cyberpsycho : aucun signalement sur la carte')
                            narrator.say('AUCUN CYBERPSYCHO SIGNALE.', force=True)
                    elif low == 'etat':
                        narrator.say(f"VIE {st.get('hp', 0):.0f} %. NIVEAU {st.get('level', '?')}. {inventory.MONEY} EDDIES. "
                                     f"{stats.get('combats', 0)} COMBATS, {stats.get('morts', 0)} MORTS.", force=True)
                    elif low in ('celebre', 'fete'):
                        # 19/10 (Olivier : « 3 ») : soutien du chat -> V fete ca (hors combat / conduite) ; le clip suit
                        if st.get('combat') or st.get('vehicle') or st.get('mounted'):
                            _log('[fete] soutien du chat : V est occupe (combat / volant), remerciement a l ecran seulement')
                        else:
                            _log('[fete] V celebre un soutien du chat : sauts, tour sur lui-meme, salut')
                            stats['fetes'] = stats.get('fetes', 0) + 1
                            _celebrate()
                    elif low == 'bilan':
                        remote.notify(_session_report(stats, t0, money_start, ach_start))
                    elif low in ('changer_quete', 'autre_quete'):
                        nxd = quests.switch(alt_target.get('hash') if alt_target else (st.get('quest') or {}).get('hash'), log=_log)
                        if nxd:
                            alt_target = _alt(nxd)
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
                            alt_target = _alt(c, f'directive : {label}', -800_000_000 - round(c['x'] + c['y']))
                            _log(f'DIRECTIVE : V part vers « {label} »')
                            remote.notify(f'V part vers {label}.')
                        else:
                            _log(f'DIRECTIVE : lieu « {lieu} » inconnu')
                            remote.notify(f'Lieu « {lieu} » inconnu (marchand/charcudoc/point de voyage rapide deja decouvert seulement).')
                    elif low in ('status', 'etat', 'etat?'):
                        _log('DIRECTIVE : etat demande')
                        remote.notify(_status_text(st))
                    elif low in ('photo', 'screenshot', 'capture'):
                        _log('DIRECTIVE : capture d ecran demandee')
                        _send_photo(st)
                    elif low == 'niveau':
                        last_levelup_t = -999.0
                        _log('DIRECTIVE : verification niveau/perks forcee')
                        remote.notify('V verifie son niveau et ses perks.')
                    elif low == 'soigne':
                        if not combat.charges(st, 'heal'):
                            remote.notify(f"Plus aucune charge de soin (vie {st.get('hp', 0):.0f} %).")
                        elif kbm.ACTIONS.get('consumable'):
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
                            CFG.courage = v_c; CFG.live.add('courage')
                            CFG.courage_t = {'prudent': (5, 4, 5, 80, 25), 'equilibre': (6, 5, 6, 60, 25), 'temeraire': (8, 7, 8, 40, 35)}[v_c]
                            _log(f'DIRECTIVE : courage regle sur {v_c}')
                            remote.notify(f'V est desormais {v_c}.')
                        else:
                            remote.notify(f'Courage inconnu ({v_c}) : prudent, equilibre ou temeraire.')
                    elif low.startswith('style '):
                        v_s = low.split(' ', 1)[1].strip()
                        if v_s in ('melee', 'mixte', 'distance'):
                            CFG.style = v_s; CFG.live.add('style')
                            _log(f'DIRECTIVE : style regle sur {v_s}')
                            remote.notify(f'V se bat desormais en style {v_s}.')
                        else:
                            remote.notify(f'Style inconnu ({v_s}) : melee, mixte ou distance.')
                    elif low.startswith('aggro '):
                        v_a = low.split(' ', 1)[1].strip()
                        if v_a in ('defensif', 'normal', 'chasseur'):
                            CFG.aggro = v_a; CFG.live.add('aggro')
                            _log(f'DIRECTIVE : agressivite reglee sur {v_a}')
                            remote.notify(f'V est desormais {v_a}.')
                        else:
                            remote.notify(f'Agressivite inconnue ({v_a}) : defensif, normal ou chasseur.')
                    elif low == 'hud':
                        rh = nav._wait(nav._send({'cmd': 'hud_toggle'}), timeout=3.0)
                        shown = bool((rh or {}).get('hud'))
                        _log(f"DIRECTIVE : HUD {'affiche' if shown else 'masque'}")
                        remote.notify(f"HUD {'affiche' if shown else 'masque'}." if rh else 'HUD : mod muet (jeu lance ?).')
                    elif low in ('config', 'reglages'):
                        _log('DIRECTIVE : config demandee')
                        remote.notify('Config utilisee : ' + CFG.summary()
                                      + ''.join(f'\nAttention : {w}' for w in CFG.warnings))
                    elif low in ('modeles', 'liste_modeles', 'modele_liste'):
                        _log('DIRECTIVE : liste des modeles Ollama demandee')
                        if CFG.provider != 'ollama':
                            remote.notify(f'Fournisseur actuel : {CFG.provider} (pas Ollama) -- pas de liste locale a proposer.')
                        else:
                            models = llm.list_models()
                            lines = [f"- {m['name']} (~{m['size_gb']:.1f} Go VRAM)" + (' (actif)' if m['name'] == CFG.model else '') for m in models]
                            remote.notify('Modeles Ollama installes (taille ~= VRAM necessaire) :\n' + ('\n'.join(lines) if lines else 'aucun trouve (Ollama injoignable ?)'))
                    elif low.startswith('modele '):
                        v_m = low.split(' ', 1)[1].strip()
                        if CFG.provider != 'ollama':
                            remote.notify(f'Fournisseur actuel : {CFG.provider} (pas Ollama) -- change de fournisseur dans le panneau in-game d abord.')
                        else:
                            models = llm.list_models()
                            names = [m['name'] for m in models]
                            if names and v_m not in names:
                                remote.notify(f"Modele « {v_m} » non installe localement (`ollama pull {v_m}` d abord). Modeles disponibles :\n" + '\n'.join(f"- {m['name']} (~{m['size_gb']:.1f} Go)" for m in models))
                            else:
                                CFG.model = v_m
                                CFG.live.add('model')
                                _log(f'DIRECTIVE : modele Ollama regle sur {v_m}')
                                remote.notify(f'V utilise desormais le modele {v_m}.')
                    else:
                        _log(f'DIRECTIVE non reconnue : « {dtv} »')
                        remote.notify('Directive non reconnue. Essaie : stop, pause, reprendre, attaque, objectif, marchand, charcudoc, explore, changer_quete, va_a <lieu>, status, photo, niveau, soigne, stats, courage/style/aggro <valeur>, modeles, modele <nom>, config.')
                    hud.update(**_hud_cfg())            # reglages eventuellement changes en direct (modele, courage...)
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
                        _foes = sorted({str(e.get('name') or '?') for e in (st.get('enemies') or [])
                                        if not e.get('dead') and float(e.get('d') or 999) < 60.0})
                        _cause = (f"{len(_foes)} type(s) d ennemis : " + ', '.join(_foes[:3])) if _foes else 'cause inconnue (aucun ennemi en vue)'
                        quests.mark_death(st['x'], st['y'], cause=_cause, level=st.get('level'))
                        _log(f'V EST MORT ({_cause}) : lieu evite 1 h puis revanche ; rechargement de la derniere sauvegarde')
                        if _reload_last_save(_log, stop=stop):
                            navp.displaced()                             # 12/10 : rechargement = deplacement
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
                                    alt_target = _alt(nxt)
                                    stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                else:
                                    alt_target = None
                            else:
                                alt_target = None
                            time.sleep(3.0); continue
                        if stop is not None and stop.is_set():
                            # 12/10 : F11 / « pause » (ou F12) pendant le rechargement : pas un echec. Le jeu a pu recharger.
                            navp.displaced(); alt_target = None; plan.last_t = -99.0; path_failures = 0
                            _log('rechargement interrompu (pause / arret) : repris a la reprise si V est encore mort')
                            time.sleep(0.3); continue
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
                # 0a4b. 12/10 V FIGE PAR LE JEU (workspot de scene) : on laisse la scene se derouler (la scene Takemura
                # demandait 88 s) ; ni touche ni voyage (la teleportation la cassait : V fige 15 min le 12/10) ; puis
                # sorties de workspot par le mod, puis pause + Telegram
                achievements.tick(log=_log, notify=remote.notify)   # 12/10 : succes debloques (toutes les 5 min)
                lock = motion.game_lock(st)
                if lock and not (st.get('dialog') or {}).get('choices') and not st.get('combat'):
                    now_l = time.perf_counter()
                    if lock_t is None:
                        lock_t, lock_step, lock_told = now_l, 0, False
                        kbm.release_all()
                        _log(f"V fige par le jeu ({lock}) : locomotion={st.get('locomotion')} frz={st.get('frz')} -> on laisse la scene se derouler (ni touche ni voyage)")
                    el_l = now_l - lock_t
                    if lock_step < 3 and el_l > 120.0 + 30.0 * lock_step:
                        lock_step += 1
                        rr_l = nav._wait(nav._send({'cmd': 'ws_exit', 'x': lock_step}), timeout=3.0) or {}
                        time.sleep(2.0)
                        _log(f"  [fige] sortie de workspot {lock_step}/3 : envoyee={rr_l.get('ok')} {rr_l.get('err') or ''} -> {motion.game_lock(motion.read_state() or {}) or 'LIBRE'}")
                    elif el_l > 300.0 and not lock_told:
                        lock_told = True
                        _log(f'V fige par le jeu depuis {el_l / 60:.0f} min ({lock}) malgre 3 sorties : PAUSE + Telegram')
                        remote.notify(f'V est fige depuis {el_l / 60:.0f} min ({lock}) : clavier et souris sans effet. Mis en pause : debloque ou recharge, puis F11 / « reprendre ».')
                        if pause is not None:
                            pause.set(); lock_t = None
                    time.sleep(0.5); continue
                if lock_t is not None:
                    _log(f'V libere apres {time.perf_counter() - lock_t:.0f} s (etape {lock_step})')
                    lock_t = None; rot_failures = 0; last_block_pos = None; navp.displaced()
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
                                navp.displaced()
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
                        navp.displaced()                                 # V a roule (meme si F11 suit)
                        if _halted(r):
                            continue                                     # 12/10 : le joueur garde la voiture
                        driving.exit_vehicle(log=_log, stop=stop)
                    else:
                        _log('V est dans un vehicule et l objectif est proche : il descend')
                        navp.displaced()
                        driving.exit_vehicle(log=_log, stop=stop)
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
                        alt_target = _alt(nxt)
                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        continue
                if q.get('text') and q['text'] != last_quest_text:
                    last_quest_text = q['text']
                    _log(f"objectif : {q['text']}  (marqueur : {'oui' if q.get('hasMappin') else 'non'})")
                    hud.event(f"nouvel objectif : {q['text']}")

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
                            alt_target = _alt(nxt)
                            stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                            same_hub, last_hub_sig = 0, None
                            time.sleep(1.0); continue
                        nxe = explore.pick(log=_log)
                        if nxe:
                            alt_target = _alt(nxe)
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
                    _near_h = min([e['d'] for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('down')] or [99.0])
                    if _near_h > 12.0 and (st.get('hp') or 100) >= 50:
                        buffs.apply(st, log=_log, in_combat=True)  # se buffer AVANT de frapper, seulement si on a 3 s devant soi
                    _log(f"combat detecte : {len(st.get('enemies') or [])} hostile(s), vie {st.get('hp', 0):.0f} %")
                    hud.event(f"engagement : {len([e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('down')])} hostile(s)")
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
                    prev_sell_t = last_sell_t
                    last_sell_t = time.perf_counter()
                    vendor.MAX_VENDOR_M = 5000.0                       # course dediee : le marchand connu le plus proche fera l affaire,
                                                                       # sell_trip() sait deja s y rendre a toute distance (conduite/voyage rapide)
                    vend = vendor.pick_vendor(vendor.list_vendors())
                    if vend:
                        _log(f"courses : poids {inventory.WEIGHT:.0f}/{inventory.CARRY:.0f}, {len(inventory.SELLABLE)} objets a vendre, soins {inventory.HEALS}, marchand a {vend['dist']:.0f} m")
                        hud.update(mode='RAVITAILLEMENT', directive=hud.ascii_up(f"marchand a {vend['dist']:.0f} m"))
                        tr = vendor.sell_trip(vend, stop=stop, log=_log)
                        navp.displaced()
                        if _halted(tr):
                            last_sell_t = prev_sell_t                    # 12/10 : coupe par F11/F12 : ni echec ni vente
                            continue
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
                # 23/09 : 85 passages, 0 implant pose -> memoire entre sessions, suspension 6 h apres 3 passages sans pose,
                # rien en debut de session ni pendant une cible choisie par V ; la directive « charcudoc » force
                if CFG.features.get('ripperdoc', True) and inventory.MONEY >= 6000 and not st.get('combat') and not _threat_near(st) and not focus \
                        and (ripper_forced or (alt_target is None and time.perf_counter() - last_ripper_t > 1800.0
                                               and vendor.ripper_allowed(time.perf_counter() - t0)[0])):
                    prev_rip = (last_ripper_t, ripper_forced)
                    last_ripper_t = time.perf_counter(); ripper_forced = False
                    vendor.MAX_VENDOR_M = 5000.0
                    rip = vendor.pick_vendor(vendor.list_vendors(), prefer='ripper')
                    if rip and 'ripper' in (rip.get('variant') or '').lower():
                        _log(f"charcudoc : {inventory.MONEY} eddies, « {rip.get('variant')} » a {rip['dist']:.0f} m : V va s optimiser")
                        hud.update(mode='OPTIMISATION CYBERNETIQUE', directive=hud.ascii_up(f"charcudoc a {rip['dist']:.0f} m"))
                        tr = vendor.sell_trip(rip, stop=stop, log=_log)
                        navp.displaced()
                        if _halted(tr):
                            last_ripper_t, ripper_forced = prev_rip      # 12/10 : coupe par F11/F12 : rien de consomme
                            continue
                        if tr.get('ok'):
                            rr = vendor.ripperdoc_shop(log=_log)
                            if rr.get('stock_cyber') is not None or rr.get('useless'):
                                vendor.ripper_stock_seen(rip, int(rr.get('stock_cyber') or 0), log=_log)
                            _log(f"charcudoc : {rr.get('poses', 0)} implant(s) pose(s) pour {rr.get('eddies', 0)} eddies ({rr.get('reason') or 'ok'})")
                            stats['implants'] = stats.get('implants', 0) + rr.get('poses', 0)
                            if rr.get('useless'):
                                # rien a poser ici (pas de stock, ne parle pas, stock illisible) : on n y revient pas de sitot
                                vendor.mark_useless(rip, rr['useless'], days=7.0, log=_log)
                            if not (stop is not None and stop.is_set()):
                                vendor.ripper_result(int(rr.get('poses') or 0), rr.get('useless') or rr.get('reason') or 'aucun implant abordable', log=_log)
                        else:
                            _log(f"charcudoc : non atteint ({tr.get('reason')}) : un autre charcudoc dans 5 min"); vendor.mark_failed(rip, log=_log)
                            # 19/10 : un charcudoc injoignable n est pas « sans implant » : pas de suspension de 6 h, on essaie un
                            # AUTRE charcudoc (celui-ci est ecarte 15 min, puis 1 jour au 2e echec) dans 5 min
                            last_ripper_t = time.perf_counter() - 1500.0
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
                if (st.get('hp') or 100) < 40 and time.perf_counter() - last_heal_t > 8.0 and combat.charges(st, 'heal'):
                    kbm.act('consumable', 0.1); last_heal_t = time.perf_counter()
                    _log(f"soin hors combat (vie {st.get('hp'):.0f} %)")

                # 12/10 (Olivier : « V ne doit pas faire les quetes de course ») : objectif suivi de COURSE -> V fait autre chose
                # (journal 10:45 / 10:55 : « Aller a la course du centre-ville », « Appeler Claire pour commencer la course »)
                if alt_target is None and q.get('text') and quests.is_race(q.get('text')) and not st.get('combat') \
                        and time.perf_counter() - race_skip_t > 60.0:
                    race_skip_t = time.perf_counter()
                    if q.get('mx') is not None:
                        quests.ban_spot(q['mx'], q['my'], 80.0)
                    if q['text'] not in race_told:
                        race_told.add(q['text'])
                        _log(f"quete de course « {q['text']} » : V ne fait pas les courses, il passe a autre chose")
                    nxt = quests.switch(q.get('hash'), log=_log, avoid=(q['mx'], q['my'], 80.0) if q.get('mx') is not None else None)
                    if nxt:
                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                    else:
                        nxt = explore.pick(log=_log)
                        if nxt:
                            stats['explorations'] = stats.get('explorations', 0) + 1
                    if nxt:
                        alt_target = _alt(nxt)
                        plan.last_t = -99.0; plan.action = 'objectif'
                        time.sleep(1.0); continue
                # 12/10 : quete suivie jugee inaccessible a pied depuis ici (echelle de deblocage plus bas) : V fait autre
                # chose tant que la mise de cote court (20, 40 puis 60 min ; memoire de session). Levee si V est a moins
                # de 30 m du marqueur ou sur directive « objectif ».
                if alt_target is None and q.get('hasMappin') and q.get('mx') is not None and not st.get('combat') \
                        and navp.unreach and time.perf_counter() - focus_skip_t > 60.0:
                    u_key = _tid(None, q)
                    w_u = navp.unreach_wait(u_key)
                    _dzu = _dz(st, q.get('mz'))
                    if w_u > 0 and math.hypot(st['x'] - q['mx'], st['y'] - q['my']) <= 30.0 and _dzu is not None and _dzu < navprog.NOVEL_DZ:
                        navp.lift(u_key); w_u = 0.0                  # V est au marqueur, au bon etage
                    if w_u > 0:
                        nxt = quests.switch(q.get('hash'), log=_log, avoid=(q['mx'], q['my'], navprog.SECTOR_M))
                        if nxt:
                            stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        else:
                            nxt = explore.pick(log=_log)
                            if nxt:
                                stats['explorations'] = stats.get('explorations', 0) + 1
                        if not nxt:
                            focus_skip_t = time.perf_counter()      # rien d autre a faire : on retente la quete, reessai dans 1 min
                        if nxt:
                            _log(f"quete suivie « {q.get('text')} » mise de cote (inaccessible d ici, encore {w_u / 60:.0f} min) : V fait autre chose (« {nxt.get('text')} »)")
                            alt_target = _alt(nxt)
                            plan.last_t = -99.0; plan.action = 'objectif'
                            time.sleep(1.0); continue
                # 4. DECISION de V (modele local) : objectif / parler / attaquer / changer_quete / attendre
                if alt_target is not None:
                    dist = math.hypot(st['x'] - alt_target['x'], st['y'] - alt_target['y'])
                    # 12/10 : marqueur sur un objet (comptoir, terminal mural) : le mod vise le navmesh dans 6 m, V plafonne
                    # a 4-8 m au meme etage -> arrivee (avant : troncons sans fin jusqu a l expiration de 15 min)
                    _arr = dist < ARRIVE_M or navp.near_stall(_tid(alt_target, q), dist, _dz(st, alt_target.get('z')))
                    if _arr:
                        for _v in navp.after_leg(None, None, True):      # V atteint une cible : il n etait pas coince
                            quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                    if _arr or time.perf_counter() - alt_target['t0'] > 900:
                        _used = None
                        if _arr and alt_target.get('text') == 'HuntForPsychoVariant' and not st.get('combat') \
                                and not (stop is not None and stop.is_set()):
                            # 19/10 : zone de traque -> fouille en spirale jusqu au contact (le psycho n est pas au centre)
                            if psycho.search(alt_target['x'], alt_target['y'], alt_target.get('z'), stop=stop, log=_log):
                                stats['cyberpsychos'] = stats.get('cyberpsychos', 0) + 1
                                alt_target = None; plan.last_t = -99.0
                                continue                                 # le combat prend le relais
                        if _arr and not st.get('combat') and not ((st.get('interact') or {}).get('choices')):
                            _used = False
                            if _approach_machine(alt_target['x'], alt_target['y'], dist, _log, stop):   # console / donneur de quete
                                stats['interactions'] += 1
                                last_close_t = time.perf_counter()
                                time.sleep(1.5)
                                _used = True
                        # 24/09 : bilan du marqueur (seulement ceux de quete choisis par V, pas exploration / directive) :
                        # toujours observe (une invite visible, ex. porte « Forcer » sans effet, ne prouve rien) ;
                        # « jamais atteint en 15 min » n est pas penalise (pauses, courses, combats faussent le delai)
                        if str(alt_target.get('text') or '').endswith('Variant') and _arr \
                                and not (stop is not None and stop.is_set()):
                            if marker_watch is not None:                # grappe de marqueurs : on clot l observation en cours
                                _pw = marker_watch
                                quests.marker_result(_pw['text'], _pw['x'], _pw['y'], False, 'reparti sans resultat', z=_pw.get('z'), log=_log)
                            marker_watch = {'text': alt_target.get('text'), 'x': alt_target['x'], 'y': alt_target['y'],
                                            'z': alt_target.get('z'), 'seen': 0.0, 'last': time.perf_counter(),
                                            'used': bool(_used), 'quest': (st.get('quest') or {}).get('text')}
                        _log('cible alternative atteinte ou expiree : retour a la quete suivie')
                        quests.mark_visited(alt_target.get('hash'))
                        alt_target = None
                        time.sleep(0.5); continue                        # 12/10 : bloc « mise de cote » d abord
                else:
                    dist = _dist_to_mappin(st)
                inter = st.get('interact')
                action = plan.maybe_decide(st, {'dist_m': dist, 'alt': alt_target is not None, 'approached': _was_approached, 'rescued': _was_rescued, 'muted': _muted}, log=_log)
                _explo = alt_target is not None and str(alt_target.get('text') or '').startswith('exploration')
                hud.update(mode='EXPLORATION' if _explo else _HUD_MODES.get(action, 'EN MISSION'),
                           directive=_hud_directive(st, alt_target, dist),
                           decision=hud.ascii_up(f'{plan.action} -- {plan.reason}')[:80],
                           model=_hud_model(), temper=hud.ascii_up(f'{CFG.courage} / {CFG.style} / {CFG.aggro}'),
                           stats={'combats': stats.get('combats', 0), 'morts': stats.get('morts', 0), 'loot': stats.get('loot', 0),
                                  'eddies': (inventory.MONEY - money_start) if money_start is not None else 0})

                if action == 'eviter':
                    hs = [e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('down') and not e.get('police')]
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
                        alt_target = _alt(nxt)
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
                    _qt = str((st.get('quest') or {}).get('text') or '').lower()
                    if (st.get('quest') or {}).get('hasMappin'):     # 12/10 : le PNJ CITE par l objectif (pas « Aldecaldo » a cote)
                        cands = [n for n in cands if str(n.get('name') or '').lower().split()[:1] and str(n.get('name') or '').lower().split()[0] in _qt]
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
                                approached[('nom', n.get('name'))] = time.perf_counter()
                                _log('  -> pas d invite de dialogue : on passe (ecarte 3 min)')
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
                    hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead') and not e.get('down') and not e.get('police')
                                and (e.get('z') is None or abs(e['z'] - st.get('z', e['z'])) < 3.5)]
                    # hostiles « muets » : cibles qui n entrent jamais en combat (PNJ fuyards, tourelles hors portee...) ->
                    # ignorees 3 min apres un engagement sans combat, pour ne pas perdre 25 s a chaque fois
                    hostiles = [e for e in hostiles if not _muted(e) and not planner.dormant_turret(e)]   # 12/10 : tourelles endormies
                    if hostiles:
                        _log(f"V engage le combat : {len(hostiles)} hostile(s), le plus proche a {hostiles[0]['d']:.0f} m"
                             + (f" (« {hostiles[0]['name']} »)" if hostiles[0].get('name') else ''))
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
                        alt_target = _alt(nxt)
                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        plan.note('a change de cible')
                        plan.last_t = -99.0; plan.action = 'objectif'     # forcer une nouvelle decision (3 changements en 3 s sinon)
                        time.sleep(1.0); continue
                    # 24/09 : plus aucun marqueur accessible (steriles exclus, bloques...) -> exploration plutot qu attendre
                    nxe = explore.pick(log=_log)
                    if nxe:
                        alt_target = _alt(nxe)
                        stats['explorations'] = stats.get('explorations', 0) + 1
                        plan.last_t = -99.0; plan.action = 'objectif'
                        time.sleep(1.0); continue
                    action = 'attendre'

                if action == 'attendre':
                    stats['attentes'] += 1
                    time.sleep(1.0); continue

                # 5. aller vers l objectif (action == 'objectif')
                # 19/10 (Olivier : « V apprend de ses morts ») : objectif pres d un lieu ou V est deja mort -> a 200 m,
                # il se soigne a fond et l annonce (revanche), une fois par lieu et par session
                _tx, _ty = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                if _tx is not None and dist is not None and dist < 200.0 and not st.get('combat'):
                    _dn = quests.death_near(_tx, _ty)
                    if _dn is not None and (_dn['x'], _dn['y']) not in revenge_done:
                        revenge_done.add((_dn['x'], _dn['y']))
                        _log(f"[revanche] retour la ou V est mort ({_dn.get('n') or 1} fois ; {_dn.get('cause') or 'cause inconnue'}) :"
                             f" soin a fond avant d y aller (vie {st.get('hp', 0):.0f} %)")
                        stats['revanches'] = stats.get('revanches', 0) + 1
                        if (st.get('hp') or 100) < 95 and kbm.ACTIONS.get('consumable'):
                            kbm.act('consumable', 0.1)
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
                if dist is not None and dist < navprog.NEAR_M and not (inter and inter.get('choices'))                 and time.perf_counter() - last_close_t > 30.0 and not st.get('combat'):
                    last_close_t = time.perf_counter()
                    tx, ty = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                    if tx is not None:
                        if _approach_machine(tx, ty, dist, _log, stop):
                            stats['interactions'] += 1
                        continue
                # 23/09 : 3 conduites abouties sur ~193 (~4 h perdues). Voiture tant qu elle marche (travel.py), jamais
                # vers une autre cible quand la quete suivie a un marqueur ailleurs (l autodrive irait vers lui) ;
                # sinon voyage rapide d abord, puis a pied.
                if dist is not None and dist > 500.0:
                    # 19/10 (Olivier : « V monte dans un vehicule, roule quelques metres et en sort ») : l autodrive ne va
                    # que vers la quete SUIVIE par le jeu. Cible choisie par V -> seulement si son marqueur a une entree de
                    # journal (on la fera suivre) ; point d exploration -> pas de voiture (roue libre garantie)
                    route_ok = alt_target is None or bool(alt_target.get('jph'))
                    tx_t, ty_t = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                    ft_key = (round(tx_t / 50.0), round(ty_t / 50.0)) if tx_t is not None and ty_t is not None else None
                    how, why_not = travel.choose(dist, route_ok, bool(kbm.ACTIONS.get('autodrive') and CFG.features.get('driving', True)),
                                                 time.perf_counter() - last_drive_t, CFG.features.get('fasttravel', True),
                                                 ft_key is None or time.perf_counter() - ft_tried.get(ft_key, -1e9) < 600.0)
                    if how == 'fasttravel':
                        _log(f'objectif a {dist:.0f} m : voyage rapide d abord ({why_not})')
                        ft = nav.fast_travel_to(tx_t, ty_t, log=_log, min_gain_m=300.0, stop=stop)
                        if ft.get('ok') and _halted():
                            navp.displaced(); continue
                        _log(f"voyage rapide : {('arrive a ' + str(ft.get('point'))) if ft.get('ok') else ft.get('reason')}")
                        if ft.get('ok'):
                            stats['voyages'] = stats.get('voyages', 0) + 1
                            navp.displaced()
                            continue
                        if _halted(ft):
                            plan.last_t = -99.0; continue             # 12/10 : F11/F12, pas un echec
                        ft_tried[ft_key] = time.perf_counter()     # echec seulement : pas de nouvel essai vers cette cible avant 10 min
                    if how == 'drive' and alt_target is not None:
                        if quests.track(int(alt_target['jph'])):
                            _log(f"[conduite] quete du marqueur « {alt_target.get('text')} » suivie dans le jeu : destination de l autodrive")
                            time.sleep(1.0)
                        else:
                            _log('[conduite] le jeu refuse de suivre cette quete : pas de voiture (l autodrive roulerait sans destination)')
                            alt_target['jph'] = None
                            how = 'walk'
                    if how == 'drive':
                        _log(f'objectif a {dist:.0f} m : V prend la voiture (autodrive)')
                        plan.note('a pris la voiture')
                        r = driving.drive_to(tx_t, ty_t, stop=stop, log=_log)
                        if r.get('boarded'):
                            navp.displaced()                             # V a roule (meme si F11 suit)
                        if _halted(r):
                            plan.last_t = -99.0; continue                # 12/10 : F11/F12, rien de consomme
                        last_drive_t = time.perf_counter()               # delai compte a partir de la FIN de la conduite
                        stats['conduites'] = stats.get('conduites', 0) + 1
                        _log(f"conduite : {'arrive' if r.get('ok') else r.get('reason')}")
                        if not r.get('ok') and r.get('reason') == 'embarquement echoue':
                            last_drive_t = time.perf_counter() + 360.0      # ici la moto ne vient pas / ne se monte pas : pas avant 10 min
                        continue

                if dist is not None and dist > ARRIVE_M and not (alt_target is None and navp.near_stall(_tid(None, q), dist, _dz(st, q.get('mz')))):
                    # (12/10 : quete suivie dont V plafonne dans la zone d arrivee -> etape 6, comme une arrivee)
                    no_mappin_since = None
                    _log(f'objectif a {dist:.0f} m : aller_a')
                    req = (lambda t=alt_target: _path_alt(t)) if alt_target is not None else nav.request_path_to_quest
                    r = nav.goto(req, arrive_m=ARRIVE_M, max_legs=1, timeout=60.0, stop=stop, log=_log,
                                 interrupt=_threat_near)
                    if r.get('reason') == 'interrompu':
                        _log('trajet interrompu : menace proche -> decision immediate')
                        plan.last_t = -99.0
                        continue
                    if _halted(r):
                        plan.last_t = -99.0                 # 12/10 : F11/F12 pendant le trajet : ni echec compte, ni escalade
                        continue
                    stats['trajets'] += 1
                    # coince au meme endroit deux fois : on memorise la zone, le mod evitera d y renvoyer
                    s_h = motion.read_state() or st
                    here = (s_h['x'], s_h['y'])
                    _rot = 'rotation initiale' in str(r.get('reason') or '')   # 12/10 : V n a pas bouge -> pas une zone bloquante
                    if _rot:
                        pass
                    elif not r.get('ok') and last_block_pos and math.hypot(here[0] - last_block_pos[0], here[1] - last_block_pos[1]) < 3.0:
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
                            s_b = motion.read_state() or st
                            if motion.game_lock(s_b):
                                rot_failures = 0; continue            # 12/10 : fige par le jeu -> etape 0a4b (aucune touche)
                            _log('la souris est sans effet depuis 2 trajets : V est sans doute dans une scene (assis, stand) -> on en sort')
                            kbm.tap('BACKSPACE', 0.09); time.sleep(0.6)
                            if (motion.read_state() or {}).get('menu'):  # 12/10 : Echap SEULEMENT si un menu est ouvert
                                kbm.tap('ESC', 0.09); time.sleep(0.6)    # (Echap a l aveugle ouvrait le menu pause : 9 fois sur 9)
                            kbm.hold('S'); time.sleep(1.5); kbm.release('S')
                            kbm.act('jump', 0.1); time.sleep(0.8)
                            rot_failures = 0
                            continue
                    else:
                        rot_failures = 0
                    # 12/10 : « un point du chemin atteint » comptait un troncon qui bute toujours au meme endroit comme un
                    # progres (« bloque, reste 36.6 m » 36 fois de suite le 05/10, 14 min, aucun echec compte). Voir navprog.
                    tx_p, ty_p = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                    tz_p = alt_target.get('z') if alt_target is not None else q.get('mz')
                    k_p = _tid(alt_target, q)
                    if k_p != navp.key:
                        path_failures = 0; straight_tried = False      # autre cible : les echecs de la precedente ne comptent pas
                    d_after = math.hypot(here[0] - tx_p, here[1] - ty_p) if tx_p is not None and ty_p is not None else None
                    n_fail = navp.leg(k_p, (tx_p, ty_p) if tx_p is not None and ty_p is not None else None, dist, d_after,
                                      bool(r.get('ok')), r.get('seconds') or 0.0, start=(st['x'], st['y'], st.get('z')),
                                      end=(s_h['x'], s_h['y'], s_h.get('z')), dz_after=_dz(s_h, tz_p))
                    for _v in navp.after_leg((st['x'], st['y']), here, navp.kind == 'arrive'):   # V n etait pas coince
                        quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                    if n_fail == 0:
                        path_failures = 0
                        if navp.true_progress:
                            straight_tried = False
                    else:
                        path_failures += n_fail
                        _log(f"  trajet : {r.get('reason')}" + (f', reste {d_after:.0f} m (record {navp.best:.0f} m)' if d_after is not None and navp.best is not None else '')
                             + f' : sans progres (echec {min(path_failures, 5)}/5)')
                        if path_failures >= 5 and dist is not None and not straight_tried and escape_tries < 3:      # meme pour une cible lointaine : d abord SORTIR d ici
                            # le maillage ne repond pas (interieur, escalier, ring...) : on marche EN LIGNE DROITE vers
                            # la cible 20 s (les portes s ouvrent en passant) ; si V a avance, le maillage se recalcule
                            straight_tried = True
                            _log(f'aucun chemin : marche en ligne droite vers la cible ({dist:.0f} m) pour sortir d ici')
                            tx, ty = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                            rw = motion.walk_to(tx, ty, timeout=20.0, stop=stop, interrupt=_threat_near, sprint=True)
                            if _halted():
                                straight_tried = False                     # 12/10 : on la refera a la reprise
                                continue
                            s2 = motion.read_state() or st
                            for _v in navp.after_leg(here, (s2['x'], s2['y'], s2.get('z')), False):   # deplacement a pied
                                quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                            moved = math.hypot(s2['x'] - st['x'], s2['y'] - st['y'])
                            _log(f"  ligne droite : {'ok' if rw.get('ok') else rw.get('reason')}, {moved:.0f} m parcourus")
                            if moved > 8.0:
                                path_failures = 0
                                continue
                            # toujours coince : V cherche une SORTIE (portes, invites, sondes dans 8 directions)
                            here_e = (s2['x'], s2['y'])
                            esc_prev = (escape_tries, escape_pos)
                            if escape_pos is not None and math.hypot(here_e[0] - escape_pos[0], here_e[1] - escape_pos[1]) < 4.0:
                                escape_tries += 1                        # meme point qu au dernier essai : pas de vrai progres
                            else:
                                escape_tries, escape_pos = 1, here_e
                            _log(f'ilot ferme : recherche d une sortie (portes, invites, sondes) [{escape_tries}/3]')
                            re = escape.escape((tx, ty), stop=stop, log=_log)
                            if _halted():
                                escape_tries, escape_pos = esc_prev; straight_tried = False
                                continue
                            s3e = motion.read_state() or s2
                            for _v in navp.after_leg(here_e, (s3e['x'], s3e['y'], s3e.get('z')), False):
                                quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                            if not re.get('ok') and re.get('moyen') != 'arret d urgence':
                                escape_fail = (_tid(alt_target, q), time.perf_counter())   # ilot avere pour CETTE cible
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
                                    _log(f'  bloque dans ce secteur depuis {stuck_cycles} cycles d evasion epuises : voyage rapide de sortie')
                                    tx2, ty2 = (alt_target['x'], alt_target['y']) if alt_target is not None else (q.get('mx'), q.get('my'))
                                    # 12/10 : n importe quel point a plus de 60 m (sortir d ici d abord) ; F11/F12 -> rien de consomme
                                    rft2 = nav.fast_travel_to(tx2, ty2, log=_log, min_gain_m=-1e9, stop=stop)
                                    if rft2.get('ok') and _halted():
                                        navp.displaced(); continue
                                    if _halted(rft2):
                                        continue
                                    stuck_cycles = 0; navp.trap_ft_mark()
                                    if rft2.get('ok'):
                                        stats['voyages'] = stats.get('voyages', 0) + 1; path_failures = 0; straight_tried = False
                                        navp.displaced()
                                        remote.notify('V etait bloque dans un secteur depuis plusieurs minutes : sorti par voyage rapide.')
                                        continue
                                    remote.notify(f"V est bloque dans un secteur depuis plusieurs minutes, voyage rapide impossible ({rft2.get('reason')}) : il change de cible.")
                                    navp.displaced()                         # coince ici : aucun verdict differe n est fiable
                                    nxts = quests.switch(alt_target.get('hash') if alt_target else q.get('hash'), log=_log, avoid=(tx2, ty2, navprog.SECTOR_M))
                                    if nxts:
                                        alt_target = _alt(nxts)
                                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                        path_failures = 0; straight_tried = False
                                        time.sleep(1.0); continue
                                    nxes = explore.pick(log=_log)
                                    if nxes:
                                        alt_target = _alt(nxes)
                                        stats['explorations'] = stats.get('explorations', 0) + 1
                                        path_failures = 0; straight_tried = False
                                        time.sleep(1.0); continue
                                    _log('secteur bloque, aucun voyage/quete/exploration possible : arret.')
                                    break
                                path_failures = 5; straight_tried = True   # force le passage aux branches suivantes (insister / voyage rapide / quete)
                                continue
                            continue
                        if path_failures >= 5:
                            # 12/10 : echelle PAR CIBLE. Le 05/10 19:55-20:14, « on insiste » 3 fois a (-396,720) : 45 s d attente
                            # a chaque fois, jamais le vehicule ni le voyage rapide annonces (cible a 100 m : ils ne s ouvraient
                            # qu au-dela de 500 m), puis tout recommencait. Desormais :
                            #   1. voyage rapide vers un point plus proche de la cible (une fois par cible / 20 min) ;
                            #   2. quete assignee : UNE courte pause (porte, foule) ;
                            #   3. deux cibles ELOIGNEES l une de l autre epuisees depuis ce secteur = V coince : aucun verdict,
                            #      voyage rapide de sortie, sinon pause + Telegram ;
                            #   4. sinon verdict pour CETTE cible et V passe a une cible a plus de 60 m.
                            if _halted():
                                continue
                            path_failures = 0; straight_tried = False
                            ex_old = navp.exhaust.get(k_p)
                            ex_n = navp.exhausted(k_p)
                            gain = navprog.ft_gain(dist)
                            island = escape_fail[0] == k_p and time.perf_counter() - escape_fail[1] < 300.0
                            if island and dist is not None:
                                # ilot avere : en plus des points plus proches, tout point a moins de 150 m de la cible ; toujours
                                # negatif (mode sortie de fast_travel_to : point a plus de 30 m de V)
                                gain = min(gain if gain is not None else 1e9, dist - 150.0, -0.001)
                            if gain is not None and CFG.features.get('fasttravel', True) and navp.ft_ready(k_p):
                                _log(f'objectif inaccessible a pied d ici (cycle {ex_n}) : voyage rapide '
                                     + ('(ilot : point a moins de 150 m de la cible)' if island else 'vers un point plus proche'))
                                rft = nav.fast_travel_to(tx_p, ty_p, log=_log, min_gain_m=gain, stop=stop)
                                if rft.get('ok') and _halted():
                                    navp.displaced(); continue
                                if _halted(rft):                     # F11/F12 : rien de consomme
                                    if ex_old is None:
                                        navp.exhaust.pop(k_p, None)
                                    else:
                                        navp.exhaust[k_p] = ex_old
                                    continue
                                navp.ft_mark(k_p)
                                if rft.get('ok'):
                                    stats['voyages'] = stats.get('voyages', 0) + 1
                                    navp.displaced()
                                    continue
                                _log(f"  voyage rapide : {rft.get('reason')}")
                                s_n = motion.read_state() or s_h
                                if math.hypot(s_n['x'] - here[0], s_n['y'] - here[1]) > navprog.MOVED_M:
                                    # 12/10 : le voyage a echoue mais V a MARCHE (vers une borne) : on reprend d ici
                                    for _v in navp.after_leg(here, (s_n['x'], s_n['y'], s_n.get('z')), False):
                                        quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                                    navp.reset()
                                    continue
                            if focus and navp.pause_ready(k_p):
                                navp.pause_mark(k_p)
                                _log('objectif inaccessible pour l instant, mais c est la quete assignee : courte pause (45 s) puis nouvel essai')
                                t_w = time.perf_counter()
                                while time.perf_counter() - t_w < 45.0 and not _halted():
                                    time.sleep(1.0)
                                if _halted():
                                    navp.paused.pop(k_p, None)
                                navp.reset()
                                continue
                            free_now = False                         # V vient de prouver qu il peut sortir a pied
                            trap = navp.sector(k_p, (tx_p, ty_p), here)
                            if trap:
                                # 12/10 : avant de conclure « V coince », le VERIFIER : retour a pied vers un endroit deja atteint
                                # a pied hors du secteur. S il y arrive, il n etait pas coince (rues denses, interieurs fermes).
                                op = navp.outside_point()
                                if op is not None:
                                    _log(f'V coince ? verification : retour a pied vers ({op[0]:.0f},{op[1]:.0f})')
                                    sec0 = navp.sec_pos
                                    rpv = nav.goto(lambda p=op: nav.request_path_to(p[0], p[1]), arrive_m=5.0, max_legs=3,
                                                   timeout=90.0, stop=stop, log=_log, interrupt=_threat_near)
                                    if _halted(rpv):
                                        continue
                                    if rpv.get('reason') == 'interrompu':      # menace en route : on decide, on reviendra ici
                                        plan.last_t = -99.0; continue
                                    s_v = motion.read_state() or s_h
                                    if math.hypot(s_v['x'] - sec0[0], s_v['y'] - sec0[1]) > navprog.SECTOR_M:
                                        _log('  V est ressorti a pied : il n etait pas coince, les cibles sont bien inaccessibles')
                                        for _v in navp.after_leg(here, (s_v['x'], s_v['y'], s_v.get('z')), False):
                                            quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                                        free_now, trap = True, False
                                    else:
                                        here = (s_v['x'], s_v['y'])
                            if trap:
                                # V est coince ICI (ilot, piece fermee, ascenseur hors maillage) : aucune cible n est en cause
                                _log(f'V coince dans ce secteur ({here[0]:.0f},{here[1]:.0f}) : {len(navp.sec_targets)} cibles eloignees inaccessibles d ici -> c est la position, pas les cibles')
                                if CFG.features.get('fasttravel', True) and navp.trap_ft_ready():
                                    rft3 = nav.fast_travel_to(tx_p, ty_p, log=_log, min_gain_m=-1e9, stop=stop)
                                    if rft3.get('ok') and _halted():
                                        navp.displaced(); continue
                                    if _halted(rft3):
                                        continue
                                    navp.trap_ft_mark()
                                    if rft3.get('ok'):
                                        stats['voyages'] = stats.get('voyages', 0) + 1
                                        navp.displaced()
                                        remote.notify('V etait coince : sorti par voyage rapide.')
                                        continue
                                    _log(f"  voyage rapide de sortie : {rft3.get('reason')}")
                                    s_n = motion.read_state() or s_h
                                    _sp0 = navp.sec_pos or here
                                    if math.hypot(s_n['x'] - _sp0[0], s_n['y'] - _sp0[1]) > navprog.SECTOR_M:
                                        # V a marche hors du secteur (vers une borne) : pas coince, verdicts normaux
                                        for _v in navp.after_leg(here, (s_n['x'], s_n['y'], s_n.get('z')), False):
                                            quests.marker_result(_v[1], _v[2], _v[3], False, 'inaccessible a pied', z=_v[4], log=_log)
                                        free_now, trap = True, False
                                    else:
                                        here = (s_n['x'], s_n['y'])
                            if trap:
                                navp.trapped()
                                navp.trap_reset()
                                remote.notify(f'V est coince en ({here[0]:.0f},{here[1]:.0f}) : plusieurs cibles inaccessibles d ici, pas de voyage rapide possible. '
                                              + ('Mis en pause : deplace-le puis F11 ou « reprendre ».' if pause is not None else 'Session arretee.'))
                                if pause is not None:
                                    _log('V coince, aucune sortie : PAUSE (deplace-le puis F11 ou « reprendre »)')
                                    pause.set()
                                    continue
                                _log('V coince, aucune sortie : arret.')
                                break
                            v_text = str(alt_target.get('text') or '') if alt_target is not None else ''
                            if alt_target is None:                 # quete suivie : mise de cote en memoire de session
                                w_u = navp.mark_unreachable(k_p)
                                if not free_now:
                                    navp.defer(('suivie', k_p))
                                _log(f"quete suivie « {q.get('text')} » inaccessible a pied d ici (cycle {ex_n}) : mise de cote {w_u / 60:.0f} min")
                                if k_p not in navp.notified:
                                    navp.notified.add(k_p)
                                    remote.notify(f"Quete suivie « {q.get('text')} » inaccessible a pied d ici : V fait autre chose {w_u / 60:.0f} min.")
                            elif v_text.endswith('Variant') and free_now:   # V vient de prouver qu il n etait pas coince
                                quests.marker_result(v_text, tx_p, ty_p, False, 'inaccessible a pied', z=alt_target.get('z'), log=_log)
                            elif v_text.endswith('Variant'):        # marqueur choisi par V : verdict ecrit si V n etait pas coince
                                navp.defer(('marqueur', v_text, tx_p, ty_p, alt_target.get('z')))
                                _log(f'marqueur « {v_text} » inaccessible a pied d ici (cycle {ex_n}) : changement de cible')
                            else:
                                _log(f'cible « {v_text} » inaccessible a pied d ici (cycle {ex_n}) : changement de cible')
                            _qa = (q.get('mx'), q.get('my'), 10.0) if navp.unreach_wait(_tid(None, q)) > 0 else None
                            nxt = quests.switch(alt_target.get('hash') if alt_target else q.get('hash'), log=_log, avoid=[(tx_p, ty_p, 60.0), _qa])
                            if nxt:
                                alt_target = _alt(nxt)
                                stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                                time.sleep(1.0); continue
                            nxe = explore.pick(log=_log)
                            if nxe:
                                alt_target = _alt(nxe)
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
                        alt_target = _alt(nxt)
                        stats['changements_quete'] = stats.get('changements_quete', 0) + 1
                        time.sleep(1.0); continue
                    nxe = explore.pick(log=_log)
                    if nxe:
                        alt_target = _alt(nxe)
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
        remote.bind(None, None)            # entre deux sessions : plus d evenement perime a armer
        try:
            dialog.unload_model()
            radio.turn_off(log=_log)
        except Exception as _e:
            _log(f'  [fin] nettoyage : {_e}')
        stats['seconds'] = time.perf_counter() - t0
        if money_start is not None:
            stats['eddies_gagnes'] = inventory.MONEY - money_start
        _log(f'=== fin : {stats} ===')
        hud.update(ended=True, paused=False, mode='SESSION TERMINEE')
        hud.flush()
        _rep = _session_report(stats, t0, money_start, ach_start, final=True)
        _log(_rep.replace('\n', ' | '))
        try:
            from . import narrator
            _mvp = youtube_chat.top(1)
            narrator.say(f"BILAN : {stats.get('combats', 0)} COMBATS, ~{combat.KILLS[0]} NEUTRALISES, {stats.get('morts', 0)} MORTS, "
                         f"{stats.get('km', 0.0):.1f} KM." + (f" MVP DU CHAT : {narrator.clean(_mvp[0][0], 16).upper()} ({_mvp[0][1]} PTS)."
                                                              if _mvp and narrator.clean(_mvp[0][0], 16) else '') + " I'LL BE BACK.", force=True)
            hud.flush()
        except Exception:
            pass
        remote.notify(_rep)
    return stats
