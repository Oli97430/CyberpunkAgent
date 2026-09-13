"""
build_release.py -- construit la release Windows :
  dist/CyberpunkAgent.exe                 l agent (run_agent.py, un seul fichier)
  dist/CyberpunkAgent-Setup.exe           l installateur (installer/install.py) qui embarque
                                          l agent, le mod Lua, README, LICENSE, CHANGELOG
  dist/CyberpunkAgent-v<version>-win64.zip  archive de release (exe + setup + mod + docs)

    pip install pyinstaller
    python build_release.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

VERSION = '0.1.0'
ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'
PAYLOAD = BUILD / 'payload'


def run(cmd: list[str]) -> None:
    print('>', ' '.join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def pyinstaller(script: Path, name: str, add_data: list[tuple[Path, str]], icon: Path | None = None, windowed: bool = False) -> Path:
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed' if windowed else '--console',
           '--name', name, '--distpath', str(DIST), '--workpath', str(BUILD / 'work'), '--specpath', str(BUILD),
           '--exclude-module', 'dxcam', '--exclude-module', 'cv2', '--exclude-module', 'numpy', '--exclude-module', 'vgamepad',
           '--exclude-module', 'PIL'] + ([] if windowed else ['--exclude-module', 'tkinter'])
    for src, dst in add_data:
        cmd += ['--add-data', f'{src};{dst}']
    if icon and icon.exists():
        cmd += ['--icon', str(icon)]
    cmd.append(str(script))
    run(cmd)
    exe = DIST / f'{name}.exe'
    assert exe.exists(), f'{exe} manquant'
    return exe


def main() -> None:
    DIST.mkdir(exist_ok=True)
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True)

    # 1. l agent + le panneau de configuration (fenetre tkinter)
    agent_exe = pyinstaller(ROOT / 'run_agent.py', 'CyberpunkAgent', [])
    gui_exe = pyinstaller(ROOT / 'agent_gui.py', 'CyberpunkAgent-Config', [], windowed=True)

    # 2. la charge utile de l installateur : agent, mod, docs, sources minimales (detection du jeu)
    shutil.copy2(agent_exe, PAYLOAD / 'CyberpunkAgent.exe')
    shutil.copy2(gui_exe, PAYLOAD / 'CyberpunkAgent-Config.exe')
    (PAYLOAD / 'mod' / 'AgentProbe').mkdir(parents=True)
    shutil.copy2(ROOT / 'mod' / 'AgentProbe' / 'init.lua', PAYLOAD / 'mod' / 'AgentProbe' / 'init.lua')   # JAMAIS agent_config.json (cle API), db, state
    for f in ('README.md', 'LICENSE', 'CHANGELOG.md'):
        shutil.copy2(ROOT / f, PAYLOAD / f)
    (PAYLOAD / 'src' / 'agent').mkdir(parents=True)
    shutil.copy2(ROOT / 'agent' / 'config.py', PAYLOAD / 'src' / 'agent' / 'config.py')
    (PAYLOAD / 'src' / 'agent' / '__init__.py').write_text('', encoding='utf-8')

    # 3. l installateur
    setup_exe = pyinstaller(ROOT / 'installer' / 'install.py', 'CyberpunkAgent-Setup', [(PAYLOAD, 'payload')])

    # 4. l archive
    zip_path = DIST / f'CyberpunkAgent-v{VERSION}-win64.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(agent_exe, 'CyberpunkAgent.exe')
        z.write(gui_exe, 'CyberpunkAgent-Config.exe')
        z.write(setup_exe, 'CyberpunkAgent-Setup.exe')
        for f in ('README.md', 'LICENSE', 'CHANGELOG.md'):
            z.write(ROOT / f, f)
        z.write(ROOT / 'mod' / 'AgentProbe' / 'init.lua', 'mod/AgentProbe/init.lua')
    print('\nrelease :')
    for f in (agent_exe, gui_exe, setup_exe, zip_path):
        print(f'  {f.name:40s} {f.stat().st_size / 1e6:6.1f} Mo')


if __name__ == '__main__':
    main()
