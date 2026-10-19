"""Clips automatiques pour le stream (19/10, Olivier : « tout » -> 1) : a chaque moment fort (Gun Fu, succes debloque,
cyberpsycho, Kerenzikov, mort), V demande a OBS de sauvegarder sa memoire tampon de rediffusion (les N dernieres secondes)
puis renomme le fichier « V_<moment>_<date>.mkv » : des Shorts prets, sans retrier la video.

OBS : obs-websocket v5 (integre a OBS 28+, Outils > Parametres du serveur WebSocket, active). Le mot de passe est lu dans la
config d OBS (%APPDATA%/obs-studio/plugin_config/obs-websocket/config.json) ou dans clips.json ; jamais journalise.
La memoire tampon est demarree si elle ne l est pas (Parametres > Sortie > Tampon de rediffusion doit etre active dans OBS).
Configuration facultative : %APPDATA%/CyberpunkAgent/clips.json {"enabled": true, "host": "127.0.0.1", "port": 4455,
"password": "..."}. Lib standard seulement (socket + trame WebSocket minimale)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path

from .config import DATA_DIR

CLIPS_FILE = DATA_DIR / 'clips.json'
OBS_WS_CFG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'obs-studio' / 'plugin_config' / 'obs-websocket' / 'config.json'
MIN_GAP = 20.0             # un clip toutes les 20 s au plus (le tampon en couvre deja autant)

# (motif ancre du journal, nom du moment, delai avant la sauvegarde en s : le tampon garde le PASSE ; pour un combat qui
# commence (cyberpsycho), on attend qu il ait eu lieu)
TRIGGERS: list[tuple[str, str, float]] = [
    (r'^\[succes\] DEBLOQUE', 'succes', 2.0),
    (r'^\[succes\] Gun Fu', 'gunfu', 2.0),
    (r'^\[succes\] Tu es deja mort', 'kerenzikov', 3.0),
    (r'^\[cyberpsycho\] contact', 'cyberpsycho', 45.0),
    (r'^V EST MORT', 'mort', 0.0),
    (r'^\[succes\] Surement des rats', 'rats', 2.0),
]
_RX = [(re.compile(p), n, d) for p, n, d in TRIGGERS]
EXTERNAL = ('[youtube]', '[telegram]', '[sms]', '[directive]', '[dialog]', 'DIRECTIVE', '"')
STATUS = {'clips': 0, 'error': None, 'last': None}
_last = [0.0]
_lock = threading.Lock()


def _cfg() -> dict:
    d: dict = {}
    try:
        d = json.loads(CLIPS_FILE.read_text(encoding='utf-8'))
        d = d if isinstance(d, dict) else {}
    except Exception:
        d = {}
    if 'password' not in d or 'port' not in d:
        try:
            o = json.loads(OBS_WS_CFG.read_text(encoding='utf-8'))
            if o.get('auth_required', True) and o.get('server_password') and 'password' not in d:
                d['password'] = o['server_password']
            d.setdefault('port', int(o.get('server_port') or 4455))
            d.setdefault('enabled', bool(o.get('server_enabled', True)))
        except Exception:
            pass
    return d


# ---- WebSocket minimal (client, texte, sans fragmentation) ------------------------------------------------------------
def _ws_send(s: socket.socket, text: str) -> None:
    data = text.encode('utf-8')
    mask = os.urandom(4)
    hdr = bytes([0x81])
    n = len(data)
    if n < 126:
        hdr += bytes([0x80 | n])
    elif n < 65536:
        hdr += bytes([0x80 | 126]) + struct.pack('>H', n)
    else:
        hdr += bytes([0x80 | 127]) + struct.pack('>Q', n)
    s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _recv_n(s: socket.socket, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError('OBS a ferme la connexion')
        buf += c
    return buf


def _ws_recv(s: socket.socket) -> dict:
    while True:
        b1, b2 = _recv_n(s, 2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack('>H', _recv_n(s, 2))[0]
        elif n == 127:
            n = struct.unpack('>Q', _recv_n(s, 8))[0]
        mask = _recv_n(s, 4) if b2 & 0x80 else None
        data = _recv_n(s, n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        op = b1 & 0x0F
        if op == 0x8:
            raise ConnectionError('OBS a ferme la connexion')
        if op == 0x1:
            return json.loads(data.decode('utf-8'))


class Obs:
    """Session obs-websocket v5 : Hello -> Identify (authentification SHA-256) -> requetes."""

    def __init__(self, host: str = '127.0.0.1', port: int = 4455, password: str | None = None, timeout: float = 5.0):
        self.s = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n').encode())
        resp = b''
        while b'\r\n\r\n' not in resp:
            c = self.s.recv(1024)
            if not c:
                raise ConnectionError('poignee de main WebSocket refusee')
            resp += c
        if b' 101 ' not in resp.split(b'\r\n', 1)[0]:
            raise ConnectionError('OBS n a pas accepte la connexion WebSocket')
        hello = _ws_recv(self.s)
        ident = {'rpcVersion': 1, 'eventSubscriptions': 0}
        auth = (hello.get('d') or {}).get('authentication')
        if auth:
            if not password:
                raise PermissionError('OBS demande un mot de passe (clips.json ou config du serveur WebSocket)')
            secret = base64.b64encode(hashlib.sha256((password + auth['salt']).encode()).digest()).decode()
            ident['authentication'] = base64.b64encode(hashlib.sha256((secret + auth['challenge']).encode()).digest()).decode()
        _ws_send(self.s, json.dumps({'op': 1, 'd': ident}))
        r = _ws_recv(self.s)
        if r.get('op') != 2:
            raise PermissionError('OBS a refuse l identification (mot de passe ?)')
        self.n = 0

    def call(self, rtype: str, data: dict | None = None) -> dict:
        self.n += 1
        rid = f'v{self.n}'
        _ws_send(self.s, json.dumps({'op': 6, 'd': {'requestType': rtype, 'requestId': rid, **({'requestData': data} if data else {})}}))
        while True:
            r = _ws_recv(self.s)
            if r.get('op') == 7 and (r.get('d') or {}).get('requestId') == rid:
                d = r['d']
                st = d.get('requestStatus') or {}
                if not st.get('result'):
                    raise RuntimeError(f"{rtype} : {st.get('comment') or st.get('code')}")
                return d.get('responseData') or {}

    def close(self) -> None:
        try:
            self.s.close()
        except Exception:
            pass


def _connect() -> Obs | None:
    c = _cfg()
    if c.get('enabled') is False:
        return None
    return Obs(c.get('host') or '127.0.0.1', int(c.get('port') or 4455), c.get('password'))


def save(moment: str, log=print) -> str | None:
    """Sauvegarde le tampon de rediffusion et renomme le fichier ; chemin du clip ou None."""
    obs = None
    try:
        obs = _connect()
        if obs is None:
            return None
        if not obs.call('GetReplayBufferStatus').get('outputActive'):
            obs.call('StartReplayBuffer')
            log('  [clips] tampon de rediffusion d OBS demarre (le prochain moment fort sera enregistre)')
            return None
        obs.call('SaveReplayBuffer')
        path = None
        for _ in range(10):                                 # le fichier s ecrit apres la reponse
            time.sleep(1.0)
            try:
                path = obs.call('GetLastReplayBufferReplay').get('savedReplayPath')
            except Exception:
                path = None
            if path and os.path.exists(path):
                break
        STATUS['clips'] += 1
        if path and os.path.exists(path):
            new = os.path.join(os.path.dirname(path), f"V_{moment}_{time.strftime('%Y-%m-%d_%H-%M-%S')}{os.path.splitext(path)[1]}")
            for _ in range(5):
                try:
                    os.replace(path, new); path = new; break
                except OSError:
                    time.sleep(1.0)                         # encore ouvert par OBS
        STATUS['last'] = path
        log(f'  [clips] clip « {moment} » enregistre : {os.path.basename(path) if path else "?"}')
        return path
    except Exception as e:
        if STATUS.get('error') != str(e)[:120]:
            log(f'  [clips] OBS injoignable ou refus : {e}')
        STATUS['error'] = str(e)[:120]
        return None
    finally:
        if obs is not None:
            obs.close()


def moment_of(line: str) -> tuple[str, float] | None:
    """Ligne du journal -> (moment, delai) si c est un moment fort (lignes internes seulement)."""
    if any(t in line for t in EXTERNAL):
        return None
    for rx, name, delay in _RX:
        if rx.search(line):
            return name, delay
    return None


def feed(line: str, log=print) -> None:
    m = moment_of(line)
    if not m:
        return
    with _lock:
        now = time.time()
        if now - _last[0] < MIN_GAP:
            return
        _last[0] = now
    name, delay = m

    def run():
        if delay:
            time.sleep(delay)
        save(name, log=log)
    threading.Thread(target=run, daemon=True).start()
