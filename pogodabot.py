import logging
import json
import os
import requests
import datetime
import re
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

BOT_TOKEN = '7961653677:AAGi3mnvMmoCgD9GAHBqcooCBKhyw_yzAdk'
OWM_API_KEY = '13549d5eb2ee34b66ea7659a72827c4b'
DATA_FILE = 'users.json'

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def load_users():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r') as f:
            content = f.read()
            return json.loads(content) if content.strip() else {}
    except Exception as e:
        logger.error(f"Ошибка загрузки пользователей: {e}")
        return {}

def save_users(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователей: {e}")

users = load_users()

def get_weather(city):
    try:
        url = f'http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OWM_API_KEY}&units=metric&lang=ru'
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()
        weather_desc = data['weather'][0]['description'].capitalize()
        temp = data['main']['temp']
        feels_like = data['main']['feels_like']
        humidity = data['main']['humidity']

        return (
            f"🌤 Погода в {city}:\n"
            f"{weather_desc}, {temp:+.0f}°C (ощущается как {feels_like:+.0f}°C)\n"
            f"💧 Влажность: {humidity}%"
        )
    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
        return None

# Константы состояний для ConversationHandler
CHOOSING_CITY, CHOOSING_TIME = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я Погодабот ☀️\n"
        "Пришли мне название своего города, и я буду присылать тебе прогноз погоды каждый день.\n"
        "После города я спрошу, во сколько отправлять прогноз."
    )
    return CHOOSING_CITY

async def handle_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    city = update.message.text.strip()

    if not get_weather(city):
        await update.message.reply_text("❌ Не могу найти такой город. Попробуйте ещё раз:")
        return CHOOSING_CITY

    # Сохраняем город, пока время не задано
    users[user_id] = {"city": city}
    save_users(users)

    await update.message.reply_text(
        "✅ Отлично! Теперь отправь время рассылки в формате ЧЧ:ММ (например, 08:00)."
    )
    return CHOOSING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    # Проверка формата времени
    match = re.match(r'^([01]?\d|2[0-3]):([0-5]\d)$', text)
    if not match:
        await update.message.reply_text("❌ Неверный формат. Пожалуйста, отправь время в формате ЧЧ:ММ, например 08:00.")
        return CHOOSING_TIME

    hour = int(match.group(1))
    minute = int(match.group(2))

    # Обновляем данные пользователя
    if user_id not in users:
        users[user_id] = {}
    users[user_id].update({"hour": hour, "minute": minute})
    save_users(users)

    await update.message.reply_text(
        f"✅ Отлично! Теперь я буду присылать погоду в {hour:02d}:{minute:02d} по Москве каждый день.\n"
        "Если хочешь изменить город или время, используй команду /start или /time."
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

# Команда для изменения времени отдельно
async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь новое время рассылки в формате ЧЧ:ММ (например, 08:00)."
    )
    return CHOOSING_TIME

# Рассылка по времени каждого пользователя (вызывается каждую минуту)
async def send_daily_forecast_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))  # Москва UTC+3
    current_hour = now.hour
    current_minute = now.minute

    logger.info(f"Проверка рассылки в {current_hour:02d}:{current_minute:02d}")

    for user_id, data in users.copy().items():
        try:
            if 'city' not in data or 'hour' not in data or 'minute' not in data:
                continue
            if data['hour'] == current_hour and data['minute'] == current_minute:
                weather = get_weather(data['city'])
                if weather:
                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=weather
                    )
        except Exception as e:
            logger.error(f"Ошибка отправки для {user_id}: {e}")
            users.pop(user_id, None)

    save_users(users)

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city)],
            CHOOSING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    # Отдельный ConversationHandler для команды /time (смена времени)
    time_conv = ConversationHandler(
        entry_points=[CommandHandler("time", time_command)],
        states={
            CHOOSING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(time_conv)

    # Запускаем job, который проверяет рассылку каждую минуту
    application.job_queue.run_repeating(
        send_daily_forecast_job,
        interval=60,
        first=10  # старт через 10 секунд после запуска
    )

    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
