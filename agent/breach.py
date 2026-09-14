"""
breach.py -- competence "resoudre le Breach Protocol" (terminal de piratage / point d acces / mini-jeu de la grille).

Donnees (commande Lua `breach_info`, servie meme pendant la pause du jeu grace au relais onDraw du mod) :
  - texts  : tous les textes hexadecimaux (1C, 55, BD, E9, 7A, FF) de l interface, avec leur position ABSOLUE dans le
             canevas ink (x, y, w, h) et la geometrie de leur parent (px, py, pw, ph = la case cliquable)
  - size   : taille de la grille (5, 6 ou 7), buffer : taille du tampon (nombre de cases selectionnables)
  - chains : sequences numeriques du jeu (rarities), avec matched / fulfilled / possible
  - programs : noms des daemons (meme ordre que les chaines), last : derniere case selectionnee (LastPlayerHackPosition)
  - root   : taille du canevas racine (pour convertir en pixels ecran)

Regles du mini-jeu : premiere selection dans la LIGNE 0 ; ensuite on alterne COLONNE de la derniere case, LIGNE de la
derniere case... ; une case ne sert qu une fois ; au plus `buffer` selections ; un daemon est valide si sa sequence
apparait d un seul tenant dans le tampon. Le chronometre ne demarre qu a la premiere selection.

Resolution : on combine les daemons (sous-ensembles + ordres, avec chevauchement des extremites), on garde les cibles
qui tiennent dans le tampon, classees par valeur (un daemon plus bas dans la liste vaut plus : DATAMINE_V3 > V2 > V1),
et on cherche par DFS un chemin de la grille qui epelle la cible (des coups "perdus" sont permis avant la cible et entre
deux daemons). Puis on clique les cases (souris absolue) en verifiant chaque selection via l export du mod.
"""
from __future__ import annotations

import itertools
import math
import re
import time

from . import input_kbm as kbm, motion, nav

HEX = re.compile(r'^[0-9A-F]{2}$')
CLICK_SETTLE_S = 0.12         # entre le deplacement de la souris et le clic
VERIFY_TIMEOUT_S = 1.6        # attente de la prise en compte d une selection
MAX_RETRY_CLICK = 2


# ---------------------------------------------------------------- lecture / analyse ---------------------------------
def info(dump: bool = False, timeout: float = 3.0) -> dict | None:
    return nav._wait(nav._send({'cmd': 'breach_info', 'x': 1 if dump else 0}), timeout=timeout)


def _rows(texts: list[dict], tol: float) -> list[list[dict]]:
    """Regroupe des textes par ligne (y proches), chaque ligne triee par x."""
    items = sorted(texts, key=lambda t: (t['y'], t['x']))
    rows: list[list[dict]] = []
    for t in items:
        if rows and abs(rows[-1][0]['y'] - t['y']) <= tol:
            rows[-1].append(t)
        else:
            rows.append([t])
    for r in rows:
        r.sort(key=lambda t: t['x'])
    return rows


def _center(t: dict) -> tuple[float, float]:
    """Centre de la case cliquable : le parent du texte s il a une taille, sinon le texte lui-meme."""
    if (t.get('pw') or 0) > 0 and (t.get('ph') or 0) > 0:
        return t['px'] + t['pw'] / 2.0, t['py'] + t['ph'] / 2.0
    if (t.get('w') or 0) > 0 and (t.get('h') or 0) > 0:
        return t['x'] + t['w'] / 2.0, t['y'] + t['h'] / 2.0
    return t['x'], t['y']


