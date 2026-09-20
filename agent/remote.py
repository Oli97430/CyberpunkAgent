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
import re
import threading
import time
import urllib.parse
import urllib.request

from . import nav
from .config import DATA_DIR

TELEGRAM_FILE = DATA_DIR / 'telegram.json'   # {"token": "...", "chat_id": "..."} -- jamais commis au depot

_Q: queue.Queue = queue.Queue()
_screen = None   # capture.Screen() : cree a la demande (premiere « photo »), pas au demarrage (cout ~230 ms)


def _creds() -> tuple[str | None, str | None]:
    try:
        cfg = json.loads(TELEGRAM_FILE.read_text(encoding='utf-8-sig'))   # utf-8-sig : tolere un BOM (Notepad/PowerShell)
        return cfg.get('token'), cfg.get('chat_id')
    except Exception:
        return None, None
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
    """Compte-rendu a l utilisateur sur Telegram, si configure (silencieux sinon) -- V parle de lui-meme
    (mort, fin de combat, niveau, secteur bloque, fin de session), pas seulement en reponse a une directive."""
    token, chat_id = _creds()
    if not (token and chat_id):
        return
    try:
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text[:4000]}).encode('utf-8')
        urllib.request.urlopen(url, data=data, timeout=8)
    except Exception:
        pass


def screenshot_jpeg(quality: int = 70) -> bytes | None:
    """Capture d ecran immediate (dxcam, deja utilise par capture.py) encodee en JPEG, ou None si indisponible."""
    global _screen
    try:
        from . import capture
        if _screen is None:
            _screen = capture.Screen()
            time.sleep(0.2)                       # dxcam : la 1ere image apres start() peut etre vide
        frame = _screen.frame()
        if frame is None:
            return None
        return capture.Screen.to_jpeg(frame, quality=quality)
    except Exception:
        return None


def send_photo(image_bytes: bytes, caption: str = '') -> bool:
    """Envoie une image sur Telegram (multipart, sans dependance externe -- le projet reste 100% stdlib)."""
    token, chat_id = _creds()
    if not (token and chat_id):
        return False
    boundary = 'CyberpunkAgentBoundary'
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode('utf-8')]
    if caption:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:1000]}\r\n'.encode('utf-8'))
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="v.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode('utf-8'))
    parts.append(image_bytes)
    parts.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
    body = b''.join(parts)
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendPhoto', data=body,
                                  headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


def ensure_started(log=print) -> None:
    """A appeler une fois au demarrage : lance le thread Telegram si telegram.json est rempli."""
    global _telegram_started
    if _telegram_started:
        return
    _telegram_started = True
    token, chat_id = _creds()
    if not (token and chat_id):
        log(f'  [telegram] non configure ({TELEGRAM_FILE.name} illisible, absent ou incomplet) : desactive, directives in-game seulement')
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


COMMANDS = ('stop', 'pause', 'reprendre', 'attaque', 'objectif', 'marchand', 'charcudoc', 'explore',
            'changer_quete', 'va_a', 'status', 'photo', 'niveau', 'soigne', 'stats', 'courage', 'style', 'aggro')
# commandes qui prennent un parametre libre (lieu pour va_a, valeur pour courage/style/aggro)
PARAM_CMDS = {'va_a': 'lieu (marchand/charcudoc/point de voyage rapide connu)',
              'courage': 'prudent, equilibre ou temeraire',
              'style': 'melee, mixte ou distance',
              'aggro': 'defensif, normal ou chasseur'}


def classify(text: str, timeout: float = 6.0) -> str | None:
    """20/09 (Olivier : « trop basique ») : traduit une phrase libre (« va vendre ton bazar », « sois plus
    prudent ») vers la commande fixe la plus proche, via le modele local deja utilise pour les dialogues/
    SMS -- pas de nouvelle dependance. None si rien ne correspond (le modele est muet ou aucune commande ne va)."""
    numbered = '\n'.join(f'{i}: {c}' for i, c in enumerate(COMMANDS))
    params = '\n'.join(f'- {c} attend un parametre : {hint}' for c, hint in PARAM_CMDS.items())
    prompt = (
        'Tu traduis une instruction donnee a V (Cyberpunk 2077) vers UNE commande fixe.\n'
        f'Instruction : « {text} »\n'
        f'Commandes possibles :\n{numbered}\n'
        f'Certaines commandes attendent un parametre :\n{params}\n'
        'Reponds UNIQUEMENT en JSON : {"index": N, "param": "..."} (param vide si la commande n en attend pas). '
        'Si rien ne correspond a une commande, {"index": -1}.'
    )
    try:
        from . import llm_client
        txt = llm_client.chat([{'role': 'user', 'content': prompt}], temperature=0.1, max_tokens=40,
                               timeout=timeout, keep_alive='5m')
        m = re.search(r'"index"\s*:\s*(-?\d+)', txt)
        if not m:
            return None
        i = int(m.group(1))
        if i < 0 or i >= len(COMMANDS):
            return None
        cmd = COMMANDS[i]
        if cmd in PARAM_CMDS:
            pm = re.search(r'"param"\s*:\s*"([^"]*)"', txt)
            param = (pm.group(1) if pm else '').strip().lower()
            return f'{cmd} {param}' if param else None
        return cmd
    except Exception as e:
        from . import llm
        from .config import CFG
        if CFG.provider == 'ollama' and ('refus' in str(e) or '10061' in str(e) or 'refused' in str(e)):
            llm.ensure()
        return None
