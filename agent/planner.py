"""
planner.py -- couche de DECISION : V choisit lui-meme quoi faire.

Toutes les PERIOD_S secondes, ou des qu un evenement nouveau apparait (invite d interaction,
hostiles en vue, vie basse, objectif change), le modele local recoit un resume de la
situation et repond par UNE action parmi ACTIONS. Le cerveau l execute avec les
competences validees (aller_a, dialogue, combat, changer de quete).

Le modele ne pilote jamais les touches : il choisit, les competences agissent. En cas de
silence du modele, une regle de secours prudente s applique.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from .config import CFG

OLLAMA_CHAT = CFG.ollama_url + '/api/chat'
MODEL = CFG.model
KEEP_ALIVE = '5m'
PERIOD_S = 3.0

ACTIONS = ('objectif', 'parler', 'aborder', 'attaquer', 'secourir', 'eviter', 'changer_quete', 'attendre')

POLICE_AFF = ('ncpd', 'maxtac', 'police')


def aggressors(st: dict) -> list:
    """PNJ qui attaquent quelqu un (agressifs ou en combat) sans etre hostiles a V, hors police."""
    out = []
    for n in (st.get('npcs') or []):
        if (n.get('aggressive') or n.get('incombat')) and not any(p in (n.get('aff') or '').lower() for p in POLICE_AFF):
            out.append(n)
    return out

# PNJ qu on n aborde pas : passants generiques et forces de l ordre
GENERIC_NAMES = ('vendeur', 'vendor', 'marchand', 'ripperdoc', 'charcudoc', 'ripper', 'police officer', 'officer', 'police', 'cop', 'resident', 'résident', 'passant', 'habitant', 'civil', 'citoyen', 'vagabond', 'fetard', 'fêtard',
                 'client', 'ouvrier', 'ouvrière', 'ouvriere', 'policier', 'ncpd', 'agent', 'garde', 'securite', 'sécurité',
                 'sans-abri', 'sdf', 'employe', 'employé', 'employée', 'serveur', 'serveuse', 'technicien', 'infirmier',
                 'medic', 'trauma', 'militech', 'arasaka', 'nomade', 'gangster', 'maelstrom', 'tyger', 'valentino',
                 'animal', 'wraith', 'scav', 'sixth', 'moxes', 'voodoo', 'cybercuit', 'cadavre', 'corps', 'lockey')


# « activer » retire : une invite « Activer » suivait V partout (13 appuis sans effet en 3 min)
DOOR_WORDS = ('ouvrir', 'forcer', 'utiliser', 'pirater', 'ramasser', 'fouiller', 'prendre', 'monter', 'descendre',
              'appeler', 'lire', 'examiner', 'déverrouiller', 'deverrouiller', 'brancher', 'allumer', 'éteindre')


def named_npcs(st: dict) -> list:
    """PNJ proches dont le nom n est pas generique (donc potentiellement interessants)."""
    out = []
    for n in (st.get('npcs') or []):
        name = (n.get('name') or '').strip()
        low = name.lower()
        if not name or len(name) < 3 or any(g in low for g in GENERIC_NAMES):
            continue
        out.append(n)
    return out

PERSONA = (
    "Tu es V, mercenaire de Night City (Cyberpunk 2077). Tu joues seul, sans humain. "
    "Priorites : 1) faire avancer la quete suivie ; 2) parler aux gens quand une conversation "
    "est proposee et peut servir (infos, quete, marchand utile) ; 3) te battre seulement contre des "
    "hostiles qui te menacent ou te barrent la route, jamais contre des civils ni la police ; "
    "4) changer de quete si l actuelle est bloquee ou trop loin ; 5) ne pas gaspiller d argent."
)

RULES = (
    'Reponds UNIQUEMENT en JSON : {"action": "<une action>", "raison": "<10 mots max>"}. '
    'Actions possibles : "objectif" (aller vers le marqueur de quete), "parler" (accepter l interaction '
    'proposee), "attaquer" (engager les hostiles en vue), "changer_quete", "attendre".\n'
    'Consignes de decision :\n'
    '- Hostiles en vue a moins de 15 m : "attaquer" (ils t attaqueront sinon). Plus loin : "objectif".\n'
    '- Interaction proposee ("Parler", "Ouvrir"...) : "parler", sauf si des hostiles sont a moins de 15 m.\n'
    '- Quete sans marqueur, ou marqueur a plus de 3000 m, ou historique montrant un blocage : "changer_quete".\n'
    '- "attendre" seulement si tu es en train de te soigner (vie < 40 %) sans hostile en vue.\n'
    '- Dans tous les autres cas : "objectif".\n'
    'Exemples :\n'
    'Situation : hostiles en vue 2, le plus proche a 9 m ; interaction aucune -> {"action":"attaquer","raison":"hostiles proches"}\n'
    'Situation : vie 30 % ; quete sans marqueur ; hostiles 0 -> {"action":"changer_quete","raison":"objectif sans marqueur"}\n'
    'Situation : interaction Parler avec Resident ; hostiles 0 ; marqueur a 2600 m -> {"action":"parler","raison":"conversation proposee"}'
)


def _situation(st: dict, extra: dict) -> str:
    q = st.get('quest') or {}
    inter = st.get('interact') or {}
    hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead')
                and (e.get('z') is None or st.get('z') is None or abs(e['z'] - st['z']) < 3.5)]   # pas un autre etage
    lines = [
        f"Vie : {st.get('hp', 0):.0f} %. En combat : {'oui' if st.get('combat') else 'non'}.",
        f"Quete suivie : {q.get('text') or 'aucune'}"
        + (f" (marqueur a {extra.get('dist_m'):.0f} m)" if extra.get('dist_m') is not None else ' (pas de marqueur)') + '.',
        f"Interaction proposee : {inter.get('choices', ['aucune'])[0] if inter else 'aucune'}"
        + (f" avec « {inter.get('title')} »" if inter and inter.get('title') else '') + '.',
        f"Hostiles en vue : {len(hostiles)}" + (f", le plus proche a {hostiles[0]['d']:.0f} m" if hostiles else '') + '.',
        f"Historique recent : {extra.get('history') or 'rien'}.",
    ]
    return '\n'.join(lines)


def decide(st: dict, extra: dict, timeout: float = 5.0) -> tuple[str, str]:
    """Regles d abord pour les cas nets ; le modele n arbitre que le cas ouvert (parler ?)."""
    hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead')
                and (e.get('z') is None or st.get('z') is None or abs(e['z'] - st['z']) < 3.5)]   # pas un autre etage
    if st.get('combat'):
        return 'attaquer', 'regle : deja en combat'
    # POLICE : jamais engagee de notre initiative (legitime defense seulement, geree en combat)
    police = [e for e in hostiles if e.get('police')]
    hostiles = [e for e in hostiles if not e.get('police')]
    if police and not hostiles and police[0]['d'] < 25:
        return 'objectif', f"regle : police hostile a {police[0]['d']:.0f} m -> on ne provoque pas, on continue"
    hp0 = st.get('hp'); hp0 = 100.0 if hp0 is None else hp0
    near45 = [e for e in hostiles if e['d'] < 45]
    # V n est pas un couard : il evite seulement les groupes vraiment trop gros pour un solo (ou quand il
    # est deja bien entame). 2 a 5 hostiles = il se bat (il a gagne ces combats), 6+ = trop.
    from .config import CFG as _C
    T_AVOID, T_AVOID_HURT, _, T_RESCUE_HP, ENGAGE_R = _C.courage_t
    if len(near45) >= T_AVOID or (len(near45) >= T_AVOID_HURT and hp0 < 60) or (len(near45) >= T_AVOID_HURT - 1 and hp0 < 35):
        return 'eviter', f'regle : {len(near45)} hostiles a < 45 m, vie {hp0:.0f} % -> zone trop dangereuse'
    if _C.aggro == 'defensif':
        hostiles_engage = [e for e in hostiles if e['d'] < 8]           # defensif : seulement au contact
    elif _C.aggro == 'chasseur':
        hostiles_engage = [e for e in hostiles if e['d'] < max(ENGAGE_R, 45)]
    else:
        hostiles_engage = [e for e in hostiles if e['d'] < ENGAGE_R]
    if hostiles_engage:
        hp = st.get('hp'); hp = 100.0 if hp is None else hp
        return 'attaquer', f"regle : hostile a {hostiles_engage[0]['d']:.0f} m ({len(hostiles)} en vue, {_C.aggro}/{_C.courage})"
    # AGRESSION a proximite : V peut choisir de jouer les sauveurs -> le modele tranche
    hp = st.get('hp'); hp = 100.0 if hp is None else hp
    aggr = aggressors(st)
    crimes = st.get('crimes') or []
    # un simple marqueur d agression a 60-80 m sans agresseur visible = souvent deja fini (2 x 1 min perdues le 13/09) :
    # on n y va que si des agresseurs sont VISIBLES, ou si le marqueur est tout proche (< 35 m)
    crimes = [c for c in crimes if (c.get('d') or 99) < 35] if not aggr else crimes
    if (aggr or crimes) and hp >= 60 and not st.get('combat') and extra.get('rescued', lambda k: False)(aggr, crimes) is False:
        n_aggr = len(aggr)
        near = min([a['d'] for a in aggr] + [c['d'] for c in crimes])
        from . import quests as _q
        spots = [(a['x'], a['y']) for a in aggr] + [(c['x'], c['y']) for c in crimes if c.get('x') is not None]
        if any(_q.near_danger(x, y) for x, y in spots):
            return 'objectif', 'regle : agression dans une zone deja jugee trop dangereuse -> on passe'
        if (n_aggr <= 3 and hp >= T_RESCUE_HP) or (n_aggr <= 5 and hp >= 90):   # sauvetage selon le courage ; les renforts arrivent souvent
            return _decide_rescue(st, extra, n_aggr, near, timeout)
    if extra.get('dist_m') is None or (extra['dist_m'] > 3000 and not _C.focus_tracked):
        return 'changer_quete', 'regle : objectif sans marqueur ou trop loin'
    if not st.get('interact'):
        cands = [n for n in named_npcs(st) if n['d'] < 12 and extra.get('approached', lambda k: False)(n) is False]
        if cands:
            return 'aborder', f"regle : PNJ nomme « {cands[0].get('name')} » a {cands[0]['d']:.0f} m"
        return 'objectif', 'regle : rien d autre a faire ici'
    # portes, dispositifs, objets : on accepte d office (pas besoin du modele)
    first = ((st.get('interact') or {}).get('choices') or [''])[0].lower()
    if any(k in first for k in ('saisir', 'porter', 'soulever')):
        return 'objectif', 'regle : on ne porte pas de corps'
    # vehicules : « Enfourcher », « Monter dans », « Prendre le controle » -> c est la competence conduite
    # (driving.py) qui s en occupe, jamais une « conversation »
    title = str((st.get('interact') or {}).get('title') or '').lower()
    my_vehicles = [str(v.get('name') or '').lower() for v in (st.get('vehicles') or []) if v.get('player')]
    if any(k in first for k in ('enfourcher', 'prendre le contr', 'monter dans', 'monter a bord'))             or (title and any(n and n in title for n in my_vehicles)):
        return 'objectif', f'regle : invite de vehicule « {first} », pas une conversation'
    if any(k in first for k in DOOR_WORDS):
        return 'parler', f'regle : dispositif « {first} »'
    # cas ouvert : une interaction est proposee -> le modele tranche parler / objectif
    return _decide_llm(st, extra, timeout)


def _decide_rescue(st: dict, extra: dict, n_aggr: int, near: float, timeout: float) -> tuple[str, str]:
    """V decide s il intervient dans une agression proche. Regle de secours : oui si en forme et <= 2 agresseurs."""
    hp = st.get('hp'); hp = 100.0 if hp is None else hp
    q = (st.get('quest') or {}).get('text') or 'aucune'
    situation = (f"Une agression a lieu a {near:.0f} m : {n_aggr} agresseur(s) visibles (aucun ne t attaque pour l instant). "
                 f"Ta vie : {hp:.0f} %. Ta quete en cours : {q}. Historique : {extra.get('history') or 'rien'}.\n"
                 'Interviens-tu pour secourir la victime ? Reponds UNIQUEMENT en JSON : '
                 '{"action": "secourir"} ou {"action": "objectif"}, avec "raison".')
    msgs = [{'role': 'system', 'content': PERSONA + ' Tu as le coeur sur la main mais tu n es pas suicidaire.'},
            {'role': 'user', 'content': situation}]
    try:
        from . import llm_client
        txt = llm_client.chat(msgs, temperature=0.4, max_tokens=50, timeout=timeout, keep_alive=KEEP_ALIVE)
        m = re.search(r'"action"\s*:\s*"([a-z_]+)"', txt)
        why = re.search(r'"raison"\s*:\s*"([^"]{0,120})"', txt)
        if m and m.group(1) in ('secourir', 'objectif'):
            return m.group(1), 'V decide : ' + (why.group(1) if why else '')
    except Exception:
        pass
    return ('secourir' if (hp >= 70 and n_aggr <= 2) else 'objectif'), 'regle de secours (modele muet)'


def _decide_llm(st: dict, extra: dict, timeout: float) -> tuple[str, str]:
    situation = _situation(st, extra)
    msgs = [{'role': 'system', 'content': PERSONA + '\n' + RULES},
            {'role': 'user', 'content': situation}]
    try:
        from . import llm_client
        txt = llm_client.chat(msgs, temperature=0.2, max_tokens=60, timeout=timeout, keep_alive=KEEP_ALIVE)
        m = re.search(r'"action"\s*:\s*"([a-z_]+)"', txt)
        why = re.search(r'"raison"\s*:\s*"([^"]{0,120})"', txt)
        if m and m.group(1) in ACTIONS:
            act = m.group(1)
            hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead')
                and (e.get('z') is None or st.get('z') is None or abs(e['z'] - st['z']) < 3.5)]   # pas un autre etage
            if act == 'attendre' and hostiles and hostiles[0]['d'] < 15:
                return 'attaquer', 'garde-fou : hostiles proches'
            if act == 'attendre' and (st.get('hp') or 100) >= 40:
                return fallback(st, extra), 'garde-fou : attendre injustifie'
            return act, (why.group(1) if why else '')
    except Exception as e:
        from . import llm
        if 'refus' in str(e) or '10061' in str(e) or 'refused' in str(e):
            llm.ensure()
        return fallback(st, extra), f'modele muet ({str(e)[:60]})'
    return fallback(st, extra), 'reponse invalide -> regle'


def fallback(st: dict, extra: dict) -> str:
    """Regle prudente si le modele ne repond pas."""
    hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead')
                and (e.get('z') is None or st.get('z') is None or abs(e['z'] - st['z']) < 3.5)]   # pas un autre etage
    if st.get('combat'):
        return 'attaquer'
    if hostiles and hostiles[0]['d'] < 15 and not all(e.get('police') for e in hostiles):
        return 'attaquer'
    if st.get('interact'):
        return 'parler'
    if extra.get('dist_m') is None or extra['dist_m'] > 3000:
        return 'changer_quete'
    return 'objectif'


class Planner:
    def __init__(self) -> None:
        self.last_t = -99.0
        self.last_sig = None
        self.action = 'objectif'
        self.reason = ''
        self.history: list[str] = []

    def note(self, event: str) -> None:
        self.history.append(event)
        del self.history[:-5]

    def maybe_decide(self, st: dict, extra: dict, log=print) -> str:
        """Redecide si PERIOD_S ecoulees ou si la situation a change de nature."""
        inter = st.get('interact') or {}
        hostiles = [e for e in (st.get('enemies') or []) if not e.get('dead')
                and (e.get('z') is None or st.get('z') is None or abs(e['z'] - st['z']) < 3.5)]   # pas un autre etage
        sig = (bool(inter), len(hostiles) > 0, bool(st.get('combat')), (st.get('quest') or {}).get('text'),
               (st.get('hp') or 100) < 40)
        now = time.perf_counter()
        if sig != self.last_sig or now - self.last_t > PERIOD_S:
            self.last_sig, self.last_t = sig, now
            extra = dict(extra, history=' ; '.join(self.history[-3:]))
            t0 = time.perf_counter()
            self.action, self.reason = decide(st, extra)
            log(f'  [decision] {self.action} — {self.reason} ({time.perf_counter() - t0:.1f} s)')
        return self.action
