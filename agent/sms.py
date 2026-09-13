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
        if msgs:
            log(f"  [sms] « {c.get('name')} » : {len(msgs)} message(s), dernier : « {last_txt[:90]} »")
        if not choices:
            continue
        from . import dialog
        texts = [ch.get('text') or '?' for ch in choices]
        k = dialog.llm_choice(f"SMS de {c.get('name')} : {last_txt[:160]}", texts, timeout=8.0)
        if k is None:
            k = 0
        rr = nav._wait(nav._send({'cmd': 'sms_reply', 'x': choices[k]['i']}), timeout=6.0)
        if rr and rr.get('ok'):
            replied += 1
            log(f"  [sms] V repond a « {c.get('name')} » : « {texts[k][:90]} »")
            time.sleep(1.0)
        else:
            log(f"  [sms] reponse refusee : {(rr or {}).get('reason')}")
    return replied
