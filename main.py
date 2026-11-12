import asyncio
import random
import logging
import os
import time
import json
import html

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ChatMemberUpdated,
    ReactionTypeEmoji
)
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest

from pyrogram import Client
from pyrogram.handlers import MessageHandler
from pyrogram.errors import SessionPasswordNeeded, FloodWait, UserAlreadyParticipant
import pyrogram.utils
import pyrogram.raw.types
import pyrogram.raw.functions

def get_peer_type_new(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"

pyrogram.utils.get_peer_type = get_peer_type_new

import config
import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.MANAGER_BOT_TOKEN)
dp = Dispatcher()

active_clients = {}
processed_messages = set()
pending_logins = {}
cached_bot_username = ""

class AccountState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_otp = State()
    waiting_for_2fa = State()

class ChannelState(StatesGroup):
    waiting_for_link = State()

class AdminJoinState(StatesGroup):
    waiting_for_link = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()
    confirm_send = State()

class AdminUserState(StatesGroup):
    waiting_for_user_id = State()

class AdminState(StatesGroup):
    waiting_for_new_admin_id = State()

def get_main_menu_markup(user_id: int, bot_username: str, is_admin_user: bool) -> InlineKeyboardMarkup:
    uname = bot_username or cached_bot_username or "DumblumbbBot"
    ch_url = f"https://t.me/{uname}?startchannel=bot_administrators&admin=post_messages+edit_messages+delete_messages+invite_users+manage_chat"
    grp_url = f"https://t.me/{uname}?startgroup=bot_administrators&admin=post_messages+edit_messages+delete_messages+invite_users+manage_chat"
    
    keyboard = [
        [
            InlineKeyboardButton(text="Add to Channel", url=ch_url, style="primary"),
            InlineKeyboardButton(text="Add to Group", url=grp_url, style="primary")
        ],
        [
            InlineKeyboardButton(text="Target Chats", callback_data="cmd_my_channels", style="primary"),
            InlineKeyboardButton(text="Connect Link / ID", callback_data="cmd_set_channel", style="success")
        ],
        [
            InlineKeyboardButton(text="User Guide", callback_data="cmd_guide", style="primary"),
            InlineKeyboardButton(text="Refresh Dashboard", callback_data="cmd_refresh", style="success")
        ]
    ]
    if is_admin_user:
        keyboard.append([InlineKeyboardButton(text="Admin Control Panel", callback_data="cmd_admin_panel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back to Dashboard", callback_data="cmd_main_menu", style="danger")]
    ])

def get_admin_panel_markup(approval_mode: bool) -> InlineKeyboardMarkup:
    mode_text = "Mode: Manual Approval" if approval_mode else "Mode: Auto-Approve"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Manage Admins", callback_data="adm_manage_admins", style="primary"),
            InlineKeyboardButton(text="Add Userbot", callback_data="cmd_add_acc", style="success")
        ],
        [
            InlineKeyboardButton(text="Manage Userbots", callback_data="cmd_manage_bots", style="primary"),
            InlineKeyboardButton(text="Join Chat All Bots", callback_data="cmd_admin_join", style="success")
        ],
        [
            InlineKeyboardButton(text="Manage Users", callback_data="adm_list_users_0", style="primary"),
            InlineKeyboardButton(text=mode_text, callback_data="adm_toggle_mode", style="primary")
        ],
        [
            InlineKeyboardButton(text="Broadcast Message", callback_data="adm_broadcast", style="success"),
            InlineKeyboardButton(text="Find User by ID", callback_data="adm_find_user", style="primary")
        ],
        [
            InlineKeyboardButton(text="Bot Statistics", callback_data="adm_stats", style="primary"),
            InlineKeyboardButton(text="Refresh Panel", callback_data="cmd_admin_panel", style="success")
        ],
        [
            InlineKeyboardButton(text="Back to Dashboard", callback_data="cmd_main_menu", style="danger")
        ]
    ])

async def safe_edit_text(message: Message, text: str, **kwargs):
    try:
        if "parse_mode" not in kwargs:
            kwargs["parse_mode"] = ParseMode.HTML
        return await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return message
        raise e
    except Exception:
        return message

