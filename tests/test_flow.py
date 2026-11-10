"""Сквозной прогон Telegram-сценариев без сети: апдейты подаются в диспетчер,
исходящие запросы к Telegram перехватываются подменённой сессией."""
from __future__ import annotations

import asyncio
import datetime as dt
import unittest
from dataclasses import replace
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendChatAction, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.assistant import HANDOFF_MARK, Assistant
from bot.config import Config
from bot.handlers import router, service_router
from bot.kb import KnowledgeBase
from tests.test_core import FakeLLM

ROOT = Path(__file__).resolve().parent.parent
CLIENT = User(id=111, is_bot=False, first_name="Тест", username="client")
CHAT = Chat(id=111, type="private")
MANAGER_ID = 999


class CaptureSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list = []
        self._next_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            return Message(message_id=self._next_id, date=dt.datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        if isinstance(method, (AnswerCallbackQuery, SendChatAction)):
            return True
        raise AssertionError(f"неожиданный запрос {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


def make_cfg() -> Config:
    return Config(bot_token="123:TEST", llm_provider="gigachat", gigachat_credentials="x",
                  gigachat_scope="GIGACHAT_API_PERS", gigachat_model="GigaChat",
                  gigachat_verify_ssl=True, gigachat_ca_bundle=None, openai_base_url="",
                  openai_api_key="", openai_model="", manager_chat_id=MANAGER_ID,
                  kb_dir=ROOT / "kb", rate_limit_per_hour=30, history_turns=6,
                  kwork_url="", business_name="ТехноДок")


class Flow:
    def __init__(self, llm_answer="Замена экрана — от 2 500 ₽ за работу.", cfg: Config | None = None):
        self.session = CaptureSession()
        self.bot = Bot("123:TEST", session=self.session)
        self.llm = FakeLLM(llm_answer)
        cfg = cfg or make_cfg()
        assistant = Assistant(self.llm, KnowledgeBase.load(cfg.kb_dir), cfg.business_name)
        self.dp = Dispatcher(assistant=assistant, cfg=cfg)
        for r in (service_router, router):
            r._parent_router = None  # роутеры модульные: отвязываем от диспетчера прошлого теста
        self.dp.include_routers(service_router, router)
        self._uid = 0

    def _next(self):
        self._uid += 1
        return self._uid

    async def text(self, text: str, chat: Chat = CHAT):
        msg = Message(message_id=self._next(), date=dt.datetime.now(), chat=chat, from_user=CLIENT, text=text)
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=msg))

    async def press(self, data: str):
        anchor = Message(message_id=self._next(), date=dt.datetime.now(), chat=CHAT, text="…")
        cq = CallbackQuery(id=str(self._next()), from_user=CLIENT, chat_instance="ci", message=anchor, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), callback_query=cq))

    def messages(self, chat_id=CHAT.id) -> list[SendMessage]:
        return [m for m in self.session.sent if isinstance(m, SendMessage) and m.chat_id == chat_id]

    def last(self, chat_id=CHAT.id) -> SendMessage:
        return self.messages(chat_id)[-1]


def buttons(msg: SendMessage) -> list[str]:
    if msg.reply_markup is None:
        return []
    return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]


