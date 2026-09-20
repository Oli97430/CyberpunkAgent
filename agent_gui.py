"""
agent_gui.py -- panneau de configuration de CyberpunkAgent (fenetre Windows, sans le jeu).

    python agent_gui.py          ou  CyberpunkAgent-Config.exe

Reglages, par onglet : Modele (Ollama local / OpenAI / Anthropic, cle API masquee, choix du
modele Ollama avec sa VRAM), Comportements (radio, conduite, sauvetages, courses, charcudoc),
Temperament (courage / style / agressivite, duree de session), Telegram (directives et
comptes-rendus a distance, facultatif).
Tout est ecrit dans %APPDATA%\\CyberpunkAgent\\config.json et telegram.json (jetons jamais
journalises). Boutons : Verifier (test du fournisseur et de l installation), Lancer V.
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
from agent import llm, remote  # noqa: E402

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
            ('sprint', 'Sprinter sans cesse en combat (perk : +60 % de regeneration en sprint)'),
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


def load_telegram() -> dict:
    try:
        return json.loads(remote.TELEGRAM_FILE.read_text(encoding='utf-8-sig'))   # utf-8-sig : tolere un BOM
    except Exception:
        return {}


def save_telegram(d: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    remote.TELEGRAM_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding='utf-8')


PAD = {'padx': 10, 'pady': 5}
SECTION_FONT = ('Segoe UI', 11, 'bold')
GROUP_FONT = ('Segoe UI', 10, 'bold')
MUTED = '#666'


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title('CyberpunkAgent — configuration')
        self.resizable(False, False)
        try:
            ttk.Style(self).theme_use('vista')     # rendu natif Windows, plus net que le theme ttk par defaut
        except tk.TclError:
            pass
        self.cfg = load()
        self.tg = load_telegram()

        outer = ttk.Frame(self, padding=(14, 12, 14, 8))
        outer.grid(sticky='nsew')

        nb = ttk.Notebook(outer)
        nb.grid(row=0, column=0, sticky='nsew')
        tab_model = ttk.Frame(nb, padding=14)
        tab_behaviors = ttk.Frame(nb, padding=14)
        tab_temper = ttk.Frame(nb, padding=14)
        tab_telegram = ttk.Frame(nb, padding=14)
        nb.add(tab_model, text='  Modele  ')
        nb.add(tab_behaviors, text='  Comportements  ')
        nb.add(tab_temper, text='  Temperament  ')
        nb.add(tab_telegram, text='  Telegram  ')

        self._build_model_tab(tab_model)
        self._build_behaviors_tab(tab_behaviors)
        self._build_temperament_tab(tab_temper)
        self._build_telegram_tab(tab_telegram)

        bottom = ttk.Frame(outer, padding=(0, 10, 0, 0))
        bottom.grid(row=1, column=0, sticky='ew')
        ttk.Separator(bottom).pack(fill='x', pady=(0, 8))
        btns = ttk.Frame(bottom); btns.pack(fill='x')
        ttk.Button(btns, text='Enregistrer', command=self.on_save).pack(side='left', padx=(0, 6))
        ttk.Button(btns, text='Verifier l installation', command=self.on_check).pack(side='left', padx=6)
        ttk.Button(btns, text='Lancer V', command=self.on_launch).pack(side='left', padx=6)
        ttk.Button(btns, text='Ouvrir les journaux', command=lambda: os.startfile(str(DATA_DIR))).pack(side='left', padx=6)
        self.status = tk.StringVar(value=f'Configuration : {CONFIG_PATH}')
        ttk.Label(bottom, textvariable=self.status, wraplength=620, foreground=MUTED).pack(fill='x', pady=(8, 0))

        self._refresh()
        self._refresh_models()

    # ------------------------------------------------------------------ onglets

    def _build_model_tab(self, frm: ttk.Frame) -> None:
        cfg = self.cfg
        ttk.Label(frm, text='Modele de decision', font=SECTION_FONT).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        self.provider = tk.StringVar(value=cfg.get('provider') or CFG.provider or 'ollama')
        r = 1
        for label, val in PROVIDERS:
            ttk.Radiobutton(frm, text=label, value=val, variable=self.provider, command=self._refresh).grid(
                row=r, column=0, columnspan=3, sticky='w', padx=20, pady=2)
            r += 1

        r += 1
        ttk.Label(frm, text='Cle API (OpenAI / Anthropic)').grid(row=r, column=0, sticky='w', **PAD)
        self.api_key = tk.StringVar(value=cfg.get('api_key') or '')
        self.key_entry = ttk.Entry(frm, textvariable=self.api_key, width=42, show='•')
        self.key_entry.grid(row=r, column=1, sticky='w', **PAD)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text='afficher', variable=self.show_key,
                         command=lambda: self.key_entry.config(show='' if self.show_key.get() else '•')).grid(row=r, column=2, sticky='w')

        r += 1
        ttk.Label(frm, text='Modele Ollama').grid(row=r, column=0, sticky='w', **PAD)
        self.model = tk.StringVar(value=cfg.get('model') or CFG.model or 'llama3.2:latest')
        self.model_combo = ttk.Combobox(frm, textvariable=self.model, width=34, values=[self.model.get()])
        self.model_combo.grid(row=r, column=1, sticky='w', **PAD)
        ttk.Button(frm, text='Rafraichir', command=self._refresh_models).grid(row=r, column=2, sticky='w')

        r += 1
        ttk.Label(frm, text='Modele OpenAI').grid(row=r, column=0, sticky='w', **PAD)
        self.openai_model = tk.StringVar(value=cfg.get('openai_model') or 'gpt-4o-mini')
        ttk.Entry(frm, textvariable=self.openai_model, width=42).grid(row=r, column=1, sticky='w', **PAD)

        r += 1
        ttk.Label(frm, text='Modele Anthropic').grid(row=r, column=0, sticky='w', **PAD)
        self.anthropic_model = tk.StringVar(value=cfg.get('anthropic_model') or 'claude-haiku-4-5-20251001')
        ttk.Entry(frm, textvariable=self.anthropic_model, width=42).grid(row=r, column=1, sticky='w', **PAD)

        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky='ew', pady=10)
        r += 1
        ttk.Label(frm, text='Dossier du jeu').grid(row=r, column=0, sticky='w', **PAD)
        self.game_dir = tk.StringVar(value=cfg.get('game_dir') or (str(CFG.game_dir) if CFG.game_dir else ''))
        ttk.Entry(frm, textvariable=self.game_dir, width=42).grid(row=r, column=1, columnspan=2, sticky='w', **PAD)

    def _build_behaviors_tab(self, frm: ttk.Frame) -> None:
        ttk.Label(frm, text='Comportements de V', font=SECTION_FONT).grid(row=0, column=0, sticky='w', pady=(0, 8))
        feats = self.cfg.get('features') or {}
        self.features: dict[str, tk.BooleanVar] = {}
        for i, (key, label) in enumerate(FEATURES):
            v = tk.BooleanVar(value=bool(self.cfg.get('focus_tracked', True)) if key == 'focus_tracked' else bool(feats.get(key, True)))
            self.features[key] = v
            ttk.Checkbutton(frm, text=label, variable=v).grid(row=1 + i, column=0, sticky='w', padx=4, pady=3)

    def _build_temperament_tab(self, frm: ttk.Frame) -> None:
        cfg = self.cfg
        ttk.Label(frm, text='Temperament de V', font=SECTION_FONT).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        self.temper: dict[str, tk.StringVar] = {}
        r = 1
        for key, label, opts in TEMPERAMENT:
            ttk.Label(frm, text=label, font=GROUP_FONT).grid(row=r, column=0, columnspan=3, sticky='w', pady=(6, 2))
            r += 1
            v = tk.StringVar(value=cfg.get(key) or {'courage': 'temeraire', 'style': 'melee', 'aggro': 'normal'}[key])
            self.temper[key] = v
            for val, text in opts:
                ttk.Radiobutton(frm, text=text, value=val, variable=v).grid(row=r, column=0, columnspan=3, sticky='w', padx=20, pady=1)
                r += 1
        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=3, sticky='ew', pady=10)
        r += 1
        ttk.Label(frm, text='Duree d une session (minutes)').grid(row=r, column=0, sticky='w', **PAD)
        self.minutes = tk.StringVar(value=str(cfg.get('minutes') or 20))
        ttk.Spinbox(frm, from_=5, to=240, increment=5, textvariable=self.minutes, width=8).grid(row=r, column=1, sticky='w', **PAD)

    def _build_telegram_tab(self, frm: ttk.Frame) -> None:
        tg = self.tg
        ttk.Label(frm, text='Telegram (directives et comptes-rendus a distance)', font=SECTION_FONT).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 4))
        ttk.Label(frm, text='Facultatif -- laisse vide pour ne piloter V que depuis le panneau in-game (CET).',
                  foreground=MUTED).grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 10))

        ttk.Label(frm, text='Jeton du bot (@BotFather)').grid(row=2, column=0, sticky='w', **PAD)
        self.tg_token = tk.StringVar(value=tg.get('token') or '')
        self.tg_token_entry = ttk.Entry(frm, textvariable=self.tg_token, width=42, show='•')
        self.tg_token_entry.grid(row=2, column=1, sticky='w', **PAD)
        self.show_tg_token = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text='afficher', variable=self.show_tg_token,
                         command=lambda: self.tg_token_entry.config(show='' if self.show_tg_token.get() else '•')).grid(row=2, column=2, sticky='w')

        ttk.Label(frm, text='Identifiant de chat (@userinfobot)').grid(row=3, column=0, sticky='w', **PAD)
        self.tg_chat_id = tk.StringVar(value=str(tg.get('chat_id') or ''))
        ttk.Entry(frm, textvariable=self.tg_chat_id, width=42).grid(row=3, column=1, sticky='w', **PAD)

        ttk.Separator(frm).grid(row=4, column=0, columnspan=3, sticky='ew', pady=10)
        ttk.Label(frm, text='Marche a suivre :', font=GROUP_FONT).grid(row=5, column=0, columnspan=3, sticky='w')
        steps = ('1. Dans Telegram, cherche @BotFather et envoie /newbot -- il te donne un jeton.\n'
                 "2. Cherche @userinfobot et envoie-lui n importe quoi -- il te donne ton identifiant de chat.\n"
                 '3. Envoie un premier message a TON bot (pour qu il puisse te repondre).\n'
                 '4. Remplis les deux champs ci-dessus et clique Enregistrer.')
        ttk.Label(frm, text=steps, foreground=MUTED, justify='left').grid(row=6, column=0, columnspan=3, sticky='w', pady=(4, 0))

    # ------------------------------------------------------------------ actions

    def _refresh(self) -> None:
        state = 'normal' if self.provider.get() != 'ollama' else 'disabled'
        self.key_entry.config(state=state)

    def _refresh_models(self) -> None:
        """Interroge Ollama (/api/tags) pour lister les modeles deja installes -- en tache de fond,
        pour ne pas geler la fenetre si Ollama est lent ou injoignable."""
        def work():
            models = llm.list_models()
            def apply():
                if models:
                    self.model_combo['values'] = [m['name'] for m in models]
                    sizes = ', '.join(f"{m['name']} (~{m['size_gb']:.1f} Go VRAM)" for m in models)
                    self.status.set(f'{len(models)} modele(s) Ollama installe(s) -- taille ~= VRAM necessaire : {sizes}')
                else:
                    self.status.set('Ollama injoignable : impossible de lister les modeles installes (le champ reste modifiable a la main).')
            self.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

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

    def collect_telegram(self) -> dict:
        return {'token': self.tg_token.get().strip(), 'chat_id': self.tg_chat_id.get().strip()}

    def on_save(self) -> None:
        save(self.collect())
        save_telegram(self.collect_telegram())
        self.status.set(f'Enregistre dans {CONFIG_PATH} et {remote.TELEGRAM_FILE.name}')

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