async def safe_edit_reply_markup(message: Message, **kwargs):
    try:
        return await message.edit_reply_markup(**kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return message
        raise e
    except Exception:
        return message

def build_dashboard_text(user_data: dict, user_id: int, is_admin_user: bool) -> str:
    channels = user_data.get("channels", [])
    safe_name = html.escape(str(user_data.get("first_name", "User")))
    
    if is_admin_user:
        status_text = "Administrator"
    elif user_data.get("status") == "approved":
        status_text = "Approved User"
    else:
        status_text = "Pending Approval"
        
    return (
        f"<b>REACTION ENGINE DASHBOARD</b>\n\n"
        f"Welcome, <b>{safe_name}</b>\n\n"
        f"<blockquote>"
        f"<b>Status:</b> {status_text}\n"
        f"<b>Account ID:</b> <code>{user_id}</code>\n"
        f"<b>Connected Targets:</b> <code>{len(channels)}</code>\n"
        f"<b>Reaction Engine:</b> Active"
        f"</blockquote>\n\n"
        f"<i>Select an option below to manage your target chats:</i>"
    )

cached_chat_emojis = {}

async def get_chat_available_emojis(chat_id: int) -> list:
    key = str(chat_id)
    if key in cached_chat_emojis and cached_chat_emojis[key]:
        return cached_chat_emojis[key]
        
    emojis = []
    for client in list(active_clients.values()):
        if client.is_connected:
            try:
                peer = await client.resolve_peer(chat_id)
                full = await client.invoke(pyrogram.raw.functions.channels.GetFullChannel(channel=peer))
                avail = full.full_chat.available_reactions
                if isinstance(avail, pyrogram.raw.types.ChatReactionsSome):
                    emojis = [r.emoticon for r in avail.reactions if hasattr(r, "emoticon")]
                elif isinstance(avail, pyrogram.raw.types.ChatReactionsAll):
                    emojis = list(config.DEFAULT_EMOJIS)
                if emojis:
                    cached_chat_emojis[key] = emojis
                    return emojis
            except Exception:
                pass
            break
            
    if not emojis:
        emojis = list(config.DEFAULT_EMOJIS)
    cached_chat_emojis[key] = emojis
    return emojis

async def safe_bot_react(chat_id: int, msg_id: int, candidate_emojis: list) -> bool:
    for emoji in candidate_emojis:
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=msg_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
            return True
        except Exception:
            continue
    return False

async def safe_react(client: Client, chat_id: int, msg_id: int, candidate_emojis: list) -> bool:
    for emoji in candidate_emojis:
        try:
            await client.send_reaction(chat_id=chat_id, message_id=msg_id, emoji=emoji)
            return True
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await client.send_reaction(chat_id=chat_id, message_id=msg_id, emoji=emoji)
                return True
            except Exception:
                pass
        except Exception:
            continue
    return False

async def dispatch_reactions_for_chat(chat_id: int, msg_id: int):
    target_chats = database.get_all_target_chat_ids()
    if chat_id not in target_chats and str(chat_id) not in target_chats:
        return
        
    candidate_emojis = await get_chat_available_emojis(chat_id)
    shuffled_bot = list(candidate_emojis)
    random.shuffle(shuffled_bot)
    asyncio.create_task(safe_bot_react(chat_id, msg_id, shuffled_bot))
    
    for acc_id, client in list(active_clients.items()):
        if client.is_connected:
            shuffled_acc = list(candidate_emojis)
            random.shuffle(shuffled_acc)
            asyncio.create_task(safe_react(client, chat_id, msg_id, shuffled_acc))
            await asyncio.sleep(0.3)

async def handle_pyrogram_message(client, message):
    chat_id = message.chat.id
    target_chats = database.get_all_target_chat_ids()
    if chat_id in target_chats or str(chat_id) in target_chats:
        msg_key = f"{chat_id}_{message.id}"
        if msg_key not in processed_messages:
            processed_messages.add(msg_key)
            if len(processed_messages) > 2000:
                processed_messages.clear()
            asyncio.create_task(dispatch_reactions_for_chat(chat_id, message.id))

async def start_all_clients():
    accounts = database.get_all_accounts()
    for acc in accounts:
        acc_id = acc["user_id"]
        try:
            c = Client(
                f"session_{acc_id}",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=acc["session_string"]
            )
            c.add_handler(MessageHandler(handle_pyrogram_message))
            await c.start()
            
            try:
                async for _ in c.get_dialogs():
                    pass
            except Exception:
                pass
                
            active_clients[acc_id] = c
            logger.info(f"Started userbot {acc.get('name')} (ID: {acc_id})")
        except Exception as e:
            logger.error(f"Failed to start userbot {acc_id}: {e}")

@dp.message(Command("start"))
async def handle_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    user_data = database.create_or_update_user(user_id, username, first_name)
    is_admin_user = database.is_admin(user_id)
    
    if is_admin_user or user_data.get("status") == "approved":
        text = build_dashboard_text(user_data, user_id, is_admin_user)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_markup(user_id, cached_bot_username, is_admin_user))
        return
        
    if user_data.get("status") == "banned":
        await message.answer(
            "<b>Access Restricted</b>\n\n"
            "<blockquote>Your account has been restricted by the Administrator. You are not permitted to use this bot.</blockquote>",
            parse_mode=ParseMode.HTML
        )
        return

    safe_name = html.escape(str(first_name or "User"))
    safe_uname = html.escape(str(username or "None"))

    await message.answer(
        "<b>Access Request Submitted</b>\n\n"
        "<blockquote>Your access request has been forwarded to the Administrator for approval.\n"
        "You will receive an instant notification right here once your access is approved.</blockquote>\n\n"
        f"<b>Your User ID:</b> <code>{user_id}</code>\n"
        f"<b>Name:</b> {safe_name}",
        parse_mode=ParseMode.HTML
    )
    
    admins = database.get_admins()
    for admin_id in admins:
        try:
            alert_markup = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Approve User", callback_data=f"adm_appr_{user_id}", style="success"),
                    InlineKeyboardButton(text="Reject User", callback_data=f"adm_rejc_{user_id}", style="danger")
                ]
            ])
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    "<b>New Access Approval Request</b>\n\n"
                    f"<blockquote><b>Name:</b> {safe_name}\n"
                    f"<b>Username:</b> @{safe_uname}\n"
                    f"<b>User ID:</b> <code>{user_id}</code></blockquote>\n\n"
                    "<i>Select an action below to grant or deny access:</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=alert_markup
            )
        except Exception:
            pass