class FlowTests(unittest.TestCase):
    def test_start_shows_menu_and_demo_note(self):
        async def go():
            f = Flow()
            await f.text("/start")
            m = f.last()
            self.assertIn("демо-бот", m.text)
            self.assertEqual(buttons(m), ["price", "book", "manager", "about"])
        asyncio.run(go())

    def test_booking_flow_reaches_manager(self):
        async def go():
            f = Flow()
            await f.press("book")
            self.assertIn("устройство", f.last().text)
            await f.text("iPhone 13")
            await f.text("разбит экран")
            await f.text("завтра после 18")
            self.assertIn("iPhone 13", f.last().text)
            self.assertEqual(buttons(f.last()), ["book_send", "cancel"])
            await f.press("book_send")
            ticket = f.last(MANAGER_ID).text
            for part in ("@client", "iPhone 13", "разбит экран", "завтра после 18"):
                self.assertIn(part, ticket)
            self.assertIn("Заявка принята", f.last().text)
            self.assertEqual(f.llm.calls, [], "запись идёт без нейросети")
        asyncio.run(go())

    def test_cancel_in_the_middle_of_booking(self):
        async def go():
            f = Flow()
            await f.press("book")
            await f.text("ноутбук")
            await f.text("/cancel")
            self.assertIn("отменена", f.last().text)
            await f.text("Сколько стоит экран?")  # после отмены снова работает нейросеть
            self.assertEqual(len(f.llm.calls), 1)
        asyncio.run(go())

    def test_free_question_goes_to_llm(self):
        async def go():
            f = Flow()
            await f.text("Сколько стоит заменить экран?")
            self.assertEqual(f.last().text, "Замена экрана — от 2 500 ₽ за работу.")
            self.assertEqual(buttons(f.last()), [])
            self.assertTrue(any(isinstance(m, SendChatAction) for m in f.session.sent))
        asyncio.run(go())

    def test_handoff_answer_offers_manager_button_and_notifies(self):
        async def go():
            f = Flow(llm_answer=f"Этого в базе нет, передаю менеджеру. {HANDOFF_MARK}")
            await f.text("Где мой телефон, сдавал неделю назад?")
            self.assertEqual(buttons(f.last()), ["manager"])
            self.assertNotIn(HANDOFF_MARK, f.last().text)
            await f.press("manager")
            note = f.last(MANAGER_ID).text
            self.assertIn("просит менеджера", note)
            self.assertIn("Где мой телефон", note)
        asyncio.run(go())

    def test_price_button_answers_from_kb_without_llm(self):
        async def go():
            f = Flow()
            await f.press("price")
            self.assertIn("Замена экрана смартфона", f.last().text)
            self.assertEqual(f.llm.calls, [])
        asyncio.run(go())

    def test_llm_failure_is_handled(self):
        async def go():
            f = Flow()

            async def boom(messages):
                raise RuntimeError("нет сети")
            f.llm.ask = boom
            await f.text("Привет")
            self.assertIn("нейросеть недоступна", f.last().text)
            self.assertEqual(buttons(f.last()), ["manager"])
        asyncio.run(go())

    def test_without_manager_chat_nothing_is_sent_outside(self):
        async def go():
            f = Flow(cfg=replace(make_cfg(), manager_chat_id=None))
            await f.press("manager")
            self.assertEqual(f.messages(MANAGER_ID), [])
            self.assertIn("Позвал менеджера", f.last().text)
        asyncio.run(go())


class GroupAndDemoTests(unittest.TestCase):
    GROUP = Chat(id=-100500, type="supergroup", title="Заявки")

    def test_bot_is_silent_in_manager_group(self):
        async def go():
            f = Flow()
            await f.text("Сколько стоит экран?", chat=self.GROUP)
            await f.text("/start", chat=self.GROUP)
            self.assertEqual(f.messages(self.GROUP.id), [])
            self.assertEqual(f.llm.calls, [], "болтовня в группе не тратит токены")
        asyncio.run(go())

    def test_myid_works_in_group(self):
        async def go():
            f = Flow()
            await f.text("/myid", chat=self.GROUP)
            m = f.last(self.GROUP.id)
            self.assertIn("этой группы", m.text)
            self.assertIn("-100500", m.text)
        asyncio.run(go())

    def test_demo_shows_client_what_manager_got(self):
        async def go():
            f = Flow(cfg=replace(make_cfg(), demo_show_manager_view=True))
            await f.press("book")
            for t in ("PS5", "греется", "сегодня вечером"):
                await f.text(t)
            await f.press("book_send")
            ticket = f.last(MANAGER_ID).text
            copy = f.last().text
            self.assertIn("пришло менеджеру", copy)
            self.assertTrue(copy.endswith(ticket))
        asyncio.run(go())

    def test_no_duplicate_when_manager_is_the_client(self):
        async def go():
            f = Flow(cfg=replace(make_cfg(), demo_show_manager_view=True, manager_chat_id=CHAT.id))
            await f.press("manager")
            self.assertFalse(any("пришло менеджеру" in m.text for m in f.messages()))
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
