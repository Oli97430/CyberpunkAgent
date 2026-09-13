"""
dialog.py -- competence "repondre dans un dialogue".

Etat (mod CET, 20 Hz) : state.dialog = {title, choices[], sel, hubs}. Verifie en jeu :
  - la MOLETTE change la selection (sens appris au 1er cran, en boucle fermee)
  - F confirme le choix selectionne (mapping Choice1 = IK_F)
Decision : petit modele local via Ollama (texte seul, ~2 Go VRAM) avec une regle de
secours deterministe. Le gros VLM (9,5 Go) n'est PAS utilise ici : il fait planter le
jeu s'il reste resident pendant le rendu.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from .config import CFG

from . import input_kbm as kbm, motion

OLLAMA_CHAT = CFG.ollama_url + '/api/chat'
OLLAMA_GEN = CFG.ollama_url + '/api/generate'
MODEL = CFG.model
KEEP_ALIVE = '3m'

OBJECTIF = ("Tu joues V dans Cyberpunk 2077. Objectif : faire avancer la quete en cours, "
            "rester cooperatif et poli, ne pas depenser d'argent inutilement, ne pas "
            "provoquer de combat, ne jamais quitter la conversation prematurement.")

QUIT_WORDS = ('partir', 'plus tard', 'laisse tomber', 'oublie', 'au revoir', 'je me casse', 'quitter')


def rule_choice(choices: list[str]) -> int:
    """Secours : premier choix qui n'est pas une sortie de conversation."""
    for i, c in enumerate(choices):
        low = c.lower()
        if not any(w in low for w in QUIT_WORDS):
            return i
    return 0


def llm_choice(title: str, choices: list[str], timeout: float = 6.0) -> int | None:
    numbered = '\n'.join(f'{i}: {c}' for i, c in enumerate(choices))
    prompt = (f"{OBJECTIF}\n\nInterlocuteur : {title}\nChoix possibles :\n{numbered}\n\n"
              'Reponds UNIQUEMENT avec un JSON de la forme {"index": N} ou N est le numero du meilleur choix.')
    try:
        from . import llm_client
        txt = llm_client.chat([{'role': 'user', 'content': prompt}], temperature=0.1, max_tokens=20,
                              timeout=timeout, keep_alive=KEEP_ALIVE)
        m = re.search(r'"index"\s*:\s*(\d+)', txt)
        if m:
            i = int(m.group(1))
            if 0 <= i < len(choices):
                return i
    except Exception as e:
        from . import llm
        if CFG.provider == 'ollama' and ('refus' in str(e) or '10061' in str(e) or 'refused' in str(e)):
            llm.ensure()
        print(f'  [dialog] modele indisponible ({str(e)[:60]}) -> regle')
    return None


