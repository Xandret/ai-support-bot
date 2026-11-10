"""Запуск: python -m bot"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from .assistant import Assistant
from .config import ROOT, Config
from .handlers import router, service_router
from .kb import KnowledgeBase
from .llm import make_llm


async def main() -> None:
    cfg = Config.load()
    kb = KnowledgeBase.load(cfg.kb_dir)
    llm = make_llm(cfg)
    try:
        await llm.check()
    except Exception as e:
        await llm.close()
        print(f"Нейросеть не готова: {e}", file=sys.stderr)
        raise SystemExit(2)  # run.cmd не перезапускает, а ждёт исправления .env
    assistant = Assistant(llm, kb, cfg.business_name, cfg.history_turns, cfg.rate_limit_per_hour)
    logging.info("База знаний: %d разделов, %d символов; нейросеть: %s",
                 len(kb.sections), kb.total_chars, cfg.llm_provider)

    bot = Bot(cfg.bot_token)
    dp = Dispatcher(assistant=assistant, cfg=cfg)
    dp.include_routers(service_router, router)
    try:
        await dp.start_polling(bot)
    finally:
        await llm.close()
        await bot.session.close()


if __name__ == "__main__":
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(ROOT / "logs" / "bot.log", encoding="utf-8")],
    )
    asyncio.run(main())
