"""carnet.py -- CARNET DE ROUTE DE V (01/11/2026, Olivier : « 4 »).

A la fin de chaque session (5 min au moins), une page HTML autonome est ecrite dans le dossier de donnees, sous-dossier
« carnet » : chiffres de la session, carte du trajet de V, moments forts (combats, morts, votes du chat, evenements du monde,
succes, implants, missions). Page sans dependance externe : on peut l ouvrir, l envoyer ou l heberger telle quelle.

Le carnet suit le journal de V (feed, appele par brain._log) et sa position (pos, 1 point toutes les 10 s). Les pseudos
et les noms de quetes viennent de l exterieur : tout texte passe par html.escape.
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path

from .config import DATA_DIR

CARNET_DIR = DATA_DIR / 'carnet'
MIN_S = 300.0                      # session plus courte : pas de carnet
POS_S = 10.0
JUMP_M = 300.0                     # saut plus long = voyage rapide ou rechargement : le trace est coupe
MAX_EVENTS = 400
MAX_POINTS = 6000

# (motif du journal, type, gabarit du texte ; {0}, {1} = groupes captures)
RULES = [
    (r'V EST MORT : arret', 'mort', 'V est mort'),
    (r'V engage le combat : (\d+) hostile.*« (.+?) »', 'combat', 'Combat : {1} ({0} hostile(s))'),
    (r'\[youtube\] VOTE : « (.+?) » l emporte \((\d+) voix sur (\d+)', 'vote', 'Le chat a vote : {0} ({1} voix sur {2})'),
    (r'\[monde\] le chat declenche : (\w+)', 'monde', 'Le chat change le monde : {0}'),
    (r'\[succes\] DEBLOQUE \(Steam\) : (.+?) \(', 'succes', 'Succes debloque : {0}'),
    (r'\[charcudoc\] POSE « (.+?) »', 'implant', 'Implant pose : {0}'),
    (r'^objectif : (.+?)\s+\(marqueur', 'mission', '{0}'),
    (r'^voyage rapide : arrive a (.+)', 'voyage', 'Voyage rapide : {0}'),
]
_RX = [(re.compile(p), k, tpl) for p, k, tpl in RULES]
COLORS = {'mort': 'var(--red)', 'combat': 'var(--orange)', 'vote': 'var(--cyan)', 'monde': 'var(--green)', 'succes': 'var(--gold)',
          'implant': 'var(--violet)', 'mission': 'var(--text-dim)', 'voyage': 'var(--text-dim)'}
LABELS = {'mort': 'Morts', 'combat': 'Combats', 'vote': 'Votes', 'monde': 'Monde', 'succes': 'Succes', 'implant': 'Implants',
          'mission': 'Missions', 'voyage': 'Voyages'}

_S: dict = {'t0': None, 'seg': [], 'events': [], 'last': None, 'last_t': 0.0}


def reset() -> None:
    _S.update(t0=time.time(), seg=[], events=[], last=None, last_t=0.0)


def pos(x: float, y: float, now: float | None = None) -> None:
    """Position de V (appel a chaque tour de boucle ; 1 point garde toutes les POS_S secondes)."""
    now = time.time() if now is None else now
    if now - _S['last_t'] < POS_S:
        return
    last = _S['last']
    if last is None or abs(x - last[0]) + abs(y - last[1]) > JUMP_M or not _S['seg']:
        _S['seg'].append([])
    if sum(len(s) for s in _S['seg']) < MAX_POINTS:
        _S['seg'][-1].append((round(x, 1), round(y, 1)))
    _S['last'], _S['last_t'] = (x, y), now


def feed(msg: str, now: float | None = None) -> None:
    """Une ligne du journal de V -> eventuellement un moment du carnet."""
    if len(_S['events']) >= MAX_EVENTS:
        return
    for rx, kind, tpl in _RX:
        m = rx.search(msg)
        if m:
            ev = _S['events']
            text = tpl.format(*m.groups())
            if ev and ev[-1]['k'] == kind and ev[-1]['text'] == text:
                return
            ev.append({'t': time.time() if now is None else now, 'k': kind, 'text': text, 'p': _S['last']})
            return


def _map_svg() -> str:
    pts = [p for s in _S['seg'] for p in s]
    if len(pts) < 2:
        return ''
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    span = max(x1 - x0, y1 - y0, 100.0)
    w, h, m = 1000.0, 620.0, 30.0
    k = min((w - 2 * m) / span, (h - 2 * m) / span)
    ox, oy = (w - (x1 - x0) * k) / 2, (h - (y1 - y0) * k) / 2

    def xy(p):
        return f'{ox + (p[0] - x0) * k:.1f},{h - oy - (p[1] - y0) * k:.1f}'     # nord en haut
    parts = [f'<svg viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="Trajet de V pendant la session">']
    for s in _S['seg']:
        if len(s) > 1:
            parts.append(f'<polyline points="{" ".join(xy(p) for p in s)}" class="trail"/>')
    for e in _S['events']:
        if e.get('p') and e['k'] in ('mort', 'combat', 'vote', 'monde', 'succes', 'implant'):
            cx, cy = xy(e['p']).split(',')
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{9 if e["k"] == "mort" else 6}" fill="{COLORS[e["k"]]}">'
                         f'<title>{html.escape(e["text"])}</title></circle>')
    a, b = xy(pts[0]).split(','), xy(pts[-1]).split(',')
    parts.append(f'<circle cx="{a[0]}" cy="{a[1]}" r="7" class="ends"/><circle cx="{b[0]}" cy="{b[1]}" r="11" class="ends"/>')
    parts.append('</svg>')
    return ''.join(parts)


CSS = """
:root{--bg:#0b0f15;--panel:#141a23;--line:#263140;--text:#e9e6dc;--text-dim:#93a0b0;--red:#ff4b42;--orange:#ff9a3d;--cyan:#52fff0;
--green:#59ff8c;--gold:#ffd447;--violet:#c785ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 Bahnschrift,'Segoe UI',system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 56px}h1{margin:0;font-size:34px;letter-spacing:.04em;color:var(--gold)}
h2{margin:36px 0 12px;font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan)}.sub{color:var(--text-dim);margin:4px 0 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-top:24px}
.kpi{background:var(--panel);border:1px solid var(--line);padding:12px 14px}.kpi b{display:block;font-size:26px;line-height:1.2}
.kpi span{font-size:13px;color:var(--text-dim)}svg{display:block;width:100%;height:auto;background:var(--panel);border:1px solid var(--line)}
.trail{fill:none;stroke:var(--cyan);stroke-width:2.5;stroke-opacity:.75;stroke-linejoin:round}.ends{fill:none;stroke:var(--text);stroke-width:3}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:8px 0 0;font-size:13px;color:var(--text-dim)}.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
ol{list-style:none;margin:0;padding:0}li{display:grid;grid-template-columns:62px 1fr;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
li time{color:var(--text-dim);font-variant-numeric:tabular-nums}li.mission,li.voyage{color:var(--text-dim)}
li .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px}
"""


def page(info: dict) -> str:
    esc = html.escape
    t0 = _S['t0'] or time.time()
    votes = sum(e['k'] == 'vote' for e in _S['events'])
    kpis = [('Duree', f"{info.get('minutes', 0):.0f} min"), ('Distance', f"{info.get('km', 0.0):.1f} km"), ('Combats', info.get('combats', 0)),
            ('Neutralises', f"~{info.get('kills', 0)}"), ('Morts', info.get('morts', 0)), ('Objets', info.get('loot', 0))]
    if info.get('eddies') is not None:
        kpis.append(('Eddies', f"{info['eddies']:+}"))
    if votes:
        kpis.append(('Votes du chat', votes))
    out = ['<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           f"<title>Carnet de V</title><style>{CSS}</style></head><body><main>",
           f"<h1>Carnet de route de V</h1><p class=\"sub\">Session du {time.strftime('%d/%m/%Y a %H:%M', time.localtime(t0))}</p>",
           '<section class="kpis">' + ''.join(f'<div class="kpi"><b>{esc(str(v))}</b><span>{esc(k)}</span></div>' for k, v in kpis) + '</section>']
    svg = _map_svg()
    if svg:
        kinds = [k for k in ('mort', 'combat', 'vote', 'monde', 'succes', 'implant') if any(e['k'] == k for e in _S['events'])]
        out.append('<h2>Le trajet</h2>' + svg + '<p class="legend">' + ''.join(f'<span><i style="background:{COLORS[k]}"></i>{LABELS[k]}</span>' for k in kinds) + '</p>')
    if info.get('top'):
        out.append('<h2>Top du chat</h2><ol>' + ''.join(f'<li><time>{i + 1}</time><span>{esc(str(a))} : {esc(str(p))} points</span></li>'
                                                         for i, (a, p) in enumerate(info['top'])) + '</ol>')
    out.append('<h2>Les moments</h2><ol>')
    for e in _S['events']:
        out.append(f'<li class="{e["k"]}"><time>{time.strftime("%H:%M", time.localtime(e["t"]))}</time>'
                   f'<span><span class="dot" style="background:{COLORS[e["k"]]}"></span>{esc(e["text"])}</span></li>')
    out.append('</ol></main></body></html>')
    return ''.join(out)


def build(info: dict) -> Path | None:
    """Ecrit la page de la session ; None si la session est trop courte ou vide."""
    if (info.get('minutes') or 0.0) * 60.0 < MIN_S or not (_S['events'] or _S['seg']):
        return None
    CARNET_DIR.mkdir(parents=True, exist_ok=True)
    path = CARNET_DIR / f"carnet_{time.strftime('%Y-%m-%d_%Hh%M', time.localtime(_S['t0'] or time.time()))}.html"
    path.write_text(page(info), encoding='utf-8')
    return path
