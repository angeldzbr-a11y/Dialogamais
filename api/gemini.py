"""Cliente do Google Gemini (API REST, método generateContent).

A chave vai no cabeçalho x-goog-api-key (nunca na URL, para não aparecer em logs) e existe só no servidor.
Nada que identifique o estudante (nome, e-mail, sala) é enviado: só os textos das situações e das respostas.
"""
import asyncio
import json
import logging

import httpx

log = logging.getLogger("dialoga.gemini")
BASE = "https://generativelanguage.googleapis.com/v1beta"


class IAErro(Exception):
    """Falha ao gerar o feedback. A mensagem é segura para registrar em log (sem conteúdo do estudante)."""


def novo_cliente(timeout):  # os testes trocam esta função por um cliente simulado
    return httpx.AsyncClient(timeout=timeout)


def _extrair_texto(dados):
    bloqueio = (dados.get("promptFeedback") or {}).get("blockReason")
    if bloqueio:
        raise IAErro(f"conteúdo bloqueado pelo Gemini ({bloqueio})")
    cands = dados.get("candidates") or []
    if not cands:
        raise IAErro("resposta sem candidatos")
    c = cands[0]
    if c.get("finishReason") not in (None, "STOP"):
        raise IAErro(f"geração interrompida ({c.get('finishReason')})")
    partes = (c.get("content") or {}).get("parts") or []
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    if not texto.strip():
        raise IAErro("resposta vazia")
    return texto


async def gerar_json(sistema, usuario, *, chave, modelo, reserva="", timeout=40.0):
    """Envia o pedido e devolve o texto (JSON) gerado. Tenta de novo 1 vez em erro temporário e,
    se o modelo principal não existir (404), usa o modelo de reserva."""
    if not chave:
        raise IAErro("GEMINI_API_KEY não configurada")
    corpo = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": usuario}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4096, "responseMimeType": "application/json"},
    }
    cab = {"x-goog-api-key": chave, "content-type": "application/json"}
    modelos = [m for m in (modelo, reserva) if m]
    async with novo_cliente(timeout) as http:
        for i, m in enumerate(modelos):
            for tentativa in (1, 2):
                try:
                    r = await http.post(f"{BASE}/models/{m}:generateContent", headers=cab, content=json.dumps(corpo))
                except httpx.HTTPError as e:
                    if tentativa == 1:
                        await asyncio.sleep(1.2)
                        continue
                    raise IAErro(f"falha de rede ({type(e).__name__})") from None
                if r.status_code == 404 and i + 1 < len(modelos):
                    log.warning("modelo %s não encontrado; usando %s", m, modelos[i + 1])
                    break  # vai para o modelo de reserva
                if r.status_code in (429, 500, 502, 503, 504) and tentativa == 1:
                    await asyncio.sleep(1.5)
                    continue
                if r.status_code != 200:
                    raise IAErro(f"Gemini respondeu HTTP {r.status_code} para o modelo {m}")
                try:
                    return _extrair_texto(r.json())
                except ValueError:
                    raise IAErro("resposta do Gemini não é JSON") from None
    raise IAErro("nenhum modelo disponível")
