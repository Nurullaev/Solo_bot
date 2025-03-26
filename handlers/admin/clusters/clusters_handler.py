import asyncio
from typing import Any

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from py3xui import AsyncApi

from backup import create_backup_and_send_to_admins
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import check_unique_server_name, get_servers
from filters.admin import IsAdminFilter
from handlers.keys.key_utils import create_key_on_cluster, create_client_on_server
from logger import logger
from .keyboard import (
    build_clusters_editor_kb,
    build_manage_cluster_kb,
    AdminClusterCallback,
    build_sync_cluster_kb,
)
from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb

router = Router()


class AdminClusterStates(StatesGroup):
    waiting_for_cluster_name = State()
    waiting_for_api_url = State()
    waiting_for_inbound_id = State()
    waiting_for_server_name = State()
    waiting_for_subscription_url = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "clusters"),
    IsAdminFilter(),
)
async def handle_servers(callback_query: CallbackQuery):
    servers = await get_servers()

    text = (
        "<b>🔧 Управление кластерами</b>\n\n"
        "<i>📌 Здесь вы можете добавить новый кластер.</i>\n\n"
        "<i>🌐 <b>Кластеры</b> — это пространство серверов, в пределах которого создается подписка.</i>\n"
        "💡 Если вы хотите выдавать по 1 серверу, то добавьте всего 1 сервер в кластер.\n\n"
        "<i>⚠️ <b>Важно:</b> Кластеры удаляются автоматически, если удалить все серверы внутри них.</i>\n\n"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_clusters_editor_kb(servers),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_clusters_add(callback_query: CallbackQuery, state: FSMContext):
    text = (
        "🔧 <b>Введите имя нового кластера:</b>\n\n"
        "<b>Имя должно быть уникальным!</b>\n"
        "<b>Имя не должно превышать 12 символов!</b>\n\n"
        "<i>Пример:</i> <code>cluster1</code> или <code>us_east_1</code>"
    )

    await callback_query.message.edit_text(text=text, reply_markup=build_admin_back_kb("clusters"))

    await state.set_state(AdminClusterStates.waiting_for_cluster_name)


@router.message(AdminClusterStates.waiting_for_cluster_name, IsAdminFilter())
async def handle_cluster_name_input(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(
            text="❌ Имя кластера не может быть пустым! Попробуйте снова.", reply_markup=build_admin_back_kb("clusters")
        )
        return

    if len(message.text) > 12:
        await message.answer(
            text="❌ Имя кластера должно превышать 12 символов! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    cluster_name = message.text.strip()
    await state.update_data(cluster_name=cluster_name)

    text = (
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию и номер сервера в имени.\n\n"
        "<i>Пример:</i> <code>de1</code>, <code>fra1</code>, <code>fi2</code>"
    )

    await message.answer(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_server_name)


@router.message(AdminClusterStates.waiting_for_server_name, IsAdminFilter())
async def handle_server_name_input(message: Message, state: FSMContext, session: Any):
    if not message.text:
        await message.answer(
            text="❌ Имя сервера не может быть пустым. Попробуйте снова.", reply_markup=build_admin_back_kb("clusters")
        )
        return

    server_name = message.text.strip()

    if len(server_name) > 12:
        await message.answer(
            text="❌ Имя сервера не должно превышать 12 символов. Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")

    if not await check_unique_server_name(server_name, session, cluster_name):
        await message.answer(
            text="❌ Сервер с таким именем уже существует. Пожалуйста, выберите другое имя.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    await state.update_data(server_name=server_name)

    text = (
        f"<b>Введите API URL для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Ссылку можно найти в поисковой строке браузера, при входе в 3X-UI.\n\n"
        "ℹ️ Формат API URL:\n"
        "<code>https://your_domain:port/panel_path/</code>"
    )

    await message.answer(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_api_url)


@router.message(AdminClusterStates.waiting_for_api_url, IsAdminFilter())
async def handle_api_url_input(message: Message, state: FSMContext):
    if not message.text or not message.text.strip().startswith("https://"):
        await message.answer(
            text="❌ API URL должен начинаться с <code>https://</code>. Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    api_url = message.text.strip().rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    await state.update_data(api_url=api_url)

    text = (
        f"<b>Введите subscription_url для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Ссылку можно найти в панели 3X-UI, в информации о клиенте.\n\n"
        "ℹ️ Формат Subscription URL:\n"
        "<code>https://your_domain:port_sub/sub_path/</code>"
    )

    await message.answer(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_subscription_url)


@router.message(AdminClusterStates.waiting_for_subscription_url, IsAdminFilter())
async def handle_subscription_url_input(message: Message, state: FSMContext):
    if not message.text or not message.text.strip().startswith("https://"):
        await message.answer(
            text="❌ subscription_url должен начинаться с <code>https://</code>. Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    subscription_url = message.text.strip().rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    await state.update_data(subscription_url=subscription_url)

    text = (
        f"<b>Введите inbound_id для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Это номер подключения vless в вашей панели 3x-ui. Обычно это <b>1</b> при чистой настройке по гайду.\n\n"
    )

    await message.answer(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.set_state(AdminClusterStates.waiting_for_inbound_id)


@router.message(AdminClusterStates.waiting_for_inbound_id, IsAdminFilter())
async def handle_inbound_id_input(message: Message, state: FSMContext):
    inbound_id = message.text.strip()

    if not inbound_id.isdigit():
        await message.answer(
            text="❌ inbound_id должен быть числовым значением. Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    api_url = user_data.get("api_url")
    subscription_url = user_data.get("subscription_url")

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO servers (cluster_name, server_name, api_url, subscription_url, inbound_id) 
        VALUES ($1, $2, $3, $4, $5)
        """,
        cluster_name,
        server_name,
        api_url,
        subscription_url,
        inbound_id,
    )
    await conn.close()

    await message.answer(
        text=f"✅ Кластер {cluster_name} и сервер {server_name} успешно добавлены!",
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.clear()


@router.callback_query(AdminClusterCallback.filter(F.action == "manage"), IsAdminFilter())
async def handle_clusters_manage(
    callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    await callback_query.message.edit_text(
        text=f"<b>🔧 Управление кластером {cluster_name}</b>",
        reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "availability"), IsAdminFilter())
async def handle_cluster_availability(
    callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    if not cluster_servers:
        await callback_query.message.edit_text(text=f"Кластер '{cluster_name}' не содержит серверов.")
        return

    text = (
        f"🖥️ Проверка доступности серверов для кластера {cluster_name}.\n\n"
        "Это может занять до 1 минуты, пожалуйста, подождите..."
    )

    await callback_query.message.edit_text(text=text)

    total_online_users = 0
    result_text = f"<b>🖥️ Проверка доступности серверов</b>\n\n⚙️ Кластер: <b>{cluster_name}</b>\n\n"

    for server in cluster_servers:
        xui = AsyncApi(server["api_url"], username=ADMIN_USERNAME, password=ADMIN_PASSWORD, logger=logger)

        try:
            await xui.login()
            online_users = len(await xui.client.online())
            total_online_users += online_users
            result_text += f"🌍 <b>{server['server_name']}</b> - онлайн: {online_users}\n"
        except Exception as e:
            result_text += f"❌ <b>{server['server_name']}</b> - ошибка: {e}\n"

    result_text += f"\n👥 Всего пользователей онлайн: {total_online_users}"

    await callback_query.message.edit_text(text=result_text, reply_markup=build_admin_back_kb("clusters"))


@router.callback_query(AdminClusterCallback.filter(F.action == "backup"), IsAdminFilter())
async def handle_clusters_backup(
    callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    for server in cluster_servers:
        xui = AsyncApi(
            server["api_url"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
            logger=logger,
        )
        await create_backup_and_send_to_admins(xui)

    text = (
        f"<b>Бэкап для кластера {cluster_name} был успешно создан и отправлен администраторам!</b>\n\n"
        f"🔔 <i>Бэкапы отправлены в боты панелей.</i>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync"), IsAdminFilter())
async def handle_sync(callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    await callback_query.message.answer(
        text=f"<b>🔄 Синхронизация кластера {cluster_name}</b>",
        reply_markup=build_sync_cluster_kb(cluster_servers, cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync-server"), IsAdminFilter())
async def handle_sync_server(callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any):
    server_name = callback_data.data

    try:
        query_keys = """
                SELECT s.*, k.tg_id, k.client_id, k.email, k.expiry_time
                FROM servers s
                JOIN keys k ON s.cluster_name = k.server_id
                WHERE s.server_name = $1;
            """
        keys_to_sync = await session.fetch(query_keys, server_name)

        if not keys_to_sync:
            await callback_query.message.answer(
                text=f"❌ Нет ключей для синхронизации в сервере {server_name}.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        text = f"<b>🔄 Синхронизация сервера {server_name}</b>\n\n🔑 Количество ключей: <b>{len(keys_to_sync)}</b>"

        await callback_query.message.answer(
            text=text,
        )

        semaphore = asyncio.Semaphore(2)
        for key in keys_to_sync:
            try:
                await create_client_on_server(
                    {
                        "api_url": key["api_url"],
                        "inbound_id": key["inbound_id"],
                        "server_name": key["server_name"],
                    },
                    key["tg_id"],
                    key["client_id"],
                    key["email"],
                    key["expiry_time"],
                    semaphore,
                )
                await asyncio.sleep(0.6)
            except Exception as e:
                logger.error(f"Ошибка при добавлении ключа {key['client_id']} в сервер {server_name}: {e}")

        await callback_query.message.answer(
            text=f"✅ Ключи успешно синхронизированы для сервера {server_name}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка синхронизации ключей для сервера {server_name}: {e}")
        await callback_query.message.answer(
            text=f"❌ Произошла ошибка при синхронизации: {e}", reply_markup=build_admin_back_kb("clusters")
        )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync-cluster"), IsAdminFilter())
async def handle_sync_cluster(callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any):
    cluster_name = callback_data.data

    try:
        query_keys = """
                SELECT tg_id, client_id, email, expiry_time
                FROM keys
                WHERE server_id = $1
            """
        keys_to_sync = await session.fetch(query_keys, cluster_name)

        if not keys_to_sync:
            await callback_query.message.answer(
                text=f"❌ Нет ключей для синхронизации в кластере {cluster_name}.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        text = f"<b>🔄 Синхронизация кластера {cluster_name}</b>\n\n🔑 Количество ключей: <b>{len(keys_to_sync)}</b>"

        await callback_query.message.answer(
            text=text,
        )

        for key in keys_to_sync:
            try:
                await create_key_on_cluster(
                    cluster_name,
                    key["tg_id"],
                    key["client_id"],
                    key["email"],
                    key["expiry_time"],
                )
                await asyncio.sleep(0.6)
            except Exception as e:
                logger.error(f"Ошибка при добавлении ключа {key['client_id']} в кластер {cluster_name}: {e}")

        await callback_query.message.answer(
            text=f"✅ Ключи успешно синхронизированы для кластера {cluster_name}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка синхронизации ключей в кластере {cluster_name}: {e}")
        await callback_query.message.answer(
            text=f"❌ Произошла ошибка при синхронизации: {e}", reply_markup=build_admin_back_kb("clusters")
        )
