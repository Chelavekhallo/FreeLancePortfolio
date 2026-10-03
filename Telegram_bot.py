import asyncio
import contextlib
import logging
import re
from datetime import datetime, timedelta
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from secretcode import token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="UTC")



@dataclass
class ReminderInfo:
    job_id: str
    chat_id: int
    text: str
    run_at: datetime


user_reminders: dict[int, dict[str, ReminderInfo]] = {}


def _register(user_id: int, info: ReminderInfo) -> None:
    user_reminders.setdefault(user_id, {})[info.job_id] = info


def _unregister(user_id: int, job_id: str) -> None:
    if user_id in user_reminders:
        user_reminders[user_id].pop(job_id, None)
        if not user_reminders[user_id]:
            user_reminders.pop(user_id, None)


# ---------------------------------------------------------------------------
# Отправка напоминания + самоочистка
# ---------------------------------------------------------------------------
async def send_reminder(user_id: int, chat_id: int, job_id: str, text: str):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Напоминание:</b>\n{text}",
        )
        logger.info("Reminder delivered user=%s job=%s", user_id, job_id)
    except Exception as e:
        logger.exception("Failed to send reminder %s: %s", job_id, e)
    finally:
        _unregister(user_id, job_id)


# ---------------------------------------------------------------------------
# Парсинг
# ---------------------------------------------------------------------------
TIME_UNITS = {
    "с": 1, "сек": 1, "секунд": 1, "секунды": 1,
    "м": 60, "мин": 60, "минут": 60, "минуты": 60, "минуту": 60,
    "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    "д": 86400, "день": 86400, "дня": 86400, "дней": 86400,
}

TOKEN_RE = re.compile(r"(\d+)\s*([а-яё]+)", re.IGNORECASE)


def parse_delay(raw: str) -> int | None:
    total = 0
    matched = False
    for num, unit in TOKEN_RE.findall(raw):
        unit = unit.lower().rstrip(".")
        seconds = TIME_UNITS.get(unit)
        if seconds is None:
            return None
        total += int(num) * seconds
        matched = True
    if not matched or total <= 0:
        return None
    return total


def humanize(seconds: int) -> str:
    parts = []
    for name, div in (("д", 86400), ("ч", 3600), ("мин", 60), ("сек", 1)):
        if seconds >= div:
            parts.append(f"{seconds // div}{name}")
            seconds %= div
    return " ".join(parts) or "0сек"



HELP_TEXT = (
    "👋 Привет! Я бот-напоминалка.\n\n"
    "<b>Создать напоминание:</b>\n"
    "<code>через 10 мин Позвонить маме</code>\n"
    "<code>через 1ч30м выпить таблетку</code>\n"
    "<code>через 2 дня оплатить счёт</code>\n\n"
    "<b>Команды:</b>\n"
    "/list — мои напоминания\n"
    "/cancel &lt;id&gt; — отменить напоминание\n"
    "/help — эта справка"
)


@dp.message(Command("start", "help"))
async def cmd_start(message: types.Message):
    await message.answer(HELP_TEXT)


@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    items = user_reminders.get(message.from_user.id, {})
    if not items:
        await message.answer("У вас нет активных напоминаний.")
        return

    now = datetime.now(scheduler.timezone)
    lines = ["<b>Ваши напоминания:</b>"]
    for info in sorted(items.values(), key=lambda x: x.run_at):
        left = max(0, int((info.run_at - now).total_seconds()))
        lines.append(
            f"• <code>{info.job_id}</code> — через {humanize(left)}\n"
            f"  <i>{info.text}</i>"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/cancel &lt;id&gt;</code>")
        return

    job_id = args[1].strip()
    items = user_reminders.get(message.from_user.id, {})
    if job_id not in items:
        await message.answer("❌ Напоминание с таким ID не найдено.")
        return

    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    _unregister(message.from_user.id, job_id)
    await message.answer(f"✅ Напоминание <code>{job_id}</code> отменено.")


@dp.message(F.text)
async def handle_reminder(message: types.Message):
    text = (message.text or "").strip()

    if not text.lower().startswith("через"):
        await message.answer(
            "Я понимаю команды, начинающиеся со слова <b>через</b>.\n"
            "Например: <code>через 10 мин Позвонить маме</code>"
        )
        return

    body = text[len("через"):].strip()
    match = re.match(r"((?:\d+\s*[а-яё]+\s*)+)(.*)", body, re.IGNORECASE)
    if not match:
        await message.answer(
            "❌ Неверный формат. Пример: <code>через 10 мин Позвонить маме</code>"
        )
        return

    delay_raw, reminder_text = match.group(1), match.group(2).strip()
    if not reminder_text:
        await message.answer("❌ Не указан текст напоминания.")
        return

    delay = parse_delay(delay_raw)
    if delay is None:
        await message.answer(
            "❌ Не удалось понять время. Используйте: "
            "<code>с/сек, мин, ч, д</code>. Пример: <code>через 1ч30м ...</code>"
        )
        return

    if delay > 365 * 86400:
        await message.answer("❌ Слишком большое время (максимум 1 год).")
        return

    user_id = message.from_user.id
    run_at = datetime.now(scheduler.timezone) + timedelta(seconds=delay)
    job_id = f"r{user_id}_{int(run_at.timestamp())}"

    scheduler.add_job(
        send_reminder,
        trigger=DateTrigger(run_date=run_at),
        args=[user_id, message.chat.id, job_id, reminder_text],
        id=job_id,
        max_instances=1,
        misfire_grace_time=60,
        replace_existing=True,
    )
    _register(user_id, ReminderInfo(job_id, message.chat.id, reminder_text, run_at))

    await message.answer(
        f"✅ Напоминание установлено через <b>{humanize(delay)}</b>.\n"
        f"ID: <code>{job_id}</code>\n"
        f"Текст: <i>{reminder_text}</i>"
    )



async def main():
    scheduler.start()
    logger.info("Bot started, scheduler running.")
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())