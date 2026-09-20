"""
remote.py -- competence "recevoir des directives" : V peut etre redirige en direct, sans attendre
qu il ait fini ce qu il fait, depuis deux canaux qui alimentent la MEME file :
  - in-game (CET) : champ texte du panneau existant (mod/AgentProbe/init.lua), lu via la commande
    get_directive (meme bridge que toutes les autres commandes -- pas de nouveau fichier a surveiller).
  - Telegram (optionnel) : si %APPDATA%/CyberpunkAgent/telegram.json existe avec un token + chat_id,
    un thread interroge l API Telegram (long polling) et met les messages recus dans la meme file.
Le token Telegram n est JAMAIS demande ni affiche ici : l utilisateur remplit lui-meme le fichier.
brain.py n a qu a appeler poll() une fois par tour de boucle et interpreter le texte recu.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse
import urllib.request

from . import nav
from .config import DATA_DIR

TELEGRAM_FILE = DATA_DIR / 'telegram.json'   # {"token": "...", "chat_id": "..."} -- jamais commis au depot

_Q: queue.Queue = queue.Queue()
_telegram_started = False


def _cet_poll(log=print) -> None:
    r = nav._wait(nav._send({'cmd': 'get_directive'}), timeout=3.0)
    txt = (r or {}).get('text') or ''
    if txt.strip():
        _Q.put(txt.strip())
        log(f"  [directive] recue (jeu) : « {txt.strip()} »")


def _telegram_loop(token: str, chat_id: str, log) -> None:
    base = f'https://api.telegram.org/bot{token}'
    offset = 0
    log('  [telegram] connecte, en ecoute')
    while True:
        try:
            url = f'{base}/getUpdates?timeout=25&offset={offset}'
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            for upd in data.get('result', []):
                offset = upd['update_id'] + 1
                msg = upd.get('message') or {}
                if str((msg.get('chat') or {}).get('id')) != str(chat_id):
                    continue                                  # ignore tout autre chat/utilisateur
                txt = (msg.get('text') or '').strip()
                if txt:
                    _Q.put(txt)
                    log(f"  [directive] recue (telegram) : « {txt} »")
        except Exception as e:
            log(f'  [telegram] erreur : {e}'); time.sleep(5.0)


def notify(text: str) -> None:
    """Compte-rendu a l utilisateur sur Telegram, si configure (silencieux sinon)."""
    try:
        cfg = json.loads(TELEGRAM_FILE.read_text(encoding='utf-8-sig'))   # utf-8-sig : tolere un BOM (Notepad/PowerShell)
        token, chat_id = cfg.get('token'), cfg.get('chat_id')
    except Exception:
        return
    if not (token and chat_id):
        return
    try:
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text[:4000]}).encode('utf-8')
        urllib.request.urlopen(url, data=data, timeout=8)
    except Exception:
        pass


def ensure_started(log=print) -> None:
    """A appeler une fois au demarrage : lance le thread Telegram si telegram.json est rempli."""
    global _telegram_started
    if _telegram_started:
        return
    _telegram_started = True
    try:
        cfg = json.loads(TELEGRAM_FILE.read_text(encoding='utf-8-sig'))   # utf-8-sig : tolere un BOM (Notepad/PowerShell)
        token, chat_id = cfg.get('token'), cfg.get('chat_id')
    except Exception:
        log(f'  [telegram] non configure ({TELEGRAM_FILE.name} absent) : desactive, directives in-game seulement')
        return
    if not (token and chat_id):
        log('  [telegram] token/chat_id manquant dans telegram.json : desactive')
        return
    threading.Thread(target=_telegram_loop, args=(token, chat_id, log), daemon=True).start()


def poll(log=print) -> str | None:
    """A appeler une fois par tour de boucle : lit le panneau in-game, puis renvoie la prochaine
    directive en attente (jeu ou Telegram) s il y en a une."""
    _cet_poll(log=log)
    try:
        return _Q.get_nowait()
    except queue.Empty:
        return None
