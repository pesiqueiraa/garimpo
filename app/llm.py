"""Porta unica para a API da OpenAI (Chat Completions).

Toda chamada LLM do projeto passa por ask_json: JSON garantido,
retry de resposta malformada e contagem de tokens para medir custo.
"""
import json
import os

import requests

MODEL = "gpt-5-mini"
API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 90
# USD por 1M tokens (gpt-5-mini) — para estimativa de custo, nao cobranca
PRICE_IN_PER_M = 0.25
PRICE_OUT_PER_M = 2.00


class LLMError(Exception):
    """Chave ausente ou resposta invalida apos retry."""


def cost_usd(tokens_in: int, tokens_out: int) -> float:
    return tokens_in / 1e6 * PRICE_IN_PER_M + tokens_out / 1e6 * PRICE_OUT_PER_M


def ask_json(prompt: str, max_tokens: int = 500,
             reasoning_effort: str = "minimal") -> tuple[dict, int, int]:
    """Pergunta ao modelo e retorna (dados, tokens_in, tokens_out).

    1 retry automatico se a resposta nao for JSON valido; tokens dos
    dois attempts sao somados. Erros HTTP propagam (requests.RequestException).
    """
    # strip: secret colado com \n/espaco quebraria o header Authorization
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise LLMError("OPENAI_API_KEY ausente")

    messages = [{"role": "user", "content": prompt}]
    total_in = total_out = 0
    for attempt in (1, 2):
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "content-type": "application/json"},
            json={
                "model": MODEL,
                "reasoning_effort": reasoning_effort,
                "response_format": {"type": "json_object"},
                "max_completion_tokens": max_tokens,
                "messages": messages,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
        total_in += usage.get("prompt_tokens", 0)
        total_out += usage.get("completion_tokens", 0)
        try:
            data = json.loads(text.replace("```json", "").replace("```", "").strip())
            return data, total_in, total_out
        except json.JSONDecodeError:
            if attempt == 2:
                raise LLMError(f"JSON invalido apos retry: {text[:200]!r}")
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": "A resposta anterior nao era JSON valido. "
                                        "Responda SOMENTE o objeto JSON, sem texto extra."})
