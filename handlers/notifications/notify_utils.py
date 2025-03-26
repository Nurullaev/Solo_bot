import os

import aiofiles
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from logger import logger


async def send_notification(
    bot: Bot,
    tg_id: int,
    image_filename: str,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    """
    Отправляет уведомление пользователю.

    Args:
        bot: Экземпляр бота для отправки сообщений
        tg_id: Telegram ID пользователя
        image_filename: Имя файла изображения в директории img
        caption: Текст сообщения
        keyboard: Клавиатура для сообщения (опционально)

    Returns:
        bool: True если сообщение успешно отправлено, False в случае ошибки
    """
    photo_path = os.path.join("img", image_filename)

    if os.path.isfile(photo_path):
        return await _send_photo_notification(bot, tg_id, photo_path, image_filename, caption, keyboard)
    else:
        logger.warning(f"Файл с изображением не найден: {photo_path}")
        return await _send_text_notification(bot, tg_id, caption, keyboard)


async def _send_photo_notification(
    bot: Bot,
    tg_id: int,
    photo_path: str,
    image_filename: str,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    """Отправляет уведомление с изображением."""
    try:
        async with aiofiles.open(photo_path, "rb") as image_file:
            image_data = await image_file.read()
        buffered_photo = BufferedInputFile(image_data, filename=image_filename)
        await bot.send_photo(tg_id, buffered_photo, caption=caption, reply_markup=keyboard)
        return True
    except TelegramForbiddenError:
        logger.error(f"Пользователь {tg_id} заблокировал бота")
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки фото для пользователя {tg_id}: {e}")
        return await _send_text_notification(bot, tg_id, caption, keyboard)


async def _send_text_notification(
    bot: Bot,
    tg_id: int,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    """Отправляет текстовое уведомление."""
    try:
        await bot.send_message(tg_id, caption, reply_markup=keyboard)
        return True
    except TelegramForbiddenError:
        logger.error(f"Пользователь {tg_id} заблокировал бота")
        return False
    except Exception as e:
        logger.error(f"Неизвестная ошибка при отправке сообщения для пользователя {tg_id}: {e}")
        return False
