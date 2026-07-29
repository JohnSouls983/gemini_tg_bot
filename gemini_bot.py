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
