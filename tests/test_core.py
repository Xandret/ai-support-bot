"""Офлайн-тесты логики бота: без Telegram и без нейросети.
Запуск: .venv\\Scripts\\python -m unittest -v"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from bot.assistant import (BOOK_MARK, HANDOFF_MARK, SAFE_PRICE_ANSWER, Assistant, History,
                           RateLimiter, clean_answer, unknown_prices)
from bot.kb import KnowledgeBase, Section, split_sections

KB_DIR = Path(__file__).resolve().parent.parent / "kb"


class FakeLLM:
    """Записывает, что ему прислали, и отвечает заготовками по очереди (последняя повторяется)."""

    def __init__(self, *answers: str):
        self.answers = list(answers) or ["Замена экрана — от 2 500 ₽."]
        self.calls: list[list[dict]] = []

    async def ask(self, messages):
        self.calls.append(messages)
        return self.answers[min(len(self.calls), len(self.answers)) - 1]

    async def check(self):
        pass

    async def close(self):
        pass


def run(coro):
    return asyncio.run(coro)


class KnowledgeBaseTests(unittest.TestCase):
    def test_real_kb_loads_and_has_price_section(self):
        kb = KnowledgeBase.load(KB_DIR)
        self.assertGreaterEqual(len(kb.sections), 8)
        self.assertIsNotNone(kb.find("цен"))
        self.assertIn("2 500", kb.find("цен").text)

    def test_split_sections_uses_h2_and_skips_h1(self):
        md = "# Файл\n\n## Первый\nтекст 1\n\n## Второй\nтекст 2\n"
        secs = split_sections(md, "src")
        self.assertEqual([s.title for s in secs], ["Первый", "Второй"])
        self.assertNotIn("Файл", secs[0].text)

    def test_small_kb_goes_whole(self):
        kb = KnowledgeBase.load(KB_DIR, max_chars=100_000)
        ctx = kb.context_for("что угодно")
        for s in kb.sections:
            self.assertIn(s.title, ctx)

    def test_big_kb_picks_relevant_sections(self):
        sections = [Section("a", f"Раздел {i}", "про погоду и котиков " * 20) for i in range(10)]
        sections.append(Section("b", "Гарантия", "Гарантия на работы 90 дней, на детали до 6 месяцев."))
        kb = KnowledgeBase(sections, max_chars=600)
        ctx = kb.context_for("Какая у вас гарантия на ремонт?")
        self.assertIn("90 дней", ctx)
        self.assertLessEqual(len(ctx), 600)


class AnswerCleaningTests(unittest.TestCase):
    def test_handoff_mark_detected_and_removed(self):
        text, handoff, book = clean_answer(f"Передаю вопрос менеджеру. {HANDOFF_MARK}")
        self.assertTrue(handoff)
        self.assertFalse(book)
        self.assertNotIn(HANDOFF_MARK, text)

    def test_book_mark_and_fake_buttons(self):
        text, handoff, book = clean_answer(f"Приходите на диагностику. [ЗАПИСАТЬСЯ НА ДИАГНОСТИКУ] {BOOK_MARK}")
        self.assertTrue(book)
        self.assertFalse(handoff)
        self.assertEqual(text, "Приходите на диагностику.")

    def test_markdown_stripped(self):
        text, handoff, _ = clean_answer("## Цены\n**Экран** — от 2 500 ₽")
        self.assertFalse(handoff)
        self.assertEqual(text, "Цены\nЭкран — от 2 500 ₽")


class PriceGuardTests(unittest.TestCase):
    KB = ("- Замена экрана смартфона: от 2 500 ₽ за работу.\n"
          "- Диагностика: без ремонта — 500 ₽.\n"
          "- Чистка ноутбука: 2 000–3 500 ₽.")

    def test_known_prices_pass_with_any_spacing(self):
        answer = "Замена экрана от 2500 ₽. Диагностика 500 руб. Чистка ноутбука до 3 500 ₽."
        self.assertEqual(unknown_prices(answer, self.KB), [])

    def test_invented_price_caught(self):
        self.assertEqual(unknown_prices("Экран на iPhone 13 — от 4 200 ₽", self.KB), ["4 200 ₽"])

    def test_real_price_of_another_service_caught(self):
        # 3 500 в базе есть, но это чистка ноутбука, а не экран — живой случай с GigaChat
        self.assertEqual(unknown_prices("Замена экрана на iPhone 13 обойдётся от 3 500 ₽", self.KB),
                         ["3 500 ₽"])

    def test_service_can_come_from_question(self):
        self.assertEqual(unknown_prices("Это стоит от 2 500 ₽.", self.KB, "Сколько замена экрана?"), [])

    def test_numbers_without_currency_ignored(self):
        self.assertEqual(unknown_prices("iPhone 13, гарантия 90 дней", self.KB), [])


class LimiterAndHistoryTests(unittest.TestCase):
    def test_rate_limit_per_hour_and_window_slides(self):
        now = [0.0]
        lim = RateLimiter(3, clock=lambda: now[0])
        self.assertEqual([lim.allow(1) for _ in range(4)], [True, True, True, False])
        self.assertTrue(lim.allow(2), "лимит у каждого пользователя свой")
        now[0] = 3600.0
        self.assertTrue(lim.allow(1), "через час окно освобождается")

    def test_history_keeps_last_turns(self):
        h = History(turns=2)
        for i in range(5):
            h.add(1, "user", f"q{i}")
            h.add(1, "assistant", f"a{i}")
        self.assertEqual([m["content"] for m in h.get(1)], ["q3", "a3", "q4", "a4"])
        h.reset(1)
        self.assertEqual(h.get(1), [])


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase.load(KB_DIR)

    def test_prompt_contains_rules_kb_and_history(self):
        llm = FakeLLM()
        a = Assistant(llm, self.kb, "ТехноДок")
        run(a.ask(7, "Сколько стоит экран?"))
        run(a.ask(7, "А гарантия?"))
        msgs = llm.calls[-1]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Никогда не называй сумму, которой нет в базе", msgs[0]["content"])
        self.assertIn("Гарантия", msgs[0]["content"])
        self.assertEqual([m["content"] for m in msgs[1:]],
                         ["Сколько стоит экран?", "Замена экрана — от 2 500 ₽.", "А гарантия?"])

    def test_handoff_flag_passes_through(self):
        a = Assistant(FakeLLM(f"Такого в базе нет, передаю менеджеру. {HANDOFF_MARK}"), self.kb, "ТехноДок")
        reply = run(a.ask(1, "Где мой телефон?"))
        self.assertTrue(reply.handoff)
        self.assertNotIn(HANDOFF_MARK, reply.text)

    def test_invented_price_is_retried_then_fixed(self):
        llm = FakeLLM("Экран iPhone 13 — от 3 500 ₽.", f"Экран — от 2 500 ₽ за работу. {BOOK_MARK}")
        reply = run(Assistant(llm, self.kb, "ТехноДок").ask(1, "Сколько экран на айфон 13?"))
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("3 500 ₽", llm.calls[1][-1]["content"], "замечание называет выдуманную сумму")
        self.assertEqual(reply.text, "Экран — от 2 500 ₽ за работу.")
        self.assertTrue(reply.book and reply.price_fixed)

    def test_invented_price_twice_gives_safe_answer(self):
        llm = FakeLLM("От 3 500 ₽.", "Ладно, от 3 700 ₽.")
        a = Assistant(llm, self.kb, "ТехноДок")
        reply = run(a.ask(1, "Сколько экран?"))
        self.assertEqual(reply.text, SAFE_PRICE_ANSWER)
        self.assertTrue(reply.book)
        self.assertNotIn("3 700", str(a.history.get(1)), "выдуманная цена не попадает в историю")

    def test_repair_status_never_goes_to_llm(self):
        llm = FakeLLM("Ваш телефон пока не найден.")  # так GigaChat ответил вживую — выдумка
        a = Assistant(llm, self.kb, "ТехноДок")
        for q in ("Где мой телефон, сдавал неделю назад", "Какой статус ремонта?",
                  "Готов ли мой ноутбук?", "Когда можно забрать готовый?"):
            reply = run(a.ask(1, q))
            self.assertTrue(reply.handoff, q)
            self.assertIn("Статус ремонта я не вижу", reply.text)
        self.assertEqual(llm.calls, [])

    def test_ordinary_questions_still_go_to_llm(self):
        llm = FakeLLM()
        a = Assistant(llm, self.kb, "ТехноДок")
        for q in ("Сколько стоит экран?", "Сколько ждать замену аккумулятора?", "Где вы находитесь?"):
            run(a.ask(1, q))
        self.assertEqual(len(llm.calls), 3)

    def test_limit_blocks_llm_call(self):
        llm = FakeLLM()
        a = Assistant(llm, self.kb, "ТехноДок", rate_limit_per_hour=1)
        run(a.ask(1, "раз"))
        reply = run(a.ask(1, "два"))
        self.assertTrue(reply.limited)
        self.assertEqual(len(llm.calls), 1, "после лимита нейросеть не вызывается")

    def test_long_question_is_truncated(self):
        llm = FakeLLM()
        a = Assistant(llm, self.kb, "ТехноДок")
        run(a.ask(1, "я" * 5000))
        self.assertEqual(len(llm.calls[0][-1]["content"]), 1500)

    def test_empty_answer_becomes_handoff(self):
        a = Assistant(FakeLLM("   "), self.kb, "ТехноДок")
        reply = run(a.ask(1, "?"))
        self.assertTrue(reply.handoff)
        self.assertTrue(reply.text)


class HandlersImportTest(unittest.TestCase):
    def test_router_builds(self):
        from bot.handlers import router  # проверяем, что aiogram-часть собирается без ошибок
        self.assertTrue(router.message.handlers)
        self.assertTrue(router.callback_query.handlers)


if __name__ == "__main__":
    unittest.main()
