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


# 19/10 (Olivier : « ameliore les sons du HUD ») : timbres plus riches -- modulation de frequence (metal numerique), grain
# 8 bits, sous-basse, echo court (la piece du HUD) ; sons nouveaux : degat, elimination, quickhack, voiture.
def _fm(f0: float, f1: float, dur: float, ratio: float = 2.0, index: float = 3.0, amp: float = 0.5,
        attack: float = 0.003, release: float = 0.03) -> list[float]:
    n = int(dur * RATE)
    out, ph, pm = [], 0.0, 0.0
    for i in range(n):
        t = i / RATE
        f = f0 + (f1 - f0) * (i / max(1, n - 1))
        pm += 2 * math.pi * f * ratio / RATE
        ph += 2 * math.pi * f / RATE
        decay = 1.0 - 0.7 * (i / max(1, n))            # l indice baisse : attaque metallique, fin plus douce
        v = math.sin(ph + index * decay * math.sin(pm))
        env = min(1.0, t / attack if attack else 1.0, (dur - t) / release if release else 1.0)
        out.append(v * amp * max(0.0, env))
    return out


def _crush(s: list[float], bits: int = 6, hold: int = 3) -> list[float]:
    q = 2 ** (bits - 1)
    out, last = [], 0.0
    for i, x in enumerate(s):
        if i % hold == 0:
            last = round(x * q) / q
        out.append(last)
    return out


def _echo(s: list[float], delay: float = 0.09, fb: float = 0.32, n: int = 3) -> list[float]:
    d = int(delay * RATE)
    out = s + [0.0] * (d * n)
    for k in range(1, n + 1):
        g = fb ** k
        for i, x in enumerate(s):
            out[i + d * k] += x * g
    return out


def _sub(f0: float, f1: float, dur: float, amp: float = 0.6) -> list[float]:
    return _tone(f0, f1, dur, 'sine', amp, attack=0.002, release=dur * 0.8)


def _seq(*parts: list[float]) -> list[float]:
    out: list[float] = []
    for p in parts:
        out += p
    return out


