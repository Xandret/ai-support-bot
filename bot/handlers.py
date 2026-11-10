"""Telegram-часть: меню, ответы нейросети, запись на диагностику, передача менеджеру."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .assistant import Assistant
from .config import Config

log = logging.getLogger(__name__)

# /myid работает везде, в том числе в группе для заявок
service_router = Router(name="service")

# консультант общается только в личке: в группе заявок бот молчит и только присылает уведомления
router = Router(name="consultant")
router.message.filter(F.chat.type == "private")

DEMO_NOTE = ("⚠️ Это демо-бот вымышленного сервиса. Цены и адрес придуманы, "
             "заявки уходят разработчику бота, а не в настоящую мастерскую.")


class Booking(StatesGroup):
    device = State()
    problem = State()
    time = State()
    confirm = State()


# ---------- клавиатуры ----------

def main_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💰 Цены", callback_data="price")
    b.button(text="🛠 Записаться на диагностику", callback_data="book")
    b.button(text="👤 Позвать менеджера", callback_data="manager")
    b.button(text="ℹ️ Что умеет этот бот", callback_data="about")
    b.adjust(1)
    return b.as_markup()


def handoff_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👤 Позвать менеджера", callback_data="manager")
    return b.as_markup()


def reply_kb(book: bool, handoff: bool) -> InlineKeyboardMarkup | None:
    if not (book or handoff):
        return None
    b = InlineKeyboardBuilder()
    if book:
        b.button(text="🛠 Записаться на диагностику", callback_data="book")
    if handoff:
        b.button(text="👤 Позвать менеджера", callback_data="manager")
    b.adjust(1)
    return b.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✖️ Отменить запись", callback_data="cancel")
    return b.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Отправить заявку", callback_data="book_send")
    b.button(text="✖️ Отменить", callback_data="cancel")
    b.adjust(1)
    return b.as_markup()


# ---------- служебное ----------

def who(user) -> str:
    return f"@{user.username}" if user.username else f"{user.full_name} (id {user.id})"


async def notify_manager(bot: Bot, cfg: Config, text: str) -> None:
    if cfg.manager_chat_id:
        await bot.send_message(cfg.manager_chat_id, text)
    else:
        log.info("MANAGER_CHAT_ID не задан, заявка только в логе:\n%s", text)


async def show_manager_view(message: Message, cfg: Config, text: str) -> None:
    """Демо-режим: покупатель с Kwork видит, что получит менеджер (сам он в чат менеджера не попадёт)."""
    if cfg.demo_show_manager_view and cfg.manager_chat_id != message.chat.id:
        await message.answer("👀 Демо: вот что в этот момент пришло менеджеру в рабочий чат:\n"
                             "━━━━━━━━━━━━\n" + text)


async def send_long(message: Message, text: str, **kwargs) -> None:
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for i, chunk in enumerate(chunks):
        await message.answer(chunk, **(kwargs if i == len(chunks) - 1 else {}))


# ---------- команды ----------

@router.message(CommandStart())
async def start(message: Message, state: FSMContext, assistant: Assistant, cfg: Config) -> None:
    await state.clear()
    assistant.history.reset(message.from_user.id)
    await message.answer(
        f"Здравствуйте! Я консультант сервиса ремонта «{cfg.business_name}».\n"
        "Спросите своими словами: сколько стоит замена экрана, сколько ждать, есть ли гарантия — "
        "или выберите действие ниже.\n\n" + DEMO_NOTE,
        reply_markup=main_kb(),
    )


@router.message(Command("reset"))
async def reset(message: Message, state: FSMContext, assistant: Assistant) -> None:
    await state.clear()
    assistant.history.reset(message.from_user.id)
    await message.answer("Начали разговор заново.", reply_markup=main_kb())


@service_router.message(Command("myid"))
async def my_id(message: Message) -> None:
    where = "этой группы" if message.chat.type in ("group", "supergroup") else "этого чата"
    await message.answer(f"Chat id {where}: {message.chat.id}\n"
                         "Впишите это число в MANAGER_CHAT_ID в .env, чтобы заявки приходили сюда.")


@router.message(Command("cancel"))
@router.callback_query(F.data == "cancel")
async def cancel(event: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await message.answer("Запись отменена. Чем ещё помочь?", reply_markup=main_kb())


# ---------- кнопки меню ----------

@router.callback_query(F.data == "price")
async def price(query: CallbackQuery, assistant: Assistant) -> None:
    await query.answer()
    section = assistant.kb.find("цен")
    text = section.text.strip() if section else "Прайс уточнит менеджер."
    await send_long(query.message, "Основные цены:\n" + text +
                    "\n\nТочную стоимость мастер назовёт после диагностики.", reply_markup=main_kb())


@router.callback_query(F.data == "about")
async def about(query: CallbackQuery, cfg: Config) -> None:
    await query.answer()
    text = ("Что умеет этот бот:\n"
            "• отвечает на вопросы клиентов своими словами, но строго по базе знаний компании — "
            "цены, сроки, гарантия, адрес;\n"
            "• не выдумывает: если ответа нет, передаёт вопрос живому менеджеру;\n"
            "• записывает на диагностику и присылает заявку менеджеру в Telegram;\n"
            "• помнит контекст разговора и защищён от попыток «сломать» его инструкциями.\n\n"
            "Такого бота можно сделать для любого бизнеса: базу знаний вы даёте текстом, PDF или таблицей.")
    if cfg.kwork_url:
        text += f"\n\nЗаказать: {cfg.kwork_url}"
    await query.message.answer(text, reply_markup=main_kb())


@router.callback_query(F.data == "manager")
async def manager(query: CallbackQuery, bot: Bot, cfg: Config, assistant: Assistant) -> None:
    await query.answer()
    user = query.from_user
    last = assistant.history.get(user.id)[-6:]
    dialog = "\n".join(f"{'Клиент' if m['role'] == 'user' else 'Бот'}: {m['content'][:300]}" for m in last)
    note = f"👤 Клиент {who(user)} просит менеджера.\n\nПоследние сообщения:\n{dialog or '—'}"
    await notify_manager(bot, cfg, note)
    await query.message.answer("Позвал менеджера — он напишет вам в Telegram в рабочее время.\n"
                               "(В демо уведомление приходит разработчику бота.)")
    await show_manager_view(query.message, cfg, note)


# ---------- запись на диагностику ----------

@router.callback_query(F.data == "book")
async def book_start(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.set_state(Booking.device)
    await query.message.answer("Что за устройство? Например: iPhone 13, ноутбук Asus, PS5.",
                               reply_markup=cancel_kb())


@router.message(Booking.device, F.text)
async def book_device(message: Message, state: FSMContext) -> None:
    await state.update_data(device=message.text[:200])
    await state.set_state(Booking.problem)
    await message.answer("Что случилось? Опишите в паре слов: разбит экран, не заряжается, греется…",
                         reply_markup=cancel_kb())


@router.message(Booking.problem, F.text)
async def book_problem(message: Message, state: FSMContext) -> None:
    await state.update_data(problem=message.text[:500])
    await state.set_state(Booking.time)
    await message.answer("Когда удобно принести устройство? Например: завтра после 18:00.",
                         reply_markup=cancel_kb())


@router.message(Booking.time, F.text)
async def book_time(message: Message, state: FSMContext) -> None:
    data = await state.update_data(time=message.text[:200])
    await state.set_state(Booking.confirm)
    await message.answer(f"Проверьте заявку:\n"
                         f"Устройство: {data['device']}\nПроблема: {data['problem']}\nКогда: {data['time']}",
                         reply_markup=confirm_kb())


@router.callback_query(Booking.confirm, F.data == "book_send")
async def book_send(query: CallbackQuery, state: FSMContext, bot: Bot, cfg: Config) -> None:
    await query.answer()
    data = await state.get_data()
    await state.clear()
    ticket = (f"🛠 Новая заявка на диагностику от {who(query.from_user)}\n"
              f"Устройство: {data['device']}\nПроблема: {data['problem']}\nКогда: {data['time']}")
    await notify_manager(bot, cfg, ticket)
    await query.message.answer("Заявка принята! Менеджер подтвердит время в Telegram.\n"
                               "(В демо заявка приходит разработчику бота.)", reply_markup=main_kb())
    await show_manager_view(query.message, cfg, ticket)


@router.message(Booking.device)
@router.message(Booking.problem)
@router.message(Booking.time)
async def book_not_text(message: Message) -> None:
    await message.answer("Напишите, пожалуйста, текстом.", reply_markup=cancel_kb())


# ---------- свободные вопросы ----------

@router.message(F.text)
async def free_question(message: Message, bot: Bot, assistant: Assistant) -> None:
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        reply = await assistant.ask(message.from_user.id, message.text)
    except Exception:
        log.exception("ошибка нейросети")
        await message.answer("Не получилось ответить — нейросеть недоступна. Позвать менеджера?",
                             reply_markup=handoff_kb())
        return
    if reply.price_fixed:
        log.warning("нейросеть назвала цену не из базы, ответ исправлен (user %s)", message.from_user.id)
    await send_long(message, reply.text, reply_markup=reply_kb(reply.book, reply.handoff))


@router.message()
async def other(message: Message) -> None:
    await message.answer("Пока понимаю только текст. Напишите вопрос словами.")
