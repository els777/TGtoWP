import httpx
from datetime import datetime
import logging
import os
from cachetools import TTLCache  # Для кеширования с временем жизни

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Кэш для категорий WordPress с временем жизни 1 час (3600 секунд)
categories_cache = TTLCache(maxsize=1, ttl=3600)

async def get_wp_categories():
    """Получение списка категорий из WordPress"""
    if "categories" in categories_cache:
        return categories_cache["categories"]  # Возвращаем закешированные категории
    
    url = f"{os.getenv('WP_URL')}/wp-json/wp/v2/categories"
    auth = httpx.BasicAuth(os.getenv('WP_USERNAME'), os.getenv('WP_PASSWORD'))
    async with httpx.AsyncClient() as client:
        response = await client.get(url, auth=auth)
        if response.status_code == 200:
            categories = response.json()
            categories_cache["categories"] = {str(cat["id"]): cat["name"] for cat in categories}
            return categories_cache["categories"]
        else:
            logging.error(f"Ошибка загрузки категорий: {response.text}")
            return {}

async def get_wp_tags():
    """Получение списка тегов из WordPress с обработкой пагинации"""
    url = f"{os.getenv('WP_URL')}/wp-json/wp/v2/tags"
    auth = httpx.BasicAuth(os.getenv('WP_USERNAME'), os.getenv('WP_PASSWORD'))
    all_tags = {}
    page = 1

    while True:
        params = {"per_page": 100, "page": page}  # Получаем до 100 тегов за раз
        async with httpx.AsyncClient() as client:
            response = await client.get(url, auth=auth, params=params)
            if response.status_code == 200:
                tags = response.json()
                if not tags:  # Если нет больше тегов, выходим из цикла
                    break
                # Нормализуем регистр для ключей
                for tag in tags:
                    all_tags[tag["name"].lower()] = tag["id"]
                page += 1  # Переходим к следующей странице
            else:
                logging.error(f"Ошибка загрузки тегов: {response.text}")
                return {}

    return all_tags

async def create_tag(tag_name):
    """Создание нового тега в WordPress"""
    url = f"{os.getenv('WP_URL')}/wp-json/wp/v2/tags"
    auth = httpx.BasicAuth(os.getenv('WP_USERNAME'), os.getenv('WP_PASSWORD'))
    data = {"name": tag_name}
    async with httpx.AsyncClient() as client:
        response = await client.post(url, auth=auth, json=data)
        if response.status_code == 201:
            new_tag = response.json()
            logging.info(f"Тег '{tag_name}' успешно создан. ID: {new_tag['id']}")
            return new_tag["id"]
        elif response.status_code == 400 and "term_exists" in response.text:
            # Тег уже существует, обновляем список тегов
            logging.warning(f"Тег '{tag_name}' уже существует. Обновление списка тегов...")
            wp_tags = await get_wp_tags()
            tag_name_normalized = tag_name.strip().lower()
            if tag_name_normalized in wp_tags:
                logging.info(f"Тег '{tag_name}' найден. ID: {wp_tags[tag_name_normalized]}.")
                return wp_tags[tag_name_normalized]
            else:
                logging.error(f"Не удалось найти ID для существующего тега '{tag_name}'.")
                return None
        else:
            logging.error(f"Не удалось создать тег '{tag_name}': {response.text}")
            return None

async def post_to_wp(data, publish_now):
    """Публикация поста в WordPress"""
    try:
        # Загрузка изображения
        media_id = await upload_image_to_wp(data.get("image"))
        if not media_id:
            return False
        
        # Получение ID тегов или создание новых
        wp_tags = await get_wp_tags()
        tag_ids = []

        for tag_name in data.get("tags", []):
            tag_name_normalized = tag_name.strip().lower()  # Нормализуем регистр
            found_tag = wp_tags.get(tag_name_normalized)

            if not found_tag:
                # Создание нового тега
                new_tag_id = await create_tag(tag_name)
                if new_tag_id:
                    tag_ids.append(new_tag_id)
                else:
                    logging.warning(f"Тег '{tag_name}' не был создан.")
            else:
                tag_ids.append(found_tag)

        # Создание основных данных поста
        post_data = {
            "title": data.get("title", ""),
            "content": data.get("body", ""),
            "categories": [int(data.get("category", 0))],
            "tags": tag_ids,  # Используем ID тегов
            "featured_media": media_id,
            "status": "publish" if publish_now else "future"
        }

        # Обработка даты публикации
        if not publish_now and "schedule_datetime" in data:
            post_data["date"] = data["schedule_datetime"].isoformat()

        # Отправка поста
        auth = httpx.BasicAuth(os.getenv('WP_USERNAME'), os.getenv('WP_PASSWORD'))
        url = f"{os.getenv('WP_URL')}/wp-json/wp/v2/posts"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, auth=auth, json=post_data)
            if response.status_code == 201:
                logging.info("Пост успешно создан.")
                return True
            else:
                logging.error(f"Ошибка создания поста: {response.text}")
                return False
    except Exception as e:
        logging.error(f"Ошибка при публикации поста: {e}")
        return False

async def upload_image_to_wp(image_url):
    """Загрузка изображения в WordPress"""
    try:
        auth = httpx.BasicAuth(os.getenv('WP_USERNAME'), os.getenv('WP_PASSWORD'))
        url = f"{os.getenv('WP_URL')}/wp-json/wp/v2/media"
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", image_url) as img_response:
                img_response.raise_for_status()
                image_data = await img_response.aread()  # Читаем содержимое изображения
                files = {"file": ("image.jpg", image_data, "image/jpeg")}
                
                # Отправка изображения
                upload_response = await client.post(url, auth=auth, files=files)
                upload_response.raise_for_status()
                return upload_response.json().get("id")
    except Exception as e:
        logging.error(f"Ошибка загрузки изображения: {e}")
        return None