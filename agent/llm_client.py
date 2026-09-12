"""
llm_client.py -- un seul point d appel pour le modele de decision, trois fournisseurs au choix :

  provider = "ollama"     (defaut) modele local, gratuit, rien ne sort du PC  -> config `model`, `ollama_url`
  provider = "openai"     cle API OpenAI (ou compatible : `openai_base_url`)   -> config `openai_model` (gpt-4o-mini)
  provider = "anthropic"  cle API Anthropic (Claude)                           -> config `anthropic_model`

La cle vient de config.json (`api_key`) ou des variables d environnement OPENAI_API_KEY / ANTHROPIC_API_KEY.
Elle n est jamais journalisee. Toutes les reponses sont demandees en JSON ; on renvoie le texte brut,
les appelants en extraient le champ voulu par expression reguliere (robuste aux modeles bavards).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .config import CFG

OPENAI_DEFAULT = 'gpt-4o-mini'
ANTHROPIC_DEFAULT = 'claude-haiku-4-5-20251001'


def provider() -> str:
    return (CFG.provider or 'ollama').lower()


def api_key() -> str | None:
    if CFG.api_key:
        return CFG.api_key
    return os.environ.get('OPENAI_API_KEY' if provider() == 'openai' else 'ANTHROPIC_API_KEY')


def model_name() -> str:
    p = provider()
    if p == 'openai':
        return CFG.openai_model or OPENAI_DEFAULT
    if p == 'anthropic':
        return CFG.anthropic_model or ANTHROPIC_DEFAULT
    return CFG.model


def describe() -> str:
    p = provider()
    if p == 'ollama':
        return f'Ollama local, modele {CFG.model} ({CFG.ollama_url})'
    key = api_key()
    masked = (key[:4] + '...' + key[-4:]) if key and len(key) > 12 else ('absente' if not key else 'presente')
    return f'{p} (cle {masked}), modele {model_name()}'


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode('utf-8'), {'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 60, timeout: float = 6.0,
         keep_alive: str = '5m') -> str:
    """messages = [{'role': 'system'|'user'|'assistant', 'content': str}, ...] -> texte de la reponse.
    Leve une exception si le fournisseur ne repond pas (les appelants retombent sur leurs regles)."""
    p = provider()
    if p == 'openai':
        key = api_key()
        if not key:
            raise RuntimeError('cle OpenAI absente (config.json "api_key" ou variable OPENAI_API_KEY)')
        base = (CFG.openai_base_url or 'https://api.openai.com/v1').rstrip('/')
        body = {'model': model_name(), 'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens,
                'response_format': {'type': 'json_object'}}
        out = _post(base + '/chat/completions', body, {'Authorization': f'Bearer {key}'}, timeout)
        return out['choices'][0]['message']['content']
    if p == 'anthropic':
        key = api_key()
        if not key:
            raise RuntimeError('cle Anthropic absente (config.json "api_key" ou variable ANTHROPIC_API_KEY)')
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        convo = [m for m in messages if m['role'] != 'system']
        if not convo:
            convo = [{'role': 'user', 'content': 'Reponds en JSON.'}]
        body = {'model': model_name(), 'max_tokens': max_tokens, 'temperature': temperature, 'messages': convo}
        if system:
            body['system'] = system + '\nReponds uniquement par un objet JSON, sans texte autour.'
        out = _post('https://api.anthropic.com/v1/messages', body,
                    {'x-api-key': key, 'anthropic-version': '2023-06-01'}, timeout)
        return ''.join(c.get('text', '') for c in out.get('content', []) if c.get('type') == 'text')
    # ollama (defaut)
    body = {'model': CFG.model, 'stream': False, 'keep_alive': keep_alive, 'format': 'json',
            'options': {'temperature': temperature, 'num_predict': max_tokens}, 'messages': messages}
    out = _post(CFG.ollama_url + '/api/chat', body, {}, timeout)
    return out['message']['content']


def check(timeout: float = 15.0) -> tuple[bool, str]:
    """Un appel minimal pour verifier le fournisseur choisi. (ok, message)"""
    try:
        txt = chat([{'role': 'user', 'content': 'Reponds UNIQUEMENT avec le JSON {"ok": 1}.'}], temperature=0.0,
                   max_tokens=10, timeout=timeout)
        return ('"ok"' in txt or 'ok' in txt.lower()), f'{describe()} : repond'
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'ignore')[:160]
        except Exception:
            pass
        return False, f'{describe()} : HTTP {e.code} {detail}'
    except Exception as e:
        return False, f'{describe()} : {str(e)[:120]}'
