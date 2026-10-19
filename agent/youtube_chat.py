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
VIEWER_CMDS = {'attaque', 'objectif', 'explore', 'marchand', 'charcudoc', 'soigne', 'changer_quete', 'mission',
               'vole', 'voiture', 'fete', 'loot', 'radio', 'cyberpsycho', 'etat', 'melee', 'distance', 'mixte', 'prudent',
               'temeraire'}     # 19/10 (Olivier : « rajoute plus d ordres »)
MOD_CMDS = VIEWER_CMDS | {'style', 'stop', 'pause', 'reprendre', 'courage', 'style', 'aggro', 'photo', 'hud'}   # photo -> Telegram d Olivier
ALIASES = {'attack': 'attaque', 'fight': 'attaque', 'quest': 'objectif', 'quete': 'objectif', 'explorer': 'explore',
           'vendre': 'marchand', 'shop': 'marchand', 'heal': 'soigne', 'soin': 'soigne', 'ripperdoc': 'charcudoc',
           'autrequete': 'changer_quete', 'skip': 'changer_quete', 'change': 'changer_quete', 'missions': 'mission',
           'quetes': 'mission', 'choix': 'mission',
           'steal': 'vole', 'voler': 'vole', 'car': 'voiture', 'caisse': 'voiture', 'party': 'fete', 'danse': 'fete',
           'gg': 'fete', 'fouille': 'loot', 'musique': 'radio', 'psycho': 'cyberpsycho', 'status': 'etat', 'vie': 'etat',
           'corps': 'melee', 'sabre': 'melee', 'katana': 'melee', 'tir': 'distance', 'gun': 'distance', 'calme': 'prudent',
           'yolo': 'temeraire', 'berserk': 'temeraire'}
MACROS = {'melee': 'style melee', 'distance': 'style distance', 'mixte': 'style mixte', 'prudent': 'courage prudent',
          'temeraire': 'courage temeraire'}     # commande de spectateur -> reglage de V
VOTE_S = 30.0          # 19/10 : vote du chat -- la commande la plus demandee en 30 s est executee
BANNER = ('VOTE : !ATTAQUE !OBJECTIF !SKIP !MISSION !EXPLORE !SHOP !CHARCUDOC !HEAL !VOLE !VOITURE !LOOT !FETE !RADIO '
          '!PSYCHO !ETAT !MELEE !DISTANCE !PRUDENT !YOLO  (1 VOTE / 30 S)')
POLL_S = 45.0          # 19/10 : le chat choisit la mission parmi 3 (!1 !2 !3)
POLL: dict = {'options': [], 't0': None, 'voices': {}}
CHAT_STATS: dict = {'orders': 0, 'by': {}}      # 19/10 : ordres du chat suivis (tableau des succes) et spectateur le plus actif


def live() -> bool:
    return bool(STATUS.get('video_id'))


def open_poll(opts: list[dict]) -> bool:
    """Sondage de mission : opts = [{'hash', 'text', 'dist'}] (2 ou 3). Faux si deja ouvert ou rien a proposer."""
    if POLL['t0'] is not None or len(opts) < 2:
        return False
    from .narrator import clean
    POLL.update(options=[(o['hash'], clean(o.get('text') or '?', 22).upper()) for o in opts[:3]], t0=time.time(), voices={})
    _poll_banner()
    try:
        from . import narrator
        narrator.say('LE CHAT CHOISIT MA MISSION : ' + '  '.join(f'!{i + 1} {lab}' for i, (_, lab) in enumerate(POLL['options'])), force=True)
    except Exception:
        pass
    return True


def poll_vote(author: str, text: str) -> bool:
    """« !2 » pendant un sondage -> voix (modifiable) ; vrai si c etait une voix."""
    t = text.strip()
    if POLL['t0'] is None or not re.fullmatch(r'!\s*[1-3]', t):
        return False
    i = int(t[-1]) - 1
    if i >= len(POLL['options']):
        return False
    POLL['voices'][author] = i
    _poll_banner()
    return True


def poll_tally() -> list[int]:
    cnt = [0] * len(POLL['options'])
    for i in POLL['voices'].values():
        cnt[i] += 1
    return cnt


def poll_close_if_due(now: float) -> tuple[int, str, int, int] | None:
    """(hash, libelle, voix, votants) quand le sondage se termine (egalite -> la mieux classee par V) ; None sinon.
    Personne n a vote -> (hash de l option 1, ..., 0, 0) : V garde son propre choix."""
    if POLL['t0'] is None or now - POLL['t0'] < POLL_S:
        return None
    cnt = poll_tally()
    best = max(range(len(cnt)), key=lambda i: (cnt[i], -i))
    h, lab = POLL['options'][best]
    res = (h, lab, cnt[best], len(POLL['voices']))
    POLL.update(options=[], t0=None, voices={})
    _banner(True)
    return res


