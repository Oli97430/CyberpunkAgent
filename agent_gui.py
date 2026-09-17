"""
agent_gui.py -- panneau de configuration de CyberpunkAgent (fenetre Windows, sans le jeu).

    python agent_gui.py          ou  CyberpunkAgent-Config.exe

Reglages : modele de decision (Ollama local / OpenAI / Anthropic), cle API (masquee), modeles,
comportements (radio, conduite, sauvetages, courses, charcudoc), duree de session.
Tout est ecrit dans %APPDATA%\\CyberpunkAgent\\config.json (la cle n est jamais journalisee).
Boutons : Verifier (test du fournisseur et de l installation), Lancer V (ouvre le lanceur).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent.config import CFG, DATA_DIR  # noqa: E402

CONFIG_PATH = DATA_DIR / 'config.json'
PROVIDERS = [('Ollama (local, gratuit, rien ne sort du PC)', 'ollama'),
             ('OpenAI (cle API)', 'openai'),
             ('Anthropic / Claude (cle API)', 'anthropic')]
FEATURES = [('radio', 'Ecouter la radio de temps en temps'),
            ('driving', 'Conduire (appeler le vehicule, autodrive) quand l objectif est loin'),
            ('rescue', 'Intervenir dans les agressions a proximite'),
            ('sell', 'Aller vendre la camelote et acheter des soins'),
            ('ripperdoc', 'Aller chez le charcudoc s optimiser (cyberware)'),
            ('buffs', 'Se buffer avant et pendant le combat (nourriture, boissons, boosters)'),
            ('stealth', 'Approcher en discretion et eliminer furtivement quand V n est pas repere'),
            ('sprint', 'Sprinter sans cesse en combat (perk : +60 % de regeneration de sante en sprint)'),
            ('steal', 'Voler une voiture arretee (par envie, ou quand la sienne n arrive pas)'),
            ('terminals', 'Se connecter aux points d acces a portee et jouer le Breach Protocol'),
            ('fasttravel', 'Utiliser le voyage rapide (bornes) pour les objectifs lointains'),
            ('phone', 'Repondre aux appels'),
            ('sms', 'Lire et repondre aux SMS'),
            ('appearance', 'Changer d apparence au miroir de l appartement de temps en temps'),
            ('recipes', 'Acheter et apprendre des plans de craft chez les marchands'),
            ('focus_tracked', 'La quete suivie (assignee par le joueur) est prioritaire sur tout le reste')]
TEMPERAMENT = [('courage', 'Courage', [('prudent', 'Prudent : evite des 5 hostiles, fuit vite, secourt a 80 % de vie'),
                                      ('equilibre', 'Equilibre : se bat jusqu a 5, fuit a 6, secourt a 60 %'),
                                      ('temeraire', 'Temeraire : se bat jusqu a 7, ne fuit que presque mort, secourt a 40 %')]),
               ('style', 'Style de combat', [('melee', 'Melee : katana / contondantes, arme a feu seulement loin ou en hauteur'),
                                            ('mixte', 'Mixte : arme a feu des 8 m'),
                                            ('distance', 'Distance : tireur, arme a feu des 4 m')]),
               ('aggro', 'Agressivite', [('defensif', 'Defensif : ne se bat que s il est attaque ou au contact'),
                                        ('normal', 'Normal : engage les hostiles a portee'),
                                        ('chasseur', 'Chasseur : attaque tout hostile en vue (45 m)')])]


def load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save(d: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding='utf-8')


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title('CyberpunkAgent — configuration')
        self.resizable(False, False)
        cfg = load()
        pad = {'padx': 10, 'pady': 4}
        frm = ttk.Frame(self, padding=12); frm.grid(sticky='nsew')

        ttk.Label(frm, text='Modele de decision', font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', **pad)
        self.provider = tk.StringVar(value=cfg.get('provider') or CFG.provider or 'ollama')
        for i, (label, val) in enumerate(PROVIDERS):
            ttk.Radiobutton(frm, text=label, value=val, variable=self.provider, command=self._refresh).grid(row=1 + i, column=0, columnspan=3, sticky='w', padx=24)

        r = 4
        ttk.Label(frm, text='Cle API (OpenAI / Anthropic)').grid(row=r, column=0, sticky='w', **pad)
        self.api_key = tk.StringVar(value=cfg.get('api_key') or '')
        self.key_entry = ttk.Entry(frm, textvariable=self.api_key, width=46, show='•')
        self.key_entry.grid(row=r, column=1, sticky='w', **pad)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text='afficher', variable=self.show_key, command=lambda: self.key_entry.config(show='' if self.show_key.get() else '•')).grid(row=r, column=2, sticky='w')

        r += 1
        ttk.Label(frm, text='Modele Ollama').grid(row=r, column=0, sticky='w', **pad)
        self.model = tk.StringVar(value=cfg.get('model') or CFG.model or 'llama3.2:latest')
        ttk.Entry(frm, textvariable=self.model, width=46).grid(row=r, column=1, sticky='w', **pad)
        r += 1
        ttk.Label(frm, text='Modele OpenAI').grid(row=r, column=0, sticky='w', **pad)
        self.openai_model = tk.StringVar(value=cfg.get('openai_model') or 'gpt-4o-mini')
        ttk.Entry(frm, textvariable=self.openai_model, width=46).grid(row=r, column=1, sticky='w', **pad)
        r += 1
        ttk.Label(frm, text='Modele Anthropic').grid(row=r, column=0, sticky='w', **pad)
        self.anthropic_model = tk.StringVar(value=cfg.get('anthropic_model') or 'claude-haiku-4-5-20251001')
        ttk.Entry(frm, textvariable=self.anthropic_model, width=46).grid(row=r, column=1, sticky='w', **pad)
        r += 1
        ttk.Label(frm, text='Dossier du jeu').grid(row=r, column=0, sticky='w', **pad)
        self.game_dir = tk.StringVar(value=cfg.get('game_dir') or (str(CFG.game_dir) if CFG.game_dir else ''))
        ttk.Entry(frm, textvariable=self.game_dir, width=46).grid(row=r, column=1, sticky='w', **pad)

        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky='ew', pady=8)
        r += 1
        ttk.Label(frm, text='Comportements de V', font=('Segoe UI', 11, 'bold')).grid(row=r, column=0, columnspan=3, sticky='w', **pad)
        feats = cfg.get('features') or {}
        self.features: dict[str, tk.BooleanVar] = {}
        for key, label in FEATURES:
            r += 1
            v = tk.BooleanVar(value=bool(cfg.get('focus_tracked', True)) if key == 'focus_tracked' else bool(feats.get(key, True)))
            self.features[key] = v
            ttk.Checkbutton(frm, text=label, variable=v).grid(row=r, column=0, columnspan=3, sticky='w', padx=24)

        self.temper: dict[str, tk.StringVar] = {}
        for key, label, opts in TEMPERAMENT:
            r += 1
            ttk.Label(frm, text=label, font=('Segoe UI', 10, 'bold')).grid(row=r, column=0, columnspan=3, sticky='w', **pad)
            v = tk.StringVar(value=cfg.get(key) or {'courage': 'temeraire', 'style': 'melee', 'aggro': 'normal'}[key])
            self.temper[key] = v
            for val, text in opts:
                r += 1
                ttk.Radiobutton(frm, text=text, value=val, variable=v).grid(row=r, column=0, columnspan=3, sticky='w', padx=24)
        r += 1
        ttk.Label(frm, text='Duree d une session (minutes)').grid(row=r, column=0, sticky='w', **pad)
        self.minutes = tk.StringVar(value=str(cfg.get('minutes') or 20))
        ttk.Spinbox(frm, from_=5, to=240, increment=5, textvariable=self.minutes, width=8).grid(row=r, column=1, sticky='w', **pad)

        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky='ew', pady=8)
        r += 1
        btns = ttk.Frame(frm); btns.grid(row=r, column=0, columnspan=3, sticky='ew')
        ttk.Button(btns, text='Enregistrer', command=self.on_save).pack(side='left', padx=4)
        ttk.Button(btns, text='Verifier l installation', command=self.on_check).pack(side='left', padx=4)
        ttk.Button(btns, text='Lancer V', command=self.on_launch).pack(side='left', padx=4)
        ttk.Button(btns, text='Ouvrir les journaux', command=lambda: os.startfile(str(DATA_DIR))).pack(side='left', padx=4)
        r += 1
        self.status = tk.StringVar(value=f'Configuration : {CONFIG_PATH}')
        ttk.Label(frm, textvariable=self.status, wraplength=560, foreground='#555').grid(row=r, column=0, columnspan=3, sticky='w', **pad)
        self._refresh()

    def _refresh(self) -> None:
        state = 'normal' if self.provider.get() != 'ollama' else 'disabled'
        self.key_entry.config(state=state)

    def collect(self) -> dict:
        d = load()
        d.update({'provider': self.provider.get(), 'model': self.model.get().strip() or 'llama3.2:latest',
                  'openai_model': self.openai_model.get().strip() or None, 'anthropic_model': self.anthropic_model.get().strip() or None,
                  'features': {k: bool(v.get()) for k, v in self.features.items() if k != 'focus_tracked'},
                  'focus_tracked': bool(self.features['focus_tracked'].get()),
                  'minutes': int(float(self.minutes.get() or 20)),
                  **{k: v.get() for k, v in self.temper.items()}})
        key = self.api_key.get().strip()
        if key:
            d['api_key'] = key
        elif 'api_key' in d and not key:
            d.pop('api_key', None)
        gd = self.game_dir.get().strip()
        if gd:
            d['game_dir'] = gd
        return d

    def on_save(self) -> None:
        save(self.collect())
        self.status.set(f'Enregistre dans {CONFIG_PATH}')

    def on_check(self) -> None:
        self.on_save()
        self.status.set('Verification en cours...')

        def work():
            exe = Path(sys.executable)
            if getattr(sys, 'frozen', False):
                cmd = [str(exe.parent / 'CyberpunkAgent.exe'), '--check']
            else:
                cmd = [sys.executable, str(Path(__file__).resolve().parent / 'run_agent.py'), '--check']
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                txt = (out.stdout or '') + (out.stderr or '')
            except Exception as e:
                txt = f'verification impossible : {e}'
            self.after(0, lambda: (self.status.set(txt.strip()[-900:]), messagebox.showinfo('Verification', txt.strip() or 'aucune sortie')))
        threading.Thread(target=work, daemon=True).start()

    def on_launch(self) -> None:
        self.on_save()
        minutes = self.minutes.get() or '20'
        if getattr(sys, 'frozen', False):
            cmd = [str(Path(sys.executable).parent / 'CyberpunkAgent.exe'), minutes]
        else:
            cmd = [sys.executable, str(Path(__file__).resolve().parent / 'run_agent.py'), minutes]
        try:
            subprocess.Popen(cmd, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
            self.status.set(f'Lanceur ouvert : V joue {minutes} min des que le jeu est au premier plan (F11 pause, F12 arret).')
        except Exception as e:
            messagebox.showerror('Lancement', str(e))


if __name__ == '__main__':
    App().mainloop()
