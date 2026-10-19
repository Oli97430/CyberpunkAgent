"""V commente ses decisions en gros sur le HUD (19/10, Olivier : « V commente sur son HUD en gros. Il annonce ses decisions en
textuel HUD T-800 (« Je vais vendre mon butin », « Merci Pseudo, j attaque ! »). Sur le stream, ca rend V vivant et ca
repond aux commandes du chat »).

Le journal de V passe par feed() : les lignes de decision deviennent une phrase courte, en majuscules, affichee en grand
en haut de l ecran par le mod (machine a ecrire, 7 s). Rythme : une phrase toutes les 4 s au plus, la meme pas avant 20 s.
say() sert aussi directement (remerciements du chat)."""
from __future__ import annotations

import re
import time

from . import hud

_last = {'t': 0.0, 'by_key': {}}
MIN_GAP, SAME_GAP = 4.0, 20.0

# (motif du journal, phrase ; {0} = premier groupe capture)
RULES: list[tuple[str, str]] = [
    (r'V EST MORT', "ECHEC CRITIQUE. RECHARGEMENT... I'LL BE BACK."),
    (r'\[succes\] DEBLOQUE \(Steam\) : (.+?) \(', 'SUCCES DEBLOQUE : {0} !'),
    (r'\[succes\] Gun Fu', 'MODE GUN FU. TROIS CIBLES, TROIS BALLES.'),
    (r'\[succes\] Surement des rats', 'LEURRE ACTIVE. VA VOIR, PETIT RAT...'),
    (r'\[succes\] Grand voyageur : (\d+)/157', 'ZONE INCONNUE. CARTOGRAPHIE {0}/157.'),
    (r'V engage le combat .*\(« (.+?) »\)', 'CIBLE ACQUISE : {0}. J ENGAGE.'),
    (r'V engage le combat', 'CIBLE ACQUISE. J ENGAGE.'),
    (r'combat detecte : (\d+) hostile', '{0} HOSTILE(S) DETECTE(S). NEUTRALISATION.'),
    (r'V JOUE LES SAUVEURS', 'AGRESSION EN COURS. J INTERVIENS.'),
    (r'\[gouts\] V repere « (.+?) »', 'CE VEHICULE ME PLAIT : {0}. JE LE PRENDS.'),
    (r'\[vol\] la voiture volee roule', 'VEHICULE ACQUIS. EN ROUTE.'),
    (r'ennemis a moins de 60 m .* descend', 'COMITE D ACCUEIL HOSTILE. J ARRIVE A PIED.'),
    (r'V prend la voiture', 'DISTANCE ELEVEE. J APPELLE MA VOITURE.'),
    (r'voyage rapide : arrive a (.+)', 'TRANSFERT TERMINE : {0}.'),
    (r'courses : ', 'INVENTAIRE PLEIN. JE VAIS VENDRE MON BUTIN.'),
    (r'charcudoc : .* V va s optimiser', 'DIRECTION LE CHARCUDOC. MISE A NIVEAU.'),
    (r'\[charcudoc\] POSE « (.+?) »', 'IMPLANT INSTALLE : {0}.'),
    (r'\[armes\] emplacement \d : .* -> « (.+?) »', 'CHANGEMENT D ARME : {0}.'),
    (r'\[exploration\] .* decouvrir « (.+?) »', 'AUCUNE MISSION ICI. J EXPLORE : {0}.'),
    (r'V fige par le jeu', 'SCENE EN COURS. J ATTENDS.'),
    (r'quete de course .* passe a autre chose', 'COURSE DE RUE ? NEGATIF.'),
    (r'^objectif : (.+?)\s+\(marqueur', 'NOUVELLE MISSION : {0}'),
]
_RX = [(re.compile(p), s) for p, s in RULES]
# 19/10 (revue de securite) : lignes dont le texte vient de l EXTERIEUR (chat YouTube, Telegram, SMS, dialogues, panneau,
# directives) : jamais lues par le narrateur -- un pseudo ou un message pourrait imiter une regle et s afficher a l ecran
EXTERNAL = ('[youtube]', '[telegram]', '[sms]', '[directive]', '[dialog]', 'directive', '"', 'DIRECTIVE')
_SAFE = re.compile(r"[^A-Za-z0-9 .,:!?'/&()-]")


def clean(text: str, n: int = 40) -> str:
    """Texte affiche a l ecran : lettres (accents translitteres), chiffres, ponctuation simple ; longueur bornee."""
    return _SAFE.sub('', hud.ascii_plain(str(text)))[:n].strip()


def say(text: str, key: str | None = None, force: bool = False) -> bool:
    """Affiche une phrase en grand (rythme limite sauf force=True). Vrai si affichee."""
    now = time.time()
    k = key or text
    if not force and (now - _last['t'] < MIN_GAP or now - _last['by_key'].get(k, 0.0) < SAME_GAP):
        return False
    _last['t'] = now
    _last['by_key'][k] = now
    if len(_last['by_key']) > 200:
        _last['by_key'].clear()
    hud.update(say=hud.ascii_up(text)[:90], say_t=int(now))
    return True


def feed(line: str) -> None:
    """Une ligne du journal de V -> eventuellement une phrase du narrateur (lignes internes seulement)."""
    if any(tag in line for tag in EXTERNAL):
        return
    for rx, tpl in _RX:
        m = rx.search(line)
        if m:
            g = clean(m.group(1)) if m.groups() else ''
            say(tpl.format(g), key=tpl)
            return


CHAT_PHRASES = {'attaque': 'J ATTAQUE !', 'objectif': 'RETOUR A LA MISSION.', 'explore': 'JE PARS EXPLORER.',
                'marchand': 'JE VAIS VENDRE MON BUTIN.', 'charcudoc': 'DIRECTION LE CHARCUDOC.', 'soigne': 'JE ME SOIGNE.',
                'changer_quete': 'JE CHANGE DE MISSION.', 'stop': 'ARRET DEMANDE. TERMINE.', 'pause': 'PAUSE.',
                'reprendre': 'JE REPRENDS.'}


def thank(who: str, cmd: str, votes: int | None = None) -> None:
    """Reponse au chat : « MERCI PSEUDO, J ATTAQUE ! » / « LE CHAT A PARLE (4 VOIX) : JE PARS EXPLORER. »"""
    act = CHAT_PHRASES.get(cmd.split()[0], cmd.upper() + '.')
    if votes is not None:
        say(f'LE CHAT A VOTE ({votes} VOIX) : {act}', force=True)
    else:
        who_c = clean(who, 20) or 'LE CHAT'
        say(f'MERCI {who_c}, {act}', force=True)
