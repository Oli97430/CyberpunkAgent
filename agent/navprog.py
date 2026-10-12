"""
navprog.py -- progres REEL vers une cible et echelle de deblocage (12/10, revu deux fois par revue multi-agents).

Journal 24/09-07/10 : un troncon partiel qui bute toujours au meme endroit (« bloque, reste 36.6 m », 36 fois de
suite a (-396,720) le 05/10, 14 min ; 23 fois a (-1398,1314)) comptait comme un progres (« un point du chemin
atteint ») et remettait le compteur d echecs a zero : aucune escalade ne se declenchait jamais. Et « on insiste »
annoncait « vehicule / voyage rapide » sans jamais les tenter (cible a 100 m, la voiture et le voyage rapide ne
s ouvraient qu au-dela de 500 m) : 3 pauses de 45 s puis tout recommencait.

Un troncon est CREDITE (aucun echec) s il :
  - arrive, ou finit dans la zone d arrivee (<= 8 m au meme etage, etage de la cible CONNU : le mod vise le navmesh
    dans 6 m du marqueur) ;
  - rapproche V de 4 m depuis le dernier credit, V ayant lui-meme bouge (ancre : une progression lente mais reguliere
    finit par compter ; un marqueur de PNJ qui vient vers V ne compte pas) ;
  - fait parcourir a V plus de 10 m jusqu a un endroit NOUVEAU (detour, pont, escalier) -- 8 fois par cible au plus ;
  - ramene V apres qu il a ete deplace entre deux troncons (mort, combat, sortie) : « retour », credit sans relancer
    la ligne droite.
Cible = identite (marqueur / objectif), pas cellule : un marqueur qui bouge (PNJ) ne remet rien a zero, sauf s il
s est deplace de plus de 30 m.

Echelle par cible (brain) : voyage rapide (plus pres de la cible), une courte pause pour la quete assignee, puis
verdict. « V coince » (aucun verdict) seulement si deux cibles ELOIGNEES l une de l autre (> 60 m ou directions
opposees) echouent depuis le meme secteur, ET que V ne parvient pas a ressortir a pied vers un endroit deja atteint
a pied hors du secteur (verification active, brain) : la position de V est en cause, pas les cibles.
"""
from __future__ import annotations

import math
import time

GAIN_M = 4.0            # credit : V s est rapproche de 4 m depuis le dernier credit
NOVEL_M = 10.0          # endroit nouveau : a plus de 10 m de tous ceux deja atteints...
NOVEL_DZ = 2.5          # ... ou a un autre etage
NOVEL_MAX = 8           # au plus 8 credits « nouveau » par cible et par session (errance bornee)
OWN_MOVE_M = 2.0        # « record » / « retour » : V doit avoir bouge d au moins 2 m pendant le troncon
MOVED_M = 15.0          # V deplace de plus de 15 m entre deux troncons : base locale (« retour »)
NEAR_M = 8.0            # zone d arrivee
TARGET_MOVED_M = 30.0   # cible deplacee de plus de 30 m (objectif relocalise) : nouvel episode
EPISODE_S = 600.0       # 10 min sans trajet vers cette cible : nouvel episode
SLOW_LEG_S = 15.0       # echec double : troncon de plus de 15 s...
STILL_M = 5.0           # ... ou V a bouge de moins de 5 m et ne s est pas rapproche
FT_EVERY_S = 1200.0     # voyage rapide de deblocage : une fois par cible toutes les 20 min
FT_MIN_DIST_M = 20.0    # jamais pour une cible a moins de 20 m (il ne peut qu eloigner)
EXHAUST_TTL_S = 1800.0  # cycles epuises oublies apres 30 min...
EXHAUST_RESET_M = 25.0  # ... ou quand V s est rapproche de 25 m depuis le dernier cycle (autre obstacle)
SECTOR_M = 60.0         # secteur de blocage de V
TRAP_SPREAD_M = 60.0    # « V coince » : cibles epuisees a plus de 60 m l une de l autre...
TRAP_ANGLE = 90.0       # ... ou dans des directions differant de plus de 90 degres
TRAP_FT_S = 600.0       # voyage rapide de sortie (V coince) : une fois toutes les 10 min
UNREACH_S = (1200.0, 2400.0, 3600.0)   # quete suivie inaccessible : mise de cote 20 min, 40 min, 1 h (session)


