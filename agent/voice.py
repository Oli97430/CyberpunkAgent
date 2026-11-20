"""
voice.py -- V DIT a voix haute les phrases du narrateur (30/10/2026, Olivier : « commentateur vocal de V »).

Le narrateur (narrator.say) affiche ses phrases en majuscules sur le HUD ; ce module les convertit en francais parle
(accents, apostrophes, « 126/157 » -> « 126 sur 157 ») et les fait dire par Piper (synthese vocale locale, hors ligne),
avec un leger effet « cyborg » (relais du HUD en introduction, echo court). Le son part sur la sortie par defaut de
Windows : OBS le capte pour le direct.

Piper n est PAS embarque dans l exe : un petit serveur de voix (voice_server.py, ecrit dans le dossier de donnees) tourne
avec le Python qui a le paquet piper-tts ; il charge la voix UNE fois puis rend un WAV par phrase (~0,3-1 s ; piper.exe
rechargeait la voix a chaque phrase : 3,4 s). Chaque phrase est mise en cache ; les phrases fixes du narrateur sont
preparees des le demarrage.

Reglages : voice.json dans le dossier de donnees (cree au premier lancement) --
{"enabled": true, "python": "F:\\PYTH\\python.exe", "model": "...fr_FR-tom-medium.onnx", "speed": 1.0, "volume": 0.9,
 "effect": "cyborg"}   (effect : "cyborg" ou "aucun")
"""
from __future__ import annotations

import atexit
import hashlib
import json
import queue
import re
import subprocess
import threading
import time
import wave

from .config import DATA_DIR

VOICE_DIR = DATA_DIR / 'voix'
CFG_FILE = DATA_DIR / 'voice.json'
DEFAULTS = {'enabled': True, 'python': r'F:\PYTH\python.exe', 'model': r'F:\tmp\piper_voices\fr_FR-tom-medium.onnx',
            'speed': 1.0, 'volume': 0.9, 'effect': 'cyborg'}
STATUS = {'ok': None, 'error': None, 'spoken': 0, 'cache_hits': 0}

SERVER_SRC = r'''
import json, sys, wave
from piper import PiperVoice
cfg = json.loads(sys.argv[1])
voice = PiperVoice.load(cfg["model"])
syn = None
try:
    from piper import SynthesisConfig
    syn = SynthesisConfig(length_scale=1.0 / max(0.5, float(cfg.get("speed", 1.0))))
except Exception:
    syn = None
print("PRET", flush=True)
for line in sys.stdin:
    try:
        req = json.loads(line)
        with wave.open(req["out"], "wb") as w:
            if syn is not None:
                voice.synthesize_wav(req["text"], w, syn_config=syn)
            else:
                voice.synthesize_wav(req["text"], w)
        print("OK " + req["out"], flush=True)
    except Exception as e:
        print("ERR " + str(e).replace("\n", " ")[:200], flush=True)
'''

# ---------------------------------------------------------------- texte HUD -> francais parle -----------------------
ACCENTS = {
    'echec': 'échec', 'systeme': 'système', 'termine': 'terminé', 'redemarrage': 'redémarrage', 'detecte': 'détecté',
    'detectes': 'détectés', 'debloque': 'débloqué', 'succes': 'succès', 'vehicule': 'véhicule', 'elevee': 'élevée',
    'negatif': 'négatif', 'enregistre': 'enregistré', 'signale': 'signalé', 'localise': 'localisé', 'active': 'activé',
    'deja': 'déjà', 'etat': 'état', 'installe': 'installé', 'acces': 'accès', 'controle': 'contrôle', 'fete': 'fête',
    'ca': 'ça', 'demande': 'demandé', 'comite': 'comité', 'scene': 'scène', 'ete': 'été', 'recu': 'reçu',
    'neutralises': 'neutralisés', 'neutralise': 'neutralisé', 'arrete': 'arrêté', 'pret': 'prêt', 'tres': 'très',
    'premiere': 'première', 'derniere': 'dernière', 'equipe': 'équipé', 'ecran': 'écran', 'reseau': 'réseau',
    'securite': 'sécurité', 'priorite': 'priorité', 'vehicules': 'véhicules', 'eddies': 'eddies',
    'meteo': 'météo', 'piratee': 'piratée', 'tempete': 'tempête', 'visibilite': 'visibilité', 'appele': 'appelé',
}
_A_VERB = re.compile(r"\b(chat|il|elle|on|qui|v) a\b")


