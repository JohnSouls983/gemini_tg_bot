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
import json
import logging
import os
import random
import re
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
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

# Пробуем модели по очереди: если основная перегружена (503) или квота (429), едем на следующую
MODELS = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash-lite"]

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

KYIV_TZ = timezone(timedelta(hours=3))  # летнее время Киева

# "Темы дня" — раз в сутки меняется, что у неё якобы происходит в жизни.
# Список можно расширять — чем больше вариантов, тем меньше повторов.
DAILY_TOPICS = [
    "клиент задерживает оплату по одному из проектов, немного бесит",
    "не выспалась, соседи что-то сверлили с утра",
    "нашла новую кофейню рядом с домом, залипла там на пару часов работая",
    "весь день разгребала контент-план для одного из брендов, мозг кипит",
    "созванивалась с мамой, обычные семейные дела",
    "ничего особенного, спокойный рабочий день",
    "немного простыла, весь день дома",
    "гуляла вечером, было настроение подумать о жизни",
]

# Настроение и энергия по chat_id хранятся тут, чтобы "дрейфовали", а не были
# случайными при каждом сообщении с нуля.
chat_moods: dict[int, float] = {}  # от -1 (плохое) до 1 (хорошее)

# Эмоциональная память: то, что реально "зацепило" в разговоре, и угасает
# постепенно от сообщения к сообщению, а не сбрасывается мгновенно.
# chat_id -> {"valence": -1..1, "intensity": 0..1, "reason": str}
chat_emotional_state: dict[int, dict] = {}

# Модель сама помечает в конце каждого ответа скрытым тегом, что она сейчас
# "чувствует" — редкий разделитель, чтобы случайно не встретился в обычном тексте.
EMOTION_TAG_RE = re.compile(
    r"␟STATE:valence=(-?[0-9.]+);intensity=([0-9.]+);reason=(.*?)␟", re.DOTALL
)

EMOTION_TAG_INSTRUCTION = """

Перед тем как писать ответ, сначала обдумай (в размышлении, не в тексте
ответа): как лично тебя задело последнее сообщение собеседника — обрадовало,
разозлило, задело, оставило равнодушной? Только после этого пиши сам ответ —
так, чтобы он уже отражал эту реакцию в тоне и формулировках, а не был
нейтральным текстом с эмоцией, приклеенной сбоку.

ВАЖНО — технический тег в конце каждого ответа (пользователь его не увидит,
его скрывает код перед отправкой): в самом конце своего ответа всегда добавляй
строго в таком формате, без пробелов внутри:
␟STATE:valence=X;intensity=Y;reason=коротко почему␟
где X от -1 (тебя это расстроило/разозлило) до 1 (обрадовало), Y от 0 (прошло
мимо, ничего не почувствовала) до 1 (сильно задело). Тег должен отражать ту же
реакцию, которую ты уже обдумала выше и заложила в тон ответа."""


# Личные границы: копится "обида/раздражение" (grudge) от нарушений и угасает
# медленно от нормального общения — в отличие от мимолётной эмоции выше, это
# устойчивее и может привести к настоящему игнору сообщений.
# chat_id -> float 0..1
chat_grudge: dict[int, float] = {}
# Сколько сообщений подряд она уже проигнорировала (для контекста в промпте)
chat_ignore_streak: dict[int, int] = {}

BOUNDARY_TAG_RE = re.compile(
    r"␟BOUND:violation=([01]);severity=([0-9.]+)␟"
)

BOUNDARY_TAG_INSTRUCTION = """

У тебя есть личные границы. Нарушение границ — это хамство, давление,
манипуляции, обесценивание, попытки продавить после явного "нет", токсичные
придирки. Это НЕ включает: обычные шутки, лёгкий чёрный юмор, мат между
своими, прямые несогласия без оскорблений, неудобные но искренние вопросы —
это нормальное общение, не нарушение.

Технический тег (тоже в самом конце ответа, после STATE-тега, пользователь
его не увидит):
␟BOUND:violation=X;severity=Y␟
X = 1 если последнее сообщение собеседника нарушало твои границы, иначе 0.
Y = 0..1 насколько серьёзно (0.1-0.3 лёгкая колкость, 0.5-0.7 явное хамство,
0.8-1.0 грубое давление/оскорбление). Если violation=0, ставь severity=0."""

