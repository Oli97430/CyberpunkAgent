"""Profil par personnage (19/10, Olivier : « 2 ») : la memoire de V etait commune a tous les personnages. Ce qui depend
de la PARTIE (terminaux deja pirates, lieux de mort, marqueurs de quete « sans interet », suivi du charcudoc) va maintenant
dans %APPDATA%/CyberpunkAgent/profils/<personnage>/ ; ce qui depend de la VILLE (marchands, charcudocs avec du stock,
statistiques de transport) et du compte (succes Steam, chat, config) reste partage.

Personnage = parcours de vie + corps (ex. « nomade_femme »), lu dans le jeu par la commande `char_id` du mod. Deux
personnages de meme parcours et meme corps : config.json {"profil": "nom"} pour les separer. Le premier profil cree
herite de la memoire existante (c est le personnage joue jusqu ici)."""
from __future__ import annotations

import json
import re
import shutil

from .config import DATA_DIR

PROFILE_FILES = ('deaths.json', 'markers_memory.json', 'terminals_skip.json', 'ripperdoc_state.json')
PROFILES_DIR = DATA_DIR / 'profils'
LIFEPATH = {'nomad': 'nomade', 'streetkid': 'gamin_des_rues', 'corporate': 'corpo'}
STATE = {'id': None, 'dir': None}


def _override() -> str | None:
    try:
        d = json.loads((DATA_DIR / 'config.json').read_text(encoding='utf-8'))
        v = str(d.get('profil') or '').strip()
        return v or None
    except Exception:
        return None


def make_id(lifepath: str | None, gender: str | None) -> str | None:
    lp = (lifepath or '').lower().replace(' ', '')
    lp_name = next((v for k, v in LIFEPATH.items() if k in lp), None)
    g = (gender or '').lower()
    g_name = 'femme' if 'female' in g or 'femme' in g else ('homme' if 'male' in g or 'homme' in g else None)
    if not lp_name or not g_name:
        return None
    return f'{lp_name}_{g_name}'


def _safe(name: str) -> str:
    return re.sub(r'[^a-z0-9_-]+', '_', name.lower()).strip('_')[:40] or 'defaut'


def activate(log=print) -> str | None:
    """Lit le personnage dans le jeu et rebranche les memoires de partie sur son dossier. None si le mod ne repond pas."""
    from . import nav
    pid = _override()
    if not pid:
        r = nav._wait(nav._send({'cmd': 'char_id'}), timeout=3.0) or {}
        if not r.get('ok'):
            return None
        pid = make_id(r.get('lifepath'), r.get('gender'))
        if not pid:
            log(f"  [profil] personnage illisible (parcours {r.get('lifepath')}, corps {r.get('gender')}) : memoire commune")
            return None
    pid = _safe(pid)
    first = not PROFILES_DIR.exists() or not any(PROFILES_DIR.iterdir())
    d = PROFILES_DIR / pid
    new = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    if new and first:
        moved = []
        for f in PROFILE_FILES:
            src = DATA_DIR / f
            if src.exists():
                shutil.copy2(src, d / f)
                moved.append(f)
        log(f"  [profil] premier profil « {pid} » : il herite de la memoire actuelle ({', '.join(moved) or 'rien'})")
    _repoint(d)
    STATE.update(id=pid, dir=d)
    log(f"  [profil] personnage « {pid} »{' (nouveau : memoire de partie vierge)' if new and not first else ''} : "
        f"terminaux, morts, marqueurs et charcudoc propres a ce personnage ; ville et succes partages")
    return pid


def _repoint(d) -> None:
    """Rebranche les modules sur les fichiers du profil et recharge ce qu ils gardent en memoire."""
    from . import quests, terminals, vendor
    quests._DEATHS = d / 'deaths.json'
    quests._danger[:] = quests._load_danger()
    quests.MEMORY_FILE = d / 'markers_memory.json'
    quests._mem = None
    terminals.SKIP_FILE = d / 'terminals_skip.json'
    try:
        sk = json.loads(terminals.SKIP_FILE.read_text(encoding='utf-8')) if terminals.SKIP_FILE.exists() else {}
        terminals._skip = sk if isinstance(sk, dict) else {}
    except Exception:
        terminals._skip = {}
    vendor.RIPPER_FILE = d / 'ripperdoc_state.json'
    vendor._ripper = None
