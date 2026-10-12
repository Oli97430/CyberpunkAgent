"""Succes Steam (12/10, Olivier : « son but est aussi de debloquer un maximum de succes steam »).

LECTURE SEULE du cache local de Steam (appcache\\stats) : schema (noms, descriptions FR) + bits debloques de l utilisateur.
Jamais d ecriture, jamais d API Steam, jamais AchievementSystem.UnlockAchievement / UnlockAllAchievementsRequest : V gagne
les succes en jouant. L identifiant du compte n est jamais journalise."""
from __future__ import annotations

import os
import struct
import time
from pathlib import Path

APPID = '1091500'
# hors de portee sur cette installation / sauvegarde (recherche du 12/10) : Phantom Liberty non installe ; The Heist passee
PL = {'AllThePresidentsMen', 'DirtyWork', 'DogtownLaw', 'EasyComeEasyGo', 'HydraIsDead', 'IHateSpider', 'KingOfTheCups',
      'KingOfThePentacles', 'KingOfTheSwords', 'KingOfTheWands', 'Kingmaker', 'RelicMaster', 'TheTower'}
PASSED = {'TheLovers'}
_STATE: dict = {'mtime': None, 'unlocked': None, 'names': {}, 't': -1e9}


def _steam_dir() -> Path | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            p = Path(str(winreg.QueryValueEx(k, 'SteamPath')[0]))
            if p.exists():
                return p
    except Exception:
        pass
    for p in (Path(r'E:\steam'), Path(r'C:\Program Files (x86)\Steam')):
        if p.exists():
            return p
    return None


def _kv(buf: bytes, i: int = 0):
    """KeyValues binaire de Steam -> (dict, position). Ne leve jamais d erreur fatale (l appelant protege)."""
    out = {}
    n = len(buf)
    while i < n:
        t = buf[i]; i += 1
        if t in (0x08, 0x0B):
            return out, i
        j = buf.index(b'\x00', i); key = buf[i:j].decode('utf-8', 'replace'); i = j + 1
        if t == 0x00:
            out[key], i = _kv(buf, i)
        elif t == 0x01:
            j = buf.index(b'\x00', i); out[key] = buf[i:j].decode('utf-8', 'replace'); i = j + 1
        elif t in (0x02, 0x04, 0x06):
            out[key] = struct.unpack_from('<i', buf, i)[0]; i += 4
        elif t == 0x03:
            out[key] = struct.unpack_from('<f', buf, i)[0]; i += 4
        elif t in (0x07, 0x0A):
            out[key] = struct.unpack_from('<q', buf, i)[0]; i += 8
        elif t == 0x05:
            j = i
            while buf[j:j + 2] != b'\x00\x00':
                j += 2
            out[key] = buf[i:j].decode('utf-16-le', 'replace'); i = j + 2
        else:
            return out, n
    return out, i


def _user_file(stats: Path) -> Path | None:
    files = [p for p in stats.glob(f'UserGameStats_*_{APPID}.bin')]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def read_steam() -> dict | None:
    """{'names': {api: (fr, en, desc_fr)}, 'unlocked': set(api), 'mtime': float} ou None si illisible."""
    try:
        sd = _steam_dir()
        if sd is None:
            return None
        stats = sd / 'appcache' / 'stats'
        schema = stats / f'UserGameStatsSchema_{APPID}.bin'
        user = _user_file(stats)
        if not schema.exists() or user is None:
            return None
        sch, _ = _kv(schema.read_bytes())
        usr, _ = _kv(user.read_bytes())
        root = sch.get(APPID) or next(iter(sch.values()), {})
        cache = usr.get('cache') or next(iter(usr.values()), {})
        names, unlocked = {}, set()
        for sid, sv in (root.get('stats') or {}).items():
            if not isinstance(sv, dict) or not isinstance(sv.get('bits'), dict):
                continue
            data = int(((cache.get(sid) or {}).get('data')) or 0) if isinstance(cache, dict) else 0
            for b, bv in sv['bits'].items():
                api = str(bv.get('name') or '')
                disp = bv.get('display') or {}
                nm, ds = disp.get('name') or {}, disp.get('desc') or {}
                names[api] = (str(nm.get('french') or nm.get('english') or api), str(nm.get('english') or api), str(ds.get('french') or ds.get('english') or ''))
                if (data >> int(b)) & 1:
                    unlocked.add(api)
        return {'names': names, 'unlocked': unlocked, 'mtime': user.stat().st_mtime}
    except Exception:
        return None


def start(log=print) -> None:
    """Debut de session : bilan Steam (lecture seule)."""
    r = read_steam()
    if r is None:
        log('[succes] statut Steam illisible (cache local absent) : on joue quand meme')
        return
    _STATE.update(mtime=r['mtime'], unlocked=set(r['unlocked']), names=r['names'], t=time.perf_counter())
    tot = len(r['names'])
    left = [a for a in r['names'] if a not in r['unlocked'] and a not in PL and a not in PASSED]
    log(f"[succes] Steam {len(r['unlocked'])}/{tot} debloques ({len(PL)} Phantom Liberty hors jeu, {len(PASSED)} passe dans cette sauvegarde) ; "
        f"{len(left)} a gagner en jouant")


def tick(log=print, notify=None) -> None:
    """Toutes les 5 min : nouveau succes debloque (cache Steam) -> journal (+ Telegram)."""
    if time.perf_counter() - _STATE['t'] < 300.0 or _STATE['unlocked'] is None:
        return
    _STATE['t'] = time.perf_counter()
    r = read_steam()
    if r is None or r['mtime'] == _STATE['mtime']:
        return
    new = sorted(set(r['unlocked']) - _STATE['unlocked'])
    _STATE.update(mtime=r['mtime'], unlocked=set(r['unlocked']), names=r['names'])
    for a in new:
        fr = r['names'].get(a, (a, a, ''))[0]
        log(f'[succes] DEBLOQUE (Steam) : {fr} ({a}) -- {len(r["unlocked"])}/{len(r["names"])}')
        if notify:
            try:
                notify(f'Succes debloque : {fr} ({len(r["unlocked"])}/{len(r["names"])})')
            except Exception:
                pass


def status_text() -> str:
    u, names = _STATE.get('unlocked'), _STATE.get('names') or {}
    if u is None:
        return 'succes Steam : statut inconnu'
    return f'succes Steam : {len(u)}/{len(names)} debloques'
