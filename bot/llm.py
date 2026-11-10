"""Нейросети за одним интерфейсом: ask(messages) -> str.
messages — список {"role": "system"|"user"|"assistant", "content": str}."""
from __future__ import annotations

from typing import Protocol

import aiohttp


class LLM(Protocol):
    async def ask(self, messages: list[dict]) -> str: ...
    async def check(self) -> None: ...
    async def close(self) -> None: ...


class GigaChatLLM:
    def __init__(self, credentials: str, scope: str, model: str,
                 verify_ssl: bool = True, ca_bundle: str | None = None):
        from gigachat import GigaChat  # импорт здесь: тестам SDK не нужен

        self._model = model
        self._client = GigaChat(
            credentials=credentials, scope=scope, model=model,
            verify_ssl_certs=verify_ssl, ca_bundle_file=ca_bundle,
            timeout=60, max_retries=2,
        )

    async def check(self) -> None:
        """При запуске: ключ рабочий и модель существует — иначе понятная ошибка сразу, а не на первом вопросе."""
        models = [m.id_ for m in (await self._client.aget_models()).data]
        if self._model not in models:
            chat_models = [m for m in models if "embed" not in m.lower()]
            raise ValueError(f"модели {self._model!r} нет. Доступны: {', '.join(chat_models)}. "
                             f"Поправьте GIGACHAT_MODEL в .env")

    async def ask(self, messages: list[dict]) -> str:
        from gigachat.models import Chat, Messages

        chat = Chat(
            messages=[Messages(role=m["role"], content=m["content"]) for m in messages],
            temperature=0.1, max_tokens=600,
        )
        response = await self._client.achat(chat)
        return response.choices[0].message.content or ""

    async def close(self) -> None:
        await self._client.aclose()


class OpenAICompatLLM:
    """Любой OpenAI-совместимый /chat/completions (YandexGPT, локальные модели, прокси)."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self._url = f"{base_url}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._session: aiohttp.ClientSession | None = None

    async def check(self) -> None:
        pass  # у OpenAI-совместимых API список моделей устроен по-разному; ошибка будет видна в логе

    async def ask(self, messages: list[dict]) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        payload = {"model": self._model, "messages": messages, "temperature": 0.1, "max_tokens": 600}
        async with self._session.post(self._url, json=payload, headers=self._headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data["choices"][0]["message"]["content"] or ""

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


def make_llm(cfg) -> LLM:
    if cfg.llm_provider == "gigachat":
        return GigaChatLLM(cfg.gigachat_credentials, cfg.gigachat_scope, cfg.gigachat_model,
                           cfg.gigachat_verify_ssl, cfg.gigachat_ca_bundle)
    return OpenAICompatLLM(cfg.openai_base_url, cfg.openai_api_key, cfg.openai_model)
