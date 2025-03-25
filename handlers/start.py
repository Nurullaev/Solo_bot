import os

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import (
    CAPTCHA_ENABLE,
    CHANNEL_EXISTS,
    CHANNEL_ID,
    CHANNEL_REQUIRED,
    CHANNEL_URL,
    DONATIONS_ENABLE,
    SUPPORT_CHAT_URL,
)

from bot import bot
from database import (
    add_connection,
    add_referral,
    check_connection_exists,
    get_referral_by_referred_id,
    get_trial,
    update_balance,
)
from handlers.captcha import generate_captcha
from handlers.keys.key_management import create_key
from handlers.texts import (
    COUPON_SUCCESS_MSG,
    GIFT_ALREADY_USED_OR_NOT_EXISTS_MSG,
    NEW_REFERRAL_NOTIFICATION,
    NOT_SUBSCRIBED_YET_MSG,
    REFERRAL_SUCCESS_MSG,
    SUBSCRIPTION_CHECK_ERROR_MSG,
    SUBSCRIPTION_CONFIRMED_MSG,
    SUBSCRIPTION_REQUIRED_MSG,
    WELCOME_TEXT,
    get_about_vpn,
)
from keyboards.admin.panel_kb import AdminPanelCallback
from logger import logger

from .utils import edit_or_send_message


router = Router()


@router.callback_query(F.data == "start")
async def handle_start_callback_query(
    callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool, captcha: bool = False
):
    await start_command(callback_query.message, state, session, admin, captcha)


@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, session: Any, admin: bool, captcha: bool = True):
    """Обрабатывает команду /start, включая логику проверки подписки, рефералов и подарков."""
    logger.info(f"Вызвана функция start_command для пользователя {message.chat.id}")

    if CAPTCHA_ENABLE and captcha:
        captcha_data = await generate_captcha(message, state)
        await edit_or_send_message(
            target_message=message,
            text=captcha_data["text"],
            reply_markup=captcha_data["markup"],
        )
        return

    state_data = await state.get_data()
    text_to_process = state_data.get("original_text", message.text)

    if CHANNEL_EXISTS and CHANNEL_REQUIRED:
        try:
            member = await bot.get_chat_member(CHANNEL_ID, message.chat.id)
            if member.status not in ["member", "administrator", "creator"]:
                await state.update_data(original_text=text_to_process)
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
                await edit_or_send_message(
                    target_message=message,
                    text=SUBSCRIPTION_REQUIRED_MSG,
                    reply_markup=builder.as_markup(),
                )
                return
            else:
                logger.info(
                    f"Пользователь {message.chat.id} подписан на канал (статус: {member.status}). Продолжаем работу."
                )
        except Exception as e:
            logger.error(f"Ошибка проверки подписки пользователя {message.chat.id}: {e}")
            await state.update_data(start_text=text_to_process)
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
            await edit_or_send_message(
                target_message=message,
                text=SUBSCRIPTION_REQUIRED_MSG,
                reply_markup=builder.as_markup(),
            )
            return
    await process_start_logic(message, state, session, admin, text_to_process)


