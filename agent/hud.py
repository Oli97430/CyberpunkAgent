"""
hud.py -- HUD in-game facon Terminator : l etat de l IA (mode, directive prioritaire, derniere decision, dernier
ordre recu, bilan de session, modele, temperament) est publie dans la table SQLite `hud` du mod, SEPAREE de la
table de commandes (emplacement unique : y ecrire ecraserait une vraie commande en attente). Le mod la relit
2 fois par seconde et l affiche par-dessus le jeu, avec les donnees vivantes (vie, menaces, pertes).

L ecriture se fait sur un fil de fond, une fois par seconde : le HUD reste vivant pendant les longues competences
(combat, danse sensorielle, conduite) qui bloquent la boucle principale. Le mod declare la liaison « perdue » si
plus rien n arrive depuis 8 s (agent arrete).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import unicodedata

from . import nav

STATE: dict = {}
_lock = threading.Lock()
_started = False


def ascii_up(s) -> str:
    """Police du HUD sans accents : on translittere (« Rechercher l indice » -> « RECHERCHER L INDICE »)."""
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')
    return s.upper()


def update(**kw) -> None:
    with _lock:
        STATE.update(kw)


def _write() -> None:
    with _lock:
        payload = dict(STATE)
    payload['ts'] = time.time()
    con = sqlite3.connect(str(nav.DB_FILE), timeout=0.2)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS hud (id INTEGER PRIMARY KEY, json TEXT)')
        con.execute('INSERT OR REPLACE INTO hud (id, json) VALUES (1, ?)', (json.dumps(payload, ensure_ascii=True),))
        con.commit()
    finally:
        con.close()


def _loop() -> None:
    while True:
        try:
            _write()
        except Exception:
            pass                      # base occupee un instant : on reessaie a la seconde suivante
        time.sleep(1.0)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()


def flush() -> None:
    """Ecriture immediate (fin de session : le fil de fond peut ne plus tourner)."""
    try:
        _write()
    except Exception:
        pass
