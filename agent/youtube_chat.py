"""Chat YouTube en direct -> directives de V (18/10/2026, Olivier : « V doit pouvoir lire le chat youtube et agir en direct »).

Lecture SEULE du chat public du direct, par la meme interface que la page `youtube.com/live_chat` (pas de cle API, pas de
quota, aucune ecriture). Seuls les messages qui commencent par « ! » sont des commandes ; elles rejoignent la meme file
que les directives Telegram / du panneau in-game (remote._Q).

Garde-fous :
- spectateurs : actions de jeu seulement (VIEWER_CMDS) ; stop / pause / reglages = proprietaire de la chaine et moderateurs ;
- 1 commande par spectateur toutes les 60 s, 1 commande acceptee toutes les 15 s pour tout le chat ;
- messages anterieurs au demarrage ignores ; texte limite a 80 caracteres.

Configuration : %APPDATA%/CyberpunkAgent/youtube.json
    {"channel": "@MaChaine"}            (ou "channel_id": "UC...", ou "video_id": "..." pour un direct precis)
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

from .config import DATA_DIR

YOUTUBE_FILE = DATA_DIR / 'youtube.json'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
VIEWER_CMDS = {'attaque', 'objectif', 'explore', 'marchand', 'charcudoc', 'soigne', 'changer_quete'}
MOD_CMDS = VIEWER_CMDS | {'stop', 'pause', 'reprendre', 'courage', 'style', 'aggro', 'photo', 'hud'}   # photo -> Telegram d Olivier
ALIASES = {'attack': 'attaque', 'fight': 'attaque', 'quest': 'objectif', 'quete': 'objectif', 'explorer': 'explore',
           'vendre': 'marchand', 'shop': 'marchand', 'heal': 'soigne', 'soin': 'soigne', 'ripperdoc': 'charcudoc',
           'autrequete': 'changer_quete', 'skip': 'changer_quete', 'change': 'changer_quete'}
USER_CD, GLOBAL_CD = 60.0, 15.0
BANNER = 'CHAT : !ATTAQUE  !OBJECTIF  !SKIP  !EXPLORE  !SHOP  !CHARCUDOC  !HEAL   (1 COMMANDE / MIN)'


def _banner(on: bool, last: str | None = None) -> None:
    """Bandeau du HUD (bas de l ecran, visible dans le direct) : commandes du chat + derniere commande acceptee."""
    try:
        from . import hud
        if not on:
            hud.update(banner=None); return
        kw = {'banner': BANNER}
        if last:
            kw.update(banner_last=hud.ascii_up(last)[:60], banner_last_t=int(time.time()))
        hud.update(**kw)
    except Exception:
        pass
_started = False
STATUS = {'video_id': None, 'messages': 0, 'accepted': 0, 'last': None, 'error': None}


def _get(url: str, data: bytes | None = None, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept-Language': 'fr-FR,fr;q=0.9',
                                                          **({'Content-Type': 'application/json'} if data else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _cfg() -> dict:
    try:
        d = json.loads(YOUTUBE_FILE.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def find_live_video(cfg: dict) -> str | None:
    """ID de la video du direct EN COURS de la chaine (page /live -> lien canonique watch?v=...)."""
    if cfg.get('video_id'):
        return str(cfg['video_id'])
    ch = cfg.get('channel') or ''
    if cfg.get('channel_id'):
        url = f"https://www.youtube.com/channel/{cfg['channel_id']}/live"
    elif ch:
        url = f"https://www.youtube.com/{ch if ch.startswith('@') else '@' + ch}/live"
    else:
        return None
    html = _get(url)
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', html)
    if m and ('"isLive":true' in html or '"isLiveNow":true' in html or 'liveStreamability' in html):
        return m.group(1)
    return None


def _initial(video_id: str) -> tuple[str, str, str]:
    """Page du chat : (cle API innertube, version du client, jeton « Chat en direct »). 18/10 : les jetons « invalidation »
    renvoyes ensuite attendent des notifications poussees (rien en interrogation) ; le jeton de RECHARGEMENT du flux
    « Chat en direct » renvoie a chaque appel les 20 derniers messages -> interrogation toutes les 4 s + dedoublonnage."""
    html = _get(f'https://www.youtube.com/live_chat?is_popout=1&v={video_id}')
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    ver = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    cont = None
    m = re.search(r'(?:window\["ytInitialData"\]|var ytInitialData)\s*=\s*(\{.*?\});\s*</script>', html, re.S)
    if m:
        try:
            d = json.loads(m.group(1))
            items = d['contents']['liveChatRenderer']['header']['liveChatHeaderRenderer']['viewSelector']['sortFilterSubMenuRenderer']['subMenuItems']
            cont = items[-1]['continuation']['reloadContinuationData']['continuation']    # « Chat en direct » (tous les messages)
        except Exception:
            cont = None
    if not cont:
        c2 = re.search(r'"continuation":"([^"]+)"', html)
        cont = c2.group(1) if c2 else None
    if not (key and cont):
        raise RuntimeError('chat introuvable (direct termine, chat desactive ou reserve aux abonnes ?)')
    return key.group(1), (ver.group(1) if ver else '2.20240101.00.00'), cont


def _text(runs: list) -> str:
    out = []
    for r in runs or []:
        if 'text' in r:
            out.append(r['text'])
        elif 'emoji' in r:
            out.append(((r['emoji'].get('shortcuts') or [''])[0]))
    return ''.join(out).strip()


def parse_actions(data: dict) -> tuple[list[dict], str | None, float]:
    """Reponse get_live_chat -> (messages, jeton suivant, attente en s)."""
    cc = ((data.get('continuationContents') or {}).get('liveChatContinuation') or {})
    msgs = []
    for a in cc.get('actions') or []:
        it = ((a.get('addChatItemAction') or {}).get('item') or {}).get('liveChatTextMessageRenderer')
        if not it:
            continue
        badges = ' '.join(str(((b.get('liveChatAuthorBadgeRenderer') or {}).get('icon') or {}).get('iconType') or '')
                          for b in it.get('authorBadges') or [])
        msgs.append({'id': it.get('id'), 'author': (it.get('authorName') or {}).get('simpleText') or '?',
                     'text': _text((it.get('message') or {}).get('runs')),
                     'ts': int(it.get('timestampUsec') or 0) / 1e6,
                     'mod': 'MODERATOR' in badges or 'OWNER' in badges})
    nxt, wait = None, 3.0
    for c in cc.get('continuations') or []:
        for k in ('invalidationContinuationData', 'timedContinuationData', 'reloadContinuationData'):
            if k in c:
                nxt = c[k].get('continuation') or nxt
                wait = max(1.5, min(10.0, int(c[k].get('timeoutMs') or 3000) / 1000.0))
    return msgs, nxt, wait


def to_command(text: str, mod: bool) -> str | None:
    """« !attaque » -> 'attaque' (si autorise pour cet auteur), sinon None. Parametre eventuel conserve (« !style mixte »)."""
    t = text.strip()
    if not t.startswith('!'):
        return None
    parts = t[1:].strip().lower().split()
    if not parts:
        return None
    cmd = ALIASES.get(parts[0], parts[0])
    allowed = MOD_CMDS if mod else VIEWER_CMDS
    if cmd not in allowed:
        return None
    return ' '.join([cmd] + parts[1:2])[:80]


def _loop(log, put) -> None:
    t_start = time.time()
    last_user: dict[str, float] = {}
    last_global = [0.0]
    seen: set[str] = set()
    while True:
        cfg = _cfg()
        try:
            vid = find_live_video(cfg)
            if not vid:
                STATUS.update(video_id=None, error='aucun direct en cours')
                _banner(False)
                time.sleep(60.0); continue
            key, ver, cont = _initial(vid)
            STATUS.update(video_id=vid, error=None)
            log(f'  [youtube] chat du direct {vid} : en ecoute (commandes « ! »)')
            _banner(True)
            t_page = time.time()
            while cont and time.time() - t_page < 600.0:           # page rechargee toutes les 10 min (jeton frais)
                body = json.dumps({'context': {'client': {'clientName': 'WEB', 'clientVersion': ver}}, 'continuation': cont}).encode()
                data = json.loads(_get(f'https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={key}&prettyPrint=false', body))
                msgs, nxt, wait = parse_actions(data)
                now = time.time()
                for m in msgs:
                    if m['id'] in seen or (m['ts'] and m['ts'] < t_start):
                        continue
                    seen.add(m['id'])
                    STATUS['messages'] += 1
                    cmd = to_command(m['text'], m['mod'])
                    if not cmd:
                        continue
                    if not m['mod'] and (now - last_user.get(m['author'], 0.0) < USER_CD or now - last_global[0] < GLOBAL_CD):
                        continue                                  # rythme : on ignore sans bruit
                    last_user[m['author']] = now; last_global[0] = now
                    STATUS['accepted'] += 1; STATUS['last'] = f"{m['author']} : {cmd}"
                    log(f"  [youtube] {m['author']}{' (mod)' if m['mod'] else ''} -> « {cmd} »")
                    try:
                        from . import hud
                        hud.event(f"chat : {m['author'][:16]} -> {cmd}")
                    except Exception:
                        pass
                    _banner(True, f"{m['author'][:20]} -> {cmd}")
                    put(cmd, m)
                if len(seen) > 5000:
                    seen = set(list(seen)[-2000:])
                time.sleep(4.0)                                     # meme jeton de rechargement (voir _initial)
            continue
            log('  [youtube] fin du chat (direct termine ?) : nouvel essai dans 60 s')
        except Exception as e:
            STATUS['error'] = str(e)[:120]
            log(f'  [youtube] erreur : {e} (nouvel essai dans 60 s)')
        time.sleep(60.0)


def ensure_started(log=print) -> None:
    """A appeler une fois au demarrage (apres remote.ensure_started) : actif seulement si youtube.json est rempli."""
    global _started
    if _started:
        return
    _started = True
    cfg = _cfg()
    if not (cfg.get('channel') or cfg.get('channel_id') or cfg.get('video_id')) or cfg.get('enabled') is False:
        log(f'  [youtube] non configure ({YOUTUBE_FILE.name} : {{"channel": "@MaChaine"}}) : chat YouTube desactive')
        return
    from . import remote

    def put(cmd: str, m: dict) -> None:
        if m['mod'] and cmd.split()[0] in ('stop', 'pause', 'reprendre'):
            if remote._urgent(cmd, log):
                return
        remote._Q.put((time.time(), cmd))
    threading.Thread(target=_loop, args=(log, put), daemon=True).start()