@dp.message(Command("admin"))
async def handle_admin_panel(message: Message):
    user_id = message.from_user.id
    if database.is_admin(user_id):
        settings = database.get_settings()
        markup = get_admin_panel_markup(settings.get("approval_required", True))
        stats = database.get_stats()
        await message.answer(
            f"<b>ADMINISTRATOR CONTROL PANEL</b>\n\n"
            f"<blockquote><b>Total Users:</b> <code>{stats['total_users']}</code>\n"
            f"<b>Approved Users:</b> <code>{stats['approved']}</code>\n"
            f"<b>Pending Users:</b> <code>{stats['pending']}</code>\n"
            f"<b>Banned Users:</b> <code>{stats['banned']}</code>\n"
            f"<b>Total Userbots:</b> <code>{stats['total_accounts']}</code>\n"
            f"<b>Target Channels/Groups:</b> <code>{stats['total_channels']}</code></blockquote>\n\n"
            f"<i>Control system mode, broadcast updates, or manage userbots, admins, and users:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
    else:
        await message.answer(
            "<b>Access Restricted</b>\n\n"
            "<blockquote>You do not have Administrator permissions to access this panel.</blockquote>",
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data == "cmd_main_menu")
@dp.callback_query(F.data == "cmd_refresh")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    user_data = database.get_user(user_id)
    if not user_data:
        user_data = database.create_or_update_user(user_id, callback.from_user.username, callback.from_user.first_name)
    is_admin_user = database.is_admin(user_id)
    
    text = build_dashboard_text(user_data, user_id, is_admin_user)
    await safe_edit_text(
        callback.message,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_markup(user_id, cached_bot_username, is_admin_user)
    )
    await callback.answer()

@dp.callback_query(F.data == "cmd_guide")
async def cb_guide(callback: CallbackQuery):
    guide_text = (
        "<b>BOT USAGE & SETUP GUIDE</b>\n\n"
        "<blockquote>"
        "<b>Step 1: Add to Channel or Group</b>\n"
        "• Tap <b>'Add to Channel or Group'</b> to add the bot as Admin with 1 click.\n"
        "• The bot automatically captures the Chat ID and registers it under your account.\n\n"
        "<b>Step 2: Private and Public Chat Support</b>\n"
        "• If it's a private chat, userbots join via invite link automatically.\n"
        "• If it's a public chat, userbots join via username automatically.\n"
        "• You can also tap <b>'Connect Link / ID'</b> and send any link or ID manually.\n\n"
        "<b>Step 3: Instant Automatic Reactions</b>\n"
        "• As soon as any new message or post is sent, all active userbots will automatically give random reactions.\n"
        "</blockquote>\n\n"
        "<i>Everything runs 24/7 fully automatically.</i>"
    )
    await safe_edit_text(callback.message, guide_text, parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
    await callback.answer()

@dp.callback_query(F.data == "adm_manage_admins")
async def cb_manage_admins(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        await callback.answer("Admin access required.", show_alert=True)
        return
        
    admins = database.get_admins()
    text = (
        f"<b>ADMINISTRATOR MANAGEMENT ({len(admins)})</b>\n\n"
        "<blockquote>Current administrators with full rights:</blockquote>\n\n"
    )
    keyboard = []
    for a_uid in admins:
        u_info = database.get_user(a_uid)
        fname = html.escape(str(u_info.get("first_name", "Admin") if u_info else "Admin"))
        is_owner = a_uid in config.ADMIN_IDS
        role_label = "Owner" if is_owner else "Admin"
        
        text += f"• <b>{fname}</b> (<code>{a_uid}</code>) - {role_label}\n"
        if not is_owner and a_uid != user_id:
            keyboard.append([
                InlineKeyboardButton(text=f"Remove Admin ({a_uid})", callback_data=f"adm_remove_admin_{a_uid}", style="danger")
            ])
            
    keyboard.append([InlineKeyboardButton(text="Add New Admin", callback_data="adm_add_admin_prompt", style="success")])
    keyboard.append([InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="primary")])
    
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(F.data == "adm_add_admin_prompt")
async def cb_add_admin_prompt(callback: CallbackQuery, state: FSMContext):
    if not database.is_admin(callback.from_user.id):
        return
    await safe_edit_text(callback.message, 
        "<b>Add New Administrator</b>\n\n"
        "<blockquote>Send the Telegram User ID (numbers only) to grant full Admin rights:</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Cancel", callback_data="adm_manage_admins", style="danger")]
        ])
    )
    await state.set_state(AdminState.waiting_for_new_admin_id)
    await callback.answer()

@dp.message(AdminState.waiting_for_new_admin_id)
async def process_new_admin_id(message: Message, state: FSMContext):
    try:
        new_admin_uid = int(message.text.strip())
    except Exception:
        await message.answer("Invalid ID format. Please send digits only.")
        return
        
    database.add_admin(new_admin_uid)
    database.set_user_status(new_admin_uid, "approved")
    await state.clear()
    
    try:
        await bot.send_message(
            chat_id=new_admin_uid,
            text=(
                "<b>Administrator Privileges Granted</b>\n\n"
                "<blockquote>You have been granted Administrator rights. Use /start or /admin to access the control panel.</blockquote>"
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
        
    await message.answer(
        f"<b>Admin Added Successfully</b>\n\n"
        f"<blockquote>User ID <code>{new_admin_uid}</code> has been granted full Administrator privileges.</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Back to Manage Admins", callback_data="adm_manage_admins", style="primary")]
        ])
    )

@dp.callback_query(F.data.startswith("adm_remove_admin_"))
async def cb_remove_admin(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        return
    target_uid = int(callback.data.replace("adm_remove_admin_", ""))
    if database.remove_admin(target_uid):
        await callback.answer("Admin removed successfully.", show_alert=True)
        try:
            await bot.send_message(
                chat_id=target_uid,
                text="<b>Admin Privileges Revoked</b>\n\n<blockquote>Your administrator privileges have been removed.</blockquote>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    else:
        await callback.answer("Cannot remove this admin.", show_alert=True)
    await cb_manage_admins(callback)

@dp.callback_query(F.data == "cmd_manage_bots")
async def cb_manage_bots(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        await callback.answer("Admin access required.", show_alert=True)
        return
        
    accounts = database.get_all_accounts()
    
    if not accounts:
        text = (
            "<b>CENTRAL USERBOT POOL (0)</b>\n\n"
            "<blockquote>No userbot accounts connected yet. Tap below to add your first userbot.</blockquote>"
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Add Userbot", callback_data="cmd_add_acc", style="success")],
            [InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="danger")]
        ])
        await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        await callback.answer()
        return

    text = f"<b>CENTRAL USERBOT POOL ({len(accounts)})</b>\n\n"
    text += "<blockquote>All approved users receive reactions from these userbot accounts:</blockquote>\n\n"
    keyboard = []
    
    for idx, acc in enumerate(accounts, 1):
        acc_uid = acc.get("user_id")
        is_live = acc_uid in active_clients and active_clients[acc_uid].is_connected
        live_icon = "Online" if is_live else "Idle"
        
        acc_name = html.escape(str(acc.get("name", "Account")))
        text += (
            f"<b>{idx}. {acc_name}</b>\n"
            f"• Mobile: <code>{acc.get('mobile_number')}</code>\n"
            f"• User ID: <code>{acc_uid}</code>\n"
            f"• Status: {live_icon}\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(text=f"Delete {acc.get('name', 'Account')[:16]}", callback_data=f"del_acc_{acc_uid}", style="danger")
        ])
        
    keyboard.append([InlineKeyboardButton(text="Add Another Userbot", callback_data="cmd_add_acc", style="success")])
    keyboard.append([InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="primary")])
    
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(F.data.startswith("del_acc_"))
async def cb_delete_account(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        await callback.answer("Admin access required.", show_alert=True)
        return
        
    acc_uid = int(callback.data.replace("del_acc_", ""))
    
    if acc_uid in active_clients:
        try:
            await active_clients[acc_uid].stop()
        except Exception:
            pass
        del active_clients[acc_uid]
        
    database.remove_account(acc_uid)
    await callback.answer("Userbot account deleted successfully.", show_alert=True)
    await cb_manage_bots(callback)

@dp.callback_query(F.data == "cmd_admin_join")
async def cb_admin_join(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        await callback.answer("Admin access required.", show_alert=True)
        return
        
    text = (
        "<b>FORCE ALL USERBOTS TO JOIN CHAT</b>\n\n"
        "<blockquote>Send any <b>Private Invite Link</b> (t.me/+... or t.me/joinchat/...) or <b>Public Link / Username</b> (@channel).\n\n"
        "All active userbots in the pool will join this chat immediately.</blockquote>\n\n"
        "<i>Send the link now:</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Cancel", callback_data="cmd_admin_panel", style="danger")]
    ])
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await state.set_state(AdminJoinState.waiting_for_link)
    await callback.answer()

