"""
inputs.py -- PLAN B : manette Xbox 360 virtuelle + arret d'urgence.

RETROGRADE EN PLAN B (recherche approfondie, post-ecriture de ce fichier) :
MixedInputFix n'est PAS installe sur cette machine, et melanger manette et
clavier/souris fait geler Cyberpunk apres 20-60 min sans lui (confirme par le
depot du correctif lui-meme, CDPR qualifie ca de "configuration non supportee").
La v1 utilise agent/input_kbm.py (clavier scancode + souris relative SEULEMENT).
Ne reactiver ce fichier que si input_kbm.py echoue le test Phase 0 ET que
MixedInputFix est installe au prealable.

Pourquoi une manette virtuelle et pas le clavier/souris (raisonnement original,
invalide sans MixedInputFix) :
Cyberpunk lit la souris en Raw Input, ce qui rend SendInput peu fiable. Une manette
ViGEm est vue par le jeu comme un vrai peripherique (verifie : Windows la liste comme
"Controleur XBOX 360 pour Windows"). Les sticks donnent en plus un controle ANALOGIQUE
continu, indispensable pour viser et se deplacer proprement.

Regle de securite : tout est relache (neutral) a la sortie, quoi qu'il arrive, pour que
V ne continue pas a courir tout seul si le script meurt.
"""
from __future__ import annotations

import atexit
import ctypes
import threading
import time

import vgamepad as vg

# --- boutons Xbox, noms courts -------------------------------------------------------
B = vg.XUSB_BUTTON
BUTTONS = {
    'A': B.XUSB_GAMEPAD_A,            # saut / confirmer
    'B': B.XUSB_GAMEPAD_B,            # esquive (double appui) / retour
    'X': B.XUSB_GAMEPAD_X,            # recharger / interagir (contexte)
    'Y': B.XUSB_GAMEPAD_Y,            # changer d'arme
    'LB': B.XUSB_GAMEPAD_LEFT_SHOULDER,
    'RB': B.XUSB_GAMEPAD_RIGHT_SHOULDER,
    'LS': B.XUSB_GAMEPAD_LEFT_THUMB,  # sprint
    'RS': B.XUSB_GAMEPAD_RIGHT_THUMB, # scanner / visee sur cible
    'START': B.XUSB_GAMEPAD_START,
    'BACK': B.XUSB_GAMEPAD_BACK,
    'UP': B.XUSB_GAMEPAD_DPAD_UP,
    'DOWN': B.XUSB_GAMEPAD_DPAD_DOWN,
    'LEFT': B.XUSB_GAMEPAD_DPAD_LEFT,
    'RIGHT': B.XUSB_GAMEPAD_DPAD_RIGHT,
}


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, float(v)))


class Gamepad:
    """Manette X360 virtuelle. Toutes les valeurs analogiques sont en [-1, 1] ou [0, 1]."""

    def __init__(self) -> None:
        self._pad = vg.VX360Gamepad()
        self._lock = threading.Lock()
        self.neutral()
        atexit.register(self.neutral)

    # -- sticks -----------------------------------------------------------------------
    def move(self, x: float, y: float) -> None:
        """Stick gauche : deplacement. x>0 = droite, y>0 = avant."""
        with self._lock:
            self._pad.left_joystick_float(x_value_float=_clamp(x), y_value_float=_clamp(y))
            self._pad.update()

    def look(self, x: float, y: float) -> None:
        """Stick droit : camera. x>0 = tourner a droite, y>0 = lever la vue."""
        with self._lock:
            self._pad.right_joystick_float(x_value_float=_clamp(x), y_value_float=_clamp(y))
            self._pad.update()

    # -- gachettes --------------------------------------------------------------------
    def aim(self, amount: float = 1.0) -> None:
        """LT : visee (maintien)."""
        with self._lock:
            self._pad.left_trigger_float(value_float=max(0.0, min(1.0, amount)))
            self._pad.update()

    def fire(self, amount: float = 1.0) -> None:
        """RT : tir (maintien). fire(0) relache."""
        with self._lock:
            self._pad.right_trigger_float(value_float=max(0.0, min(1.0, amount)))
            self._pad.update()

    # -- boutons ----------------------------------------------------------------------
    def hold(self, name: str) -> None:
        with self._lock:
            self._pad.press_button(button=BUTTONS[name])
            self._pad.update()

    def release(self, name: str) -> None:
        with self._lock:
            self._pad.release_button(button=BUTTONS[name])
            self._pad.update()

    def tap(self, name: str, duration: float = 0.08) -> None:
        """Appui bref. 80 ms = fiable pour le jeu sans etre percu comme un maintien."""
        self.hold(name)
        time.sleep(duration)
        self.release(name)

    def double_tap(self, name: str = 'B', gap: float = 0.10) -> None:
        """Esquive = double appui rapide sur B."""
        self.tap(name)
        time.sleep(gap)
        self.tap(name)

    # -- securite ---------------------------------------------------------------------
    def neutral(self) -> None:
        """Relache TOUT. Appele a la sortie et par l'arret d'urgence."""
        with self._lock:
            self._pad.reset()
            self._pad.update()


# --- arret d'urgence ---------------------------------------------------------------------
_user32 = ctypes.windll.user32
VK = {'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B, 'PAUSE': 0x13, 'END': 0x23}


class KillSwitch:
    """
    Surveille une touche GLOBALE (GetAsyncKeyState, zero dependance) et declenche
    l'arret. F11 par defaut : libre dans Cyberpunk, et F12 est pris par Steam
    (capture d'ecran). Un appui = l'agent relache la manette et s'arrete.
    """

    def __init__(self, gamepad: Gamepad, key: str = 'F11', poll_hz: float = 50.0) -> None:
        self._pad = gamepad
        self._vk = VK[key]
        self._period = 1.0 / poll_hz
        self.triggered = threading.Event()
        self._thread = threading.Thread(target=self._loop, name='killswitch', daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self.triggered.is_set():
            # bit 15 = touche actuellement enfoncee
            if _user32.GetAsyncKeyState(self._vk) & 0x8000:
                self.triggered.set()
                self._pad.neutral()
                print('[KILL] arret d urgence : manette relachee.')
                return
            time.sleep(self._period)

    @property
    def active(self) -> bool:
        return self.triggered.is_set()


if __name__ == '__main__':
    # Test a blanc, SANS jeu : cree la manette, bouge les sticks 2 s, relache.
    pad = Gamepad()
    ks = KillSwitch(pad)
    print('manette creee. Appuie sur F11 pour tester l arret d urgence (2 s max)...')
    t0 = time.time()
    while time.time() - t0 < 2.0 and not ks.active:
        pad.move(0.0, 0.5)
        time.sleep(0.05)
    pad.neutral()
    print('OK' if not ks.active else 'arret d urgence detecte')
