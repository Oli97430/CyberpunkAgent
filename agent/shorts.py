"""Shorts prets a poster (19/10, Olivier : « 4 ») : chaque clip OBS est recadre en vertical 9:16 (1080x1920, centre de
l image), avec la phrase du narrateur incrustee en haut (police Impact, rouge HUD sur bandeau noir) -> dossier « Shorts »
a cote des clips. ffmpeg du systeme (PATH ou scoop) ; encodage en priorite basse, 2 fils : le jeu et le stream d abord.
Rien n est publie : les fichiers sont a poster a la main (YouTube Shorts, TikTok)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
from pathlib import Path

FONT = 'C:/Windows/Fonts/impact.ttf'
_lock = threading.Lock()               # un encodage a la fois


def ffmpeg_path() -> str | None:
    p = shutil.which('ffmpeg')
    if p:
        return p
    for c in (Path.home() / 'scoop' / 'shims' / 'ffmpeg.exe', Path('C:/ffmpeg/bin/ffmpeg.exe')):
        if c.exists():
            return str(c)
    return None


def caption_lines(text: str, width: int = 20, max_lines: int = 4) -> str:
    """Phrase -> lignes courtes (drawtext ne coupe pas) ; caracteres simples seulement."""
    from .narrator import clean
    t = clean(text, 90).upper()
    return '\n'.join(textwrap.wrap(t, width)[:max_lines])


def build_cmd(ffmpeg: str, src: str, dst: str, has_caption: bool) -> list[str]:
    vf = 'crop=ih*9/16:ih,scale=1080:1920,setsar=1'
    if has_caption:
        # police et texte copies dans le dossier temporaire (cwd) : pas de « C: » a echapper dans le graphe de filtres
        vf += (",drawtext=fontfile=font.ttf:textfile=caption.txt:fontsize=78:fontcolor=0xFF2B1A:line_spacing=10"
               ":box=1:boxcolor=black@0.6:boxborderw=24:x=(w-text_w)/2:y=h*0.10")
    return [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', src, '-vf', vf,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-threads', '2', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', dst]


def make(src: str, caption: str | None, log=print) -> str | None:
    """Clip -> Short vertical ; chemin du Short ou None (ffmpeg absent / erreur)."""
    ff = ffmpeg_path()
    if not ff:
        log('  [shorts] ffmpeg introuvable : pas de Short (le clip reste disponible)')
        return None
    out_dir = Path(src).parent / 'Shorts'
    out_dir.mkdir(exist_ok=True)
    dst = str(out_dir / (Path(src).stem + '_short.mp4'))
    cap = caption_lines(caption or '')
    with _lock, tempfile.TemporaryDirectory() as tmp:
        if cap and os.path.exists(FONT):
            Path(tmp, 'caption.txt').write_bytes(cap.encode('utf-8'))      # octets : pas de CRLF (ligne vide en plus)
            shutil.copyfile(FONT, os.path.join(tmp, 'font.ttf'))
        else:
            cap = ''
        flags = 0
        if os.name == 'nt':
            flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            r = subprocess.run(build_cmd(ff, src, dst, bool(cap)), cwd=tmp, capture_output=True, text=True,
                               timeout=600, creationflags=flags)
        except Exception as e:
            log(f'  [shorts] echec : {e}')
            return None
    if r.returncode != 0 or not os.path.exists(dst):
        log(f"  [shorts] echec ffmpeg : {(r.stderr or '').strip()[-160:]}")
        return None
    log(f'  [shorts] pret : {os.path.basename(dst)}')
    return dst