@dp.message(AdminJoinState.waiting_for_link)
async def process_admin_join(message: Message, state: FSMContext):
    link = message.text.strip()
    status_msg = await message.answer("<i>Commanding all userbots to join chat...</i>", parse_mode=ParseMode.HTML)
    
    success_count = 0
    resolved_title = link
    resolved_id = None
    
    for acc_id, client in list(active_clients.items()):
        if not client.is_connected:
            continue
        try:
            chat = await client.join_chat(link)
            success_count += 1
            resolved_id = chat.id
            resolved_title = getattr(chat, "title", resolved_title)
        except UserAlreadyParticipant:
            success_count += 1
            try:
                chat = await client.get_chat(link)
                resolved_id = chat.id
                resolved_title = getattr(chat, "title", resolved_title)
            except Exception:
                pass
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                chat = await client.join_chat(link)
                success_count += 1
                resolved_id = chat.id
                resolved_title = getattr(chat, "title", resolved_title)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Userbot {acc_id} join failed: {e}")
            
        await asyncio.sleep(0.4)
        
    if resolved_id:
        ch_data = {
            "chat_id": resolved_id,
            "title": resolved_title,
            "username": link if link.startswith("@") else "",
            "type": "channel" if str(resolved_id).startswith("-100") else "group",
            "added_at": int(time.time())
        }
        database.add_user_channel(message.from_user.id, ch_data)
        
    safe_title = html.escape(str(resolved_title))
    await safe_edit_text(status_msg, 
        f"<b>Join Command Finished</b>\n\n"
        f"<blockquote><b>Chat:</b> {safe_title}\n"
        f"<b>Chat ID:</b> <code>{resolved_id or 'Resolved via userbot'}</code>\n"
        f"<b>Userbots Joined:</b> <code>{success_count}/{len(active_clients)}</code></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="primary")]
        ])
    )
    await state.clear()

@dp.callback_query(F.data == "cmd_my_channels")
async def cb_my_channels(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_data = database.get_user(user_id)
    channels = user_data.get("channels", []) if user_data else []
    
    b_name = cached_bot_username or "DumblumbbBot"
    ch_url = f"https://t.me/{b_name}?startchannel=bot_administrators&admin=post_messages+edit_messages+delete_messages+invite_users+manage_chat"
    grp_url = f"https://t.me/{b_name}?startgroup=bot_administrators&admin=post_messages+edit_messages+delete_messages+invite_users+manage_chat"
    if not channels:
        text = (
            "<b>TARGET CHATS & GROUPS (0)</b>\n\n"
            "<blockquote>You have not connected any target chats yet.\n"
            "Tap below to add the bot to your channel or group with Admin rights.</blockquote>"
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Add to Channel", url=ch_url, style="primary"),
                InlineKeyboardButton(text="Add to Group", url=grp_url, style="primary")
            ],
            [InlineKeyboardButton(text="Connect Manually", callback_data="cmd_set_channel", style="success")],
            [InlineKeyboardButton(text="Back to Dashboard", callback_data="cmd_main_menu", style="danger")]
        ])
        await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        await callback.answer()
        return

    text = f"<b>TARGET CHATS & GROUPS ({len(channels)})</b>\n\n"
    text += "<blockquote>All incoming messages in these chats receive automatic reactions:</blockquote>\n\n"
    keyboard = []
    
    for idx, ch in enumerate(channels, 1):
        cid = ch.get("chat_id")
        title = ch.get("title", "Target Chat")
        safe_title = html.escape(str(title))
        ch_type = ch.get("type", "chat").upper()
        text += (
            f"<b>{idx}. {safe_title}</b>\n"
            f"• Type: <b>{ch_type}</b>\n"
            f"• Chat ID: <code>{cid}</code>\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(text=f"Disconnect {title[:16]}", callback_data=f"del_ch_{cid}", style="danger")
        ])
        
    keyboard.append([
        InlineKeyboardButton(text="Add to Channel", url=ch_url, style="primary"),
        InlineKeyboardButton(text="Add to Group", url=grp_url, style="primary")
    ])
    keyboard.append([InlineKeyboardButton(text="Connect Link / ID", callback_data="cmd_set_channel", style="success")])
    keyboard.append([InlineKeyboardButton(text="Back to Dashboard", callback_data="cmd_main_menu", style="danger")])
    
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(F.data.startswith("del_ch_"))
async def cb_delete_channel(callback: CallbackQuery):
    user_id = callback.from_user.id
    cid = int(callback.data.replace("del_ch_", ""))
    database.remove_user_channel(user_id, cid)
    await callback.answer("Target chat disconnected.", show_alert=True)
    await cb_my_channels(callback)