def _poll_banner() -> None:
    try:
        from . import hud
        cnt = poll_tally()
        left = max(0, POLL_S - (time.time() - (POLL['t0'] or time.time())))
        hud.update(banner=hud.ascii_up(f'MISSION ? {left:.0f} S : ' + '   '.join(f'!{i + 1} {lab} ({cnt[i]})' for i, (_, lab) in enumerate(POLL['options'])))[:110],
                   banner_last=None)
    except Exception:
        pass


def count_order(author: str) -> None:
    CHAT_STATS['orders'] += 1
    CHAT_STATS['by'][author] = CHAT_STATS['by'].get(author, 0) + 1
    try:
        from . import achievements
        achievements.publish()
    except Exception:
        pass


def top_viewer() -> str | None:
    by = {a: n for a, n in CHAT_STATS['by'].items() if a not in ('vote', 'le chat')}
    return max(by, key=lambda a: by[a]) if by else None


class Vote:
    """Vote du chat (19/10, Olivier) : chaque spectateur a UNE voix par tour (il peut changer d avis), le tour dure VOTE_S
    a partir de la premiere voix ; egalite -> la commande qui a recu sa premiere voix le plus tot."""

    def __init__(self, duration: float = VOTE_S):
        self.duration = duration
        self.t0: float | None = None
        self.voices: dict[str, str] = {}
        self.first: dict[str, float] = {}

    def add(self, author: str, cmd: str, now: float) -> None:
        if self.t0 is None:
            self.t0 = now
        self.voices[author] = cmd
        self.first.setdefault(cmd, now)

    def tally(self) -> list[tuple[str, int]]:
        cnt: dict[str, int] = {}
        for c in self.voices.values():
            cnt[c] = cnt.get(c, 0) + 1
        return sorted(cnt.items(), key=lambda kv: (-kv[1], self.first.get(kv[0], 0.0)))

    def remaining(self, now: float) -> float:
        return 0.0 if self.t0 is None else max(0.0, self.duration - (now - self.t0))

    def close_if_due(self, now: float) -> tuple[str, int, int] | None:
        """(gagnant, voix, votants) si le tour est fini, sinon None ; remet le vote a zero."""
        if self.t0 is None or now - self.t0 < self.duration:
            return None
        t = self.tally()
        res = (t[0][0], t[0][1], len(self.voices)) if t else None
        self.t0, self.voices, self.first = None, {}, {}
        return res


def _banner_tally(v: 'Vote', now: float) -> None:
    try:
        from . import hud
        t = v.tally()
        if t:
            hud.update(banner_last=hud.ascii_up(f"VOTE {v.remaining(now):.0f} S : " + ' . '.join(f'{c} {n}' for c, n in t[:4]))[:70],
                       banner_last_t=int(time.time()))
    except Exception:
        pass


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


def _support(item: dict) -> dict | None:
    """Soutien du chat -> {'kind': superchat|sticker|membre|cadeau, 'author', 'amount', 'text', ...} ; None sinon."""
    for key, kind in (('liveChatPaidMessageRenderer', 'superchat'), ('liveChatPaidStickerRenderer', 'sticker'),
                      ('liveChatMembershipItemRenderer', 'membre')):
        it = item.get(key)
        if it:
            return {'id': it.get('id'), 'kind': kind, 'author': (it.get('authorName') or {}).get('simpleText') or '?',
                    'amount': (it.get('purchaseAmountText') or {}).get('simpleText') or '',
                    'text': _text((it.get('message') or {}).get('runs')), 'ts': int(it.get('timestampUsec') or 0) / 1e6, 'mod': False}
    g = item.get('liveChatSponsorshipsGiftPurchaseAnnouncementRenderer')
    if g:
        h = ((g.get('header') or {}).get('liveChatSponsorshipsHeaderRenderer') or {})
        n = re.search(r'\d+', _text((h.get('primaryText') or {}).get('runs')) or '')
        return {'id': g.get('id'), 'kind': 'cadeau', 'author': (h.get('authorName') or {}).get('simpleText') or '?',
                'amount': (n.group(0) + ' abonnement(s)') if n else '', 'text': '',
                'ts': int(g.get('timestampUsec') or 0) / 1e6, 'mod': False}
    return None


SUPPORT_SAY = {'superchat': 'MERCI {who} POUR LE SUPER CHAT ({amt}) ! ON FETE CA.', 'sticker': 'MERCI {who} ({amt}) ! ON FETE CA.',
               'membre': 'BIENVENUE DANS L EQUIPE, {who} !', 'cadeau': 'MERCI {who} POUR LES CADEAUX ({amt}) !'}