PREFIX_V2 = {
    'say': lambda: _echo(_seq(*[_crush(_fm(f, f, 0.02, 3.0, 2.0, 0.35), 6, 2) + _silence(0.011)
                                for f in (1800, 2400, 3000, 2100, 2700, 3300)]), 0.06, 0.25, 2),
    'lock': lambda: _echo(_mix(_seq(_fm(880, 880, 0.055, 2.0, 2.5, 0.42), _silence(0.025), _fm(1320, 1320, 0.055, 2.0, 2.5, 0.42),
                                    _silence(0.025), _fm(1760, 1760, 0.2, 1.5, 3.5, 0.48, release=0.08)),
                               _sub(110, 55, 0.18, 0.5)), 0.08, 0.3, 3),
    'scan': lambda: _echo(_mix(_tone(300, 2400, 0.55, 'sine', 0.42, trem=24.0), _crush(_tone(150, 1200, 0.55, 'square', 0.12), 5, 4)),
                          0.11, 0.3, 2),
    'alarm': lambda: _seq(*[_mix(_fm(660, 640, 0.13, 1.01, 4.0, 0.5), _sub(82, 82, 0.13, 0.4)) + _silence(0.07) for _ in range(3)]),
    'death': lambda: _echo(_mix(_mix(_fm(800, 50, 1.3, 1.5, 5.0, 0.42, release=0.5), _tone(0, 0, 1.3, 'noise', 0.12, release=0.7)),
                                _sub(70, 30, 1.2, 0.55)), 0.17, 0.35, 3),
    'success': lambda: _echo(_seq(_fm(523, 523, 0.09, 2.0, 1.5, 0.4), _fm(659, 659, 0.09, 2.0, 1.5, 0.4), _fm(784, 784, 0.09, 2.0, 1.5, 0.4),
                                  _mix(_fm(1047, 1047, 0.32, 2.0, 2.5, 0.45, release=0.14), _sub(131, 131, 0.3, 0.35))), 0.1, 0.33, 3),
    'boot': lambda: _echo(_seq(_mix(_tone(100, 1600, 0.9, 'noise', 0.14, attack=0.3), _fm(110, 880, 0.9, 1.5, 3.0, 0.32)),
                               _crush(_seq(*[_fm(f, f, 0.03, 2.0, 2.0, 0.3) + _silence(0.015) for f in (1200, 1600, 2000, 2400)]), 6, 2),
                               _mix(_fm(440, 440, 0.25, 2.0, 1.2, 0.35, release=0.15), _sub(55, 55, 0.25, 0.45))), 0.12, 0.3, 2),
    # nouveaux
    'hit': lambda: _mix(_crush(_tone(0, 0, 0.22, 'noise', 0.55, attack=0.001, release=0.15), 4, 6), _sub(90, 40, 0.22, 0.7)),
    'kill': lambda: _echo(_mix(_seq(_fm(1500, 900, 0.07, 2.0, 3.0, 0.42), _silence(0.02), _fm(1100, 500, 0.12, 2.0, 3.0, 0.42)),
                               _sub(120, 45, 0.2, 0.5)), 0.08, 0.3, 2),
    'hack': lambda: _echo(_seq(_crush(_seq(*[_tone(f, f, 0.032, 'square', 0.3) + _silence(0.008)
                                              for f in (660, 990, 880, 1320, 1180, 1760, 1570, 2350)]), 6, 2),
                               _fm(400, 2600, 0.3, 1.5, 2.0, 0.3, release=0.1)), 0.07, 0.3, 2),
    'drive': lambda: _echo(_seq(_mix(_tone(120, 340, 0.6, 'sine', 0.4, attack=0.1, trem=18.0), _sub(60, 90, 0.6, 0.4)),
                                _fm(1600, 1600, 0.05, 2.0, 2.0, 0.3), _silence(0.03), _fm(2000, 2000, 0.05, 2.0, 2.0, 0.3)), 0.09, 0.25, 2),
}
PRIO = {'death': 5, 'boot': 4, 'success': 4, 'alarm': 3, 'hit': 3, 'kill': 3, 'lock': 2, 'hack': 2, 'drive': 1, 'scan': 1, 'say': 1}
_dur: dict[str, float] = {}
FEED: list[tuple[str, str]] = [            # lignes INTERNES du journal -> son (debut de ligne, sans indentation)
    (r'^\[combat\] quickhack « [^»]+ » \(', 'hack'),
    (r'^\[conduite\] autodrive enclenche', 'drive'),
    (r'^\[vol\] la voiture volee roule', 'drive'),
]


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
    samples = (PREFIX_V2.get(kind) or PREFIX_V2['say'])()
    if bucket >= 0:
        samples += _silence(0.05) + _ticks(bucket * 10 + 5)
    path = SOUND_DIR / f"v2_{kind}_{'seul' if bucket < 0 else bucket}_{int(volume * 100)}.wav"
    _write(path, samples, volume)
    _cache[key] = str(path)
    _dur[str(path)] = len(samples) / RATE
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
    now = time.time()
    if now < _last.get('end', 0.0) and PRIO.get(kind, 1) < PRIO.get(_last.get('kind') or 'say', 1):
        return False                                   # 19/10 : ne coupe pas un son plus important en cours
    try:
        with _lock:
            path = build(kind, n_chars, float(c.get('volume', 0.5)))
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        _last.update(t=now, kind=kind, end=now + _dur.get(path, 0.5))
        STATUS['played'] += 1
        return True
    except Exception as e:
        STATUS['error'] = str(e)[:120]
        return False


def for_text(text: str) -> bool:
    """Son d une phrase du narrateur : famille selon le texte + crepitement de sa longueur."""
    return play(kind_of(text), len(str(text)))


def feed(line: str) -> None:
    """Ligne interne du journal -> son (quickhack, voiture) ; lignes venues de l exterieur ignorees."""
    import re
    if any(t in line for t in ('[youtube]', '[telegram]', '[sms]', 'DIRECTIVE', '"')):
        return
    for rx, kind in FEED:
        if re.search(rx, line):
            play(kind)
            return


def prewarm() -> None:
    """Genere les sons courants en arriere-plan au demarrage (la premiere phrase ne doit pas attendre)."""
    def run():
        v = float(cfg().get('volume', 0.5))
        for k in PREFIX_V2:
            for n in ((0, 15, 25, 35, 45, 55) if k in ('say', 'lock', 'scan', 'success', 'alarm', 'death') else (0,)):
                try:
                    with _lock:
                        build(k, n, v)
                except Exception:
                    pass
    threading.Thread(target=run, daemon=True).start()