def parse(inf: dict) -> dict:
    """Separe grille / sequences / tampon a partir des textes hexadecimaux. Renvoie {'ok': False, 'reason'} sinon."""
    texts = [t for t in (inf.get('texts') or []) if HEX.match(str(t.get('t', '')))]
    if not texts:
        return {'ok': False, 'reason': 'aucun texte hexadecimal'}
    n = int(inf.get('size') or 0)
    heights = sorted([t.get('h') or t.get('ph') or 0 for t in texts if (t.get('h') or t.get('ph') or 0) > 0])
    tol = max(6.0, (heights[len(heights) // 2] if heights else 20.0) * 0.45)
    rows = _rows(texts, tol)
    # grille : N lignes de N textes partageant la meme signature de colonnes (les lignes des sequences, a droite,
    # s intercalent en y avec celles de la grille : on ne peut pas exiger des lignes consecutives)
    candidates = [n] if n else [7, 6, 5]
    grid_rows = None
    for k in candidates:
        groups: list[list[list[dict]]] = []          # groupes de lignes dont les colonnes s alignent (tolerance)
        for r in rows:
            if len(r) != k:
                continue
            for g in groups:
                if all(abs(r[j]['x'] - g[0][j]['x']) <= tol * 2 for j in range(k)):
                    g.append(r)
                    break
            else:
                groups.append([r])
        for g in groups:
            if len(g) >= k:
                grid_rows, n = sorted(g, key=lambda r: r[0]['y'])[:k], k
                break
        if grid_rows:
            break
    if not grid_rows:
        return {'ok': False, 'reason': f'grille introuvable (taille {n or "?"}, {len(texts)} textes, {len(rows)} lignes)'}
    grid = [[t['t'] for t in r] for r in grid_rows]
    cells = {(ri, ci): _center(t) for ri, r in enumerate(grid_rows) for ci, t in enumerate(r)}
    gx_min = min(t['x'] for r in grid_rows for t in r)
    gx_max = max(t['x'] + (t.get('w') or 0) for r in grid_rows for t in r)
    gy_min = min(t['y'] for r in grid_rows for t in r)
    gy_max = max(t['y'] for r in grid_rows for t in r)
    in_grid = {id(t) for r in grid_rows for t in r}
    others = [r for r in rows if not any(id(t) in in_grid for t in r)]
    # sequences : lignes a DROITE de la grille (disposition du jeu) ; sinon lignes sous / au-dessus hors tampon
    right = [r for r in others if min(t['x'] for t in r) > gx_max - tol]
    seq_rows = right if right else [r for r in others if r[0]['y'] > gy_max + tol]
    seq_rows = [r for r in seq_rows if 1 <= len(r) <= 8]
    seq_rows.sort(key=lambda r: r[0]['y'])
    seqs = [[t['t'] for t in r] for r in seq_rows]
    buffer_rows = [r for r in others if r[0]['y'] < gy_min - tol and min(t['x'] for t in r) < gx_max]   # au-dessus de la grille
    buffer_filled = [t['t'] for r in buffer_rows for t in r]
    return {'ok': True, 'n': n, 'buffer': int(inf.get('buffer') or 0) or None, 'grid': grid, 'cells': cells,
            'seqs': seqs, 'buffer_filled': buffer_filled, 'root': inf.get('root') or {}, 'chains': inf.get('chains') or [],
            'programs': inf.get('programs') or [], 'n_hex': len(texts), 'last': inf.get('last'),
            'state': inf.get('state'), 'timer': inf.get('timer')}


# ---------------------------------------------------------------- resolution -----------------------------------------
def _merge(seqs: list[list[str]]) -> tuple[list[str], list[bool]]:
    """Concatene des sequences avec le plus grand chevauchement fin/debut. Renvoie (cible, coup_libre_avant[i])."""
    target: list[str] = []
    free: list[bool] = []            # True si un coup perdu est permis juste AVANT la position i (frontiere sans chevauchement)
    for s in seqs:
        if not target:
            target, free = list(s), [True] + [False] * (len(s) - 1)
            continue
        ov = 0
        for k in range(min(len(target), len(s)), 0, -1):
            if target[-k:] == s[:k]:
                ov = k
                break
        rest = s[ov:]
        target += rest
        free += ([ov == 0] if rest else []) + [False] * (max(0, len(rest) - 1))
    return target, free


def _find_path(grid: list[list[str]], target: list[str], free: list[bool], buffer: int) -> list[tuple[int, int]] | None:
    """DFS : chemin (ligne 0 d abord, puis colonne/ligne alternees, cases uniques, <= buffer coups) qui epelle la cible.
    Des coups perdus sont permis avant la cible et aux frontieres `free`, tant qu il reste des coups."""
    n = len(grid)
    slack = buffer - len(target)
    if slack < 0:
        return None
    best: list[tuple[int, int]] | None = None

    def moves(last, horizontal, used):
        if last is None:
            return [(0, c) for c in range(n) if (0, c) not in used]
        r, c = last
        if horizontal:
            return [(r, cc) for cc in range(n) if (r, cc) not in used]
        return [(rr, c) for rr in range(n) if (rr, c) not in used]

    def dfs(pos, last, horizontal, used, path, slack_left):
        nonlocal best
        if pos == len(target):
            if best is None or len(path) < len(best):
                best = list(path)
            return True
        if len(path) >= buffer:
            return False
        for cell in moves(last, horizontal, used):
            sym = grid[cell[0]][cell[1]]
            if sym == target[pos]:
                used.add(cell); path.append(cell)
                if dfs(pos + 1, cell, not horizontal, used, path, slack_left):
                    return True
                path.pop(); used.discard(cell)
        if slack_left > 0 and free[pos]:            # coup perdu (avant la cible ou entre deux daemons)
            for cell in moves(last, horizontal, used):
                used.add(cell); path.append(cell)
                if dfs(pos, cell, not horizontal, used, path, slack_left - 1):
                    return True
                path.pop(); used.discard(cell)
        return False

    dfs(0, None, True, set(), [], slack)
    return best


def solve(grid: list[list[str]], seqs: list[list[str]], buffer: int, weights: list[float] | None = None,
          skip: set[int] | None = None) -> dict:
    """Meilleure combinaison de daemons realisable. Renvoie {'path': [(r,c)...], 'daemons': [i...], 'value', 'symbols'}."""
    idx = [i for i in range(len(seqs)) if seqs[i] and not (skip and i in skip)]
    w = weights or [1.0 + 0.5 * i for i in range(len(seqs))]          # plus bas dans la liste = plus precieux
    options = []
    for k in range(len(idx), 0, -1):
        for combo in itertools.combinations(idx, k):
            for order in itertools.permutations(combo):
                target, free = _merge([seqs[i] for i in order])
                if len(target) <= buffer:
                    options.append((sum(w[i] for i in order), -len(target), order, target, free))
    options.sort(key=lambda o: (-o[0], o[1]))
    seen = set()
    for value, _, order, target, free in options:
        key = tuple(target)
        if key in seen:
            continue
        seen.add(key)
        path = _find_path(grid, target, free, buffer)
        if path:
            return {'path': path, 'daemons': list(order), 'value': value, 'symbols': [grid[r][c] for r, c in path], 'target': target}
    return {'path': [], 'daemons': [], 'value': 0.0, 'symbols': [], 'target': []}


# ---------------------------------------------------------------- execution ------------------------------------------
def _to_screen(x: float, y: float, root: dict, client: tuple[int, int, int, int]) -> tuple[int, int]:
    """Canevas ink (racine rw x rh, ajuste en hauteur et centre) -> pixels ecran, d apres le rectangle client du jeu."""
    left, top, cw, ch = client
    rw, rh = float(root.get('w') or 0), float(root.get('h') or 0)
    if rw <= 0 or rh <= 0:
        rw, rh = 1920.0, 1080.0
    scale = ch / rh
    offx = (cw - rw * scale) / 2.0
    return int(round(left + offx + x * scale)), int(round(top + y * scale))


def _click_cell(cell_xy: tuple[float, float], root: dict, client, log) -> tuple[int, int]:
    sx, sy = _to_screen(cell_xy[0], cell_xy[1], root, client)
    kbm.move_abs(sx, sy); time.sleep(CLICK_SETTLE_S)
    kbm.move_abs(sx + 1, sy); time.sleep(0.03)          # un petit mouvement pour declencher le survol (mise en surbrillance)
    kbm.mouse_tap('left', 0.08)
    return sx, sy


def _selection_registered(before: dict, cell: tuple[int, int], log, conv: list) -> tuple[bool, dict | None]:
    """Attend que la selection soit prise en compte : LastPlayerHackPosition == case (convention apprise) ou un texte
    hexadecimal de plus (le tampon s est rempli)."""
    t0 = time.perf_counter()
    r, c = cell
    while time.perf_counter() - t0 < VERIFY_TIMEOUT_S:
        inf = info(timeout=1.0)
        if inf and inf.get('ok'):
            last = inf.get('last') or {}
            lx, ly = last.get('x'), last.get('y')
            if lx is not None and ly is not None:
                if conv and conv[0] == 'xy' and int(round(lx)) == c and int(round(ly)) == r:
                    return True, inf
                if conv and conv[0] == 'yx' and int(round(lx)) == r and int(round(ly)) == c:
                    return True, inf
                if not conv:                       # convention (x=colonne,y=ligne ou l inverse) apprise au 1er coup
                    if int(round(lx)) == c and int(round(ly)) == r:
                        if c != r:
                            conv.append('xy')
                        return True, inf
                    if int(round(lx)) == r and int(round(ly)) == c:
                        if c != r:
                            conv.append('yx')
                        return True, inf
            if len(inf.get('texts') or []) > (before.get('n_hex') or 0):
                return True, inf
            if int(inf.get('state') or 0) in (2, 3):
                return True, inf
        time.sleep(0.15)
    return False, None


def run(stop=None, log=print, dry: bool = False) -> dict:
    """Resout et joue le Breach Protocol ouvert. dry=True : analyse + solution seulement, aucun clic."""
    t0 = time.perf_counter()
    inf = None
    for _ in range(8):                                   # le mod capture le controleur a l ouverture ; on lui laisse 2 s
        inf = info()
        if inf and inf.get('ok'):
            break
        if stop is not None and stop.is_set():
            return {'ok': False, 'reason': 'arret'}
        time.sleep(0.25)
    if not inf or not inf.get('ok'):
        return {'ok': False, 'reason': f"pas d info du mod ({(inf or {}).get('reason')})"}
    p = parse(inf)
    if not p.get('ok'):
        info(dump=True)                                  # journalise tous les textes pour diagnostiquer la disposition
        return {'ok': False, 'reason': p.get('reason')}
    n, grid, seqs = p['n'], p['grid'], p['seqs']
    buffer = p.get('buffer') or 0
    chains = p.get('chains') or []
    programs = p.get('programs') or []
    if buffer <= 0:
        buffer = max((len(s) for s in seqs), default=4) + 2
    skip = {i for i, ch in enumerate(chains) if ch.get('fulfilled') or ch.get('possible') is False}
    log(f"  [breach] grille {n}x{n}, tampon {buffer}, {len(seqs)} daemon(s) : "
        + ' | '.join(f"{(programs[i].get('name') if i < len(programs) else '?')}={' '.join(s)}" for i, s in enumerate(seqs))
        + (f"  (ignores : {sorted(skip)})" if skip else ''))
    for row in grid:
        log('  [breach]   ' + ' '.join(row))
    if chains and len(chains) != len(seqs):
        log(f"  [breach] attention : {len(chains)} chaines cote jeu, {len(seqs)} lignes de sequence lues")
    sol = solve(grid, seqs, buffer, skip=skip)
    if not sol['path']:
        return {'ok': False, 'reason': 'aucun daemon realisable', 'parse': p}
    log(f"  [breach] solution : {' '.join(sol['symbols'])} -> daemons {sol['daemons']} (valeur {sol['value']:.1f}), "
        f"cases {sol['path']}")
    if dry:
        return {'ok': True, 'dry': True, 'solution': sol, 'parse': p}
    client = kbm.game_client_rect()
    if not client:
        return {'ok': False, 'reason': 'fenetre du jeu introuvable (pas au premier plan ?)'}
    log(f"  [breach] fenetre {client}, canevas {p['root']}")
    conv: list = []
    before = p
    done = 0
    for i, cell in enumerate(sol['path']):
        if stop is not None and stop.is_set():
            return {'ok': False, 'reason': 'arret', 'clics': done}
        ok = False
        for attempt in range(MAX_RETRY_CLICK + 1):
            sx, sy = _click_cell(p['cells'][cell], p['root'], client, log)
            ok, inf2 = _selection_registered(before, cell, log, conv)
            if ok:
                if inf2:
                    before = {'n_hex': len(inf2.get('texts') or [])}
                break
            log(f"  [breach] case {cell} ({sol['symbols'][i]}) non prise en compte (ecran {sx},{sy}), essai {attempt + 2}")
            time.sleep(0.3)
        if not ok:
            return {'ok': False, 'reason': f'selection {cell} refusee', 'clics': done, 'solution': sol}
        done += 1
        log(f"  [breach] {i + 1}/{len(sol['path'])} : {sol['symbols'][i]} en {cell}")
        if inf2 and int(inf2.get('state') or 0) in (2, 3):
            break
    # fin du mini-jeu : il se termine seul quand le tampon est plein ou tous les daemons valides ; sinon on comble le tampon
    t1 = time.perf_counter()
    state = None
    while time.perf_counter() - t1 < 12.0:
        inf3 = info(timeout=1.0)
        state = int((inf3 or {}).get('state') or 0)
        if state in (2, 3) or not inf3:
            break
        if state == 1 and inf3.get('ok'):
            # il reste des coups et le jeu attend : on joue une case autorisee au hasard pour remplir le tampon
            p3 = parse(inf3)
            if p3.get('ok') and len(p3.get('buffer_filled') or []) < buffer:
                last = sol['path'][-1] if sol['path'] else None
                horizontal = (len(sol['path']) % 2 == 0)
                used = set(sol['path'])
                cands = ([(last[0], c) for c in range(n)] if (last and horizontal) else
                         [(r, last[1]) for r in range(n)] if last else [(0, c) for c in range(n)])
                cands = [c for c in cands if c not in used]
                if cands:
                    cell = cands[0]
                    _click_cell(p3['cells'][cell], p3['root'], client, log)
                    sol['path'].append(cell)
                    log(f"  [breach] coup de remplissage en {cell}")
                    time.sleep(0.6)
                    continue
        time.sleep(0.4)
    res = 'reussi' if state == 2 else ('echoue' if state == 3 else f'etat {state}')
    log(f"  [breach] {res} en {time.perf_counter() - t0:.1f} s ({done} selections)")
    # ecran de resultat : il se ferme seul ou sur une touche ; on attend que le mod reprenne (etat non 'paused')
    t2 = time.perf_counter()
    while time.perf_counter() - t2 < 8.0:
        st = motion.read_state() or {}
        if not st.get('paused') and int((st.get('breach') or {}).get('state') or 0) != 1:
            break
        time.sleep(0.3)
    else:
        kbm.tap('ESC', 0.08); time.sleep(1.0)
    return {'ok': state == 2, 'state': state, 'clics': done, 'solution': sol, 'seconds': time.perf_counter() - t0}
