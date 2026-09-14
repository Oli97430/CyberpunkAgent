"""
input_kbm.py -- actionneur PRIMAIRE pour la v1 (clavier scancode + souris relative).

Pourquoi pas la manette (vgamepad, voir inputs.py) en v1 : MixedInputFix n'est PAS
installe sur cette machine (verifie : red4ext\\plugins ne contient que ArchiveXL,
bHapticsMod, Codeware, input_loader, TweakXL). Sans lui, melanger manette et
clavier/souris fait geler le jeu apres 20-60 min (comportement documente, CDPR le
qualifie de "configuration non supportee"). v1 = clavier + souris uniquement.

Pourquoi ca devrait marcher quand meme : des mods Nexus de GAMEPLAY en AutoHotkey
tournent sur cette version du jeu (autowalk, toggle-walk, Breach Protocol
Autosolver) -- donc SendInput en scancode et la souris relative sont bien recus
par le jeu. Piege documente : plusieurs de ces mods exigent d'etre lances EN
ADMINISTRATEUR, ce qui explique probablement les vieux rapports "CP2077 ignore
l'entree virtuelle" (tous dates de decembre 2020).
"""
from __future__ import annotations

import atexit
import ctypes
import re
import ctypes.wintypes as wt
import signal
import sys
import threading
import time

user32 = ctypes.WinDLL('user32', use_last_error=True)

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _U)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008

# Scancodes (set 1), verifies dans la recherche (code reel de JoyShockMapper).
SC = {
    'W': 0x11, 'A': 0x1E, 'S': 0x1F, 'D': 0x20, 'SPACE': 0x39, 'ENTER': 0x1C, 'LSHIFT': 0x2A,
    'LCTRL': 0x1D, 'C': 0x2E, 'R': 0x13, 'F': 0x21, 'E': 0x12, 'Q': 0x10,
    'TAB': 0x0F, 'ESC': 0x01, '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, 'X': 0x2D,
    'G': 0x22, 'LALT': 0x38, 'V': 0x2F, 'B': 0x30, 'T': 0x14, 'M': 0x32, 'BACKSPACE': 0x0E,
    'RSHIFT': 0x36, 'TILDE': 0x29, 'Y': 0x15, 'U': 0x16, 'I': 0x17, 'O': 0x18, 'P': 0x19, 'H': 0x23, 'J': 0x24,
    'K': 0x25, 'L': 0x26, 'N': 0x31, 'Z': 0x2C, 'UP': 0x48, 'DOWN': 0x50, 'LEFT': 0x4B, 'RIGHT': 0x4D,
    'NUM1': 0x4F, 'NUM2': 0x50, 'NUM3': 0x51, 'NUM4': 0x4B, 'NUM5': 0x4C,
    'NUM6': 0x4D, 'NUM7': 0x47, 'NUM8': 0x48, 'NUM9': 0x49, 'NUM0': 0x52,
}

_held: set[int] = set()
_held_ext: set[int] = set()     # scancodes maintenus avec le drapeau ETENDU (fleches)
_acc = [0.0, 0.0]
EXTENDED = {'UP', 'DOWN', 'LEFT', 'RIGHT'}   # memes scancodes que le pave numerique : sans E0, UP = Numpad 8
_focus_cache = [0.0, True]

# ---- TOUCHES PAR ACTION, lues dans les reglages du joueur ------------------------------
# Lecon du 2026-09-11 : le joueur est en AZERTY avec des reassignations (interagir = E,
# cyberware = F, quick melee = ~, esquive = Ctrl gauche, sprint maintenu = Maj droite).
# V appuyait sur F pour ramasser (= cyberware) et sur E pour le cyberware (= interagir).
# Les scancodes sont PHYSIQUES : pour une touche IK_<lettre> on demande a Windows le
# scancode qui produit cette lettre dans la disposition ACTIVE (MapVirtualKeyW).
from .config import CFG as _CFG
USER_SETTINGS = str(_CFG.user_settings)   # %LOCALAPPDATA%/CD Projekt Red/Cyberpunk 2077/UserSettings.json
_IK_SPECIAL = {'IK_Space': 'SPACE', 'IK_LShift': 'LSHIFT', 'IK_RShift': 'RSHIFT', 'IK_LControl': 'LCTRL',
               'IK_Tab': 'TAB', 'IK_Escape': 'ESC', 'IK_Backspace': 'BACKSPACE', 'IK_Tilde': 'TILDE',
               'IK_Alt': 'LALT', 'IK_Up': 'UP', 'IK_Down': 'DOWN', 'IK_Left': 'LEFT', 'IK_Right': 'RIGHT'}