def on_support(m: dict, log, put) -> None:
    """Soutien : remerciement en grand (pseudo et montant nettoyes), celebration de V ; un Super Chat qui contient une
    commande « ! » est execute TOUT DE SUITE (sans vote)."""
    from .narrator import clean
    who = (clean(m['author'], 20) or 'TOI').upper()
    amt = clean(m.get('amount') or '', 24).upper()
    log(f"  [youtube] soutien ({m['kind']}) de {m['author'][:30]} {m.get('amount') or ''}")
    STATUS['soutiens'] = STATUS.get('soutiens', 0) + 1
    try:
        from . import narrator
        narrator.say(SUPPORT_SAY[m['kind']].format(who=who, amt=amt).replace(' ()', ''), force=True)
    except Exception:
        pass
    cmd = to_command(m.get('text') or '', False) if m['kind'] == 'superchat' else None
    put('celebre', {'author': 'soutien', 'mod': False})
    if cmd:
        log(f"  [youtube] commande du Super Chat executee sans vote : « {cmd} »")
        count_order(m['author'])
        put(cmd, {'author': m['author'], 'mod': False})


def parse_actions(data: dict) -> tuple[list[dict], str | None, float]:
    """Reponse get_live_chat -> (messages, jeton suivant, attente en s)."""
    cc = ((data.get('continuationContents') or {}).get('liveChatContinuation') or {})
    msgs = []
    for a in cc.get('actions') or []:
        item = ((a.get('addChatItemAction') or {}).get('item') or {})
        sup = _support(item)                     # 19/10 : Super Chat / sticker / nouveau membre / abonnements offerts
        if sup:
            msgs.append(sup)
            continue
        it = item.get('liveChatTextMessageRenderer')
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
    if cmd in MACROS:
        return MACROS[cmd]
    return ' '.join([cmd] + parts[1:2])[:80]


def _loop(log, put) -> None:
    t_start = time.time()
    vote = Vote()
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
                    if m.get('kind'):
                        on_support(m, log, put)
                        continue
                    if poll_vote(m['author'], m['text']):           # 19/10 : !1 !2 !3 pendant un sondage de mission
                        log(f"  [youtube] voix de mission de {m['author']} : {m['text'].strip()}")
                        continue
                    cmd = to_command(m['text'], m['mod'])
                    if not cmd:
                        continue
                    if not m['mod']:
                        CHAT_STATS['by'][m['author']] = CHAT_STATS['by'].get(m['author'], 0) + 1   # participation
                        vote.add(m['author'], cmd, now)          # spectateur : une voix (modifiable) pour ce tour de vote
                        log(f"  [youtube] vote de {m['author']} : « {cmd} »")
                        _banner_tally(vote, now)
                        continue
                    STATUS['accepted'] += 1; STATUS['last'] = f"{m['author']} : {cmd}"
                    log(f"  [youtube] {m['author']}{' (mod)' if m['mod'] else ''} -> « {cmd} »")
                    try:
                        from . import hud
                        hud.event(f"chat : {m['author'][:16]} -> {cmd}")
                    except Exception:
                        pass
                    _banner(True, f"{m['author'][:20]} -> {cmd}")
                    try:
                        from . import narrator
                        narrator.thank(m['author'], cmd)
                    except Exception:
                        pass
                    count_order(m['author'])
                    put(cmd, m)
                pres = poll_close_if_due(time.time())
                if pres:
                    h, lab, n, voters = pres
                    if voters:
                        log(f"  [youtube] SONDAGE : mission « {lab} » ({n} voix sur {voters} votant(s))")
                        try:
                            from . import narrator
                            narrator.say(f'LE CHAT A CHOISI ({n} VOIX) : {lab}.', force=True)
                        except Exception:
                            pass
                        count_order('vote')
                        put(f'mission_choix {h}', {'author': 'vote', 'mod': False})
                    else:
                        log('  [youtube] sondage de mission sans voix : V garde son choix')
                elif POLL['t0'] is not None:
                    _poll_banner()
                res = vote.close_if_due(time.time())
                if res:
                    w, n, voters = res
                    STATUS['accepted'] += 1; STATUS['last'] = f'vote : {w} ({n}/{voters})'
                    log(f"  [youtube] VOTE : « {w} » l emporte ({n} voix sur {voters} votant(s))")
                    try:
                        from . import hud
                        hud.event(f'vote du chat : {w} ({n} voix)')
                    except Exception:
                        pass
                    _banner(True, f'LE CHAT A VOTE : {w} ({n} voix)')
                    try:
                        from . import narrator
                        narrator.thank('le chat', w, votes=n)
                    except Exception:
                        pass
                    count_order('vote')
                    put(w, {'author': 'vote', 'mod': False})
                elif vote.t0 is not None:
                    _banner_tally(vote, time.time())
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