def unload_model() -> None:
    if CFG.provider != 'ollama':
        return
    try:
        body = {'model': MODEL, 'keep_alive': 0}
        req = urllib.request.Request(OLLAMA_GEN, json.dumps(body).encode(), {'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def confirm() -> None:
    """Confirme le choix de dialogue : touche d interaction du joueur (selectChoice), puis la
    touche UI (selectChoiceUI) si elle differe — l une des deux valide selon le contexte."""
    kbm.act('interact', 0.1)
    if kbm.ACTIONS.get('ui_confirm') and kbm.ACTIONS.get('ui_confirm') != kbm.ACTIONS.get('interact'):
        time.sleep(0.15); kbm.act('ui_confirm', 0.1)


def current_dialog() -> dict | None:
    st = motion.read_state()
    return st.get('dialog') if st else None


def _fresh_sel(seq_before: int | None) -> tuple[int | None, int | None]:
    st = motion.read_state(seq_before, timeout=0.4)
    if not st or not st.get('dialog'):
        return None, None
    return st['dialog']['sel'], st['seq']


_wheel_sign = [0]   # +1 si un cran vers le HAUT augmente sel, -1 sinon ; 0 = inconnu


def select_index(target: int, max_steps: int = 12) -> bool:
    """Amene la selection sur `target` a la molette, en boucle fermee sur l'etat."""
    st = motion.read_state()
    if not st or not st.get('dialog'):
        return False
    sel, seq = st['dialog']['sel'], st['seq']
    for _ in range(max_steps):
        if sel == target:
            return True
        direction = _wheel_sign[0] if _wheel_sign[0] else 1
        want_up = (target > sel)
        steps = direction if want_up else -direction
        kbm.wheel(steps)
        new_sel, new_seq = _fresh_sel(seq)
        if new_sel is None:
            return False
        if _wheel_sign[0] == 0 and new_sel != sel:
            # on a envoye steps=+1 ("haut") : sel est passe de sel a new_sel
            _wheel_sign[0] = 1 if (new_sel > sel) else -1
            print(f'  [dialog] sens molette appris : haut => sel {"+1" if _wheel_sign[0] > 0 else "-1"}')
        sel, seq = new_sel, new_seq
        time.sleep(0.08)
    return sel == target


_tried: dict[str, set] = {}   # signature du hub -> indices deja confirmes sans effet


def hub_signature(d: dict) -> str:
    return d.get('title', '?') + '|' + '|'.join(d.get('choices', []))


def answer_once(stop=None, log=print) -> dict | None:
    """Si un dialogue est ouvert : choisit, selectionne, confirme. Renvoie ce qui a ete fait.
    - ignore les choix INACTIFS (grises : le jeu les saute a la molette, F ne fait rien)
    - ne re-propose pas un choix deja confirme sans effet sur ce meme hub
    """
    d = current_dialog()
    if not d or not d.get('choices'):
        return None
    title, choices = d.get('title', '?'), d['choices']
    low_t = str(title).lower(); low_c = ' '.join(choices).lower()
    is_stand = any(w in low_t for w in ('vendeur', 'vendor', 'marchand', 'stand'))
    buy_words = ('apporte-moi', 'a boire', 'à boire', 'a manger', 'à manger', 'commander', 'un verre')
    if is_stand or all(any(w in c.lower() for w in buy_words) for c in choices):
        log(f'  [dialog] stand « {title} » : on ne commande rien, on recule')
        kbm.tap('ESC', 0.09); time.sleep(0.5)
        kbm.hold('S'); time.sleep(1.2); kbm.release('S')
        return {'title': title, 'choices': choices, 'index': -1, 'source': 'stand ignore'}
    inactive = list(d.get('inactive') or [0] * len(choices))
    # ARGENT : un choix qui fait payer plus que ce que V possede (ou plus de la moitie de sa fortune) est ecarte
    # (llama3.2 a paye 21 000 eddies a Viktor le 13/09 avec 31 000 en poche)
    try:
        from . import inventory as _inv
        money = int(getattr(_inv, 'MONEY', 0) or 0)
        for i, c in enumerate(choices):
            lc = c.lower()
            amounts = [int(a.replace(' ', '').replace(' ', '').replace('.', '')) for a in re.findall(r'(\d[\d\s .]{2,})', c)]
            pays = any(w in lc for w in ('payer', 'paye', 'eddies', '€$', 'eurodollar', 'donner', 'verser', 'rembourser'))
            if amounts and pays and money and (max(amounts) > money or max(amounts) > money * 0.5):
                if not inactive[i] and sum(1 for x in inactive if not x) > 1:
                    inactive[i] = 1
                    print(f'  [dialog] choix ecarte (trop cher : {max(amounts)} eddies, V en a {money}) : {c[:60]}')
    except Exception:
        pass
    sig = hub_signature(d)
    tried = _tried.setdefault(sig, set())
    allowed = [i for i in range(len(choices)) if not inactive[i] and i not in tried]
    if not allowed:
        allowed = [i for i in range(len(choices)) if not inactive[i]] or list(range(len(choices)))
        tried.clear()
    t0 = time.perf_counter()
    idx, source = None, 'modele'
    if len(allowed) == 1:
        idx, source = allowed[0], 'seul choix possible'
    else:
        sub = [choices[i] for i in allowed]
        k = llm_choice(title, sub)
        if k is not None:
            idx = allowed[k]
        else:
            idx, source = allowed[rule_choice(sub)], 'regle'
    tried.add(idx)
    grey = [choices[i] for i in range(len(choices)) if inactive[i]]
    log(f'  "{title}" -> choix {idx} ({source}, {time.perf_counter() - t0:.1f} s) : {choices[idx]}'
        + (f'   [grises : {grey}]' if grey else ''))
    if stop is not None and stop.is_set():
        return None
    if not select_index(idx):
        log('  [dialog] selection non atteinte, confirmation du choix courant')
    confirm()
    return {'title': title, 'choices': choices, 'index': idx, 'source': source}


def run_conversation(stop=None, idle_end: float = 6.0, max_turns: int = 30, log=print) -> dict:
    """Repond tant que des choix apparaissent ; termine apres idle_end s sans dialogue."""
    t0 = time.perf_counter()
    turns, last_dialog_t, last_sig = [], time.perf_counter(), None
    while len(turns) < max_turns:
        if stop is not None and stop.is_set():
            break
        d = current_dialog()
        if d and d.get('choices'):
            sig = d['title'] + '|' + '|'.join(d['choices'])
            if sig != last_sig:
                last_sig = sig
                r = answer_once(stop=stop, log=log)
                if r:
                    turns.append(r)
                # laisser le jeu enchainer : attendre que ce hub disparaisse (max 8 s)
                t1 = time.perf_counter()
                while time.perf_counter() - t1 < 8.0:
                    d2 = current_dialog()
                    if not d2 or (d2['title'] + '|' + '|'.join(d2['choices'])) != sig:
                        break
                    time.sleep(0.1)
            last_dialog_t = time.perf_counter()
        elif time.perf_counter() - last_dialog_t > idle_end and turns:
            break
        time.sleep(0.1)
    unload_model()
    return {'turns': turns, 'seconds': time.perf_counter() - t0}
