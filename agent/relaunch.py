"""Relance automatique apres un crash du jeu (19/10, Olivier : « 1 » ; il garde DLSS 5, qui fige parfois le jeu -> le
watchdog du moteur le coupe au bout de 120 s). Le jeu a disparu -> relance par Steam (options de lancement d Olivier
respectees), puis Entree a l ecran-titre (« Continuer » = derniere sauvegarde, choix par defaut) jusqu a ce que le mod
renvoie un etat VIVANT (V en vie, etat qui bouge). 3 crashs en 1 h -> on arrete et on previent (pas de boucle infinie)."""
from __future__ import annotations

import os
import subprocess
import time

from . import input_kbm as kbm, motion

GAME_EXE = 'Cyberpunk2077.exe'
STEAM_URL = 'steam://rungameid/1091500'
MAX_PER_HOUR = 3
_crashes: list[float] = []


def game_running(exe: str = GAME_EXE) -> bool:
    """Processus du jeu present ? (tasklist, sans fenetre). En cas de doute : vrai (on ne relance pas a tort)."""
    try:
        r = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {exe}', '/NH', '/FO', 'CSV'], capture_output=True, text=True,
                           timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return exe.lower() in (r.stdout or '').lower()
    except Exception:
        return True


def allowed(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    _crashes[:] = [t for t in _crashes if now - t < 3600.0]
    return len(_crashes) < MAX_PER_HOUR


def _sleep(s: float, stop) -> bool:
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        if stop is not None and stop.is_set():
            return False
        time.sleep(0.25)
    return True


def _alive(stop, timeout: float) -> bool:
    """Etat du mod qui BOUGE (seq qui change deux fois) avec V en vie et une position."""
    t0, last = time.perf_counter(), None
    seen = 0
    while time.perf_counter() - t0 < timeout:
        if stop is not None and stop.is_set():
            return False
        s = motion.read_state(timeout=0.5)
        if s and s.get('x') is not None and (s.get('hp') or 0) > 5 and s.get('seq') != last:
            if last is not None:
                seen += 1
                if seen >= 2:
                    return True
            last = s.get('seq')
        time.sleep(1.0)
    return False


def relaunch(log=print, stop=None, start=os.startfile) -> bool:
    """Relance et recharge ; vrai quand V est de nouveau en jeu."""
    _crashes.append(time.time())
    log(f'[relance] le jeu a disparu (crash ?) : relance par Steam ({len(_crashes)} crash(s) dans l heure)')
    if not _sleep(15.0, stop):                      # le rapport de crash du moteur finit de s ecrire
        return False
    # 19/10 (18:39, journal de Steam) : Steam refuse le lancement (« WaitingPrevProcess » -> echec) tant que l ancien
    # processus finit d ecrire son rapport de crash (il n a disparu qu a 18:42) -> on relance Steam CHAQUE MINUTE, 6 essais
    started = False
    for attempt in range(6):
        try:
            start(STEAM_URL)
        except Exception as e:
            log(f'[relance] Steam injoignable : {e}')
            return False
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 60.0:
            if game_running():
                started = True
                break
            if not _sleep(3.0, stop):
                return False
        if started:
            break
        log(f'[relance] le jeu ne demarre pas (essai {attempt + 1}/6 : Steam attend sans doute la fin de l ancien processus) : on relance Steam')
    if not started:
        log('[relance] le jeu ne demarre pas apres 6 essais (6 min) : abandon')
        return False
    log('[relance] jeu demarre : ecran-titre puis « Continuer »')
    # ecran-titre : Entree (« appuyez sur une touche » puis « Continuer », premier choix) toutes les 6 s, seulement quand le
    # jeu est au premier plan ; on s arrete des que V est en jeu. Jamais F (cyberware iconique d Olivier une fois en jeu).
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 420.0:
        if _alive(stop, 6.0):
            log(f'[relance] V est de retour en jeu ({time.perf_counter() - t0:.0f} s apres le demarrage)')
            _sleep(20.0, stop)                      # chauffe du mod apres chargement
            return True
        if stop is not None and stop.is_set():
            return False
        if kbm.game_focused():
            kbm.tap('ENTER', 0.08)
    log('[relance] pas de signe de vie 7 min apres le demarrage : abandon')
    return False