@dp.my_chat_member()
async def handle_bot_added_to_chat(event: ChatMemberUpdated):
    new_status = event.new_chat_member.status
    if new_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER]:
        chat = event.chat
        adder = event.from_user
        adder_id = adder.id if adder else None
        if not adder_id:
            admins = database.get_admins()
            adder_id = admins[0] if admins else 9156503142
            adder_user = ""
            adder_fname = "Admin"
        else:
            adder_user = adder.username or ""
            adder_fname = adder.first_name or "User"
            
        database.create_or_update_user(adder_id, adder_user, adder_fname)
        
        chat_type_str = "channel" if str(chat.type).endswith("CHANNEL") or "channel" in str(chat.type).lower() else "group"
        channel_data = {
            "chat_id": chat.id,
            "title": chat.title or "Target Chat",
            "username": chat.username or "",
            "type": chat_type_str,
            "added_at": int(time.time())
        }
        database.add_user_channel(adder_id, channel_data)
        
        if chat.username:
            for acc_id, client in list(active_clients.items()):
                if client.is_connected:
                    try:
                        await client.join_chat(chat.username)
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)
        else:
            try:
                invite = await bot.create_chat_invite_link(chat.id, name="UserbotAutoJoin")
                invite_link = invite.invite_link
                for acc_id, client in list(active_clients.items()):
                    if client.is_connected:
                        try:
                            await client.join_chat(invite_link)
                        except Exception:
                            pass
                        await asyncio.sleep(0.4)
            except Exception:
                pass
        
        if adder:
            safe_chat_title = html.escape(str(chat.title or "Target Chat"))
            try:
                await bot.send_message(
                    chat_id=adder.id,
                    text=(
                        "<b>Target Successfully Connected</b>\n\n"
                        f"<blockquote><b>Chat Title:</b> {safe_chat_title}\n"
                        f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
                        f"<b>Chat Type:</b> <b>{chat_type_str.upper()}</b></blockquote>\n\n"
                        "Auto-reaction engine is now active for all incoming messages.",
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

@dp.channel_post()
async def on_channel_post(message: Message):
    chat_id = message.chat.id
    target_chats = database.get_all_target_chat_ids()
    if chat_id in target_chats or str(chat_id) in target_chats:
        msg_key = f"{chat_id}_{message.message_id}"
        if msg_key not in processed_messages:
            processed_messages.add(msg_key)
            if len(processed_messages) > 2000:
                processed_messages.clear()
            asyncio.create_task(dispatch_reactions_for_chat(chat_id, message.message_id))

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def on_group_message(message: Message):
    chat_id = message.chat.id
    target_chats = database.get_all_target_chat_ids()
    if chat_id in target_chats or str(chat_id) in target_chats:
        msg_key = f"{chat_id}_{message.message_id}"
        if msg_key not in processed_messages:
            processed_messages.add(msg_key)
            if len(processed_messages) > 2000:
                processed_messages.clear()
            asyncio.create_task(dispatch_reactions_for_chat(chat_id, message.message_id))

@dp.callback_query(F.data == "cmd_set_channel")
async def cb_set_channel(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_data = database.get_user(user_id)
    if not user_data or user_data.get("status") != "approved":
        await callback.answer("You are not approved to use this feature.", show_alert=True)
        return

    text = (
        "<b>CONNECT TARGET CHAT MANUALLY</b>\n\n"
        "<blockquote>Send any <b>Private Invite Link</b> (t.me/+... or t.me/joinchat/...), <b>Public Username</b> (@mychannel), or <b>Chat ID</b> (-100...).\n\n"
        "All active userbots will join the chat automatically.</blockquote>\n\n"
        "<i>Send the link/username now:</i>"
    )
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
    await state.set_state(ChannelState.waiting_for_link)
    await callback.answer()

@dp.message(ChannelState.waiting_for_link)
async def process_channel_link(message: Message, state: FSMContext):
    link = message.text.strip()
    user_id = message.from_user.id
    
    status_msg = await message.answer("<i>Processing chat and connecting userbots...</i>", parse_mode=ParseMode.HTML)
    
    chat_id_to_save = None
    chat_title_to_save = link
    chat_type_str = "channel"
    success_count = 0
    
    if link.startswith("-100") or (link.startswith("-") and link[1:].isdigit()):
        try:
            chat_id_to_save = int(link)
            try:
                chat_info = await bot.get_chat(chat_id_to_save)
                chat_title_to_save = chat_info.title or f"Chat {chat_id_to_save}"
                chat_type_str = "channel" if str(chat_info.type).endswith("CHANNEL") else "group"
            except Exception:
                pass
        except Exception:
            pass

    for acc_id, client in list(active_clients.items()):
        if not client.is_connected:
            continue
        try:
            chat = await client.join_chat(link)
            success_count += 1
            chat_id_to_save = chat.id
            chat_title_to_save = chat.title or chat_title_to_save
            chat_type_str = str(chat.type).lower()
        except UserAlreadyParticipant:
            success_count += 1
            try:
                chat = await client.get_chat(link)
                chat_id_to_save = chat.id
                chat_title_to_save = chat.title or chat_title_to_save
                chat_type_str = str(chat.type).lower()
            except Exception:
                pass
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                chat = await client.join_chat(link)
                success_count += 1
                chat_id_to_save = chat.id
                chat_title_to_save = chat.title or chat_title_to_save
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Join error: {e}")
        await asyncio.sleep(0.4)

    if not chat_id_to_save:
        try:
            chat_info = await bot.get_chat(link)
            chat_id_to_save = chat_info.id
            chat_title_to_save = chat_info.title or link
            chat_type_str = "channel" if str(chat_info.type).endswith("CHANNEL") else "group"
        except Exception:
            pass

    if chat_id_to_save:
        ch_data = {
            "chat_id": chat_id_to_save,
            "title": chat_title_to_save,
            "username": link if link.startswith("@") else "",
            "type": chat_type_str,
            "added_at": int(time.time())
        }
        database.add_user_channel(user_id, ch_data)
        safe_title = html.escape(str(chat_title_to_save))
        await safe_edit_text(status_msg, 
            f"<b>Target Successfully Connected</b>\n\n"
            f"<blockquote><b>Title:</b> {safe_title}\n"
            f"<b>Chat ID:</b> <code>{chat_id_to_save}</code>\n"
            f"<b>Userbots Joined:</b> <code>{success_count}/{len(active_clients)}</code></blockquote>\n\n"
            f"Auto-reaction engine is now active for this target.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_markup()
        )
    else:
        await safe_edit_text(status_msg, 
            "<b>Failed to resolve chat.</b>\n\n"
            "<blockquote>Make sure the link or username is valid, or simply use the <b>'Add to Channel or Group'</b> button to connect it with 1 tap.</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_markup()
        )
    await state.clear()

@dp.callback_query(F.data == "cmd_add_acc")
async def cb_add_acc(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        await callback.answer("Only Administrators can add userbots to the central pool.", show_alert=True)
        return
        
    text = (
        "<b>CONNECT USERBOT TO CENTRAL POOL</b>\n\n"
        "<blockquote>Send the phone number with international country code.\n\n"
        "<b>Example:</b> <code>+12025550143</code> or <code>+919876543210</code>\n\n"
        "All approved users will receive reactions from this account.</blockquote>\n\n"
        "<i>Send the phone number now:</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Cancel", callback_data="cmd_admin_panel", style="danger")]
    ])
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await state.set_state(AccountState.waiting_for_phone)
    await callback.answer()

@dp.message(AccountState.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    status_msg = await message.answer("<i>Sending login verification code via Telegram...</i>", parse_mode=ParseMode.HTML)
    
    app = Client(f"temp_{message.from_user.id}", api_id=config.API_ID, api_hash=config.API_HASH, in_memory=True)
    
    try:
        await asyncio.wait_for(app.connect(), timeout=15.0)
        sent_code = await asyncio.wait_for(app.send_code(phone), timeout=25.0)
    except asyncio.TimeoutError:
        await safe_edit_text(status_msg, 
            "<b>Telegram API Timeout</b>\n\n<blockquote>Could not reach Telegram datacenter in time. Please try again.</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_markup()
        )
        await state.clear()
        try:
            await app.disconnect()
        except Exception:
            pass
        return
    except Exception as e:
        await safe_edit_text(status_msg, 
            f"<b>Failed to send code:</b> {e}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_markup()
        )
        await state.clear()
        try:
            await app.disconnect()
        except Exception:
            pass
        return

    await state.update_data(phone=phone, phone_code_hash=sent_code.phone_code_hash)
    pending_logins[message.from_user.id] = app
    
    await safe_edit_text(status_msg, 
        "<b>Verification Code Sent</b>\n\n"
        "<blockquote>Please send the OTP code you received on Telegram.\n\n"
        "<b>Tip:</b> You can send it with spaces like <code>1 2 3 4 5</code></blockquote>",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(AccountState.waiting_for_otp)

@dp.message(AccountState.waiting_for_otp)
async def process_otp(message: Message, state: FSMContext):
    otp = message.text.replace(" ", "").replace(",", "").replace("-", "")
    data = await state.get_data()
    app = pending_logins.get(message.from_user.id)
    if not app:
        await message.answer("Session expired. Please start again from /start", reply_markup=get_back_markup())
        await state.clear()
        return

    try:
        await app.sign_in(data["phone"], data["phone_code_hash"], otp)
    except SessionPasswordNeeded:
        await message.answer(
            "<b>Two-Step Verification (2FA) Required</b>\n\n"
            "<blockquote>Please send your 2FA Cloud Password:</blockquote>",
            parse_mode=ParseMode.HTML
        )
        await state.set_state(AccountState.waiting_for_2fa)
        return
    except Exception as e:
        await message.answer(f"<b>Sign in error:</b> {e}", parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
        await state.clear()
        return

    await finalize_userbot_login(app, message, state, data)

@dp.message(AccountState.waiting_for_2fa)
async def process_2fa(message: Message, state: FSMContext):
    data = await state.get_data()
    app = pending_logins.get(message.from_user.id)
    try:
        await app.check_password(message.text)
        data["2fa"] = message.text
    except Exception as e:
        await message.answer(f"<b>Invalid 2FA password:</b> {e}", parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
        await state.clear()
        return

    await finalize_userbot_login(app, message, state, data)

async def finalize_userbot_login(app: Client, message: Message, state: FSMContext, data: dict):
    me = await app.get_me()
    session_string = await app.export_session_string()
    
    acc_data = {
        "user_id": me.id,
        "name": me.first_name or "Userbot",
        "mobile_number": data.get("phone", ""),
        "session_string": session_string,
        "two_step_password": data.get("2fa", ""),
        "added_at": int(time.time()),
        "is_active": True
    }
    
    database.add_account(acc_data)
    
    try:
        await app.disconnect()
    except Exception:
        pass

    try:
        c = Client(
            f"session_{me.id}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=session_string
        )
        c.add_handler(MessageHandler(handle_pyrogram_message))
        await c.start()
        
        try:
            async for _ in c.get_dialogs():
                pass
        except Exception:
            pass
            
        active_clients[me.id] = c
    except Exception as e:
        logger.error(f"Failed to start new client: {e}")

    safe_me_name = html.escape(str(me.first_name or "Account"))
    await message.answer(
        f"<b>Userbot Successfully Added to Central Pool</b>\n\n"
        f"<blockquote><b>Account:</b> {safe_me_name}\n"
        f"<b>Account ID:</b> <code>{me.id}</code>\n"
        f"<b>Phone:</b> <code>{data.get('phone')}</code>\n"
        f"<b>Total Pool Bots:</b> <code>{len(active_clients)}</code></blockquote>\n\n"
        f"All users will now receive reactions from this account.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Manage Userbots", callback_data="cmd_manage_bots", style="primary")],
            [InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="danger")]
        ])
    )
    await state.clear()