def ft_gain(dist):
    """Gain minimal du voyage rapide de deblocage (point plus pres de la cible que V), None = pas de voyage."""
    if dist is None or dist < FT_MIN_DIST_M:
        return None
    return 0.0 if dist <= 400.0 else 100.0


def _d2(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _z(p):
    z = p[2] if p is not None and len(p) > 2 else None
    return z if isinstance(z, (int, float)) else None


def _far(a, b) -> bool:
    if _d2(a, b) > NOVEL_M:
        return True
    za, zb = _z(a), _z(b)
    return za is not None and zb is not None and abs(za - zb) > NOVEL_DZ


def _bearing(frm, to) -> float:
    return math.degrees(math.atan2(to[1] - frm[1], to[0] - frm[0]))


class Tracker:
    def __init__(self) -> None:
        self.key = None
        self.tpos = None             # position de la cible au debut de l episode
        self.best = None             # record de distance (journal, exhausted)
        self.anc = None              # ancre de credit (baisse seulement sur un credit)
        self.anc_base = None         # ancre locale (repart quand V est deplace entre deux troncons)
        self.ends: list = []         # endroits distincts atteints (fins de troncon dedoublonnees)
        self.last_end = None
        self.novel_n = 0
        self.novel_by: dict = {}     # cle -> credits « nouveau » deja accordes (session)
        self.foot: list = []         # fins de troncon atteintes A PIED depuis le dernier deplacement autrement
        self.near_hits = 0
        self.t = -1e9
        self.kind = ''               # dernier troncon : arrive / zone / record / nouveau / retour / echec
        self.exhaust: dict = {}      # cle -> (cycles, heure, record au dernier cycle)
        self.ft: dict = {}           # cle -> heure du dernier voyage rapide de deblocage
        self.paused: dict = {}       # cle -> heure de la courte pause (quete assignee)
        self.unreach: dict = {}      # cle quete suivie -> (jusqu a, palier)
        self.notified: set = set()
        self.sec_pos = None          # secteur de blocage en cours
        self.sec_targets: dict = {}  # cle -> position de la cible epuisee depuis ce secteur
        self.pending: list = []      # verdicts differes : ('marqueur', texte, x, y, z) / ('suivie', cle)
        self.trap_ft_t = -1e9

    # ------------------------------------------------------------------ progres
    def reset(self) -> None:
        """Apres un voyage rapide / une pause : la base locale repart de la prochaine position."""
        self.anc_base = None
        self.last_end = None

    def _new_episode(self, key, tpos, d_before) -> None:
        self.key, self.tpos = key, tpos
        self.best = self.anc = self.anc_base = d_before
        self.ends, self.last_end, self.near_hits = [], None, 0
        self.novel_n = self.novel_by.get(key, 0)

    def leg(self, key, tpos, d_before, d_after, ok: bool, seconds: float = 0.0, start=None, end=None,
            dz_after=None, now: float | None = None) -> int:
        """Bilan d un trajet vers la cible `key` (position `tpos`) : 0 = credite (voir self.kind), sinon nombre
        d echecs a compter (1 ou 2). `dz_after` : ecart d etage V / cible a l arrivee du troncon (None = inconnu)."""
        now = time.perf_counter() if now is None else now
        if (self.key != key or self.anc is None or now - self.t > EPISODE_S
                or (tpos is not None and self.tpos is not None and _d2(tpos, self.tpos) > TARGET_MOVED_M)):
            self._new_episode(key, tpos, d_before)
        elif self.anc_base is None or (start is not None and self.last_end is not None and _d2(start, self.last_end) > MOVED_M):
            self.anc_base = d_before
        self.t = now
        travelled = start is not None and end is not None and _far(start, end)
        novel = end is not None and travelled and all(_far(end, e) for e in self.ends)
        if end is not None:
            if all(_far(end, e) for e in self.ends):
                self.ends.append(end)
                del self.ends[:-40]
            self.last_end = end
        if d_after is not None:
            self.best = d_after if self.best is None else min(self.best, d_after)
        kind = 'echec'
        if ok:
            kind = 'arrive'
        elif d_after is not None and d_after <= NEAR_M and dz_after is not None and dz_after < NOVEL_DZ:
            kind = 'zone'
        elif (d_after is not None and self.anc is not None and d_after < self.anc - GAIN_M
              and (start is None or end is None or _d2(start, end) >= OWN_MOVE_M)):
            kind = 'record'
        elif novel and self.novel_n < NOVEL_MAX:
            kind = 'nouveau'
        elif (d_after is not None and self.anc_base is not None and d_after < self.anc_base - GAIN_M
              and (start is None or end is None or _d2(start, end) >= OWN_MOVE_M)):
            kind = 'retour'
        self.kind = kind
        if kind == 'echec':
            moved = _d2(start, end) if start is not None and end is not None else None
            closer = (d_before - d_after) if d_before is not None and d_after is not None else 0.0
            return 2 if (seconds or 0.0) > SLOW_LEG_S and (moved is None or moved < STILL_M) and closer < 1.5 else 1
        if d_after is not None:                     # credit : les ancres descendent
            self.anc = d_after if self.anc is None else min(self.anc, d_after)
            self.anc_base = d_after if self.anc_base is None else min(self.anc_base, d_after)
        if kind == 'nouveau':
            self.novel_n += 1
            self.novel_by[key] = self.novel_n
            if len(self.novel_by) > 100:
                self.novel_by.clear()
        if kind == 'zone':
            self.near_hits += 1
        if kind == 'arrive':
            self.near_hits = 0
            self.novel_by.pop(key, None)
            self.exhaust.pop(key, None)
            self.paused.pop(key, None)
            self.ft.pop(key, None)
        return 0

    @property
    def true_progress(self) -> bool:
        """Credit qui justifie de retenter la ligne droite : V s est vraiment rapproche (pas un simple retour au point
        de blocage, ni un detour qui peut tourner en rond)."""
        return self.kind in ('arrive', 'record')

    def near_stall(self, key, dist, dz=None) -> bool:
        """V plafonne dans la zone d arrivee de cette cible (2 troncons au moins), au meme etage CONNU : la traiter
        comme atteinte."""
        return (key is not None and key == self.key and dist is not None and dist <= NEAR_M
                and dz is not None and dz < NOVEL_DZ and self.near_hits >= 2)

    # ------------------------------------------------------------------ echelle
    def exhausted(self, key, now: float | None = None) -> int:
        """Un cycle d echecs de plus vers `key` ; renvoie son rang. Echelle neuve (voyage et pause a nouveau
        permis) apres 30 min ou si V s est rapproche de 25 m depuis le dernier cycle."""
        now = time.perf_counter() if now is None else now
        n, t, b = self.exhaust.get(key, (0, -1e9, None))
        cur = self.best if self.key == key else None
        if now - t > EXHAUST_TTL_S or (b is not None and cur is not None and cur < b - EXHAUST_RESET_M):
            n = 0
            self.ft.pop(key, None)
            self.paused.pop(key, None)
        n += 1
        self.exhaust[key] = (n, now, cur)
        if len(self.exhaust) > 100:
            for k in sorted(self.exhaust, key=lambda kk: self.exhaust[kk][1])[:50]:
                self.exhaust.pop(k, None)
        return n

    def ft_ready(self, key, now: float | None = None) -> bool:
        now = time.perf_counter() if now is None else now
        return now - self.ft.get(key, -1e9) > FT_EVERY_S

    def ft_mark(self, key, now: float | None = None) -> None:
        self.ft[key] = time.perf_counter() if now is None else now

    def pause_ready(self, key, now: float | None = None) -> bool:
        now = time.perf_counter() if now is None else now
        return now - self.paused.get(key, -1e9) > EXHAUST_TTL_S

    def pause_mark(self, key, now: float | None = None) -> None:
        self.paused[key] = time.perf_counter() if now is None else now

    def fresh_start(self) -> None:
        """Directive « objectif » : echelle neuve pour toutes les cibles, mises de cote levees."""
        self.unreach.clear(); self.notified.clear(); self.exhaust.clear(); self.ft.clear(); self.paused.clear()
        self.trap_reset()

    # ------------------------------------------------------------------ quete suivie inaccessible (memoire de session)
    def mark_unreachable(self, ukey, now: float | None = None) -> float:
        now = time.perf_counter() if now is None else now
        _, n = self.unreach.get(ukey, (0.0, 0))
        n = min(n + 1, len(UNREACH_S))
        w = UNREACH_S[n - 1]
        self.unreach[ukey] = (now + w, n)
        return w

    def unreach_wait(self, ukey, now: float | None = None) -> float:
        now = time.perf_counter() if now is None else now
        until, _ = self.unreach.get(ukey, (0.0, 0))
        return max(0.0, until - now)

    def lift(self, ukey) -> None:
        """Mise de cote levee (V est pres du marqueur), palier conserve : une rechute repartira plus longue."""
        if ukey in self.unreach:
            self.unreach[ukey] = (0.0, self.unreach[ukey][1])

    # ------------------------------------------------------------------ secteur de blocage (V coince ?)
    def sector(self, key, tpos, vpos, now: float | None = None) -> bool:
        """Cible `key` (en `tpos`) epuisee depuis la position de V `vpos`. True = V coince : depuis ce secteur, au
        moins deux cibles ELOIGNEES l une de l autre (> 60 m, ou directions differant de plus de 90 degres) ont
        echoue -- deux cibles voisines (meme batiment ferme) ne prouvent rien."""
        now = time.perf_counter() if now is None else now
        if vpos is None or tpos is None:
            return False
        if self.sec_pos is None or _d2(vpos, self.sec_pos) > SECTOR_M:
            self.sec_pos, self.sec_targets, self.pending = vpos, {}, []
        self.sec_targets[key] = tpos
        pts = list(self.sec_targets.values())
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                if _d2(pts[i], pts[j]) > TRAP_SPREAD_M:
                    return True
                da = abs((_bearing(self.sec_pos, pts[i]) - _bearing(self.sec_pos, pts[j]) + 180.0) % 360.0 - 180.0)
                if da > TRAP_ANGLE:
                    return True
        return False

    def defer(self, verdict: tuple) -> None:
        self.pending.append(verdict)

    def after_leg(self, start, end, arrived: bool) -> list:
        """Apres chaque troncon. V a quitte le secteur A PIED, ou est arrive quelque part : il n y etait pas coince
        -> verdicts differes des marqueurs a ecrire. Deplace autrement (voyage rapide, voiture, rechargement) : on ne
        sait pas -> verdicts abandonnes (et quete suivie remise en jeu)."""
        if end is not None:
            self.foot.append((end[0], end[1]))
            del self.foot[:-60]
        if self.sec_pos is None:
            return []
        if start is not None and _d2(start, self.sec_pos) > SECTOR_M + MOVED_M:
            # deplacement NON signale (joueur pendant une pause, scene, metro...) : prudence, verdicts des marqueurs
            # abandonnes ; la quete suivie mise de cote le reste (les vrais voyages passent par displaced())
            self.trap_reset()
            self.foot = self.foot[-1:]          # avant le deplacement : plus rien n est « joignable a pied »
            return []
        if arrived or (end is not None and _d2(end, self.sec_pos) > SECTOR_M):
            p = [v for v in self.pending if v[0] == 'marqueur']
            self.trap_reset()
            return p
        return []

    def trapped(self) -> None:
        """V coince dans le secteur : aucune des cibles n est en cause -> verdicts differes annules (la quete suivie
        est remise en jeu, son palier conserve)."""
        for v in self.pending:
            if v[0] == 'suivie':
                self.lift(v[1])
        self.pending = []

    def displaced(self) -> None:
        """V deplace autrement qu a pied (voyage rapide, voiture, rechargement) : on ne sait rien de ses cibles depuis
        l ancien secteur -> verdicts differes abandonnes, secteur et positions « a pied » oublies."""
        self.trapped()
        self.trap_reset()
        self.foot = []
        self.reset()

    def outside_point(self):
        """Dernier endroit atteint A PIED hors du secteur de blocage (plus de 70 m), ou None : s il existe, V devrait
        pouvoir y retourner -- sinon il est vraiment coince."""
        if self.sec_pos is None:
            return None
        for p in reversed(self.foot):
            if _d2(p, self.sec_pos) > SECTOR_M + 10.0:
                return p
        return None

    def trap_reset(self) -> None:
        self.sec_pos, self.sec_targets, self.pending = None, {}, []

    def trap_ft_ready(self, now: float | None = None) -> bool:
        now = time.perf_counter() if now is None else now
        return now - self.trap_ft_t > TRAP_FT_S

    def trap_ft_mark(self, now: float | None = None) -> None:
        self.trap_ft_t = time.perf_counter() if now is None else now