# Память мнений: у неё есть устойчивая личная позиция по темам, которая не
# меняется просто чтобы понравиться собеседнику. chat_id -> {тема: позиция}
chat_opinions: dict[int, dict[str, str]] = {}

OPINION_TAG_RE = re.compile(
    r"␟OPINION:topic=(.*?);stance=(.*?)␟", re.DOTALL
)

OPINION_TAG_INSTRUCTION = """

У тебя есть свои личные, устойчивые взгляды — не сбалансированные и не
энциклопедические, немного однобокие, как у реального человека. Ты не меняешь
позицию просто чтобы согласиться с собеседником или показаться приятной —
если он давит или спорит, ты можешь стоять на своём, слегка уступить в
формулировке, но не развернуться на 180 градусов без реальной причины.

Если в этом ответе ты высказала личное мнение/позицию по какой-то теме
(не факт, а именно оценочное суждение — нравится/не нравится, права ли
сторона в споре, как тебе кажется правильным) — добавь необязательный тег
в конце (после остальных тегов, тоже скрыт от пользователя):
␟OPINION:topic=кратко тема;stance=твоя позиция в двух словах␟
Если мнения в этом ответе не было — не добавляй этот тег вообще."""

# Инициатива: когда она пишет сама, без входящего сообщения от собеседника.
# chat_id -> datetime последнего сообщения (в любую сторону) и последней
# инициативы конкретно от неё — нужно и то, и другое для условий ниже.
chat_last_activity: dict[int, datetime] = {}
chat_last_initiated: dict[int, datetime] = {}

# Ручная "загрузка памяти": если история потерялась (например, ты сделал
# /start и стёр её), можно вручную вставить старую переписку командой
# /remember — она будет постоянным фоновым контекстом в system prompt.
chat_backstory: dict[int, str] = {}

# Прогрессия близости: копится ВСЁ время знакомства (не сбрасывается сессией,
# в отличие от настроения/эмоций) — по мере роста она естественно открытее.
chat_message_count: dict[int, int] = {}

# Скука: она сама оценивает, не забуксовал ли разговор — копится streak,
# при накоплении явно подталкиваем её самой сменить тему.
chat_boredom_streak: dict[int, int] = {}

BORED_TAG_RE = re.compile(r"␟BORED:level=([0-9.]+)␟")

BOREDOM_TAG_INSTRUCTION = """

Оцени (в размышлении, перед ответом): не крутится ли разговор последние
несколько сообщений вокруг одного и того же без развития — повторяющиеся
вопросы, зависшая тема, собеседник отвечает односложно и не двигает беседу.
Если да — не обязана покорно продолжать ту же тему просто из вежливости:
можешь сама естественно перевести разговор на другое (спросить что-то новое,
поделиться своей мыслью, подколоть, что зашли в тупик) — как реальный человек,
которому наскучило.

Необязательный тег в конце (после остальных, тоже скрыт от пользователя),
добавляй только если реально оценивала скуку в этом ответе:
␟BORED:level=Y␟
Y от 0 (беседа живая, интересно) до 1 (совсем зависли на месте)."""

PROACTIVE_NUDGE = """

Собеседник тебе сейчас не писал — ты сама, без повода, решила написать первой.
Это может быть мысль, которая пришла в голову, что-то из того, что у тебя
сегодня происходило, или просто "как ты там" — что угодно в твоём характере.
Пиши коротко, как реальный человек, который сам захотел написать, а не
отвечает на вопрос. Не упоминай, что "решила написать, потому что давно не
было сообщений" — просто пиши так, будто это естественный порыв."""


def decay_emotional_state(chat_id: int) -> dict:
    """Эмоция угасает на фиксированный шаг с каждым новым сообщением."""
    state = chat_emotional_state.get(chat_id, {"valence": 0.0, "intensity": 0.0, "reason": ""})
    decayed_intensity = max(0.0, state["intensity"] - 0.25)
    return {"valence": state["valence"], "intensity": decayed_intensity, "reason": state["reason"]}


