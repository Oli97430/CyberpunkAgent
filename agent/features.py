"""Ce qui a VRAIMENT marche en jeu (19/10, Olivier : « 1 ») : chaque fonction est reconnue a sa ligne de journal ; le bilan de
session liste celles qui se sont declenchees (combien de fois) et celles restees a 0. Cumul entre sessions dans
features_seen.json (premiere et derniere fois vue) : une fonction jamais vue reste « a valider en jeu »."""
from __future__ import annotations

import json
import re
import time

from .config import DATA_DIR

SEEN_FILE = DATA_DIR / 'features_seen.json'

# (cle, libelle, motif ancre sur la ligne du journal sans indentation) -- lignes INTERNES seulement (voir EXTERNAL)
FEATURES: list[tuple[str, str, str]] = [
    ('vote', 'vote du chat', r'^\[youtube\] VOTE : '),
    ('sondage', 'mission choisie par le chat', r'^\[quetes\] mission choisie par le chat'),
    ('soutien', 'remerciement Super Chat / membre', r'^\[fete\] '),
    ('clip', 'clip OBS enregistre', r'^\[clips\] clip '),
    ('short', 'Short vertical genere', r'^\[shorts\] pret'),
    ('cyberpsycho', 'cyberpsycho trouve', r'^\[cyberpsycho\] contact'),
    ('kerenzikov', 'rafale Kerenzikov', r'^\[succes\] Tu es deja mort : rafale'),
    ('gunfu', 'Gun Fu', r'^\[succes\] Gun Fu'),
    ('rats', 'leurre (Surement des rats)', r'^\[succes\] Surement des rats'),
    ('revanche', 'revanche sur un lieu de mort', r'^\[revanche\] '),
    ('arrivee', 'arret avant un groupe d ennemis', r'ennemis a moins de 60 m .* descend'),
    ('vol', 'voiture volee', r'^\[vol\] la voiture volee roule'),
    ('vente', 'vente au marchand', r'^vente : [1-9]'),
    ('charcudoc', 'implant pose', r'^\[charcudoc\] POSE '),
    ('quickhack', 'quickhack lance', r'^\[combat\] quickhack « [^»]+ » (?!jamais)'),
    ('succes', 'succes Steam debloque', r'^\[succes\] DEBLOQUE'),
    ('relance', 'relance apres crash', r'^\[relance\] V est de retour'),
]
EXTERNAL = ('[telegram]', '[sms]', '[directive]', '[dialog]', 'DIRECTIVE', '"')
_RX = [(k, lab, re.compile(p)) for k, lab, p in FEATURES]
COUNTS: dict[str, int] = {}


def reset() -> None:
    COUNTS.clear()


def feed(line: str) -> None:
    if any(t in line for t in EXTERNAL):
        return
    # [youtube] : seules les lignes produites par l agent (VOTE / sondage), jamais le texte d un spectateur
    if line.startswith('[youtube]') and not line.startswith('[youtube] VOTE : '):
        return
    for k, _, rx in _RX:
        if rx.search(line):
            COUNTS[k] = COUNTS.get(k, 0) + 1
            return


def _load() -> dict:
    try:
        d = json.loads(SEEN_FILE.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save() -> dict:
    """Cumul entre sessions : {cle: {'n': total, 'first': date, 'last': date}}."""
    seen = _load()
    today = time.strftime('%Y-%m-%d %H:%M')
    for k, n in COUNTS.items():
        e = seen.get(k) if isinstance(seen.get(k), dict) else {}
        seen[k] = {'n': int(e.get('n') or 0) + n, 'first': e.get('first') or today, 'last': today}
    try:
        SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception:
        pass
    return seen


def report(seen: dict | None = None) -> str:
    """Texte du bilan : vu cette session / jamais vu (meme les sessions d avant)."""
    seen = _load() if seen is None else seen
    ok = [f'{lab} x{COUNTS[k]}' for k, lab, _ in _RX if COUNTS.get(k)]
    zero = [lab for k, lab, _ in _RX if not COUNTS.get(k)]
    never = [lab for k, lab, _ in _RX if not COUNTS.get(k) and not (seen.get(k) or {}).get('n')]
    out = ['VALIDE EN JEU cette session : ' + (', '.join(ok) if ok else 'rien')]
    if zero:
        out.append('pas declenche cette session : ' + ', '.join(zero))
    if never:
        out.append('JAMAIS VU EN JEU (a valider) : ' + ', '.join(never))
    return '\n'.join(out)
