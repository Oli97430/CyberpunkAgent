"""Sons du HUD T-800 (19/10, Olivier : « rajoute les sons du hud du t800 ») : bips de donnees, verrouillage de cible,
balayage, alarme, extinction, fanfare de succes ; le texte du narrateur crepite pendant qu il s ecrit (machine a ecrire du
HUD, 45 caracteres/s). Sons SYNTHETISES ici (aucun extrait du film : pas de souci de droits sur le stream), joues par
Windows sur la sortie par defaut (OBS les capte avec l audio du bureau). Un son a la fois (winsound) ; fichiers WAV generes
une fois dans %APPDATA%/CyberpunkAgent/sons/.

Configuration : %APPDATA%/CyberpunkAgent/sons.json {"enabled": true, "volume": 0.5}."""
from __future__ import annotations

import json
import math
import random
import struct
import threading
import time
import wave

from .config import DATA_DIR

RATE = 22050
SOUND_DIR = DATA_DIR / 'sons'
CFG_FILE = DATA_DIR / 'sons.json'
TYPE_CPS = 45.0                  # vitesse de la machine a ecrire du mod (AgentDrawSay)
_lock = threading.Lock()
_cache: dict[tuple, str] = {}
_last = {'t': 0.0, 'kind': None}
STATUS = {'played': 0, 'error': None}

# texte du narrateur (majuscules, sans accents) -> famille de son
KINDS: list[tuple[tuple[str, ...], str]] = [
    (('ECHEC CRITIQUE',), 'death'),
    (('SUCCES DEBLOQUE', 'PROGRES', 'LE CHAT A CHOISI', 'MERCI', 'BIENVENUE'), 'success'),
    (('CIBLE ACQUISE', 'HOSTILE', 'CYBERPSYCHO LOCALISE', 'KERENZIKOV', 'GUN FU', 'REVANCHE', 'J ATTAQUE'), 'lock'),
    (('COMITE D ACCUEIL', 'AGRESSION', 'VIE CRITIQUE'), 'alarm'),
    (('J EXPLORE', 'ZONE INCONNUE', 'CYBERPSYCHO SIGNALE', 'NOUVELLE MISSION', 'ANALYSE', 'LE CHAT CHOISIT'), 'scan'),
]


def cfg() -> dict:
    try:
        d = json.loads(CFG_FILE.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def kind_of(text: str) -> str:
    t = str(text).upper()
    for words, k in KINDS:
        if any(w in t for w in words):
            return k
    return 'say'


# ---- synthese ---------------------------------------------------------------------------------------------------------
def _tone(f0: float, f1: float, dur: float, shape: str = 'sine', amp: float = 0.6, attack: float = 0.004,
          release: float = 0.02, trem: float = 0.0) -> list[float]:
    n = int(dur * RATE)
    out, ph = [], 0.0
    for i in range(n):
        t = i / RATE
        f = f0 + (f1 - f0) * (i / max(1, n - 1))
        ph += 2 * math.pi * f / RATE
        if shape == 'square':
            v = 1.0 if math.sin(ph) >= 0 else -1.0
            v *= 0.5
        elif shape == 'noise':
            v = random.uniform(-1, 1)
        else:
            v = math.sin(ph)
        env = min(1.0, t / attack if attack else 1.0, (dur - t) / release if release else 1.0)
        if trem:
            env *= 0.6 + 0.4 * math.sin(2 * math.pi * trem * t)
        out.append(v * amp * max(0.0, env))
    return out


def _silence(dur: float) -> list[float]:
    return [0.0] * int(dur * RATE)


def _mix(a: list[float], b: list[float]) -> list[float]:
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else 0.0) + (b[i] if i < len(b) else 0.0) for i in range(n)]


def _chirp() -> list[float]:
    """Rafale de donnees : 6 bips tres courts a frequences aleatoires (le HUD « calcule »)."""
    rnd = random.Random(800)
    s: list[float] = []
    for _ in range(6):
        f = rnd.choice((1800, 2400, 3000, 2100, 2700))
        s += _tone(f, f, 0.018, 'square', 0.35) + _silence(0.012)
    return s