def to_speech(text: str) -> str:
    s = ' '.join(str(text).split())
    s = s.replace('(S)', 'S').replace('(s)', 's')
    s = re.sub(r'(\d+)\s*/\s*(\d+)', r'\1 sur \2', s)
    s = s.lower()
    s = re.sub(r"\bi m\b", "i'm", s)
    s = re.sub(r"\b(j|l|d|c|m|t|s|n|qu) (?=[aeiouyhéèêàâîôû])", r"\1'", s)
    s = re.sub(r"\b([a-zàâçéèêëîïôûù]+)\b", lambda m: ACCENTS.get(m.group(1), m.group(1)), s)
    marks = _A_VERB.findall(s)
    s = re.sub(r"\ba\b", 'à', s)
    for subj in set(marks):                                    # « le chat a voté » : verbe avoir, pas la preposition
        s = s.replace(f'{subj} à', f'{subj} a')
    s = re.sub(r"\ba vote\b", 'a voté', s)
    s = re.sub(r"([.!?:]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), s)   # majuscule en debut de phrase
    return s[:1].upper() + s[1:] if s else s


# ---------------------------------------------------------------- reglages, cache ----------------------------------
def cfg() -> dict:
    try:
        d = json.loads(CFG_FILE.read_text(encoding='utf-8'))
        return {**DEFAULTS, **(d if isinstance(d, dict) else {})}
    except Exception:
        try:
            CFG_FILE.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
        return dict(DEFAULTS)


def _key(spoken: str, c: dict) -> str:
    h = hashlib.sha1(json.dumps([spoken, c.get('model'), c.get('speed'), c.get('effect'), c.get('volume')]).encode()).hexdigest()
    return h[:16]


# ---------------------------------------------------------------- serveur de voix ----------------------------------
class _Server:
    def __init__(self):
        self.p = None
        self.lock = threading.Lock()

    def start(self, c: dict) -> bool:
        if self.p and self.p.poll() is None:
            return True
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        src = DATA_DIR / 'voice_server.py'
        src.write_text(SERVER_SRC, encoding='utf-8')
        try:
            self.p = subprocess.Popen([c['python'], '-I', str(src), json.dumps({'model': c['model'], 'speed': c['speed']})],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                      encoding='utf-8', bufsize=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            line = self.p.stdout.readline().strip()          # « PRET » quand la voix est chargee
            if line != 'PRET':
                raise RuntimeError(f'serveur de voix : {line or "aucune reponse (piper-tts installe ?)"}')
            STATUS['ok'], STATUS['error'] = True, None
            return True
        except Exception as e:
            STATUS['ok'], STATUS['error'] = False, str(e)[:200]
            self.stop()
            return False

    def synth(self, text: str, out: str, c: dict) -> bool:
        with self.lock:
            if not self.start(c):
                return False
            try:
                self.p.stdin.write(json.dumps({'text': text, 'out': out}, ensure_ascii=False) + '\n')
                self.p.stdin.flush()
                line = self.p.stdout.readline().strip()
            except Exception as e:
                STATUS['error'] = str(e)[:200]; self.stop(); return False
            if not line.startswith('OK '):
                STATUS['error'] = line[:200]
                return False
            return True

    def stop(self) -> None:
        if self.p:
            try:
                self.p.kill()
            except Exception:
                pass
        self.p = None


_server = _Server()
atexit.register(_server.stop)
_jobs: queue.Queue = queue.Queue()
_worker_started = [False]


def _post(raw: str, final: str, c: dict) -> None:
    """Effet « cyborg » + volume, au format des sons du HUD (relais en introduction, echo court)."""
    from . import sounds
    with wave.open(raw, 'rb') as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        frames = w.readframes(n)
    import array
    a = array.array('h', frames)
    if ch == 2:
        a = array.array('h', a[0::2])
    src = [x / 32768.0 for x in a]
    R = sounds.RATE
    ratio = sr / R                                              # reechantillonnage lineaire vers le taux des sons du HUD
    out = []
    for i in range(int(len(src) / ratio)):
        p = i * ratio; j = int(p); f = p - j
        out.append(src[j] * (1 - f) + (src[j + 1] if j + 1 < len(src) else 0.0) * f)
    if c.get('effect') == 'cyborg':
        out = sounds._seq(sounds.PREFIX_V3['say'](), sounds._silence(0.04), sounds._echo(out, delay=0.06, fb=0.22, n=2))
    sounds._write(final, out, float(c.get('volume', 0.9)))


def _worker() -> None:
    from . import sounds
    while True:
        spoken, final, t_req, play = _jobs.get()
        c = cfg()
        try:
            raw = str(VOICE_DIR / ('brut_' + final.split('\\')[-1].split('/')[-1]))
            if _server.synth(spoken, raw, c):
                _post(raw, final, c)
                if play and time.time() - t_req < 5.0:          # trop tard : la phrase n est plus a l ecran
                    sounds.play_path(final, 'voice')
                    STATUS['spoken'] += 1
        except Exception as e:
            STATUS['error'] = str(e)[:200]


def _ensure_worker() -> None:
    if not _worker_started[0]:
        _worker_started[0] = True
        threading.Thread(target=_worker, daemon=True).start()


def speak(text: str) -> bool:
    """Dit la phrase (asynchrone). Vrai si la voix s en charge (le HUD ne joue alors pas son crepitement)."""
    c = cfg()
    if not c.get('enabled') or STATUS['ok'] is False:
        return False
    spoken = to_speech(text)
    if not spoken:
        return False
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    final = str(VOICE_DIR / (_key(spoken, c) + '.wav'))
    from pathlib import Path
    if Path(final).exists():
        from . import sounds
        STATUS['cache_hits'] += 1; STATUS['spoken'] += 1
        return sounds.play_path(final, 'voice')
    _ensure_worker()
    _jobs.put((spoken, final, time.time(), True))
    return True


def prewarm(phrases) -> None:
    """Prepare en arriere-plan les phrases fixes (aucune attente quand elles tombent)."""
    c = cfg()
    if not c.get('enabled'):
        return
    from pathlib import Path
    _ensure_worker()
    for t in phrases:
        if '{' in t:
            continue
        spoken = to_speech(t)
        final = str(VOICE_DIR / (_key(spoken, c) + '.wav'))
        if not Path(final).exists():
            _jobs.put((spoken, final, 0.0, False))