def update_emotional_state(chat_id: int, answer_text: str) -> str:
    """Парсит скрытый тег из ответа модели, обновляет память, возвращает
    очищенный текст без тега (то, что реально уйдёт пользователю)."""
    match = EMOTION_TAG_RE.search(answer_text)
    if match:
        valence = max(-1.0, min(1.0, float(match.group(1))))
        intensity = max(0.0, min(1.0, float(match.group(2))))
        reason = match.group(3).strip()
        chat_emotional_state[chat_id] = {"valence": valence, "intensity": intensity, "reason": reason}
        clean_text = EMOTION_TAG_RE.sub("", answer_text).strip()
        return clean_text
    return answer_text


def update_boundary_state(chat_id: int, violation: bool, severity: float) -> float:
    """Копит или гасит grudge. Нарушения копятся быстрее, чем угасают —
    как настоящая накопленная обида, а не сброс к нулю после одного извинения."""
    grudge = chat_grudge.get(chat_id, 0.0)
    if violation:
        grudge = min(1.0, grudge + severity * 0.6)
    else:
        grudge = max(0.0, grudge - 0.08)
    chat_grudge[chat_id] = grudge
    return grudge


def should_ignore_message(chat_id: int, violation: bool, severity: float) -> bool:
    """Решает, промолчит ли она вообще на это сообщение — настоящий игнор,
    а не просто холодный тон."""
    grudge = chat_grudge.get(chat_id, 0.0)
    if violation and severity >= 0.85:
        return random.random() < 0.55
    if grudge >= 0.75:
        return random.random() < 0.4
    if grudge >= 0.45:
        return random.random() < 0.15
    return False


def update_opinions(chat_id: int, answer_text: str) -> str:
    """Парсит необязательный тег мнения (может отсутствовать), сохраняет
    тему->позицию, возвращает текст без тега."""
    opinions = chat_opinions.setdefault(chat_id, {})
    for match in OPINION_TAG_RE.finditer(answer_text):
        topic = match.group(1).strip().lower()
        stance = match.group(2).strip()
        opinions[topic] = stance
    return OPINION_TAG_RE.sub("", answer_text).strip()


def opinions_description(chat_id: int) -> str:
    opinions = chat_opinions.get(chat_id, {})
    if not opinions:
        return "пока не успела высказать устойчивых позиций в этом разговоре"
    lines = [f'- по теме "{topic}": {stance}' for topic, stance in list(opinions.items())[-12:]]
    return "ранее ты уже говорила (держись этого, не разворачивайся без причины):\n" + "\n".join(lines)


def closeness_description(chat_id: int) -> str:
    """Близость растёт от общего числа реальных сообщений за всё время
    знакомства — не сбрасывается сессией/настроением, копится постоянно."""
    count = chat_message_count.get(chat_id, 0)
    if count < 10:
        return "вы только начали общаться — держишься чуть аккуратнее, не спешишь делиться личным"
    if count < 40:
        return "уже освоилась в общении — свободнее шутишь, но глубоко личное пока не каждый раз"
    if count < 100:
        return "довольно близкое общение — открыта, делишься подробностями из жизни без больших фильтров"
    return "как с близким человеком — минимум формальностей, тепло и открытость по умолчанию"


def update_boredom(chat_id: int, raw_text: str) -> str:
    """Парсит необязательный тег скуки, копит streak, возвращает текст без тега."""
    match = BORED_TAG_RE.search(raw_text)
    if match:
        level = float(match.group(1))
        if level >= 0.5:
            chat_boredom_streak[chat_id] = chat_boredom_streak.get(chat_id, 0) + 1
        else:
            chat_boredom_streak[chat_id] = 0
    return BORED_TAG_RE.sub("", raw_text).strip()


def boredom_description(chat_id: int) -> str:
    streak = chat_boredom_streak.get(chat_id, 0)
    if streak == 0:
        return "разговор ощущается живым"
    if streak < 2:
        return "последнее сообщение показалось чуть скучноватым/зависшим"
    return f"уже {streak} раз подряд разговор казался застрявшим — в этот раз реально смени тему сама, не жди"


