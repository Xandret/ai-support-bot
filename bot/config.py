"""Настройки из переменных окружения (.env). Секреты живут только там."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "да")


def _int(value: str | None, default: int | None) -> int | None:
    if value is None or value.strip() == "":
        return default
    return int(value.strip())


def _resolve(path: str | None) -> str | None:
    """Относительный путь к сертификату считаем от папки проекта, а не от текущей."""
    if path and not Path(path).is_absolute() and (ROOT / path).exists():
        return str(ROOT / path)
    return path


@dataclass(frozen=True)
class Config:
    bot_token: str
    llm_provider: str              # "gigachat" | "openai"
    gigachat_credentials: str
    gigachat_scope: str
    gigachat_model: str
    gigachat_verify_ssl: bool
    gigachat_ca_bundle: str | None
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    manager_chat_id: int | None    # куда слать заявки; пусто — только в лог
    kb_dir: Path
    rate_limit_per_hour: int
    history_turns: int
    kwork_url: str
    business_name: str
    demo_show_manager_view: bool = False  # демо: показывать клиенту копию уведомления менеджеру

    @staticmethod
    def load() -> "Config":
        load_dotenv(ROOT / ".env")
        cfg = Config(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            llm_provider=os.getenv("LLM_PROVIDER", "gigachat").strip().lower(),
            gigachat_credentials=os.getenv("GIGACHAT_CREDENTIALS", "").strip(),
            gigachat_scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip(),
            gigachat_model=os.getenv("GIGACHAT_MODEL", "GigaChat-2").strip(),
            gigachat_verify_ssl=_bool(os.getenv("GIGACHAT_VERIFY_SSL"), True),
            gigachat_ca_bundle=_resolve((os.getenv("GIGACHAT_CA_BUNDLE") or "").strip() or None),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/"),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "").strip(),
            manager_chat_id=_int(os.getenv("MANAGER_CHAT_ID"), None),
            kb_dir=ROOT / os.getenv("KB_DIR", "kb"),
            rate_limit_per_hour=_int(os.getenv("RATE_LIMIT_PER_HOUR"), 30) or 30,
            history_turns=_int(os.getenv("HISTORY_TURNS"), 6) or 6,
            kwork_url=os.getenv("KWORK_URL", "").strip(),
            business_name=os.getenv("BUSINESS_NAME", "ТехноДок").strip(),
            demo_show_manager_view=_bool(os.getenv("DEMO_SHOW_MANAGER_VIEW"), False),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        problems = []
        if not self.bot_token:
            problems.append("BOT_TOKEN пуст: получите токен у @BotFather")
        if self.llm_provider == "gigachat":
            if not self.gigachat_credentials:
                problems.append("GIGACHAT_CREDENTIALS пуст: ключ авторизации из личного кабинета GigaChat API")
        elif self.llm_provider == "openai":
            if not (self.openai_base_url and self.openai_api_key and self.openai_model):
                problems.append("для LLM_PROVIDER=openai нужны OPENAI_BASE_URL, OPENAI_API_KEY и OPENAI_MODEL")
        else:
            problems.append(f"неизвестный LLM_PROVIDER={self.llm_provider!r} (gigachat или openai)")
        if not self.kb_dir.is_dir():
            problems.append(f"нет папки базы знаний {self.kb_dir}")
        if self.gigachat_ca_bundle and not (ROOT / self.gigachat_ca_bundle).exists() \
                and not Path(self.gigachat_ca_bundle).exists():
            problems.append(f"файл сертификата {self.gigachat_ca_bundle} не найден")
        if problems:
            print("Настройка .env не завершена:\n  - " + "\n  - ".join(problems), file=sys.stderr)
            raise SystemExit(2)  # код 2: run.cmd не перезапускает, а ждёт исправления .env
