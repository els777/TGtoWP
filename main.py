# main.py
import os
from telegram.ext import Application, ConversationHandler
from handlers import start, cancel, conv_handler

# Загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Запуск бота
if __name__ == "__main__":
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(conv_handler)
    application.run_polling()