@dp.callback_query(F.data == "cmd_admin_panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext = None):
    if state is not None:
        await state.clear()
    user_id = callback.from_user.id
    if not database.is_admin(user_id):
        await callback.answer("Admin access required.", show_alert=True)
        return
        
    settings = database.get_settings()
    stats = database.get_stats()
    approval_mode = settings.get("approval_required", True)
    mode_badge = "Manual Approval Required" if approval_mode else "Open Public (Auto-Approve)"
    
    text = (
        "<b>ADMINISTRATOR CONTROL PANEL</b>\n\n"
        f"<blockquote><b>Total Users:</b> <code>{stats['total_users']}</code>\n"
        f"<b>Approved:</b> <code>{stats['approved']}</code>\n"
        f"<b>Pending:</b> <code>{stats['pending']}</code>\n"
        f"<b>Banned:</b> <code>{stats['banned']}</code>\n"
        f"<b>Total Userbots:</b> <code>{stats['total_accounts']}</code>\n"
        f"<b>Target Chats:</b> <code>{stats['total_channels']}</code>\n"
        f"<b>Access Policy:</b> <b>{mode_badge}</b></blockquote>\n\n"
        "<i>Select an action below to manage userbots, admins, users, or broadcast:</i>"
    )
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=get_admin_panel_markup(approval_mode))
    await callback.answer()

@dp.callback_query(F.data == "adm_toggle_mode")
async def cb_toggle_mode(callback: CallbackQuery, state: FSMContext = None):
    if not database.is_admin(callback.from_user.id):
        return
    new_mode = database.toggle_approval_mode()
    mode_str = "Manual Approval Required" if new_mode else "Open Public"
    await callback.answer(f"Access Policy switched to: {mode_str}.", show_alert=True)
    await cb_admin_panel(callback, state)

