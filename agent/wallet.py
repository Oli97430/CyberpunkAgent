"""
wallet.py -- garde-fou des depenses (20/11/2026).

Le 20/11 a 10:42, V a loue un appartement pour ~110 000 eddies par un choix de dialogue sans prix dans le texte : la regle
ARGENT de dialog.py n a rien vu, et personne ne l a su avant la fin de la session. Ici : chaque achat PREVU (vendor.buy)
declare ce qu il a coute ; a chaque passe d inventaire, la baisse d argent que rien n explique est comparee a un seuil.
Au-dessus : ligne dans le journal + message Telegram, pour pouvoir recharger une sauvegarde tant qu elle est recente.

Le controle ne peut pas annuler une depense : il la SIGNALE (au plus 5 min apres, le rythme des passes d inventaire).
"""
from __future__ import annotations

ALERT_MIN = 10000            # eddies : en dessous, une baisse inexpliquee n est pas signalee (munitions, voyage, amendes)
ALERT_RATIO = 0.10           # ... ni en dessous de 10 % de la fortune
_state = {'last': None, 'planned': 0}
ALERTS: list[dict] = []      # baisses signalees cette session (pour le bilan)


def planned(amount) -> None:
    """Un achat voulu par l agent vient de couter `amount` eddies."""
    try:
        _state['planned'] += max(0, int(amount or 0))
    except (TypeError, ValueError):
        pass


def check(money, log=print) -> int | None:
    """A appeler apres chaque lecture de l argent. Renvoie la baisse inexpliquee si elle depasse le seuil, sinon None."""
    try:
        money = int(money)
    except (TypeError, ValueError):
        return None
    last, plan = _state['last'], _state['planned']
    _state['last'], _state['planned'] = money, 0
    if last is None or money <= 0:                   # premiere lecture, ou inventaire illisible (0) : rien a comparer
        if money <= 0:
            _state['last'] = last
        return None
    lost = (last - money) - plan
    if lost < max(ALERT_MIN, ALERT_RATIO * last):
        return None
    ALERTS.append({'eddies': lost, 'avant': last, 'apres': money})
    log(f'ALERTE ARGENT : {lost} eddies depenses sans achat prevu ({last} -> {money}, achats prevus {plan}) ; '
        'si ce n etait pas voulu, recharge une sauvegarde d il y a moins de 5 min')
    return lost


def reset() -> None:
    _state['last'], _state['planned'] = None, 0
    ALERTS.clear()