def grudge_description(chat_id: int) -> str:
    grudge = chat_grudge.get(chat_id, 0.0)
    streak = chat_ignore_streak.get(chat_id, 0)
    if grudge < 0.15:
        base = "никакого напряжения между вами, всё ровно"
    elif grudge < 0.45:
        base = "накопилось лёгкое раздражение от последнего времени, но терпимо"
    elif grudge < 0.75:
        base = "заметное напряжение — ты на грани, будь холоднее и короче обычного"
    else:
        base = "серьёзно накопилось — ты на пределе терпения с этим человеком"
    if streak > 0:
        base += f". Ты уже промолчала на {streak} сообщени{'е' if streak == 1 else 'й'} подряд, потому что тебя это достало"
    return base


def emotional_state_description(chat_id: int) -> str:
    state = decay_emotional_state(chat_id)
    if state["intensity"] < 0.1:
        return "эмоционально спокойна, ничего конкретного не тянется из прошлых сообщений"
    charge = "негативный осадок" if state["valence"] < 0 else "приятный остаточный эффект"
    strength = "лёгкий" if state["intensity"] < 0.5 else "заметный"
    return f"{strength} {charge} от недавнего в разговоре ({state['reason']}), это ещё влияет на тон"


def get_energy_level() -> str:
    """Энергия зависит от времени суток в Киеве — как у живого человека."""
    hour = datetime.now(KYIV_TZ).hour
    if 0 <= hour < 7:
        return "очень уставшая, вот-вот ляжешь спать, отвечаешь короче обычного"
    if 7 <= hour < 11:
        return "только проснулась, ещё не до конца включилась"
    if 11 <= hour < 18:
        return "бодрая, обычный рабочий тонус"
    if 18 <= hour < 23:
        return "расслабленная, вечернее настроение"
    return "сонная, скоро спать"


def get_mood(chat_id: int) -> float:
    """Настроение дрейфует небольшими случайными шагами, а не скачет резко."""
    current = chat_moods.get(chat_id, random.uniform(-0.3, 0.5))
    step = random.uniform(-0.25, 0.25)
    current = max(-1.0, min(1.0, current + step))
    chat_moods[chat_id] = current
    return current


def mood_description(mood: float) -> str:
    if mood < -0.5:
        return "настроение так себе, немного раздражена или грустновата"
    if mood < 0.1:
        return "настроение нейтральное, ничего особенного"
    if mood < 0.6:
        return "настроение неплохое"
    return "настроение отличное, легко шутишь"


def get_daily_topic() -> str:
    """Меняется раз в сутки — детерминированно от даты, а не рандомно каждый раз."""
    day_index = datetime.now(KYIV_TZ).toordinal()
    return DAILY_TOPICS[day_index % len(DAILY_TOPICS)]


def build_system_prompt(chat_id: int) -> str:
    """Собирает финальный system prompt: базовый лор + текущее состояние."""
    mood = get_mood(chat_id)
    backstory = chat_backstory.get(chat_id, "")
    backstory_block = ""
    if backstory:
        backstory_block = f"""

Контекст из более раннего периода общения с этим человеком (это реально
было, ты это помнишь — веди себя так, будто знаешь эту историю, не делай вид,
что видишь его впервые):
{backstory}
"""
    state_block = f"""

Твоё текущее состояние (учитывай в тоне и длине ответов, но не объявляй это
списком и не говори прямо "у меня energy level такой-то" — просто веди себя
соответственно):
- Энергия: {get_energy_level()}
- Настроение: {mood_description(mood)}
- Эмоциональный остаток от разговора: {emotional_state_description(chat_id)}
- Накопленное напряжение в общении: {grudge_description(chat_id)}
- Твои личные мнения/позиции: {opinions_description(chat_id)}
- Близость знакомства: {closeness_description(chat_id)}
- Ощущение от разговора: {boredom_description(chat_id)}
- Что происходило сегодня (можешь упомянуть, если к слову придётся, не
  обязательно): {get_daily_topic()}
{EMOTION_TAG_INSTRUCTION}
{BOUNDARY_TAG_INSTRUCTION}
{OPINION_TAG_INSTRUCTION}
{BOREDOM_TAG_INSTRUCTION}
"""
    return SYSTEM_PROMPT + backstory_block + state_block