@dp.callback_query(F.data == "adm_stats")
async def cb_adm_stats(callback: CallbackQuery):
    if not database.is_admin(callback.from_user.id):
        return
    stats = database.get_stats()
    text = (
        "<b>DETAILED SYSTEM ANALYTICS</b>\n\n"
        f"<blockquote><b>Total Registered:</b> <code>{stats['total_users']}</code>\n"
        f"<b>Approved Members:</b> <code>{stats['approved']}</code>\n"
        f"<b>Pending Requests:</b> <code>{stats['pending']}</code>\n"
        f"<b>Banned Users:</b> <code>{stats['banned']}</code>\n\n"
        f"<b>Live Userbot Sessions:</b> <code>{len(active_clients)}</code>\n"
        f"<b>Active Target Chats:</b> <code>{stats['total_channels']}</code></blockquote>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="danger")]
    ])
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_list_users_"))
async def cb_list_users(callback: CallbackQuery):
    if not database.is_admin(callback.from_user.id):
        return
    page = int(callback.data.replace("adm_list_users_", ""))
    users = database.get_all_users()
    page_size = 6
    start_idx = page * page_size
    page_users = users[start_idx:start_idx + page_size]
    
    text = f"<b>REGISTERED USERS ({len(users)})</b>\n\n"
    keyboard = []
    
    for u in page_users:
        uid = u.get("user_id")
        fname = u.get("first_name", "User")
        status = u.get("status", "pending")
        btn_style = "success" if status == "approved" else ("primary" if status == "pending" else "danger")
        keyboard.append([
            InlineKeyboardButton(text=f"{fname} ({uid}) - {status.upper()}", callback_data=f"adm_user_{uid}", style=btn_style)
        ])
        
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="Previous", callback_data=f"adm_list_users_{page - 1}", style="primary"))
    if start_idx + page_size < len(users):
        nav_row.append(InlineKeyboardButton(text="Next", callback_data=f"adm_list_users_{page + 1}", style="primary"))
    if nav_row:
        keyboard.append(nav_row)
        
    keyboard.append([InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="danger")])
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

def get_user_details_view(target_uid: int):
    u = database.get_user(target_uid)
    if not u:
        return None
    status = u.get("status", "pending")
    status_label = "Approved" if status == "approved" else ("Pending" if status == "pending" else "Banned")
    ch_count = len(u.get("channels", []))
    safe_name = html.escape(str(u.get("first_name", "User")))
    safe_uname = html.escape(str(u.get("username") or "None"))
    
    text = (
        f"<b>USER DETAILS: {safe_name}</b>\n\n"
        f"<blockquote><b>User ID:</b> <code>{target_uid}</code>\n"
        f"<b>Username:</b> @{safe_uname}\n"
        f"<b>Status:</b> <b>{status_label}</b>\n"
        f"<b>Target Chats:</b> <code>{ch_count}</code></blockquote>\n\n"
        f"<i>Select an action for this user:</i>"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Approve User", callback_data=f"adm_setstat_{target_uid}_approved", style="success"),
            InlineKeyboardButton(text="Disapprove", callback_data=f"adm_setstat_{target_uid}_pending", style="danger")
        ],
        [
            InlineKeyboardButton(text="Ban User", callback_data=f"adm_setstat_{target_uid}_banned", style="danger")
        ],
        [InlineKeyboardButton(text="Back to Users List", callback_data="adm_list_users_0", style="primary")]
    ])
    return text, markup

@dp.callback_query(F.data.startswith("adm_user_"))
async def cb_view_user(callback: CallbackQuery):
    if not database.is_admin(callback.from_user.id):
        return
    target_uid = int(callback.data.replace("adm_user_", ""))
    view = get_user_details_view(target_uid)
    if not view:
        await callback.answer("User not found.", show_alert=True)
        return
    text, markup = view
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_setstat_"))
async def cb_set_user_status(callback: CallbackQuery):
    if not database.is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    target_uid = int(parts[2])
    new_status = parts[3]
    
    database.set_user_status(target_uid, new_status)
    await callback.answer(f"Status changed to: {new_status}.", show_alert=True)
    
    if new_status == "approved":
        try:
            await bot.send_message(
                chat_id=target_uid,
                text="<b>Access Approved</b>\n\n<blockquote>The Administrator has approved your access. You can now use all bot features. Click /start to proceed.</blockquote>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    elif new_status == "pending":
        try:
            await bot.send_message(
                chat_id=target_uid,
                text="<b>Access Disapproved</b>\n\n<blockquote>Your access has been revoked or set to pending by the Administrator.</blockquote>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    view = get_user_details_view(target_uid)
    if view:
        text, markup = view
        await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)

@dp.callback_query(F.data.startswith("adm_appr_"))
async def cb_quick_approve(callback: CallbackQuery):
    if not database.is_admin(callback.from_user.id):
        return
    target_uid = int(callback.data.replace("adm_appr_", ""))
    database.set_user_status(target_uid, "approved")
    
    try:
        await safe_edit_reply_markup(callback.message, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Approved by {callback.from_user.first_name}", callback_data="none", style="success")]
        ]))
    except Exception:
        pass
        
    try:
        await bot.send_message(
            chat_id=target_uid,
            text="<b>Access Approved</b>\n\n<blockquote>The Administrator has granted you full access to the Reaction Engine. Tap /start to begin.</blockquote>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    await callback.answer("User approved.")

@dp.callback_query(F.data.startswith("adm_rejc_"))
async def cb_quick_reject(callback: CallbackQuery):
    if not database.is_admin(callback.from_user.id):
        return
    target_uid = int(callback.data.replace("adm_rejc_", ""))
    database.set_user_status(target_uid, "banned")
    
    try:
        await safe_edit_reply_markup(callback.message, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Rejected by {callback.from_user.first_name}", callback_data="none", style="danger")]
        ]))
    except Exception:
        pass
        
    try:
        await bot.send_message(
            chat_id=target_uid,
            text="<b>Access Request Denied</b>\n\n<blockquote>Your access request was rejected by the Administrator.</blockquote>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    await callback.answer("User rejected.")

@dp.callback_query(F.data == "adm_find_user")
async def cb_find_user(callback: CallbackQuery, state: FSMContext):
    if not database.is_admin(callback.from_user.id):
        return
    await safe_edit_text(callback.message, 
        "<b>Find User by ID</b>\n\n"
        "<blockquote>Send the Telegram User ID (numbers only):</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Cancel", callback_data="cmd_admin_panel", style="danger")]
        ])
    )
    await state.set_state(AdminUserState.waiting_for_user_id)
    await callback.answer()

