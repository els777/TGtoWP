from telegram.ext import Application, CommandHandler
from handlers import conv_handler  # Импортируем conversation handler
import os

# Загрузка токена бота из .env
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Инициализация базы данных
from database import init_db
init_db()

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавление обработчиков
    application.add_handler(conv_handler)

    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()