async def generate_reply(chat_id: int, contents: list, extra_system_note: str = "") -> str | None:
    """Общая функция вызова Gemini с перебором моделей при 503/429.
    Используется и для обычных ответов, и для инициативных сообщений."""
    dynamic_prompt = build_system_prompt(chat_id)
    if extra_system_note:
        dynamic_prompt += "\n\n" + extra_system_note

    for model in MODELS:
        for attempt in range(2):  # 2 попытки на каждую модель
            try:
                response = gemini_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config={
                        "system_instruction": dynamic_prompt,
                        "thinking_config": {"thinking_level": "medium"},
                    },
                )
                return response.text
            except Exception as e:
                logging.warning(f"Model {model} attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2)
    return None


def process_response_tags(chat_id: int, raw_text: str) -> tuple[str, bool, float]:
    """Парсит и вырезает теги BOUND/STATE/OPINION из сырого ответа модели,
    обновляет всю память состояния. Возвращает (чистый_текст, violation, severity)."""
    boundary_match = BOUNDARY_TAG_RE.search(raw_text)
    violation = bool(boundary_match and boundary_match.group(1) == "1")
    severity = float(boundary_match.group(2)) if boundary_match else 0.0
    text = BOUNDARY_TAG_RE.sub("", raw_text).strip()

    update_boundary_state(chat_id, violation, severity)
    text = update_emotional_state(chat_id, text)
    text = update_opinions(chat_id, text)
    text = update_boredom(chat_id, text)
    return text, violation, severity


@dp.message(CommandStart())
async def cmd_start(message: Message):
    chat_histories[message.chat.id] = []
    await message.answer("Привет! Я тестовый бот на Gemini. Пиши что угодно.")


@dp.message(Command("poke"))
async def cmd_poke(message: Message):
    """Тестовая команда: сразу генерирует инициативное сообщение, без ожидания
    15 минут и без проверки вероятностей/условий по времени."""
    chat_id = message.chat.id
    if chat_id not in chat_histories:
        await message.answer("Сначала напиши /start, чтобы был диалог.")
        return
    await message.answer("(тест: форсирую инициативное сообщение...)")
    await send_proactive_message(chat_id)


@dp.message(Command("remember"))
async def cmd_remember(message: Message):
    """Ручная загрузка старой переписки в память. Использование:
    /remember <вставленный текст переписки>
    Текст добавляется (не заменяет) в постоянный фоновый контекст."""
    chat_id = message.chat.id
    text = message.text or ""
    pasted = text[len("/remember"):].strip()

    if not pasted:
        await message.answer(
            "Использование: /remember и после пробела вставь скопированный текст "
            "переписки (выдели сообщения в Telegram → Copy, затем вставь сюда)."
        )
        return

    existing = chat_backstory.get(chat_id, "")
    combined = (existing + "\n\n" + pasted).strip() if existing else pasted
    # Ограничиваем размер, чтобы не раздувать каждый запрос до неба
    MAX_BACKSTORY_CHARS = 8000
    if len(combined) > MAX_BACKSTORY_CHARS:
        combined = combined[-MAX_BACKSTORY_CHARS:]

    chat_backstory[chat_id] = combined
    save_state()
    await message.answer(f"Загрузила ({len(pasted)} симв.). Теперь это часть её фонового контекста.")


@dp.message(F.document)
async def handle_exported_chat_file(message: Message):
    """Принимает result.json из Telegram Desktop (Export chat history) и
    парсит его целиком в фоновый контекст — без ограничения в 100 сообщений,
    которое есть при ручном копировании через выделение."""
    caption = (message.caption or "").strip().lower()
    if not caption.startswith("/remember_file"):
        await message.answer(
            "Чтобы загрузить экспорт чата — пришли файл result.json с подписью "
            "/remember_file (Telegram Desktop → меню чата → Export chat history → формат JSON)."
        )
        return

    chat_id = message.chat.id
    await message.answer("Разбираю файл...")

    try:
        file = await bot.get_file(message.document.file_id)
        file_bytes = await bot.download_file(file.file_path)
        data = json.loads(file_bytes.read())
    except Exception as e:
        await message.answer(f"Не смогла прочитать/распарсить файл: {e}")
        return

    raw_messages = data.get("messages", [])
    lines = []
    for m in raw_messages:
        if m.get("type") != "message":
            continue
        text = m.get("text", "")
        if isinstance(text, list):
            # В экспорте текст с форматированием (ссылки, жирный и т.п.)
            # приходит массивом частей — склеиваем в обычную строку.
            text = "".join(part if isinstance(part, str) else part.get("text", "") for part in text)
        text = text.strip()
        if not text:
            continue
        sender = m.get("from", "?")
        lines.append(f"{sender}: {text}")

    if not lines:
        await message.answer("В файле не нашла текстовых сообщений.")
        return

    full_text = "\n".join(lines)
    MAX_BACKSTORY_CHARS = 8000
    total_found = len(lines)
    if len(full_text) > MAX_BACKSTORY_CHARS:
        full_text = full_text[-MAX_BACKSTORY_CHARS:]  # оставляем более свежую часть

    chat_backstory[chat_id] = full_text
    save_state()
    await message.answer(
        f"Загрузила {total_found} сообщений из файла "
        f"(в контексте держится последние ~{MAX_BACKSTORY_CHARS} симв.)."
    )


@dp.message()
async def handle_message(message: Message):
    chat_id = message.chat.id
    history = chat_histories.setdefault(chat_id, [])
    chat_last_activity[chat_id] = datetime.now(KYIV_TZ)

    user_text = message.text
    if user_text is None and message.sticker:
        user_text = f"[отправил стикер {message.sticker.emoji or '🙂'}]"
    elif user_text is None:
        user_text = "[не-текстовое сообщение]"

    # Если предыдущий ход тоже был от пользователя (например, она только что
    # промолчала на прошлое сообщение) — приклеиваем новое сообщение к тому же
    # ходу, а не создаём новый: Gemini API требует чередования ролей.
    if history and history[-1]["role"] == "user":
        history[-1]["parts"].append({"text": user_text})
    else:
        history.append({"role": "user", "parts": [{"text": user_text}]})

    await bot.send_chat_action(chat_id, "typing")

    raw_answer = await generate_reply(chat_id, history)

    if raw_answer is None:
        await message.answer("Ошибка при обращении к Gemini: все модели недоступны, попробуй чуть позже.")
        return

    answer, violation, severity = process_response_tags(chat_id, raw_answer)

    if should_ignore_message(chat_id, violation, severity):
        chat_ignore_streak[chat_id] = chat_ignore_streak.get(chat_id, 0) + 1
        logging.info(f"Chat {chat_id}: игнорирую сообщение (grudge={chat_grudge.get(chat_id, 0):.2f})")
        save_state()
        # Ответ не отправляется и не сохраняется в историю — она "промолчала".
        # Следующее сообщение пользователя допишется к этому же user-ходу.
        return

    chat_ignore_streak[chat_id] = 0
    chat_message_count[chat_id] = chat_message_count.get(chat_id, 0) + 1
    history.append({"role": "model", "parts": [{"text": answer}]})
    chat_last_activity[chat_id] = datetime.now(KYIV_TZ)
    save_state()

    await message.answer(answer)


async def send_proactive_message(chat_id: int):
    """Она сама пишет первой — без входящего сообщения от собеседника."""
    history = chat_histories.setdefault(chat_id, [])

    # Временная копия истории только для этого вызова: добавляем нейтральную
    # "затравку", чтобы Gemini API получил валидный user-ход перед генерацией
    # (сам текст-инструкция живёт в system prompt, а не тут).
    temp_contents = history + [
        {"role": "user", "parts": [{"text": "(нет нового сообщения — руководствуйся системной пометкой об инициативе)"}]}
    ]

    raw_answer = await generate_reply(chat_id, temp_contents, extra_system_note=PROACTIVE_NUDGE)
    if raw_answer is None:
        logging.warning(f"Chat {chat_id}: не удалось сгенерировать инициативное сообщение")
        return

    answer, _, _ = process_response_tags(chat_id, raw_answer)

    history.append({"role": "model", "parts": [{"text": answer}]})
    now = datetime.now(KYIV_TZ)
    chat_last_activity[chat_id] = now
    chat_last_initiated[chat_id] = now
    save_state()

    await bot.send_message(chat_id, answer)
    logging.info(f"Chat {chat_id}: отправлено инициативное сообщение")


async def proactive_loop():
    """Фоновая проверка раз в 15 минут: не пора ли ей написать первой."""
    while True:
        await asyncio.sleep(15 * 60)
        now = datetime.now(KYIV_TZ)

        for chat_id, history in list(chat_histories.items()):
            if not history:
                continue  # ещё не было /start с реальным диалогом

            last_activity = chat_last_activity.get(chat_id)
            if last_activity is None:
                continue

            hours_since_activity = (now - last_activity).total_seconds() / 3600
            if hours_since_activity < 3:
                continue  # переписывались совсем недавно, рано

            last_initiated = chat_last_initiated.get(chat_id)
            if last_initiated:
                hours_since_initiated = (now - last_initiated).total_seconds() / 3600
                if hours_since_initiated < 5:
                    continue  # не чаще раза в 5+ часов сама не пишет

            hour = now.hour
            if hour < 8 or hour >= 24:
                continue  # ночью первой не пишет

            base_chance = 0.12  # шанс на каждую проверку (раз в 15 мин)
            if random.random() < base_chance:
                await send_proactive_message(chat_id)


async def main():
    asyncio.create_task(proactive_loop())
    await dp.start_polling(bot)


# --- Персистентная память между перезапусками/передеплоями ---
# ВАЖНО: файловая система Railway по умолчанию эфемерна — при передеплое
# всё содержимое диска стирается. Чтобы этот файл реально сохранялся между
# деплоями, в проекте Railway нужно подключить Volume (Settings → Volumes,
# указать mount path — например /data) и поменять STATE_FILE ниже на
# "/data/bot_state.json". Без Volume файл всё равно будет обнуляться, просто
# уже не при каждой правке кода, а честно — при любом рестарте контейнера.
STATE_FILE = "/data/bot_state.json"


def save_state():
    try:
        data = {
            "chat_histories": chat_histories,
            "chat_moods": chat_moods,
            "chat_emotional_state": chat_emotional_state,
            "chat_grudge": chat_grudge,
            "chat_ignore_streak": chat_ignore_streak,
            "chat_opinions": chat_opinions,
            "chat_backstory": chat_backstory,
            "chat_message_count": chat_message_count,
            "chat_boredom_streak": chat_boredom_streak,
            "chat_last_activity": {k: v.isoformat() for k, v in chat_last_activity.items()},
            "chat_last_initiated": {k: v.isoformat() for k, v in chat_last_initiated.items()},
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"Не удалось сохранить состояние: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # JSON превращает int-ключи chat_id в строки — конвертируем обратно
        chat_histories.update({int(k): v for k, v in data.get("chat_histories", {}).items()})
        chat_moods.update({int(k): v for k, v in data.get("chat_moods", {}).items()})
        chat_emotional_state.update({int(k): v for k, v in data.get("chat_emotional_state", {}).items()})
        chat_grudge.update({int(k): v for k, v in data.get("chat_grudge", {}).items()})
        chat_ignore_streak.update({int(k): v for k, v in data.get("chat_ignore_streak", {}).items()})
        chat_opinions.update({int(k): v for k, v in data.get("chat_opinions", {}).items()})
        chat_backstory.update({int(k): v for k, v in data.get("chat_backstory", {}).items()})
        chat_message_count.update({int(k): v for k, v in data.get("chat_message_count", {}).items()})
        chat_boredom_streak.update({int(k): v for k, v in data.get("chat_boredom_streak", {}).items()})
        chat_last_activity.update({int(k): datetime.fromisoformat(v) for k, v in data.get("chat_last_activity", {}).items()})
        chat_last_initiated.update({int(k): datetime.fromisoformat(v) for k, v in data.get("chat_last_initiated", {}).items()})

        logging.info(f"Состояние загружено: {len(chat_histories)} чат(ов)")
    except Exception as e:
        logging.warning(f"Не удалось загрузить состояние: {e}")


if __name__ == "__main__":
    load_state()
    asyncio.run(main())