@dp.message(AdminUserState.waiting_for_user_id)
async def process_find_user_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except Exception:
        await message.answer("Invalid ID format. Please send digits only.")
        return
        
    u = database.get_user(uid)
    if not u:
        await message.answer("User not found in database.", reply_markup=get_back_markup())
        await state.clear()
        return
        
    await state.clear()
    status = u.get("status", "pending")
    status_label = "Approved" if status == "approved" else ("Pending" if status == "pending" else "Banned")
    
    safe_fname = html.escape(str(u.get("first_name", "User")))
    safe_uname = html.escape(str(u.get("username") or "None"))
    text = (
        f"<b>USER DETAILS: {safe_fname}</b>\n\n"
        f"<blockquote><b>User ID:</b> <code>{uid}</code>\n"
        f"<b>Username:</b> @{safe_uname}\n"
        f"<b>Status:</b> <b>{status_label}</b>\n"
        f"<b>Target Chats:</b> <code>{len(u.get('channels', []))}</code></blockquote>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Approve", callback_data=f"adm_setstat_{uid}_approved", style="success"),
            InlineKeyboardButton(text="Disapprove", callback_data=f"adm_setstat_{uid}_pending", style="danger")
        ],
        [InlineKeyboardButton(text="Ban", callback_data=f"adm_setstat_{uid}_banned", style="danger")],
        [InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="primary")]
    ])
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not database.is_admin(message.from_user.id):
        return
    users = database.get_all_users()
    text = (
        "<b>BROADCAST DISPATCHER</b>\n\n"
        f"<blockquote><b>Audience Size:</b> <code>{len(users)}</code> registered users\n\n"
        f"<b>Supported Message Formats:</b>\n"
        f"• Text (formatting, links, spoilers)\n"
        f"• Photos & Captions\n"
        f"• Videos & Animations (GIFs)\n"
        f"• Voice Messages & Video Notes\n"
        f"• Audio / MP3 Files\n"
        f"• Documents & Files\n"
        f"• Stickers (Static, Animated, Video)</blockquote>\n\n"
        f"<i>Send or forward the message you want to broadcast now:</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Cancel Broadcast", callback_data="cmd_admin_panel", style="danger")]
    ])
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await state.set_state(BroadcastState.waiting_for_message)

@dp.callback_query(F.data == "adm_broadcast")
async def cb_start_broadcast(callback: CallbackQuery, state: FSMContext):
    if not database.is_admin(callback.from_user.id):
        return
        
    users = database.get_all_users()
    text = (
        "<b>BROADCAST DISPATCHER</b>\n\n"
        f"<blockquote><b>Audience Size:</b> <code>{len(users)}</code> registered users\n\n"
        f"<b>Supported Message Formats:</b>\n"
        f"• Text (formatting, links, spoilers)\n"
        f"• Photos & Captions\n"
        f"• Videos & Animations (GIFs)\n"
        f"• Voice Messages & Video Notes\n"
        f"• Audio / MP3 Files\n"
        f"• Documents & Files\n"
        f"• Stickers (Static, Animated, Video)</blockquote>\n\n"
        f"<i>Send or forward the message you want to broadcast now:</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Cancel Broadcast", callback_data="cmd_admin_panel", style="danger")]
    ])
    await safe_edit_text(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.answer()

@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    users = database.get_all_users()
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Confirm & Send Broadcast", callback_data="adm_confirm_broadcast", style="success"),
            InlineKeyboardButton(text="Cancel", callback_data="cmd_admin_panel", style="danger")
        ]
    ])
    await message.answer(
        f"<b>Broadcast Ready for Transmission</b>\n\n"
        f"<blockquote>Targeting <code>{len(users)}</code> users.\n"
        f"Are you sure you want to broadcast this message?</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=markup
    )
    await state.set_state(BroadcastState.confirm_send)

@dp.callback_query(BroadcastState.confirm_send, F.data == "adm_confirm_broadcast")
async def cb_execute_broadcast(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    await state.clear()
    
    status_msg = await safe_edit_text(callback.message, 
        "<b>Broadcasting in progress...</b>\n\n<blockquote>Please wait while messages are being delivered.</blockquote>",
        parse_mode=ParseMode.HTML
    )
    
    users = database.get_all_users()
    total_users = len(users)
    success_count = 0
    failed_count = 0
    start_time = time.time()
    
    for u in users:
        uid = u.get("user_id")
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=source_chat_id,
                message_id=source_message_id
            )
            success_count += 1
        except TelegramForbiddenError:
            failed_count += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id
                )
                success_count += 1
            except Exception:
                failed_count += 1
        except Exception:
            failed_count += 1
            
        await asyncio.sleep(0.05)

    elapsed = time.time() - start_time
    await safe_edit_text(status_msg, 
        "<b>Broadcast Transmission Finished</b>\n\n"
        f"<blockquote><b>Total Target Users:</b> <code>{total_users}</code>\n"
        f"<b>Successfully Delivered:</b> <code>{success_count}</code>\n"
        f"<b>Failed / Blocked:</b> <code>{failed_count}</code>\n"
        f"<b>Time Elapsed:</b> <code>{elapsed:.1f}s</code></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Back to Admin Panel", callback_data="cmd_admin_panel", style="primary")]
        ])
    )

async def main():
    global cached_bot_username
    database.init_db()
    
    print("Initializing Pyrogram Userbots from database...")
    await start_all_clients()
    
    print("Retrieving Manager Bot information...")
    try:
        bot_info = await bot.get_me()
        cached_bot_username = bot_info.username
        print(f"Manager Bot active as @{cached_bot_username}")
    except Exception as e:
        logger.error(f"Failed to get bot info: {e}")
        
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"Webhook reset: {e}")
        
    print("Reaction Engine Manager Bot Polling Started...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
