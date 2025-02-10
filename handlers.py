import logging
import httpx
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
    ContextTypes
)
from utils import post_to_wp, upload_image_to_wp, get_wp_categories  # Импорт из utils.py
from database import save_user_data, load_user_data, delete_user_data  # Импорт из database.py
import os

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Состояния разговора
TITLE, BODY, CATEGORY, TAGS, IMAGE, PUBLISH, SCHEDULE_DATE, SCHEDULE_TIME = range(8)

def is_authorized(user_id):
    from dotenv import load_dotenv
    load_dotenv()
    ALLOWED_USERS = set(map(int, os.getenv("ALLOWED_USERS", "").split(',')))
    return user_id in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало публикации"""
    user_id = update.message.from_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("У вас нет доступа к публикации.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("Отмена", callback_data="cancel")]]
    await update.message.reply_text(
        "Введите заголовок поста:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение заголовка"""
    user_id = update.message.from_user.id
    logging.info(f"User {user_id} entered title: {update.message.text}")
    data = load_user_data(user_id) or {}
    data["title"] = update.message.text
    save_user_data(user_id, data)
    await update.message.reply_text("Теперь введите текст поста:")
    return BODY

async def get_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение текста поста с сохранением форматирования"""
    user_id = update.message.from_user.id
    logging.info(f"User {user_id} entered body: {update.message.text}")
    data = load_user_data(user_id) or {}
    if update.message.entities:
        raw_text = update.message.text_html
    else:
        raw_text = update.message.text
        # Замена маркера ### на <!--more-->
    more_tag = "<!--more--><br>"
    marker = "###"
    while marker in raw_text:
        parts = raw_text.split(marker, 1)
        if len(parts) == 2:
            raw_text = f"{parts[0].strip()} {more_tag} {parts[1].strip()}"
        else:
            raw_text = raw_text.replace(marker, more_tag)
    data["body"] = raw_text
    save_user_data(user_id, data)
    categories = await get_wp_categories()
    if not categories:
        await update.message.reply_text("Ошибка загрузки категорий. Попробуйте позже.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(name, callback_data=cat_id)]
                for cat_id, name in categories.items()]
    await update.message.reply_text("Выберите категорию:",
                                    reply_markup=InlineKeyboardMarkup(keyboard))
    return CATEGORY

async def get_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора категории"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    selected_category = query.data
    categories = await get_wp_categories()
    if selected_category not in categories:
        await query.message.reply_text("Ошибка выбора категории.")
        return ConversationHandler.END
    data = load_user_data(user_id) or {}
    data["category"] = selected_category
    save_user_data(user_id, data)
    keyboard = [[InlineKeyboardButton("Пропустить", callback_data="skip")]]
    await query.message.reply_text(
        f"Вы выбрали категорию: {categories[selected_category]}\n\n"
        "Введите теги (через запятую) или нажмите 'Пропустить':",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TAGS

async def get_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение тегов"""
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if update.callback_query and update.callback_query.data == "skip":
        data = load_user_data(user_id) or {}
        data["tags"] = []
        save_user_data(user_id, data)
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Отправьте изображение для обложки:")
        return IMAGE
    tags = update.message.text
    data = load_user_data(user_id) or {}
    data["tags"] = [tag.strip() for tag in tags.split(",")] if tags else []
    save_user_data(user_id, data)
    await update.message.reply_text("Отправьте изображение для обложки:")
    return IMAGE

async def get_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загруженного изображения"""
    user_id = update.message.from_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_url = file.file_path
    data = load_user_data(user_id) or {}
    data["image"] = image_url
    save_user_data(user_id, data)
    keyboard = [
        [InlineKeyboardButton("Опубликовать сейчас", callback_data="now")],
        [InlineKeyboardButton("Отложить", callback_data="schedule")]
    ]
    await update.message.reply_text("Когда опубликовать?", reply_markup=InlineKeyboardMarkup(keyboard))
    return PUBLISH

async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора времени публикации"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = load_user_data(user_id) or {}
    if query.data == "now":
        success = await post_to_wp(data, True)
        await query.message.reply_text("Пост опубликован!" if success else "Ошибка публикации.")
    elif query.data == "schedule":
        await query.message.reply_text("Введите дату публикации в формате ГГГГ-ММ-ДД:")
        return SCHEDULE_DATE
    delete_user_data(user_id)
    return ConversationHandler.END

async def get_schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение даты публикации"""
    user_id = update.message.from_user.id
    try:
        date = datetime.strptime(update.message.text, "%Y-%m-%d").date()
        data = load_user_data(user_id) or {}
        data["schedule_date"] = date
        save_user_data(user_id, data)
        await update.message.reply_text("Введите время публикации в формате ЧЧ:ММ:")
        return SCHEDULE_TIME
    except ValueError:
        await update.message.reply_text("Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД:")
        return SCHEDULE_DATE

async def get_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение времени публикации"""
    user_id = update.message.from_user.id
    try:
        time = datetime.strptime(update.message.text, "%H:%M").time()
        data = load_user_data(user_id) or {}
        if "schedule_date" not in data:
            await update.message.reply_text("Ошибка: дата не была выбрана. Пожалуйста, начните заново.")
            delete_user_data(user_id)
            return ConversationHandler.END
        schedule_datetime = datetime.combine(data["schedule_date"], time)
        data["schedule_datetime"] = schedule_datetime
        save_user_data(user_id, data)
        success = await post_to_wp(data, False)
        await update.message.reply_text("Пост успешно отложен!" if success else "Ошибка отложенной публикации.")
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ:")
        return SCHEDULE_TIME
    delete_user_data(user_id)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена публикации"""
    user_id = update.message.from_user.id
    delete_user_data(user_id)
    await update.message.reply_text("Публикация отменена.")
    return ConversationHandler.END

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title), CallbackQueryHandler(cancel)],
        BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_body)],
        CATEGORY: [CallbackQueryHandler(get_category)],
        TAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tags), CallbackQueryHandler(get_tags)],
        IMAGE: [MessageHandler(filters.PHOTO, get_image)],
        PUBLISH: [CallbackQueryHandler(publish)],
        SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_schedule_date)],
        SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_schedule_time)]
    },
    fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel)]
)