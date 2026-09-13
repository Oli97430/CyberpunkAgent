"""
install.py -- installateur de CyberpunkAgent (compile en CyberpunkAgent-Setup.exe par build_release.py).

Ce qu il fait, dans l ordre, en expliquant chaque etape :
  1. trouve le dossier du jeu (Steam / GOG / Epic) ou le demande ;
  2. verifie Cyber Engine Tweaks (obligatoire : le mod Lua tourne dedans) ;
  3. copie le mod `AgentProbe` dans <jeu>/bin/x64/plugins/cyber_engine_tweaks/mods/ ;
  4. installe le programme (CyberpunkAgent.exe, README, LICENSE) dans %LOCALAPPDATA%/Programs/CyberpunkAgent ;
  5. verifie Ollama et telecharge le modele de decision (llama3.2, ~2 Go) si absent ;
  6. ecrit config.json et cree un raccourci sur le Bureau.

Mode silencieux : CyberpunkAgent-Setup.exe /S [/GAME="D:\\...\\Cyberpunk 2077"]
Les fichiers a installer sont embarques a cote de ce script (dossier `payload`) par PyInstaller.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP = 'CyberpunkAgent'
MOD = 'AgentProbe'
MODEL = 'llama3.2:latest'
CET_URL = 'https://www.nexusmods.com/cyberpunk2077/mods/107'
OLLAMA_URL = 'https://ollama.com/download/windows'

SILENT = any(a.lower().rstrip('/\\') in ('/s', '-s', '--silent', 's:') for a in sys.argv[1:]) or not sys.stdin.isatty()


def pause_exit(msg: str) -> None:
    """Attend Entree seulement si une console interactive est la (sinon rien a lire)."""
    if SILENT:
        return
    try:
        input(msg)
    except EOFError:
        pass
ARG_GAME = next((a.split('=', 1)[1].strip('"') for a in sys.argv[1:] if a.upper().startswith('/GAME=')), None)


def payload_dir() -> Path:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'payload'


def say(msg: str) -> None:
    print(msg, flush=True)


def ask(prompt: str, default: str = '') -> str:
    if SILENT:
        return default
    try:
        v = input(f'{prompt} [{default}] : ').strip()
    except EOFError:
        return default
    return v or default


def yes(prompt: str, default: bool = True) -> bool:
    if SILENT:
        return default
    v = ask(prompt + (' (O/n)' if default else ' (o/N)'), 'o' if default else 'n').lower()
    return v.startswith(('o', 'y'))


def find_game() -> Path | None:
    sys.path.insert(0, str(payload_dir() / 'src'))
    try:
        from agent.config import detect_game_dir
        return detect_game_dir()
    except Exception:
        return None


def step_game() -> Path:
    say('\n[1/6] Dossier du jeu')
    g = Path(ARG_GAME) if ARG_GAME else find_game()
    while True:
        if g and (g / 'bin' / 'x64' / 'Cyberpunk2077.exe').exists():
            say(f'      trouve : {g}')
            if SILENT or yes('      utiliser ce dossier ?'):
                return g
        else:
            say('      Cyberpunk 2077 introuvable automatiquement.')
        if SILENT:
            say('      ERREUR : passe /GAME="<dossier du jeu>"'); sys.exit(2)
        g = Path(ask('      chemin du dossier "Cyberpunk 2077"', str(g) if g else r'C:\Program Files (x86)\Steam\steamapps\common\Cyberpunk 2077'))


def step_cet(game: Path) -> None:
    say('\n[2/6] Cyber Engine Tweaks')
    cet = game / 'bin' / 'x64' / 'plugins' / 'cyber_engine_tweaks'
    if cet.exists() and (game / 'bin' / 'x64' / 'version.dll').exists():
        say('      present.'); return
    say(f'      ABSENT. Le mod tourne dans Cyber Engine Tweaks : installe-le d abord ({CET_URL}),')
    say('      lance le jeu une fois pour choisir la touche de la console, puis relance cet installateur.')
    pause_exit('      Entree pour quitter...')
    sys.exit(3)


def step_mod(game: Path) -> Path:
    say('\n[3/6] Mod AgentProbe')
    dst = game / 'bin' / 'x64' / 'plugins' / 'cyber_engine_tweaks' / 'mods' / MOD
    src = payload_dir() / 'mod' / MOD
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    say(f'      installe dans {dst}')
    say('      (Le jeu doit etre relance pour charger le mod.)')
    return dst


def step_program() -> Path:
    say('\n[4/6] Programme')
    dst = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / APP
    dst.mkdir(parents=True, exist_ok=True)
    for name in ('CyberpunkAgent.exe', 'CyberpunkAgent-Config.exe', 'README.md', 'LICENSE', 'CHANGELOG.md'):
        f = payload_dir() / name
        if f.exists():
            shutil.copy2(f, dst / name)
    say(f'      installe dans {dst}')
    return dst


def ollama_exe() -> str | None:
    exe = shutil.which('ollama')
    if exe:
        return exe
    for c in (Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe', Path(r'C:\Program Files\Ollama\ollama.exe')):
        if c.exists():
            return str(c)
    return None


def step_provider() -> dict:
    """Choix du fournisseur du modele de decision. La cle est saisie sans echo et ecrite dans
    config.json (%APPDATA%/CyberpunkAgent) : elle ne quitte pas ce PC autrement que vers l API choisie."""
    say('\n[5/6] Modele de decision')
    say('      1. Ollama, modele local (gratuit, rien ne sort du PC, ~2 Go de VRAM)  [defaut]')
    say('      2. OpenAI (cle API, gpt-4o-mini par defaut)')
    say('      3. Anthropic / Claude (cle API)')
    choice = ask('      choix', '1')
    prov = {'2': 'openai', '3': 'anthropic'}.get(choice, 'ollama')
    cfg = {'provider': prov}
    if prov != 'ollama':
        import getpass
        env = 'OPENAI_API_KEY' if prov == 'openai' else 'ANTHROPIC_API_KEY'
        if os.environ.get(env):
            say(f'      variable {env} detectee : elle sera utilisee (rien a saisir).')
        elif not SILENT:
            try:
                key = getpass.getpass(f'      cle API {prov} (saisie masquee, Entree pour la renseigner plus tard dans config.json) : ').strip()
            except Exception:
                key = ''
            if key:
                cfg['api_key'] = key
        if prov == 'openai':
            m = ask('      modele OpenAI', 'gpt-4o-mini'); cfg['openai_model'] = m
        else:
            m = ask('      modele Anthropic', 'claude-haiku-4-5-20251001'); cfg['anthropic_model'] = m
    return cfg


def step_ollama(prov: str) -> str | None:
    say('\n[5/6] Ollama (modele local de decision)' if prov == 'ollama' else '\n      Ollama (optionnel : secours local)')
    exe = ollama_exe()
    if prov != 'ollama':
        return exe
    if not exe:
        say(f'      Ollama est absent : installe-le ({OLLAMA_URL}), puis `ollama pull {MODEL}`.')
        say('      L agent fonctionne sans, avec ses regles seules (dialogues moins pertinents).')
        return None
    say(f'      trouve : {exe}')
    try:
        out = subprocess.run([exe, 'list'], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        out = ''
    if MODEL.split(':')[0] in out:
        say(f'      modele {MODEL} deja present.')
    elif SILENT or yes(f'      telecharger le modele {MODEL} (~2 Go) maintenant ?'):
        say('      telechargement (quelques minutes selon la connexion)...')
        try:
            subprocess.run([exe, 'pull', MODEL], timeout=3600)
        except Exception as e:
            say(f'      echec du telechargement : {e} (refais `ollama pull {MODEL}` plus tard)')
    return exe


def step_config(game: Path, prog: Path, ollama: str | None, prov_cfg: dict | None = None) -> None:
    say('\n[6/6] Configuration et raccourci')
    data = Path(os.environ.get('APPDATA', str(Path.home()))) / APP
    data.mkdir(parents=True, exist_ok=True)
    cfg = {'game_dir': str(game), 'ollama_exe': ollama, 'model': MODEL, 'ollama_url': 'http://127.0.0.1:11434'}
    cfg.update(prov_cfg or {})
    (data / 'config.json').write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    say(f'      config : {data / "config.json"}')
    # raccourci Bureau via PowerShell (pas de dependance COM cote Python)
    exe = prog / 'CyberpunkAgent.exe'
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\{APP}.lnk');"
          f"$s.TargetPath='{exe}';$s.Arguments='20';$s.WorkingDirectory='{prog}';$s.Description='V joue seul (F11 = pause/reprise, F12 = arret)';$s.Save()")
    gui = prog / 'CyberpunkAgent-Config.exe'
    ps2 = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\{APP} Configuration.lnk');"
           f"$s.TargetPath='{gui}';$s.WorkingDirectory='{prog}';$s.Description='Reglages de l agent (modele, cle API, comportements)';$s.Save()")
    try:
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], capture_output=True, timeout=30)
        if gui.exists():
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps2], capture_output=True, timeout=30)
        say('      raccourcis « CyberpunkAgent » et « CyberpunkAgent Configuration » crees sur le Bureau.')
    except Exception:
        say(f'      (raccourci non cree : lance {exe} directement)')


def main() -> None:
    say('=' * 64)
    say('  CyberpunkAgent -- installation (V joue seul a Cyberpunk 2077)')
    say('=' * 64)
    game = step_game()
    step_cet(game)
    step_mod(game)
    prog = step_program()
    prov_cfg = step_provider()
    ollama = step_ollama(prov_cfg['provider'])
    step_config(game, prog, ollama, prov_cfg)
    say('\nTermine. Pour jouer :')
    say('  1. lance Cyberpunk 2077, charge une partie, V a pied ;')
    say('  2. double-clique le raccourci CyberpunkAgent (ou CyberpunkAgent.exe 30 pour 30 minutes) ;')
    say('  3. reviens sur le jeu : V prend la main. F11 = pause / reprise, F12 = arret.')
    pause_exit('\nEntree pour fermer...')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