async def process_start_logic(
    message: Message, state: FSMContext, session: Any, admin: bool, text_to_process: str = None
):
    text = text_to_process if text_to_process is not None else message.text
    if text:
        try:
            connection_exists = await check_connection_exists(message.chat.id)
            logger.info(f"Проверка существования подключения: {connection_exists}")

            if not connection_exists:
                await add_connection(tg_id=message.chat.id, session=session)
                logger.info(f"Пользователь {message.chat.id} успешно добавлен в базу данных.")

            if "coupons_" in text:
                logger.info(f"Обнаружена ссылка на купон: {text}")
                coupon_code = text.split("coupons_")[1].strip()
                logger.info(f"Пользователь {message.chat.id} ввёл купон: {coupon_code}")

                coupon = await session.fetchrow(
                    "SELECT id, code, amount, usage_limit, usage_count, is_used FROM coupons WHERE code = $1",
                    coupon_code,
                )
                if coupon is None:
                    logger.warning(f"Купон {coupon_code} не найден.")
                    await message.answer("❌ Купон не найден!")
                    return await show_start_menu(message, admin, session)

                usage_exists = await session.fetchval(
                    "SELECT 1 FROM coupon_usages WHERE coupon_id = $1 AND user_id = $2",
                    coupon["id"],
                    message.chat.id,
                )
                if usage_exists:
                    logger.info(f"Пользователь {message.chat.id} уже активировал купон {coupon_code}.")
                    await message.answer("❌ Вы уже использовали этот купон!")
                    return await show_start_menu(message, admin, session)

                if coupon["is_used"] or coupon["usage_count"] >= coupon["usage_limit"]:
                    logger.info(f"Купон {coupon_code} уже использован или исчерпан.")
                    await message.answer("❌ Этот купон уже использован!")
                    return await show_start_menu(message, admin, session)

                await update_balance(message.chat.id, coupon["amount"])
                logger.info(f"Начислено {coupon['amount']} единиц для пользователя {message.chat.id}")

                new_usage_count = coupon["usage_count"] + 1
                is_used = new_usage_count >= coupon["usage_limit"]

                await session.execute(
                    "UPDATE coupons SET usage_count = $1, is_used = $2 WHERE code = $3",
                    new_usage_count,
                    is_used,
                    coupon_code,
                )

                await session.execute(
                    "INSERT INTO coupon_usages (coupon_id, user_id, used_at) VALUES ($1, $2, NOW())",
                    coupon["id"],
                    message.chat.id,
                )

                logger.info(
                    f"Купон {coupon_code} успешно использован пользователем {message.chat.id}, начислено {coupon['amount']} RUB."
                )
                await message.answer(COUPON_SUCCESS_MSG.format(amount=coupon["amount"]))
                return await show_start_menu(message, admin, session)

            if "gift_" in text:
                logger.info(f"Обнаружена ссылка на подарок: {text}")
                parts = text.split("gift_")[1].split("_")
                if len(parts) < 2:
                    logger.error("Неверный формат ссылки на подарок: недостаточно частей после 'gift_'")
                    await message.answer("❌ Неверный формат ссылки на подарок.")
                    return await show_start_menu(message, admin, session)
                gift_id = parts[0]
                recipient_tg_id = message.chat.id

                gift_info = await session.fetchrow(
                    """
                    SELECT sender_tg_id, selected_months, expiry_time, is_used, recipient_tg_id 
                    FROM gifts WHERE gift_id = $1
                    """,
                    gift_id,
                )

                if gift_info is None:
                    logger.warning(f"Подарок с ID {gift_id} уже был использован или не существует.")
                    await message.answer(GIFT_ALREADY_USED_OR_NOT_EXISTS_MSG)
                    return await show_start_menu(message, admin, session)

                if gift_info["is_used"]:
                    logger.warning(f"Подарок с ID {gift_id} уже был активирован ранее.")
                    await message.answer("Этот подарок уже был использован.")
                    return await show_start_menu(message, admin, session)

                if gift_info["sender_tg_id"] == recipient_tg_id:
                    logger.warning(f"Пользователь {recipient_tg_id} попытался активировать свой же подарок.")
                    await message.answer("❌ Вы не можете получить подарок от самого себя.")
                    return await show_start_menu(message, admin, session)

                if gift_info["recipient_tg_id"] is not None:
                    logger.warning(
                        f"Подарок {gift_id} уже привязан к другому пользователю ({gift_info['recipient_tg_id']})."
                    )
                    await message.answer("❌ Этот подарок уже был активирован другим пользователем.")
                    return await show_start_menu(message, admin, session)

                if not connection_exists:
                    await add_referral(recipient_tg_id, gift_info["sender_tg_id"], session)
                    logger.info(
                        f"Пользователь {recipient_tg_id} теперь является рефералом отправителя {gift_info['sender_tg_id']}."
                    )

                await session.execute(
                    """
                    UPDATE connections SET trial = 1 WHERE tg_id = $1
                    """,
                    recipient_tg_id,
                )

                selected_months = gift_info["selected_months"]
                expiry_time = gift_info["expiry_time"].replace(tzinfo=None)

                logger.info(f"Подарок с ID {gift_id} успешно найден для пользователя {recipient_tg_id}.")

                await create_key(recipient_tg_id, expiry_time, state, session, message)
                logger.info(f"Ключ создан для пользователя {recipient_tg_id} на срок {selected_months} месяцев.")

                await session.execute(
                    """
                    UPDATE gifts SET is_used = TRUE, recipient_tg_id = $1 
                    WHERE gift_id = $2
                    """,
                    recipient_tg_id,
                    gift_id,
                )

                await message.answer(
                    f"🎉 Ваш подарок на {selected_months} "
                    f"{'месяц' if selected_months == 1 else 'месяца' if selected_months in [2, 3, 4] else 'месяцев'} активирован!"
                )
                logger.info(f"Подарок на {selected_months} месяцев активирован для пользователя {recipient_tg_id}.")
                return

            elif "referral_" in text:
                try:
                    referrer_tg_id = int(text.split("referral_")[1])
                    if connection_exists:
                        logger.info(f"Пользователь {message.chat.id} уже зарегистрирован и не может стать рефералом.")
                        await message.answer("❌ Вы уже зарегистрированы и не можете использовать реферальную ссылку.")
                        return await show_start_menu(message, admin, session)

                    if referrer_tg_id == message.chat.id:
                        logger.warning(f"Пользователь {message.chat.id} попытался стать рефералом самого себя.")
                        await message.answer("❌ Вы не можете быть рефералом самого себя.")
                        return await show_start_menu(message, admin, session)

                    existing_referral = await get_referral_by_referred_id(message.chat.id, session)
                    if existing_referral:
                        logger.info(f"Реферал с ID {message.chat.id} уже существует.")
                        return await show_start_menu(message, admin, session)

                    await add_referral(message.chat.id, referrer_tg_id, session)
                    logger.info(f"Реферал {message.chat.id} использовал ссылку от пользователя {referrer_tg_id}")
                    await message.answer(REFERRAL_SUCCESS_MSG.format(referrer_tg_id=referrer_tg_id))
                    try:
                        await bot.send_message(
                            referrer_tg_id, NEW_REFERRAL_NOTIFICATION.format(referred_id=message.chat.id)
                        )
                        logger.info(
                            f"Уведомление отправлено пользователю {referrer_tg_id} о новом реферале {message.chat.id}"
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление пригласившему ({referrer_tg_id}): {e}")

                    return await show_start_menu(message, admin, session)

                except (ValueError, IndexError) as e:
                    logger.error(f"Ошибка при обработке реферальной ссылки: {e}")
                return

            else:
                logger.info(f"Пользователь {message.chat.id} зашел без реферальной ссылки, подарка или купона.")

            await show_start_menu(message, admin, session)

        except (ValueError, IndexError) as e:
            logger.error(f"Ошибка при обработке сообщения пользователя {message.chat.id}: {e}")
            await message.answer("❌ Произошла ошибка. Пожалуйста, попробуйте снова.")
    else:
        await show_start_menu(message, admin, session)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool):
    user_id = callback_query.from_user.id
    logger.info(f"[CALLBACK] Получен callback 'check_subscription' от пользователя {user_id}")
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.info(f"[CALLBACK] Статус подписки пользователя {user_id}: {member.status}")

        if member.status not in ["member", "administrator", "creator"]:
            await callback_query.answer(NOT_SUBSCRIBED_YET_MSG, show_alert=True)
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
            await callback_query.message.edit_text(
                SUBSCRIPTION_REQUIRED_MSG,
                reply_markup=builder.as_markup(),
            )
        else:
            await callback_query.answer(SUBSCRIPTION_CONFIRMED_MSG)
            data = await state.get_data()
            original_text = data.get("original_text")
            if not original_text:
                original_text = callback_query.message.text
            await process_start_logic(callback_query.message, state, session, admin, text_to_process=original_text)
            logger.info(f"[CALLBACK] Завершен вызов process_start_logic для пользователя {user_id}")
    except Exception as e:
        logger.error(f"[CALLBACK] Ошибка проверки подписки для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer(SUBSCRIPTION_CHECK_ERROR_MSG, show_alert=True)


async def show_start_menu(message: Message, admin: bool, session: Any):
    """Функция для отображения стандартного меню через редактирование сообщения.
    Если редактирование не удалось, отправляем новое сообщение."""
    logger.info(f"Показываю главное меню для пользователя {message.chat.id}")

    image_path = os.path.join("img", "pic.jpg")
    builder = InlineKeyboardBuilder()

    if session is not None:
        trial_status = await get_trial(message.chat.id, session)
        logger.info(f"Trial status для {message.chat.id}: {trial_status}")
        if trial_status == 0:
            builder.row(InlineKeyboardButton(text="🔗 Подключить VPN", callback_data="create_key"))
    else:
        logger.warning(f"Сессия базы данных отсутствует, пропускаем проверку триала для {message.chat.id}")

    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    if CHANNEL_EXISTS:
        builder.row(
            InlineKeyboardButton(text="📞 Поддержка", url=SUPPORT_CHAT_URL),
            InlineKeyboardButton(text="📢 Канал", url=CHANNEL_URL),
        )
    else:
        builder.row(InlineKeyboardButton(text="📞 Поддержка", url=SUPPORT_CHAT_URL))

    if admin:
        builder.row(
            InlineKeyboardButton(text="🔧 Администратор", callback_data=AdminPanelCallback(action="admin").pack())
        )

    builder.row(InlineKeyboardButton(text="🌐 О VPN", callback_data="about_vpn"))

    await edit_or_send_message(
        target_message=message,
        text=WELCOME_TEXT,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))
    builder.row(InlineKeyboardButton(text="📞 Техническая поддержка", url=SUPPORT_CHAT_URL))
    if CHANNEL_EXISTS:
        builder.row(InlineKeyboardButton(text="📢 Канал", url=CHANNEL_URL))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    text = get_about_vpn("3.2.3-minor")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=None,
        force_text=False,
    )
