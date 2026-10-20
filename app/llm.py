"""Porta única para a API da OpenAI.

Toda chamada de LLM do projeto passa por aqui: JSON garantido (com JSON Schema
quando o chamador passa um), retry de resposta malformada, cache por hash do
prompt, contagem de tokens e teto de custo por execução. Embeddings também.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import requests

MODEL = "gpt-5-mini"
EMBED_MODEL = "text-embedding-3-small"
API_URL = "https://api.openai.com/v1/chat/completions"
EMBED_URL = "https://api.openai.com/v1/embeddings"
TIMEOUT = 90
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# USD por 1M tokens — estimativa de custo, não cobrança. Atualize se o preço mudar.
PRICE_IN_PER_M = 0.25
PRICE_OUT_PER_M = 2.00
PRICE_EMBED_PER_M = 0.02


class LLMError(Exception):
    """Chave ausente ou resposta inválida após retry."""


class BudgetExceeded(Exception):
    """O teto de custo da execução foi atingido."""


_state = {"conn": None, "budget": None, "in": 0, "out": 0, "embed": 0, "calls": 0, "cached": 0}


def configure(conn=None, budget_usd: float | None = None) -> None:
    """Liga o cache (precisa de conexão) e define o teto da execução. Zera o contador."""
    _state.update(conn=conn, budget=budget_usd, **{"in": 0, "out": 0, "embed": 0,
                                                     "calls": 0, "cached": 0})


def set_budget(budget_usd: float | None) -> None:
    _state["budget"] = budget_usd


def cost_usd(tokens_in: int, tokens_out: int) -> float:
    return tokens_in / 1e6 * PRICE_IN_PER_M + tokens_out / 1e6 * PRICE_OUT_PER_M


def usage() -> dict:
    usd = cost_usd(_state["in"], _state["out"]) + _state["embed"] / 1e6 * PRICE_EMBED_PER_M
    return {"in": _state["in"], "out": _state["out"], "embed_tokens": _state["embed"],
            "usd": round(usd, 5), "calls": _state["calls"], "cached": _state["cached"]}


def available() -> bool:
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())


def _api_key() -> str:
    # strip: secret colado com \n/espaço quebraria o header Authorization
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise LLMError("OPENAI_API_KEY ausente")
    return key


def _check_budget() -> None:
    budget = _state["budget"]
    if budget is not None and usage()["usd"] >= budget:
        raise BudgetExceeded(f"teto da execução atingido (US${budget:.2f})")


def load_prompt(name: str) -> str:
    """Lê app/prompts/<name>.txt. O nome do arquivo é a versão do prompt."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _cache_key(prompt: str, max_tokens: int, effort: str, schema: dict | None) -> str:
    raw = json.dumps([MODEL, effort, max_tokens, schema, prompt], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _post(url: str, body: dict) -> dict:
    """POST com 1 retry para 429/5xx/timeout. Outros erros HTTP propagam."""
    for attempt in (1, 2):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {_api_key()}",
                                            "content-type": "application/json"},
                              json=body, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(3)
                continue
            r.raise_for_status()
            return r.json()
        except requests.Timeout:
            if attempt == 2:
                raise
            time.sleep(3)
    raise LLMError("falha após retry")


def _parse(text: str):
    return json.loads((text or "").replace("```json", "").replace("```", "").strip())


def ask_json(prompt: str, max_tokens: int = 500, reasoning_effort: str = "minimal",
             schema: dict | None = None, cache: bool = True) -> tuple[dict, int, int]:
    """Pergunta ao modelo e retorna (dados, tokens_in, tokens_out).

    Com `schema` ({"name": ..., "schema": {...}}), usa Structured Outputs; sem,
    pede um objeto JSON. Resposta em cache devolve tokens 0 (não custou nada).
    1 retry automático se a resposta não for JSON válido.
    """
    key = _cache_key(prompt, max_tokens, reasoning_effort, schema)
    conn = _state["conn"]
    if cache and conn is not None:
        from app import db
        hit = db.cache_get(conn, key)
        if hit is not None:
            _state["cached"] += 1
            return hit[0], 0, 0
    _check_budget()
    _api_key()

    if schema:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": schema["name"], "strict": True,
                                           "schema": schema["schema"]}}
    else:
        response_format = {"type": "json_object"}

    messages = [{"role": "user", "content": prompt}]
    total_in = total_out = 0
    for attempt in (1, 2):
        body = {"model": MODEL, "reasoning_effort": reasoning_effort,
                "response_format": response_format, "max_completion_tokens": max_tokens,
                "messages": messages}
        try:
            payload = _post(API_URL, body)
        except requests.HTTPError as e:
            # schema recusado pela API: tenta uma vez em modo JSON simples
            if schema and e.response is not None and e.response.status_code == 400 \
                    and response_format["type"] == "json_schema":
                print(f"[llm] schema '{schema['name']}' recusado, usando json_object")
                response_format = {"type": "json_object"}
                continue
            raise
        text = payload["choices"][0]["message"].get("content") or ""
        used = payload.get("usage", {})
        total_in += used.get("prompt_tokens", 0)
        total_out += used.get("completion_tokens", 0)
        _state["in"] += used.get("prompt_tokens", 0)
        _state["out"] += used.get("completion_tokens", 0)
        _state["calls"] += 1
        try:
            data = _parse(text)
            if not isinstance(data, dict):
                raise ValueError("JSON não é objeto")
        except ValueError:
            if attempt == 2:
                raise LLMError(f"JSON inválido após retry: {text[:200]!r}")
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "A resposta anterior não era um objeto JSON válido. "
                                            "Responda SOMENTE o objeto JSON, sem texto extra."}]
            continue
        if cache and conn is not None:
            from app import db
            db.cache_put(conn, key, data, total_in, total_out)
        return data, total_in, total_out
    raise LLMError("sem resposta válida")


def embed(texts: list[str], dims: int = 256) -> list[list[float]]:
    """Embeddings em lote (até 100 por chamada). Levanta LLMError/BudgetExceeded/HTTPError."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 100):
        _check_budget()
        batch = [t[:2000] or " " for t in texts[start:start + 100]]
        payload = _post(EMBED_URL, {"model": EMBED_MODEL, "input": batch, "dimensions": dims})
        _state["embed"] += payload.get("usage", {}).get("total_tokens", 0)
        data = sorted(payload["data"], key=lambda d: d["index"])
        vectors += [d["embedding"] for d in data]
    return vectors
