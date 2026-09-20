"""
llm.py -- chien de garde Ollama : le serveur s est arrete deux fois en cours de session
(2026-09-11 06:53 et 21:18), rendant V muet (regle de secours seulement). Des qu un appel
echoue par connexion refusee, on relance `ollama serve` (au plus une fois par minute) et on
attend qu il reponde.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

from .config import CFG
OLLAMA_EXE = CFG.ollama_exe or 'ollama'
TAGS = CFG.ollama_url + '/api/tags'
_last_start = [-999.0]


def alive(timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(TAGS, timeout=timeout).read()
        return True
    except Exception:
        return False


def list_models(timeout: float = 4.0) -> list[dict]:
    """20/09 (Olivier : choisir d autres modeles locaux) : modeles Ollama deja installes (`ollama pull ...`),
    avec leur taille sur disque -- une approximation courante et raisonnable de la VRAM necessaire pour les
    charger entierement sur le GPU (hors contexte/KV-cache, qui grandit avec l usage). Renvoie
    [{'name': str, 'size_gb': float}, ...] trie du plus petit au plus gros."""
    try:
        data = json.loads(urllib.request.urlopen(TAGS, timeout=timeout).read())
        models = [{'name': m['name'], 'size_gb': round((m.get('size') or 0) / 1e9, 1)}
                  for m in data.get('models', []) if m.get('name')]
        return sorted(models, key=lambda m: m['size_gb'])
    except Exception:
        return []


def ensure(log=print, wait_s: float = 25.0) -> bool:
    """Relance le serveur s il ne repond pas. Renvoie True s il repond a la fin."""
    if alive():
        return True
    now = time.perf_counter()
    if now - _last_start[0] < 60.0:
        return False
    _last_start[0] = now
    log('  [ollama] serveur injoignable : relance de `ollama serve`')
    try:
        subprocess.Popen([OLLAMA_EXE, 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as e:
        log(f'  [ollama] relance impossible : {e}')
        return False
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < wait_s:
        if alive(1.0):
            log(f'  [ollama] de retour en {time.perf_counter() - t0:.0f} s')
            return True
        time.sleep(1.0)
    log('  [ollama] toujours muet apres relance')
    return False