_DEFAULT_ACTIONS = {'interact': 'F', 'iconic': 'E', 'quickmelee': 'Q', 'dodge': None, 'sprint': 'LSHIFT',
                    'consumable': 'X', 'crouch': 'C', 'jump': 'SPACE', 'reload': 'R', 'holster': 'B',
                    'ui_confirm': 'F', 'scanner': 'TAB', 'forward': 'W', 'back': 'S', 'left': 'A', 'right': 'D',
                    'weapon1': '1', 'weapon2': '2', 'weapon3': '3', 'callvehicle': 'V', 'walktoggle': None, 'dropbody': None,
                    'autodrive': None, 'exitvehicle': 'F', 'phone': 'T',
                    'bd_pause': 'SPACE', 'bd_fwd': 'E', 'bd_back': 'Q', 'bd_layer': 'LSHIFT', 'bd_mode': 'TAB', 'bd_restart': 'R', 'bd_exit': 'X'}
_SETTING_NAMES = {'interact': 'selectChoice', 'iconic': 'iconic', 'quickmelee': 'quickmelee', 'dodge': 'dodgeDash',
                  'sprint': 'sprintHold', 'consumable': 'useConsumable', 'crouch': 'crouchToggle', 'jump': 'jump',
                  'reload': 'reload', 'holster': 'holsterWeapon', 'ui_confirm': 'selectChoiceUI', 'scanner': 'visionHold',
                  'forward': 'forward', 'back': 'back', 'left': 'left', 'right': 'right',
                  'weapon1': 'weapon1', 'weapon2': 'weapon2', 'weapon3': 'weapon3', 'callvehicle': 'callVehicle',
                  'walktoggle': 'walkToggle', 'dropbody': 'dropCarriedObject',
                  'autodrive': 'vehicleAutodrive', 'exitvehicle': 'exitVehicle', 'phone': 'openPhone',
                  'bd_pause': 'braindancePause', 'bd_fwd': 'braindanceForward', 'bd_back': 'braindanceBackward', 'bd_layer': 'braindanceLayer',
                  'bd_mode': 'braindanceMode', 'bd_restart': 'braindanceRestart', 'bd_exit': 'braindanceExit'}
ACTIONS: dict[str, str | None] = dict(_DEFAULT_ACTIONS)


def _ik_to_keyname(ik: str) -> str | None:
    """IK_E -> nom de touche de la table SC (scancode PHYSIQUE calcule pour la disposition active)."""
    if ik in _IK_SPECIAL:
        return _IK_SPECIAL[ik]
    m = re.fullmatch(r'IK_([A-Z0-9])', ik)
    if not m:
        return None
    ch = m.group(1)
    vk = ord(ch)                                  # VK des lettres/chiffres = code ASCII majuscule
    sc = user32.MapVirtualKeyW(vk, 0)             # MAPVK_VK_TO_VSC, disposition active
    if not sc:
        return None
    name = 'K_' + ch
    SC[name] = sc
    return name


def load_user_keys() -> None:
    try:
        import json
        d = json.loads(open(USER_SETTINGS, encoding='utf-8').read())
    except Exception:
        return
    found: dict[str, str] = {}

    def walk(o):
        if isinstance(o, dict):
            n, v = o.get('name'), o.get('value')
            if isinstance(n, str) and isinstance(v, str) and v.startswith('IK_') and n not in found:
                found[n] = v
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(d)
    for action, setting in _SETTING_NAMES.items():
        ik = found.get(setting)
        if ik:
            kn = _ik_to_keyname(ik)
            if kn:
                ACTIONS[action] = kn


