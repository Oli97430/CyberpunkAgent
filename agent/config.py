"""
config.py -- chemins et reglages de l agent, sans rien de code en dur.

Ordre de priorite :
  1. config.json a cote de l executable / du depot (ou %APPDATA%\\CyberpunkAgent\\config.json) ;
  2. detection automatique : dossier du jeu via Steam (registre + libraryfolders.vdf) ou GOG/Epic
     (chemins habituels), reglages du joueur dans %LOCALAPPDATA%, Ollama dans le PATH ou ses
     emplacements d installation usuels.
Les fichiers produits par l agent (journaux, memoire des lieux dangereux) vont dans DATA_DIR
(%APPDATA%\\CyberpunkAgent), jamais dans le dossier du jeu ni dans le dossier du programme.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

APP_NAME = 'CyberpunkAgent'
MOD_NAME = 'AgentProbe'
MOD_REL = Path('bin') / 'x64' / 'plugins' / 'cyber_engine_tweaks' / 'mods' / MOD_NAME
CET_LOG_REL = Path('bin') / 'x64' / 'plugins' / 'cyber_engine_tweaks' / 'scripting.log'

DATA_DIR = Path(os.environ.get('APPDATA', str(Path.home()))) / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _base_dir() -> Path:
    """Dossier du programme : celui de l exe (PyInstaller) ou la racine du depot."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _steam_libraries() -> list[Path]:
    libs: list[Path] = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam')):
            try:
                k = winreg.OpenKey(hive, key)
                for name in ('SteamPath', 'InstallPath'):
                    try:
                        libs.append(Path(winreg.QueryValueEx(k, name)[0]))
                    except OSError:
                        pass
            except OSError:
                pass
    except Exception:
        pass
    libs.append(Path(r'C:\Program Files (x86)\Steam'))
    out: list[Path] = []
    for steam in libs:
        vdf = steam / 'steamapps' / 'libraryfolders.vdf'
        if vdf.exists():
            try:
                for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding='utf-8', errors='ignore')):
                    out.append(Path(m.group(1).replace('\\\\', '\\')))
            except Exception:
                pass
        out.append(steam)
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k); uniq.append(p)
    return uniq


def detect_game_dir() -> Path | None:
    """Dossier « Cyberpunk 2077 » contenant bin\\x64\\Cyberpunk2077.exe (Steam, GOG, Epic)."""
    cands: list[Path] = []
    for lib in _steam_libraries():
        cands.append(lib / 'steamapps' / 'common' / 'Cyberpunk 2077')
    for root in (r'C:\Program Files (x86)\GOG Galaxy\Games', r'C:\GOG Games', r'C:\Program Files\Epic Games',
                 r'D:\Games', r'E:\Games', r'F:\Games'):
        cands.append(Path(root) / 'Cyberpunk 2077')
    for c in cands:
        if (c / 'bin' / 'x64' / 'Cyberpunk2077.exe').exists():
            return c
    return None


def detect_user_settings() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'CD Projekt Red' / 'Cyberpunk 2077' / 'UserSettings.json'


def detect_ollama() -> str | None:
    exe = shutil.which('ollama')
    if exe:
        return exe
    for c in (Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe',
              Path(r'C:\Program Files\Ollama\ollama.exe'),
              Path.home() / '.openhuman' / 'bin' / 'ollama' / 'ollama.exe'):
        if c.exists():
            return str(c)
    return None


class Config:
    def __init__(self) -> None:
        base = _base_dir()
        user = {}
        for p in (base / 'config.json', DATA_DIR / 'config.json'):
            user.update(_load_json(p))
        self.base_dir = base
        gd = user.get('game_dir')
        self.game_dir: Path | None = Path(gd) if gd else detect_game_dir()
        self.mod_dir: Path | None = (self.game_dir / MOD_REL) if self.game_dir else None
        # reglages saisis DANS LE JEU (fenetre CET du mod) : ecrits par le mod dans son dossier, prioritaires
        if self.mod_dir and (self.mod_dir / 'agent_config.json').exists():
            user.update({k: v for k, v in _load_json(self.mod_dir / 'agent_config.json').items() if v not in ('', None)})
        self.cet_log: Path | None = (self.game_dir / CET_LOG_REL) if self.game_dir else None
        self.user_settings = Path(user.get('user_settings') or detect_user_settings())
        self.ollama_exe = user.get('ollama_exe') or detect_ollama()
        self.ollama_url = user.get('ollama_url', 'http://127.0.0.1:11434')
        self.model = user.get('model', 'llama3.2:latest')
        # fournisseur du modele de decision : "ollama" (local, defaut), "openai" ou "anthropic" (cle API)
        self.provider = (user.get('provider') or os.environ.get('CYBERPUNKAGENT_PROVIDER') or 'ollama').lower()
        self.api_key = user.get('api_key') or None
        self.openai_model = user.get('openai_model') or None
        self.anthropic_model = user.get('anthropic_model') or None
        self.openai_base_url = user.get('openai_base_url') or None
        self.language = user.get('language', 'fr')
        # comportements activables (panneau de configuration / fenetre in-game) : tout est actif par defaut
        feats = user.get('features') or {}
        self.features = {k: bool(feats.get(k, True)) for k in ('radio', 'driving', 'rescue', 'sell', 'ripperdoc', 'buffs', 'stealth', 'fasttravel', 'phone', 'sms', 'appearance', 'recipes')}
        self.minutes = int(user.get('minutes') or 20)
        # temperament : courage (prudent / equilibre / temeraire), style de combat (melee / mixte / distance),
        # agressivite (defensif = ne se bat que s il est attaque ; normal ; chasseur = attaque tout hostile en vue)
        # la quete SUIVIE (assignee par le joueur) est prioritaire sur tout le reste (courses, charcudoc, miroir, changements)
        self.focus_tracked = bool(user.get('focus_tracked', True))
        self.courage = str(user.get('courage') or 'temeraire').lower()
        self.style = str(user.get('style') or 'melee').lower()
        self.aggro = str(user.get('aggro') or 'normal').lower()
        # seuils derives du courage : (hostiles pour EVITER, hostiles pour EVITER si vie < 60, hostiles pour FUIR, vie mini pour SECOURIR, portee d engagement m)
        self.courage_t = {'prudent': (5, 4, 5, 80, 25), 'equilibre': (6, 5, 6, 60, 25), 'temeraire': (8, 7, 8, 40, 35)}.get(self.courage, (8, 7, 8, 40, 35))
        self.log_file = DATA_DIR / 'brain_log.txt'
        self.deaths_file = DATA_DIR / 'deaths.json'

    def state_file(self) -> Path:
        return (self.mod_dir or Path('.')) / 'state.bin'

    def as_dict(self) -> dict:
        return {'game_dir': str(self.game_dir) if self.game_dir else None, 'mod_dir': str(self.mod_dir) if self.mod_dir else None,
                'user_settings': str(self.user_settings), 'ollama_exe': self.ollama_exe, 'ollama_url': self.ollama_url,
                'model': self.model, 'provider': self.provider, 'api_key': ('***' if self.api_key else None),
                'openai_model': self.openai_model, 'anthropic_model': self.anthropic_model, 'features': self.features,
                'minutes': self.minutes, 'courage': self.courage, 'style': self.style, 'aggro': self.aggro, 'data_dir': str(DATA_DIR)}


CFG = Config()
