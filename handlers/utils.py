import json
import os
import re
import secrets
import string

import aiofiles
import aiohttp
import asyncpg

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from config import DATABASE_URL

from bot import bot
from database import get_all_keys, get_servers
from logger import logger


async def get_usd_rate():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr-xml-daily.ru/daily_json.js") as response:
                if response.status == 200:
                    data = await response.text()
                    usd = float(json.loads(data)["Valute"]["USD"]["Value"])
                else:
                    usd = float(100)
    except Exception as e:
        logger.exception(f"Error fetching USD rate: {e}")
        usd = float(100)
    return usd


def sanitize_key_name(key_name: str) -> str:
    """
    Очищает название ключа, оставляя только допустимые символы.

    Args:
        key_name (str): Исходное название ключа.

    Returns:
        str: Очищенное название ключа в нижнем регистре.
    """
    return re.sub(r"[^a-z0-9@._-]", "", key_name.lower())


def generate_random_email(length: int = 6) -> str:
    """
    Генерирует случайный email с заданной длиной.

    Args:
        length (int, optional): Длина случайной строки. По умолчанию 6.

    Returns:
        str: Сгенерированная случайная строка.
    """
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length)) if length > 0 else ""


async def get_least_loaded_cluster() -> str:
    """
    Определяет кластер с наименьшей загрузкой.

    Returns:
        str: Идентификатор наименее загруженного кластера.
    """
    servers = await get_servers()
    server_to_cluster = {}
    cluster_loads = dict.fromkeys(servers.keys(), 0)
    for cluster_name, cluster_servers in servers.items():
        for server in cluster_servers:
            server_to_cluster[server["server_name"]] = cluster_name
    logger.info(f"Сопоставление серверов и кластеров: {server_to_cluster}")
    async with asyncpg.create_pool(DATABASE_URL) as pool:
        async with pool.acquire() as conn:
            keys = await get_all_keys(conn)
            for key in keys:
                server_id = key["server_id"]

                cluster_id = server_to_cluster.get(server_id, server_id)

                if cluster_id in cluster_loads:
                    cluster_loads[cluster_id] += 1
                else:
                    logger.warning(f"⚠️ Сервер {server_id} не найден в известных кластерах!")
    logger.info(f"Загруженность кластеров после запроса к БД: {cluster_loads}")
    if not cluster_loads:
        logger.warning("⚠️ В базе данных или конфигурации нет кластеров!")
        return "cluster1"
    least_loaded_cluster = min(cluster_loads, key=lambda k: (cluster_loads[k], k))
    logger.info(f"✅ Выбран наименее загруженный кластер: {least_loaded_cluster}")

    return least_loaded_cluster


async def handle_error(tg_id: int, callback_query: object | None = None, message: str = "") -> None:
    """
    Обрабатывает ошибку, отправляя сообщение пользователю.

    Args:
        tg_id (int): Идентификатор пользователя в Telegram.
        callback_query (Optional[object], optional): Объект запроса обратного вызова. По умолчанию None.
        message (str, optional): Текст сообщения об ошибке. По умолчанию пустая строка.
    """
    try:
        if callback_query and hasattr(callback_query, "message"):
            try:
                await bot.delete_message(chat_id=tg_id, message_id=callback_query.message.message_id)
            except Exception as delete_error:
                logger.warning(f"Не удалось удалить сообщение: {delete_error}")

        await bot.send_message(tg_id, message)

    except Exception as e:
        logger.error(f"Ошибка при обработке ошибки: {e}")


def format_time_until_deletion(seconds: int) -> str:
    if seconds <= 0:
        return "0 минут"

    days = seconds // (3600 * 24)
    hours = (seconds % (3600 * 24)) // 3600
    minutes = (seconds % 3600 + 59) // 60

    parts = []

    if days > 0:
        if days == 1:
            parts.append(f"{days} день")
        elif 2 <= days <= 4:
            parts.append(f"{days} дня")
        else:
            parts.append(f"{days} дней")

    if hours > 0:
        if hours == 1:
            parts.append(f"{hours} час")
        elif 2 <= hours <= 4:
            parts.append(f"{hours} часа")
        else:
            parts.append(f"{hours} часов")

    if minutes > 0 and days == 0:
        if minutes == 1:
            parts.append("1 минута")
        elif 2 <= minutes <= 4:
            parts.append(f"{minutes} минуты")
        else:
            parts.append(f"{minutes} минут")

    return " и ".join(parts) if parts else "менее минуты"


async def edit_or_send_message(
    target_message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    media_path: str = None,
    disable_web_page_preview: bool = False,
    force_text: bool = False,
):
    """
    Универсальная функция для редактирования исходного сообщения target_message.

    - Если media_path указан и существует, считается, что сообщение содержит фото, и используется редактирование медиа
      (замена фото и подписи) через edit_media. Если редактирование не удаётся, отправляется новое сообщение с фото.

    - Если media_path не указан:
        - Если force_text=False и target_message уже имеет caption, пытаемся отредактировать подпись (edit_caption).
        - Иначе (или если редактирование caption не удалось) — редактируем текст (edit_text).

    В случае неудачи fallback – отправка нового сообщения.
    """
    if media_path and os.path.isfile(media_path):
        async with aiofiles.open(media_path, "rb") as f:
            image_data = await f.read()
        media = InputMediaPhoto(
            media=BufferedInputFile(image_data, filename=os.path.basename(media_path)),
            caption=text,
        )
        try:
            await target_message.edit_media(media=media, reply_markup=reply_markup)
            return
        except Exception:
            await target_message.answer_photo(
                photo=BufferedInputFile(image_data, filename=os.path.basename(media_path)),
                caption=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
    else:
        if not force_text and target_message.caption is not None:
            try:
                await target_message.edit_caption(caption=text, reply_markup=reply_markup)
                return
            except Exception as e:
                logger.error(f"Ошибка редактирования подписи: {e}")
        try:
            await target_message.edit_text(
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
        except Exception:
            await target_message.answer(
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )


def convert_to_bytes(value: float, unit: str) -> int:
    """
    Конвертирует значение с указанной единицей измерения в байты.
    Args:
        value (float): Числовое значение.
        unit (str): Единица измерения ('KB', 'MB', 'GB', 'TB').
    Returns:
        int: Количество байт.
    """
    KB = 1024
    MB = KB * 1024
    GB = MB * 1024
    TB = GB * 1024
    units = {"KB": KB, "MB": MB, "GB": GB, "TB": TB}
    return int(value * units.get(unit.upper(), 1))