def K(action: str) -> str:
    """Nom de touche (cle de SC) pour une action ; leve si l action n a pas de touche."""
    k = ACTIONS.get(action)
    if not k:
        raise KeyError(f'aucune touche pour l action {action!r}')
    return k


_missing_warned: set = set()


def _key_or_warn(action: str) -> str | None:
    k = ACTIONS.get(action)
    if not k and action not in _missing_warned:
        _missing_warned.add(action); print(f'  [kbm] action sans touche dans les reglages : {action!r} (ignoree)')
    return k


def act(action: str, duration: float = 0.08) -> None:
    k = _key_or_warn(action)
    if k: tap(k, duration)


def act_hold(action: str) -> None:
    k = _key_or_warn(action)
    if k: hold(k)


def act_release(action: str) -> None:
    k = ACTIONS.get(action)
    if k: release(k)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def foreground_process_name() -> str:
    """Nom de l'exe au premier plan ('' si indetermine). Sert de garde de focus."""
    hwnd = user32.GetForegroundWindow()
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wt.DWORD(260)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        if not ok:
            return ''
        return buf.value.rsplit('\\', 1)[-1]
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def game_focused(exe_name: str = 'Cyberpunk2077.exe') -> bool:
    return foreground_process_name().lower() == exe_name.lower()


def game_focused_cached(max_age: float = 0.25) -> bool:
    """game_focused() avec un cache court : appele a chaque touche, il ne doit rien couter."""
    now = time.perf_counter()
    if now - _focus_cache[0] > max_age:
        _focus_cache[0] = now
        try:
            _focus_cache[1] = game_focused()
        except Exception:
            _focus_cache[1] = True
    return _focus_cache[1]