def _ticks(n_chars: int) -> list[float]:
    """Crepitement de la machine a ecrire : un clic par caractere (espaces muets)."""
    rnd = random.Random(n_chars)
    step = 1.0 / TYPE_CPS
    s: list[float] = []
    for i in range(n_chars):
        click = _tone(rnd.uniform(2600, 3400), 1500, 0.006, 'square', 0.22, attack=0.0005, release=0.004)
        s += click + _silence(max(0.0, step - 0.006))
    return s


PREFIX = {
    'say': lambda: _chirp(),
    'lock': lambda: (_tone(880, 880, 0.06, 'square', 0.4) + _silence(0.03) + _tone(1320, 1320, 0.06, 'square', 0.4)
                     + _silence(0.03) + _tone(1760, 1760, 0.16, 'square', 0.45, release=0.05)),
    'scan': lambda: _mix(_tone(300, 2400, 0.55, 'sine', 0.45, trem=24.0), _tone(150, 1200, 0.55, 'square', 0.12)),
    'alarm': lambda: sum((_tone(660, 660, 0.12, 'square', 0.5) + _silence(0.08) for _ in range(3)), []),
    'death': lambda: _mix(_tone(800, 60, 1.2, 'square', 0.4, release=0.4), _tone(0, 0, 1.2, 'noise', 0.12, release=0.6)),
    'success': lambda: (_tone(523, 523, 0.09, 'square', 0.4) + _tone(659, 659, 0.09, 'square', 0.4)
                        + _tone(784, 784, 0.09, 'square', 0.4) + _tone(1047, 1047, 0.28, 'square', 0.45, release=0.12)),
    'boot': lambda: _mix(_tone(100, 1600, 0.9, 'noise', 0.15, attack=0.3), _tone(220, 880, 0.9, 'square', 0.25)) + _chirp(),
}


def _write(path, samples: list[float], volume: float) -> None:
    peak = max(1e-6, max(abs(x) for x in samples)) if samples else 1.0
    g = min(1.0, 0.9 / peak) * max(0.0, min(1.0, volume))
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b''.join(struct.pack('<h', int(max(-1.0, min(1.0, x * g)) * 32767)) for x in samples))


def build(kind: str, n_chars: int = 0, volume: float = 0.5) -> str:
    """Fichier WAV (en cache) : son de la famille + crepitement pour un texte de n_chars (arrondi a 10)."""
    bucket = min(9, n_chars // 10) if n_chars else -1      # -1 : son seul, sans crepitement
    key = (kind, bucket, round(volume, 2))
    if key in _cache:
        return _cache[key]
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    samples = PREFIX.get(kind, PREFIX['say'])()
    if bucket >= 0:
        samples += _silence(0.05) + _ticks(bucket * 10 + 5)
    path = SOUND_DIR / f"{kind}_{'seul' if bucket < 0 else bucket}_{int(volume * 100)}.wav"
    _write(path, samples, volume)
    _cache[key] = str(path)
    return str(path)


def play(kind: str, n_chars: int = 0) -> bool:
    """Joue un son (asynchrone, n interrompt pas le jeu). Faux si coupe / indisponible."""
    c = cfg()
    if c.get('enabled') is False:
        return False
    try:
        import winsound
    except ImportError:
        return False
    try:
        with _lock:
            path = build(kind, n_chars, float(c.get('volume', 0.5)))
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        _last.update(t=time.time(), kind=kind)
        STATUS['played'] += 1
        return True
    except Exception as e:
        STATUS['error'] = str(e)[:120]
        return False


def for_text(text: str) -> bool:
    """Son d une phrase du narrateur : famille selon le texte + crepitement de sa longueur."""
    return play(kind_of(text), len(str(text)))


def prewarm() -> None:
    """Genere les sons courants en arriere-plan au demarrage (la premiere phrase ne doit pas attendre)."""
    def run():
        v = float(cfg().get('volume', 0.5))
        for k in PREFIX:
            for n in (0, 15, 25, 35, 45, 55):
                try:
                    with _lock:
                        build(k, n, v)
                except Exception:
                    pass
    threading.Thread(target=run, daemon=True).start()
