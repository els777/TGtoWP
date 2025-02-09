import logging
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
import httpx  # Для работы с HTTP-запросами

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Состояния разговора
TITLE, BODY, CATEGORY, TAGS, IMAGE, PREVIEW, PUBLISH, SCHEDULE_DATE, SCHEDULE_TIME = range(9)

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
    await update.message.reply_text("*Теперь введите текст поста:*", parse_mode="Markdown")
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
    more_tag = "<!--more-->"
    marker = "####"
    while marker in raw_text:
        parts = raw_text.split(marker, 1)
        if len(parts) == 2:
            raw_text = f"{parts[0].strip()} {more_tag} {parts[1].strip()}"
        else:
            raw_text = raw_text.replace(marker, more_tag)
    data["body"] = raw_text
    save_user_data(user_id, data)

    categories_tree = await get_wp_categories()
    if not categories_tree:
        await update.message.reply_text("Ошибка загрузки категорий. Попробуйте позже.")
        return ConversationHandler.END

    # Формирование кнопок с учётом иерархии
    def build_keyboard(categories, prefix=""):
        buttons = []
        for cat_id, cat_data in categories.items():
            name = f"{prefix}{cat_data['name']}"
            buttons.append([InlineKeyboardButton(name, callback_data=str(cat_id))])
            for child in cat_data.get("children", []):
                buttons.extend(build_keyboard({child["id"]: {"name": child["name"], "children": []}}, prefix="˪ "))
        return buttons

    keyboard = build_keyboard(categories_tree)
    await update.message.reply_text(
        "*Выберите категорию:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CATEGORY

async def get_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора категории"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    selected_category = query.data
    categories_tree = await get_wp_categories()

    # Поиск выбранной категории
    def find_category_by_id(cat_id, categories):
        for cid, cat_data in categories.items():
            if str(cid) == str(cat_id):
                return cid, cat_data["name"]
            for child in cat_data.get("children", []):
                if str(child["id"]) == str(cat_id):
                    return child["id"], child["name"]
        return None, None

    category_id, category_name = find_category_by_id(selected_category, categories_tree)
    if not category_id:
        await query.message.reply_text("Ошибка выбора категории.")
        return ConversationHandler.END

    data = load_user_data(user_id) or {}
    data["category"] = category_id
    save_user_data(user_id, data)

    await query.message.reply_text(
        f"*Вы выбрали категорию:* {category_name}\n\n*Введите теги (через запятую) или нажмите 'Пропустить':*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="skip")]])
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
        await update.callback_query.message.reply_text("*Отправьте изображение для обложки:*", parse_mode="Markdown")
        return IMAGE

    tags = update.message.text.split(",")
    data = load_user_data(user_id) or {}
    data["tags"] = [tag.strip() for tag in tags if tag.strip()]
    save_user_data(user_id, data)

    tag_list = ", ".join(data["tags"])
    await update.message.reply_text(
        f"*Выбранные теги:* {tag_list}\n\n*Отправьте изображение для обложки:*",
        parse_mode="Markdown"
    )
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

    # Загружаем изображение в WordPress и получаем его URL
    wp_image_id = await upload_image_to_wp(image_url)
    if wp_image_id:
        wp_image_url = await get_image_url_from_id(wp_image_id)
        if wp_image_url:
            data["image_url"] = wp_image_url  # Сохраняем публичный URL изображения
            save_user_data(user_id, data)
        else:
            logging.error("Не удалось получить URL изображения.")
    else:
        logging.error("Не удалось загрузить изображение в WordPress.")

    # Переходим к шагу превью
    await preview_post(update, context)
    return PREVIEW

async def preview_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает превью поста перед публикацией"""
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    query = update.callback_query if update.callback_query else None
    data = load_user_data(user_id) or {}

    # Если превью уже было показано, завершаем выполнение
    if data.get("preview_shown"):
        return PUBLISH

    # Создание превью
    preview_text = (
        f"*Заголовок:* {data.get('title', 'Не указан')}\n\n"
        f"{data.get('body', 'Текст поста не указан.')[:100]}...\n\n"
        f"*Категория:* {await get_category_name(data.get('category'))}\n"
        f"*Теги:* {', '.join(data.get('tags', [])) or 'Нет тегов'}\n"
    )

    # Отправка изображения, если оно есть
    if "image_url" in data and query:
        try:
            await query.message.reply_photo(
                photo=data["image_url"],
                caption=preview_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Опубликовать сейчас", callback_data="now")],
                    [InlineKeyboardButton("Отложить", callback_data="schedule")],
                    [InlineKeyboardButton("Отменить", callback_data="cancel")]
                ])
            )
        except Exception as e:
            logging.error(f"Ошибка отправки изображения: {e}")
            await query.message.reply_text(
                preview_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Опубликовать сейчас", callback_data="now")],
                    [InlineKeyboardButton("Отложить", callback_data="schedule")],
                    [InlineKeyboardButton("Отменить", callback_data="cancel")]
                ])
            )
    elif query:
        await query.message.reply_text(
            preview_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Опубликовать сейчас", callback_data="now")],
                [InlineKeyboardButton("Отложить", callback_data="schedule")],
                [InlineKeyboardButton("Отменить", callback_data="cancel")]
            ])
        )
    else:
        await update.message.reply_text(
            preview_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Опубликовать сейчас", callback_data="now")],
                [InlineKeyboardButton("Отложить", callback_data="schedule")],
                [InlineKeyboardButton("Отменить", callback_data="cancel")]
            ])
        )

    # Устанавливаем флаг, что превью уже показано
    data["preview_shown"] = True
    save_user_data(user_id, data)

    return PUBLISH

async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора времени публикации"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = load_user_data(user_id) or {}

    # Если превью уже показано, не создаем его снова
    if not data.get("preview_shown"):
        await preview_post(update, context)
        return PUBLISH

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
    await update.message.reply_text("Публикация отменена.", parse_mode="Markdown")
    return ConversationHandler.END

async def get_category_name(category_id):
    """Получение имени категории по её ID"""
    categories_tree = await get_wp_categories()
    if not categories_tree:
        return "Неизвестная категория"

    def find_category_name(cat_id, categories):
        for cid, cat_data in categories.items():
            if str(cid) == str(cat_id):
                return cat_data["name"]
            for child in cat_data.get("children", []):
                if str(child["id"]) == str(cat_id):
                    return child["name"]
        return "Неизвестная категория"

    return find_category_name(category_id, categories_tree)

async def get_image_url_from_id(image_id):
    """Получение URL изображения по его ID в WordPress"""
    url = f"{os.getenv('WP_URL')}/wp-json/wp/v2/media/{image_id}"
    auth = httpx.BasicAuth(os.getenv('WP_USERNAME'), os.getenv('WP_PASSWORD'))
    async with httpx.AsyncClient() as client:
        response = await client.get(url, auth=auth)
        if response.status_code == 200:
            media_data = response.json()
            return media_data.get("source_url")
        else:
            logging.error(f"Ошибка получения URL изображения: {response.text}")
            return None

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title), CallbackQueryHandler(cancel)],
        BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_body)],
        CATEGORY: [CallbackQueryHandler(get_category)],
        TAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tags), CallbackQueryHandler(get_tags)],
        IMAGE: [MessageHandler(filters.PHOTO, get_image)],
        PREVIEW: [CallbackQueryHandler(preview_post)],  # Шаг превью
        PUBLISH: [CallbackQueryHandler(publish)],
        SCHEDULE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_schedule_date)],
        SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_schedule_time)]
    },
    fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cancel)]
)