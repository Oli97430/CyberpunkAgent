"""
sms.py -- V lit ses SMS et repond (JournalManager : contacts -> messages et choix -> activation du choix).
Le choix de la reponse est fait par le modele de decision (dialog.llm_choice), comme pour un dialogue.
Declenchement : toutes les 3 minutes hors combat, ou des que le mod signale un nouveau message.
"""
from __future__ import annotations

import time

from . import nav

_last = {'t': -999.0, 'hash': None}
PERIOD_S = 180.0


_LOGGED: dict = {}               # contact -> dernier message deja journalise (plus de repetition toutes les 3 min)
_REFUSED: set = set()            # contacts dont la reponse est refusee par le jeu (ChangeEntryState) : plus d essai


def _refused_file():
    from .config import DATA_DIR
    return DATA_DIR / 'sms_refused.json'


def _load_refused() -> dict:
    """19/10 : refus MEMORISES entre les sessions (contact -> dernier message refuse). Avant, _REFUSED etait perdu a
    chaque relance et le meme refus revenait sans fin (~1500 lignes dans le journal du 19/10)."""
    try:
        import json
        return json.loads(_refused_file().read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_refused(d: dict) -> None:
    try:
        import json
        _refused_file().write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


_REFUSED_MSG: dict = _load_refused()


def _done_file():
    from .config import DATA_DIR
    return DATA_DIR / 'sms_done.json'


def _load_done() -> dict:
    """19/10 soir : messages DEJA traites (contact -> dernier message auquel V a repondu, ou deja repondu par le joueur).
    La session de test repondait aux 7 memes messages toutes les 3 min."""
    try:
        import json
        return json.loads(_done_file().read_text(encoding='utf-8'))
    except Exception:
        return {}


_DONE_MSG: dict = _load_done()


def _mark_done(name, last_txt: str) -> None:
    _DONE_MSG[name] = last_txt[:120]
    try:
        import json
        _done_file().write_text(json.dumps(_DONE_MSG, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def check_and_reply(st: dict, log=print, force: bool = False) -> int:
    ph = st.get('phone') or {}
    now = time.perf_counter()
    new_msg = ph.get('msg_hash') not in (None, 0) and ph.get('msg_hash') != _last['hash']
    if not (force or new_msg or now - _last['t'] > PERIOD_S):
        return 0
    _last['t'] = now
    if new_msg:
        _last['hash'] = ph.get('msg_hash')
    r = nav._wait(nav._send({'cmd': 'sms_list'}), timeout=6.0)
    if not (r and r.get('ok')):
        log(f"  [sms] liste illisible : {(r or {}).get('reason', 'mod muet')}")
        return 0
    replied = 0
    for c in (r.get('contacts') or []):
        if not (c.get('unread') or 0) and not c.get('can_reply'):
            continue
        m = nav._wait(nav._send({'cmd': 'sms_read', 'x': c['i']}), timeout=6.0)
        if not (m and m.get('ok')):
            continue
        msgs = m.get('messages') or []
        choices = m.get('choices') or []
        last_txt = (msgs[-1].get('text') if msgs else '') or ''
        if msgs and _LOGGED.get(c.get('name')) != last_txt:
            _LOGGED[c.get('name')] = last_txt                    # le meme dernier message n est journalise qu une fois
            log(f"  [sms] « {c.get('name')} » : {len(msgs)} message(s), dernier : « {last_txt[:90]} »")
        if not choices or c.get('name') in _REFUSED:
            continue
        if _REFUSED_MSG.get(c.get('name')) == last_txt[:120]:
            continue                                              # deja refuse pour CE message (session precedente) : on attend un nouveau message
        if _DONE_MSG.get(c.get('name')) == last_txt[:120]:
            continue                                              # 19/10 soir : deja repondu a CE message : on attend un nouveau message
        from . import dialog
        texts = [ch.get('text') or '?' for ch in choices]
        k = dialog.llm_choice(f"SMS de {c.get('name')} : {last_txt[:160]}", texts, timeout=8.0)
        if k is None:
            k = 0
        rr = nav._wait(nav._send({'cmd': 'sms_reply', 'x': choices[k]['i']}), timeout=6.0)
        if rr and rr.get('ok'):
            replied += 1
            _mark_done(c.get('name'), last_txt)
            log(f"  [sms] V repond a « {c.get('name')} » : « {texts[k][:90]} » ({rr.get('how')} : {rr.get('state_before')} -> {rr.get('state_after')})")
            time.sleep(1.0)
        elif rr and rr.get('already'):
            _mark_done(c.get('name'), last_txt)                  # deja repondu (par le joueur ou avant) : on n y touche plus
            log(f"  [sms] « {c.get('name')} » : deja repondu, on attend un nouveau message")
        else:
            _REFUSED.add(c.get('name'))                         # le jeu refuse : on n insiste plus ce contact-ci
            _REFUSED_MSG[c.get('name')] = last_txt[:120]; _save_refused(_REFUSED_MSG)
            log(f"  [sms] reponse refusee a « {c.get('name')} » : {(rr or {}).get('reason')} (on n insiste plus, memorise)")
    return replied
