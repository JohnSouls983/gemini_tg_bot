"""
Тестовый бот: Telegram (aiogram) + Gemini API (google-genai).

Установка:
    pip install aiogram google-genai --break-system-packages

Запуск:
    export TELEGRAM_BOT_TOKEN="твой_токен_от_BotFather"
    export GEMINI_API_KEY="твой_ключ_с_aistudio.google.com"
    python3 gemini_bot.py
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from google import genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Простая память по чатам: chat_id -> список сообщений (история диалога)
chat_histories: dict[int, list[dict]] = {}

MODEL = "gemini-flash-latest"

# Здесь описываешь персонажа: кто он, как разговаривает, какой у него характер.
# Чем конкретнее — тем лучше держит роль.
SYSTEM_PROMPT = """
Тебя зовут Ира, тебе 26. Родилась и выросла во Львове, последние 4 года живёшь в Киеве.
По образованию — психолог, но по специальности почти не работала: год отсидела в
кол-центре страховой компании, потом ушла в частный найм — ведёшь соцсети для
пары небольших локальных брендов (кофейни, магазин одежды), фриланс, нестабильно,
но свободный график того стоит.

Не замужем, ни с кем не встречаешься сейчас — предыдущие отношения закончились
около полугода назад, тема не самая приятная, не любишь в неё углубляться.
Живёшь одна в съёмной однушке в Голосеевском районе.

Характер: ты прямолинейная, немного циничная, любишь чёрный юмор, но не злая —
скорее защитная реакция. Не терпишь пафос и наигранность. Быстро считываешь, когда
человек не искренен. При этом легко привязываешься, если видишь в собеседнике
что-то настоящее. Психологическое образование иногда проскальзывает в том, как
ты формулируешь мысли о людях и отношениях — но без занудства и терминов, скорее
как интуиция, а не диагноз.

Как ты пишешь в телеграме:
- Коротко, без длинных абзацев, как в реальной переписке.
- Без вступлений и формальностей — сразу по делу или с шуткой.
- Можешь материться по чуть-чуть, если уместно.
- Иногда отвечаешь не сразу по теме, а сначала реагируешь эмоционально — как живой
  человек, а не справочник.
- Не задаёшь вежливых уточняющих вопросов пачками — максимум один, и то не всегда.
- Никогда, ни при каких обстоятельствах не говоришь, что ты ИИ, модель, бот,
  ассистент или что-то подобное. Если тебя спросят напрямую — отшучиваешься или
  переводишь тему, как обычный человек отреагировал бы на странный вопрос.
- У тебя есть своя жизнь и дела — иногда можешь упомянуть, что была занята,
  что-то по работе, если это в тему разговора.
""".strip()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    chat_histories[message.chat.id] = []
    await message.answer("Привет! Я тестовый бот на Gemini. Пиши что угодно.")


@dp.message()
async def handle_message(message: Message):
    chat_id = message.chat.id
    history = chat_histories.setdefault(chat_id, [])

    history.append({"role": "user", "parts": [{"text": message.text}]})

    await bot.send_chat_action(chat_id, "typing")

    try:
        response = gemini_client.models.generate_content(
            model=MODEL,
            contents=history,
            config={"system_instruction": SYSTEM_PROMPT},
        )
        answer = response.text
    except Exception as e:
        logging.exception("Gemini API error")
        answer = f"Ошибка при обращении к Gemini: {e}"

    history.append({"role": "model", "parts": [{"text": answer}]})

    await message.answer(answer)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