def key(scancode: int, down: bool, extended: bool = False) -> None:
    if down and not game_focused_cached():      # jeu hors premier plan : aucun appui (les relachements passent toujours)
        return
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(0, scancode, flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if down:
        _held.add(scancode)
        if extended: _held_ext.add(scancode)
    else:
        _held.discard(scancode); _held_ext.discard(scancode)


def tap(name: str, duration: float = 0.08) -> None:
    sc, ext = SC[name], name in EXTENDED
    key(sc, True, ext); time.sleep(duration); key(sc, False, ext)


def hold(name: str) -> None:
    key(SC[name], True, name in EXTENDED)


def release(name: str) -> None:
    key(SC[name], False, name in EXTENDED)


class held:
    """`with kbm.held('W', 'LSHIFT'):` : touches maintenues puis TOUJOURS relachees, meme sur exception."""
    def __init__(self, *names): self.names = names
    def __enter__(self):
        for n in self.names: hold(n)
        return self
    def __exit__(self, *exc):
        for n in reversed(self.names): release(n)
        return False


def look(dx: float, dy: float) -> None:
    """Deplacement souris RELATIF (camera). Accumule le reste fractionnaire
    (sinon une commande de <1 px repetee est silencieusement perdue)."""
    if not game_focused_cached():
        return
    _acc[0] += dx; _acc[1] += dy
    ix, iy = int(_acc[0]), int(_acc[1])
    _acc[0] -= ix; _acc[1] -= iy
    if ix == 0 and iy == 0:
        return
    inp = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(ix, iy, 0, MOUSEEVENTF_MOVE, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def look_primed(dx: float, dy: float) -> None:
    """
    Salve de visee avec amorce. Un rapport AHK sur ce jeu observe que le PREMIER
    MouseMove d'une salve fait un retour brutal, puis le reste se comporte
    normalement : on envoie une amorce negligeable (effet net nul) avant la
    vraie commande, pour ne jamais mesurer/calibrer sur ce premier echantillon.
    """
    look(1, 0); look(-1, 0)
    look(dx, dy)


def wheel(steps: int) -> None:
    """Molette : steps>0 = vers le haut, <0 = vers le bas (1 cran = 120). Navigue les
    choix de dialogue (verifie en jeu : la molette change la selection)."""
    data = (steps * WHEEL_DELTA) & 0xFFFFFFFF      # DWORD, valeur signee en complement a 2
    inp = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, data, MOUSEEVENTF_WHEEL, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


_MB = {'left': (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
       'right': (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
       'middle': (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}
_mouse_held: set[str] = set()


def mouse(button: str, down: bool) -> None:
    """Bouton souris : left = attaque/tir, right = parade/visee, middle = grenade (mapping)."""
    if down and not game_focused_cached():
        return
    flag = _MB[button][0 if down else 1]
    inp = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, flag, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    (_mouse_held.add if down else _mouse_held.discard)(button)


def mouse_tap(button: str = 'left', duration: float = 0.09) -> None:
    mouse(button, True); time.sleep(duration); mouse(button, False)


MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000


def game_client_rect() -> tuple[int, int, int, int] | None:
    """(gauche, haut, largeur, hauteur) en pixels ecran de la zone CLIENT de la fenetre du jeu au premier plan
    (mode fenetre : la barre de titre est exclue). None si le jeu n est pas au premier plan."""
    if not game_focused():
        return None
    hwnd = user32.GetForegroundWindow()
    rc = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return int(pt.x), int(pt.y), int(rc.right - rc.left), int(rc.bottom - rc.top)


def move_abs(x: int, y: int) -> None:
    """Place le curseur en coordonnees ECRAN absolues (bureau virtuel), pour cliquer dans une interface."""
    if not game_focused_cached():
        return
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)          # origine du bureau virtuel
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)          # taille du bureau virtuel
    if vw <= 0 or vh <= 0:
        vw, vh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1); vx = vy = 0
    nx = int((x - vx) * 65535 / max(1, vw - 1)); ny = int((y - vy) * 65535 / max(1, vh - 1))
    nx = max(0, min(65535, nx)); ny = max(0, min(65535, ny))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    inp = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(nx, ny, 0, flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def click(down: bool) -> None:
    flag = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    inp = INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(0, 0, 0, flag, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def release_all() -> None:
    for sc in list(_held):
        key(sc, False, sc in _held_ext)
    _held.clear()
    for b in list(_mouse_held):
        mouse(b, False)
    _mouse_held.clear()


load_user_keys()
atexit.register(release_all)
signal.signal(signal.SIGINT, lambda *a: (release_all(), sys.exit(0)))
signal.signal(signal.SIGTERM, lambda *a: (release_all(), sys.exit(0)))


class KillSwitch:
    """Touches globales (GetAsyncKeyState, meme hors focus) :
       F11 = BASCULE pause / reprise de l agent (entrees relachees en pause, le joueur reprend la main),
       F12 = arret definitif.
    `triggered` : arret ; `paused` : en pause ; `halt` : objet a passer aux competences comme `stop`
    (is_set() vrai en pause OU a l arret, pour qu elles rendent la main immediatement)."""
    VK_F11, VK_F12 = 0x7A, 0x7B

    class _Halt:
        def __init__(self, ks): self._ks = ks
        def is_set(self) -> bool: return self._ks.triggered.is_set() or self._ks.paused.is_set()

    def __init__(self) -> None:
        self.triggered = threading.Event()
        self.paused = threading.Event()
        self.halt = KillSwitch._Halt(self)
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self) -> None:
        f11_down = False
        while not self.triggered.is_set():
            if user32.GetAsyncKeyState(self.VK_F12) & 0x8000:
                self.triggered.set()
                release_all()
                print('[STOP] F12 : entrees relachees, arret definitif.')
                return
            down = bool(user32.GetAsyncKeyState(self.VK_F11) & 0x8000)
            if down and not f11_down:                       # front montant = bascule
                if self.paused.is_set():
                    self.paused.clear(); print('[F11] reprise : V rejoue.')
                else:
                    self.paused.set(); release_all(); print('[F11] pause : entrees relachees, a toi. F11 pour reprendre, F12 pour arreter.')
            f11_down = down
            time.sleep(0.02)


if __name__ == '__main__':
    print(f"admin : {is_admin()}")
    print(f"fenetre au premier plan : {foreground_process_name()!r}")
    print(f"Cyberpunk focus : {game_focused()}")
