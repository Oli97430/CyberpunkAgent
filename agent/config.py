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


INGAME_KEYS = {'provider', 'api_key', 'model', 'openai_model', 'anthropic_model', 'minutes',
               'features', 'courage', 'style', 'aggro', 'focus_tracked', 'language'}


def _ts(v) -> float:
    """Horodatage d un reglage (secondes epoch : time.time() / os.time() du mod) ; 0 si absent ou illisible."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def merge_ingame(user: dict, ing: dict) -> dict:
    """23/09 (revue : le panneau CET ecrasait en silence le panneau Windows, modele compris) : chaque reglage
    prend la valeur MODIFIEE EN DERNIER. Les deux panneaux horodatent chaque cle que l utilisateur change
    (_changed_at) -- pas la date du fichier : CyberpunkAgent-Config reenregistre tout a chaque « Lancer V ».
    Une valeur in-game SANS horodatage (ancien panneau, qui reecrivait tout, defauts compris) ne fait que
    combler une cle absente cote Windows. Modifie `user` en place ; renvoie {cle: horodatage in-game} des
    valeurs in-game retenues ('features.<nom>' pour un comportement). Liste blanche INGAME_KEYS : ce fichier
    est dans le dossier du JEU et ne doit jamais fixer un executable, une URL ou un chemin."""
    win_ts = user.get('_changed_at') if isinstance(user.get('_changed_at'), dict) else {}
    ts = ing.get('_changed_at') if isinstance(ing.get('_changed_at'), dict) else {}
    applied: dict = {}
    for k in ['provider'] + sorted(INGAME_KEYS - {'features', 'provider'}):     # fournisseur d abord (voir plus bas)
        v = ing.get(k)
        if v in ('', None):
            continue
        t = _ts(ts.get(k))
        if t <= 0:
            # ancien panneau : il ecrivait TOUT ; on ne comble jamais une cle API ni le modele d un fournisseur
            # inutilise (sinon le panneau Windows recopierait la cle en clair du dossier du jeu dans config.json)
            prov = str(user.get('provider') or 'ollama').lower()
            if (k == 'api_key' and prov == 'ollama') or (k == 'openai_model' and prov != 'openai') \
                    or (k == 'anthropic_model' and prov != 'anthropic'):
                continue
        # non horodatee : ne comble qu une cle que le panneau Windows n a jamais fixee (ni videe expres)
        if (t > 0 and t > _ts(win_ts.get(k))) or (t <= 0 and user.get(k) in ('', None) and _ts(win_ts.get(k)) <= 0):
            user[k] = v; applied[k] = t
    ifeats = ing.get('features') if isinstance(ing.get('features'), dict) else {}
    if ifeats:
        feats = dict(user.get('features') or {})
        for fk, fv in ifeats.items():
            t = _ts(ts.get('features.' + fk))
            if isinstance(fv, bool) and ((t > 0 and t > _ts(win_ts.get('features.' + fk)))
                                         or (t <= 0 and fk not in feats and _ts(win_ts.get('features.' + fk)) <= 0)):
                feats[fk] = fv; applied['features.' + fk] = t
        user['features'] = feats
    return applied


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
        # reglages saisis DANS LE JEU (fenetre CET du mod) : la valeur modifiee en dernier l emporte (merge_ingame)
        self._file_keys = {k for k, v in user.items() if v not in ('', None)} | {'features.' + f for f in (user.get('features') or {})}
        self.ingame_applied: dict = {}       # cle -> horodatage in-game (0 = ancien panneau) des valeurs in-game retenues
        self.warnings: list = []
        self.live: set = set()               # reglages changes en direct par directive (modele, courage...)
        ing_file = (self.mod_dir / 'agent_config.json') if self.mod_dir else None
        if ing_file and ing_file.exists():
            ing = _load_json(ing_file)
            self.ingame_applied = merge_ingame(user, ing)
            if ing.get('api_key') and str(user.get('provider') or 'ollama').lower() == 'ollama':
                self.warnings.append('cle API en clair dans le dossier du jeu (agent_config.json), inutile avec Ollama : '
                                     'bouton « Oublier les reglages in-game » du panneau CET pour l effacer')
        self.cet_log: Path | None = (self.game_dir / CET_LOG_REL) if self.game_dir else None
        self.user_settings = Path(user.get('user_settings') or detect_user_settings())
        self.ollama_exe = user.get('ollama_exe') or detect_ollama()
        self.ollama_url = str(user.get('ollama_url') or 'http://127.0.0.1:11434').rstrip('/')
        self.model = str(user.get('model') or 'llama3.2:latest')
        # fournisseur du modele de decision : "ollama" (local, defaut), "openai" ou "anthropic" (cle API)
        self.provider = (user.get('provider') or os.environ.get('CYBERPUNKAGENT_PROVIDER') or 'ollama').lower()
        self.api_key = user.get('api_key') or None
        self.openai_model = user.get('openai_model') or None
        self.anthropic_model = user.get('anthropic_model') or None
        self.openai_base_url = user.get('openai_base_url') or None
        if self.openai_base_url and not str(self.openai_base_url).lower().startswith(('https://', 'http://127.0.0.1', 'http://localhost')):
            print(f'  [config] openai_base_url refuse (https ou local seulement) : {self.openai_base_url}'); self.openai_base_url = None
        self.language = user.get('language', 'fr')
        # comportements activables (panneau de configuration / fenetre in-game) : tout est actif par defaut
        feats = user.get('features') or {}
        self.features = {k: bool(feats.get(k, True)) for k in ('radio', 'driving', 'rescue', 'sell', 'ripperdoc', 'buffs', 'stealth', 'sprint', 'steal', 'terminals', 'fasttravel', 'phone', 'sms', 'appearance', 'recipes')}
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

    def source(self, key: str) -> str:
        """D ou vient la valeur effective d un reglage ('features.<nom>' pour un comportement)."""
        if key in self.live:
            return 'directive en direct'
        if key in self.ingame_applied:
            return 'panneau in-game'
        return 'panneau Windows' if key in self._file_keys else 'defaut'

    def summary(self) -> str:
        """Config effective en une ligne, sans cle API (journal de debut de session, directive « config »)."""
        mk = 'model' if self.provider == 'ollama' else f'{self.provider}_model'
        mdl = self.model if self.provider == 'ollama' else (getattr(self, mk, None) or 'defaut du fournisseur')
        tsrc = ' / '.join(sorted({self.source(k) for k in ('courage', 'style', 'aggro')}))
        off = [k for k, v in self.features.items() if not v]
        fsrc = [k for k in self.features if self.source('features.' + k) == 'panneau in-game']
        return (f"fournisseur {self.provider} ({self.source('provider')}), modele {mdl} ({self.source(mk)}), "
                f"temperament {self.courage} / {self.style} / {self.aggro} ({tsrc}), "
                f"comportements coupes : {', '.join(off) or 'aucun'}"
                + (f" (regles en jeu : {', '.join(fsrc)})" if fsrc else ''))

    def state_file(self) -> Path:
        return (self.mod_dir or Path('.')) / 'state.bin'

    def as_dict(self) -> dict:
        return {'game_dir': str(self.game_dir) if self.game_dir else None, 'mod_dir': str(self.mod_dir) if self.mod_dir else None,
                'user_settings': str(self.user_settings), 'ollama_exe': self.ollama_exe, 'ollama_url': self.ollama_url,
                'model': self.model, 'provider': self.provider, 'api_key': ('***' if self.api_key else None),
                'openai_model': self.openai_model, 'anthropic_model': self.anthropic_model, 'features': self.features,
                'minutes': self.minutes, 'courage': self.courage, 'style': self.style, 'aggro': self.aggro, 'data_dir': str(DATA_DIR)}


CFG = Config()
