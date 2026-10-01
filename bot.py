import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone

import psycopg2

from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.constants import ChatMemberStatus

from telegram.ext import (
    Application,
    CommandHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip())
except (TypeError, ValueError):
    ADMIN_ID = 0


# حذف پیام خوش آمدگویی بعد از 3 دقیقه
WELCOME_DELETE_SECONDS = 180


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# CHECK CONFIG
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if not ADMIN_ID:
    logger.warning(
        "ADMIN_ID is not set. Main admin access is disabled."
    )


# =========================================================
# DATABASE
# =========================================================

def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            admin_id BIGINT NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_settings (
            chat_id BIGINT PRIMARY KEY,
            rules TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    cur.close()
    conn.close()

    logger.info("Database initialized")


def add_warning(chat_id, user_id, admin_id, reason):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO warnings
        (chat_id, user_id, admin_id, reason)
        VALUES (%s, %s, %s, %s)
        """,
        (
            chat_id,
            user_id,
            admin_id,
            reason,
        ),
    )

    conn.commit()

    cur.close()
    conn.close()


def get_warning_count(chat_id, user_id):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM warnings
        WHERE chat_id = %s
        AND user_id = %s
        """,
        (
            chat_id,
            user_id,
        ),
    )

    count = cur.fetchone()[0]

    cur.close()
    conn.close()

    return count


def clear_warnings(chat_id, user_id):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM warnings
        WHERE chat_id = %s
        AND user_id = %s
        """,
        (
            chat_id,
            user_id,
        ),
    )

    conn.commit()

    cur.close()
    conn.close()


# =========================================================
# ADMIN SYSTEM
# =========================================================

def is_main_admin(user_id: int) -> bool:

    """
    مدیر اصلی ربات.
    اگر User ID با ADMIN_ID یکی باشد،
    بدون نیاز به بررسی مدیر بودن در گروه دسترسی دارد.
    """

    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


async def is_group_admin(
    update: Update,
    user_id: int = None,
) -> bool:

    if not update.effective_chat:
        return False

    if user_id is None:

        if not update.effective_user:
            return False

        user_id = update.effective_user.id

    # -----------------------------------------------------
    # MAIN ADMIN
    # -----------------------------------------------------

    if is_main_admin(user_id):
        return True

    # -----------------------------------------------------
    # GROUP ADMIN
    # -----------------------------------------------------

    try:

        member = await update.effective_chat.get_member(
            user_id
        )

        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as e:

        logger.error(
            "Group admin check error: %s",
            e,
        )

        return False


async def require_admin(update: Update) -> bool:

    if not update.effective_user:
        return False

    user_id = update.effective_user.id

    # =====================================================
    # MAIN ADMIN
    # =====================================================

    if is_main_admin(user_id):
        return True

    # =====================================================
    # GROUP ADMIN
    # =====================================================

    if await is_group_admin(update, user_id):
        return True

    # =====================================================
    # ACCESS DENIED
    # =====================================================

    if update.message:

        await update.message.reply_text(
            "⛔ شما دسترسی مدیریت این ربات را ندارید."
        )

    return False


async def query_user_is_admin(
    query,
    user_id: int,
) -> bool:

    # =====================================================
    # MAIN ADMIN
    # =====================================================

    if is_main_admin(user_id):
        return True

    # =====================================================
    # GROUP ADMIN
    # =====================================================

    try:

        if not query.message:
            return False

        member = await query.message.chat.get_member(
            user_id
        )

        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as e:

        logger.error(
            "Callback admin check error: %s",
            e,
        )

        return False


# =========================================================
# TARGET USER
# =========================================================

def get_target_user(message):

    if not message:
        return None

    if message.reply_to_message:

        return message.reply_to_message.from_user

    return None


# =========================================================
# DELETE AFTER DELAY
# =========================================================

async def delete_after_delay(
    context,
    chat_id,
    message_id,
    seconds,
):

    await asyncio.sleep(seconds)

    try:

        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )

    except Exception as e:

        logger.info(
            "Could not delete message: %s",
            e,
        )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    await update.message.reply_text(
        "🤖 ربات مدیریت گروه «کافه فیلم بین و ضربان موزیک» فعال است.\n\n"
        "🎬 درخواست فیلم و سریال\n"
        "🎵 درخواست آهنگ و موزیک\n\n"
        "📌 برای مشاهده دستورات:\n"
        "/help"
    )


# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    text = """
🤖 راهنمای ربات

🎬🎵 درخواست‌ها:

نام فیلم، سریال، آهنگ یا خواننده را در گروه ارسال کنید.

━━━━━━━━━━━━━━

🛡️ دستورات مدیران:

/panel
پنل مدیریت

/warn
اخطار

/warnings
تعداد اخطار

/clearwarn
پاک کردن اخطارها

/mute
میوت

/unmute
رفع میوت

/ban
بن

/unban
رفع بن

/kick
اخراج

/del
حذف پیام

/members
تعداد اعضا

/admins
مدیران

/rules
قوانین

/id
آیدی

━━━━━━━━━━━━━━

📌 برای مدیریت یک کاربر:
روی پیام کاربر Reply کنید.

📌 برای باز کردن پنل عمومی:
/panel
"""


    await update.message.reply_text(text)


# =========================================================
# WELCOME
# =========================================================

async def new_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    result = update.chat_member

    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    if (
        old_status in (
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED,
        )
        and new_status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,
        )
    ):

        user = result.new_chat_member.user
        chat = result.chat

        name = user.first_name or "دوست عزیز"

        welcome_text = (
            f"🎉 خوش اومدی {name}!\n\n"
            "🎬🎵 به گروه «کافه فیلم بین و ضربان موزیک» خوش آمدی.\n\n"
            "🎬 برای درخواست فیلم یا سریال، نام اثر را ارسال کن.\n"
            "🎵 برای درخواست موزیک، نام آهنگ یا خواننده را ارسال کن.\n\n"
            "📌 لطفاً قوانین گروه را رعایت کن.\n"
            "❤️ امیدواریم کنار هم لحظات خوبی داشته باشیم."
        )

        try:

            welcome = await context.bot.send_message(
                chat_id=chat.id,
                text=welcome_text,
            )

            asyncio.create_task(
                delete_after_delay(
                    context,
                    chat.id,
                    welcome.message_id,
                    WELCOME_DELETE_SECONDS,
                )
            )

        except Exception as e:

            logger.error(
                "Welcome error: %s",
                e,
            )


# =========================================================
# ID
# =========================================================

async def id_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    target = get_target_user(update.message)

    if target:

        await update.message.reply_text(
            f"🆔 آیدی کاربر:\n`{target.id}`",
            parse_mode="Markdown",
        )

    else:

        await update.message.reply_text(
            f"🆔 آیدی شما:\n`{update.effective_user.id}`",
            parse_mode="Markdown",
        )


# =========================================================
# MEMBERS
# =========================================================

async def members_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    try:

        count = await context.bot.get_chat_member_count(
            update.effective_chat.id
        )

        await update.message.reply_text(
            f"👥 تعداد اعضای گروه:\n\n{count}"
        )

    except Exception as e:

        logger.error(
            "Members error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ دریافت تعداد اعضا انجام نشد."
        )


# =========================================================
# WARN
# =========================================================

async def warn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    message = update.message
    target = get_target_user(message)

    if not target:

        await message.reply_text(
            "⚠️ برای اخطار دادن، روی پیام کاربر Reply کن."
        )

        return

    if await is_group_admin(update, target.id):

        await message.reply_text(
            "❌ نمی‌توان به مدیر گروه اخطار داد."
        )

        return

    reason = " ".join(context.args).strip()

    if not reason:
        reason = "بدون دلیل مشخص"

    add_warning(
        update.effective_chat.id,
        target.id,
        update.effective_user.id,
        reason,
    )

    count = get_warning_count(
        update.effective_chat.id,
        target.id,
    )

    await message.reply_text(
        f"⚠️ اخطار ثبت شد.\n\n"
        f"👤 کاربر: {target.first_name}\n"
        f"🔢 تعداد اخطار: {count}/3\n"
        f"📝 دلیل: {reason}"
    )

    if count >= 3:

        try:

            await mute_user(
                context.bot,
                update.effective_chat.id,
                target.id,
                60,
            )

            await message.reply_text(
                f"🔇 کاربر {target.first_name} "
                "به دلیل رسیدن به ۳ اخطار، "
                "به مدت ۱ ساعت میوت شد."
            )

        except Exception as e:

            logger.error(
                "Auto mute error: %s",
                e,
            )


# =========================================================
# WARNINGS
# =========================================================

async def warnings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    target = get_target_user(update.message)

    if target:

        user_id = target.id
        name = target.first_name

    else:

        user_id = update.effective_user.id
        name = update.effective_user.first_name

    count = get_warning_count(
        update.effective_chat.id,
        user_id,
    )

    await update.message.reply_text(
        f"⚠️ اخطارهای {name}:\n\n"
        f"تعداد: {count}"
    )


# =========================================================
# CLEAR WARNINGS
# =========================================================

async def clearwarn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target = get_target_user(update.message)

    if not target:

        await update.message.reply_text(
            "⚠️ روی پیام کاربر Reply کن."
        )

        return

    clear_warnings(
        update.effective_chat.id,
        target.id,
    )

    await update.message.reply_text(
        f"✅ تمام اخطارهای {target.first_name} پاک شد."
    )


# =========================================================
# MUTE
# =========================================================

async def mute_user(
    bot,
    chat_id,
    user_id,
    minutes,
):

    until_date = (
        datetime.now(timezone.utc)
        + timedelta(minutes=minutes)
    )

    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(
            can_send_messages=False
        ),
        until_date=until_date,
    )


async def mute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target = get_target_user(update.message)

    if not target:

        await update.message.reply_text(
            "🔇 برای میوت کردن، روی پیام کاربر Reply کن."
        )

        return

    if await is_group_admin(update, target.id):

        await update.message.reply_text(
            "❌ نمی‌توان مدیر گروه را میوت کرد."
        )

        return

    minutes = 60

    if context.args:

        try:
            minutes = int(context.args[0])
        except ValueError:
            minutes = 60

    minutes = max(
        1,
        min(minutes, 10080),
    )

    try:

        await mute_user(
            context.bot,
            update.effective_chat.id,
            target.id,
            minutes,
        )

        await update.message.reply_text(
            f"🔇 {target.first_name} به مدت "
            f"{minutes} دقیقه میوت شد."
        )

    except Exception as e:

        logger.error(
            "Mute error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ میوت کردن انجام نشد."
        )


# =========================================================
# UNMUTE
# =========================================================

async def unmute_user(
    bot,
    chat_id,
    user_id,
):

    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        ),
    )


async def unmute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target = get_target_user(update.message)

    if not target:

        await update.message.reply_text(
            "🔊 برای رفع میوت، روی پیام کاربر Reply کن."
        )

        return

    try:

        await unmute_user(
            context.bot,
            update.effective_chat.id,
            target.id,
        )

        await update.message.reply_text(
            f"🔊 میوت {target.first_name} برداشته شد."
        )

    except Exception as e:

        logger.error(
            "Unmute error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ رفع میوت انجام نشد."
        )


# =========================================================
# BAN
# =========================================================

async def ban_user(
    bot,
    chat_id,
    user_id,
):

    await bot.ban_chat_member(
        chat_id=chat_id,
        user_id=user_id,
    )


async def ban_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target = get_target_user(update.message)

    if not target:

        await update.message.reply_text(
            "🚫 برای بن کردن، روی پیام کاربر Reply کن."
        )

        return

    if await is_group_admin(update, target.id):

        await update.message.reply_text(
            "❌ نمی‌توان مدیر گروه را بن کرد."
        )

        return

    try:

        await ban_user(
            context.bot,
            update.effective_chat.id,
            target.id,
        )

        await update.message.reply_text(
            f"🚫 {target.first_name} از گروه بن شد."
        )

    except Exception as e:

        logger.error(
            "Ban error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ بن کردن انجام نشد."
        )


# =========================================================
# UNBAN
# =========================================================

async def unban_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target = get_target_user(update.message)

    if not target:

        await update.message.reply_text(
            "♻️ برای رفع بن، روی پیام کاربر Reply کن."
        )

        return

    try:

        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            only_if_banned=True,
        )

        await update.message.reply_text(
            f"♻️ بن {target.first_name} برداشته شد."
        )

    except Exception as e:

        logger.error(
            "Unban error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ رفع بن انجام نشد."
        )


# =========================================================
# KICK
# =========================================================

async def kick_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target = get_target_user(update.message)

    if not target:

        await update.message.reply_text(
            "👢 برای اخراج، روی پیام کاربر Reply کن."
        )

        return

    if await is_group_admin(update, target.id):

        await update.message.reply_text(
            "❌ نمی‌توان مدیر گروه را اخراج کرد."
        )

        return

    try:

        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
        )

        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
        )

        await update.message.reply_text(
            f"👢 {target.first_name} از گروه اخراج شد."
        )

    except Exception as e:

        logger.error(
            "Kick error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ اخراج انجام نشد."
        )


# =========================================================
# DELETE
# =========================================================

async def del_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_admin(update):
        return

    target_message = update.message.reply_to_message

    if not target_message:

        await update.message.reply_text(
            "🗑️ روی پیامی که می‌خواهی حذف شود Reply کن "
            "و سپس /del بزن."
        )

        return

    try:

        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=target_message.message_id,
        )

        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )

    except Exception as e:

        logger.error(
            "Delete error: %s",
            e,
        )


# =========================================================
# RULES
# =========================================================

async def rules_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    rules = """
📜 قوانین کافه فیلم بین و ضربان موزیک

1️⃣ احترام به اعضای گروه الزامی است.
2️⃣ توهین و مزاحمت ممنوع است.
3️⃣ اسپم و تبلیغات بدون اجازه ممنوع است.
4️⃣ درخواست فیلم، سریال و موزیک را واضح ارسال کنید.
5️⃣ ارسال محتوای نامرتبط ممنوع است.
6️⃣ دستورات مدیران گروه باید رعایت شود.

🎬 درخواست فیلم:
نام فیلم + سال در صورت امکان

🎵 درخواست موزیک:
نام آهنگ + خواننده در صورت امکان
"""

    await update.message.reply_text(rules)


# =========================================================
# ADMINS
# =========================================================

async def admins_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        admins = await context.bot.get_chat_administrators(
            update.effective_chat.id
        )

        text = "👑 مدیران گروه:\n\n"

        for admin in admins:

            user = admin.user

            if user.username:

                text += (
                    f"• {user.first_name} "
                    f"(@{user.username})\n"
                )

            else:

                text += (
                    f"• {user.first_name}\n"
                )

        await update.message.reply_text(text)

    except Exception as e:

        logger.error(
            "Admins error: %s",
            e,
        )

        await update.message.reply_text(
            "❌ دریافت لیست مدیران انجام نشد."
        )


# =========================================================
# ADMIN ID TEST
# =========================================================

async def adminid_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    user_id = update.effective_user.id

    await update.message.reply_text(
        f"🆔 User ID شما:\n"
        f"{user_id}\n\n"
        f"🔐 ADMIN_ID ربات:\n"
        f"{ADMIN_ID}\n\n"
        f"✅ مدیر اصلی:\n"
        f"{'بله' if is_main_admin(user_id) else 'خیر'}"
    )


# =========================================================
# PANEL
# =========================================================

async def panel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    # =====================================================
    # ADMIN ACCESS
    # =====================================================

    if not await require_admin(update):
        return

    message = update.message

    if not message:
        return

    # =====================================================
    # TARGET USER
    # =====================================================

    target = get_target_user(message)

    # =====================================================
    # USER PANEL
    # =====================================================

    if target:

        if await is_group_admin(update, target.id):

            await message.reply_text(
                "❌ پنل مدیریتی برای مدیران گروه قابل اجرا نیست."
            )

            return

        keyboard = [
            [
                InlineKeyboardButton(
                    "⚠️ اخطار",
                    callback_data=f"warn:{target.id}",
                ),
                InlineKeyboardButton(
                    "🔇 میوت",
                    callback_data=f"mute:{target.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔊 رفع میوت",
                    callback_data=f"unmute:{target.id}",
                ),
                InlineKeyboardButton(
                    "🗑 حذف پیام",
                    callback_data=f"del:{message.reply_to_message.message_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🚫 بن",
                    callback_data=f"ban:{target.id}",
                ),
                InlineKeyboardButton(
                    "👢 اخراج",
                    callback_data=f"kick:{target.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚠️ تعداد اخطار",
                    callback_data=f"warnings:{target.id}",
                ),
                InlineKeyboardButton(
                    "♻️ پاک کردن اخطار",
                    callback_data=f"clearwarn:{target.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 آمار گروه",
                    callback_data="members",
                ),
                InlineKeyboardButton(
                    "👑 مدیران",
                    callback_data="admins",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📜 قوانین",
                    callback_data="rules",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ بستن پنل",
                    callback_data="close",
                ),
            ],
        ]

        await message.reply_text(
            f"🛡️ پنل مدیریت کاربر\n\n"
            f"👤 کاربر: {target.first_name}\n"
            f"🆔 `{target.id}`\n\n"
            "یکی از گزینه‌ها را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

        return

    # =====================================================
    # GENERAL PANEL
    # =====================================================

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 آمار گروه",
                callback_data="members",
            ),
            InlineKeyboardButton(
                "👑 مدیران",
                callback_data="admins",
            ),
        ],
        [
            InlineKeyboardButton(
                "📜 قوانین",
                callback_data="rules",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ بستن پنل",
                callback_data="close",
            ),
        ],
    ]

    await message.reply_text(
        "🛡️ پنل مدیریت فیلم‌بین\n\n"
        "مدیریت گروه از طریق گزینه‌های زیر انجام می‌شود.\n\n"
        "💡 برای مدیریت یک کاربر:\n"
        "روی پیام آن کاربر Reply کن و دوباره /panel را بزن.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# PANEL CALLBACK
# =========================================================

async def panel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    # =====================================================
    # ADMIN ACCESS
    # =====================================================

    if not await query_user_is_admin(
        query,
        query.from_user.id,
    ):

        await query.answer(
            "⛔ شما دسترسی مدیریت این ربات را ندارید.",
            show_alert=True,
        )

        return

    await query.answer()

    data = query.data or ""

    if not query.message:
        return

    chat_id = query.message.chat.id

    # =====================================================
    # CLOSE
    # =====================================================

    if data == "close":

        try:

            await query.message.delete()

        except Exception:

            pass

        return

    # =====================================================
    # MEMBERS
    # =====================================================

    if data == "members":

        try:

            count = await context.bot.get_chat_member_count(
                chat_id
            )

            await query.message.reply_text(
                f"📊 تعداد اعضای گروه:\n\n{count}"
            )

        except Exception as e:

            logger.error(
                "Panel members error: %s",
                e,
            )

        return

    # =====================================================
    # ADMINS
    # =====================================================

    if data == "admins":

        try:

            admins = await context.bot.get_chat_administrators(
                chat_id
            )

            text = "👑 مدیران گروه:\n\n"

            for admin in admins:

                user = admin.user

                if user.username:

                    text += (
                        f"• {user.first_name} "
                        f"(@{user.username})\n"
                    )

                else:

                    text += (
                        f"• {user.first_name}\n"
                    )

            await query.message.reply_text(text)

        except Exception as e:

            logger.error(
                "Panel admins error: %s",
                e,
            )

        return

    # =====================================================
    # RULES
    # =====================================================

    if data == "rules":

        rules = """
📜 قوانین کافه فیلم بین و ضربان موزیک

1️⃣ احترام به اعضای گروه الزامی است.
2️⃣ توهین و مزاحمت ممنوع است.
3️⃣ اسپم و تبلیغات بدون اجازه ممنوع است.
4️⃣ درخواست فیلم، سریال و موزیک را واضح ارسال کنید.
5️⃣ ارسال محتوای نامرتبط ممنوع است.
6️⃣ دستورات مدیران گروه باید رعایت شود.
"""

        await query.message.reply_text(rules)

        return

    # =====================================================
    # DELETE BUTTON
    # =====================================================

    if data.startswith("del:"):

        try:

            target_message_id = int(
                data.split(":", 1)[1]
            )

            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=target_message_id,
            )

            await query.message.reply_text(
                "🗑️ پیام حذف شد."
            )

        except Exception as e:

            logger.error(
                "Panel delete error: %s",
                e,
            )

            await query.message.reply_text(
                "❌ حذف پیام انجام نشد."
            )

        return

    # =====================================================
    # PARSE TARGET
    # =====================================================

    parts = data.split(":")

    if len(parts) != 2:
        return

    action = parts[0]

    try:

        target_id = int(parts[1])

    except ValueError:

        return

    # =====================================================
    # TARGET ADMIN CHECK
    # =====================================================

    if action in (
        "warn",
        "mute",
        "unmute",
        "ban",
        "kick",
        "clearwarn",
        "warnings",
    ):

        try:

            target_member = await context.bot.get_chat_member(
                chat_id,
                target_id,
            )

            if target_member.status in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):

                await query.answer(
                    "❌ این کاربر مدیر گروه است.",
                    show_alert=True,
                )

                return

        except Exception as e:

            logger.error(
                "Target admin check error: %s",
                e,
            )

    # =====================================================
    # WARN
    # =====================================================

    if action == "warn":

        add_warning(
            chat_id,
            target_id,
            query.from_user.id,
            "اخطار از طریق پنل مدیریت",
        )

        count = get_warning_count(
            chat_id,
            target_id,
        )

        try:

            member = await context.bot.get_chat_member(
                chat_id,
                target_id,
            )

            name = member.user.first_name

        except Exception:

            name = "کاربر"

        await query.message.reply_text(
            f"⚠️ اخطار ثبت شد.\n\n"
            f"👤 کاربر: {name}\n"
            f"🔢 تعداد اخطار: {count}/3"
        )

        if count >= 3:

            try:

                await mute_user(
                    context.bot,
                    chat_id,
                    target_id,
                    60,
                )

                await query.message.reply_text(
                    f"🔇 {name} به دلیل رسیدن به "
                    "۳ اخطار، به مدت ۱ ساعت میوت شد."
                )

            except Exception as e:

                logger.error(
                    "Panel auto mute error: %s",
                    e,
                )

        return

    # =====================================================
    # WARNINGS
    # =====================================================

    if action == "warnings":

        count = get_warning_count(
            chat_id,
            target_id,
        )

        await query.message.reply_text(
            f"⚠️ تعداد اخطارهای کاربر:\n\n{count}"
        )

        return

    # =====================================================
    # CLEAR WARNINGS
    # =====================================================

    if action == "clearwarn":

        clear_warnings(
            chat_id,
            target_id,
        )

        await query.message.reply_text(
            "✅ تمام اخطارهای کاربر پاک شد."
        )

        return

    # =====================================================
    # MUTE
    # =====================================================

    if action == "mute":

        try:

            await mute_user(
                context.bot,
                chat_id,
                target_id,
                60,
            )

            await query.message.reply_text(
                "🔇 کاربر به مدت ۶۰ دقیقه میوت شد."
            )

        except Exception as e:

            logger.error(
                "Panel mute error: %s",
                e,
            )

            await query.message.reply_text(
                "❌ میوت انجام نشد."
            )

        return

    # =====================================================
    # UNMUTE
    # =====================================================

    if action == "unmute":

        try:

            await unmute_user(
                context.bot,
                chat_id,
                target_id,
            )

            await query.message.reply_text(
                "🔊 میوت کاربر برداشته شد."
            )

        except Exception as e:

            logger.error(
                "Panel unmute error: %s",
                e,
            )

            await query.message.reply_text(
                "❌ رفع میوت انجام نشد."
            )

        return

    # =====================================================
    # BAN
    # =====================================================

    if action == "ban":

        try:

            await ban_user(
                context.bot,
                chat_id,
                target_id,
            )

            await query.message.reply_text(
                "🚫 کاربر از گروه بن شد."
            )

        except Exception as e:

            logger.error(
                "Panel ban error: %s",
                e,
            )

            await query.message.reply_text(
                "❌ بن انجام نشد."
            )

        return

    # =====================================================
    # KICK
    # =====================================================

    if action == "kick":

        try:

            await context.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=target_id,
            )

            await context.bot.unban_chat_member(
                chat_id=chat_id,
                user_id=target_id,
            )

            await query.message.reply_text(
                "👢 کاربر از گروه اخراج شد."
            )

        except Exception as e:

            logger.error(
                "Panel kick error: %s",
                e,
            )

            await query.message.reply_text(
                "❌ اخراج انجام نشد."
            )

        return


# =========================================================
# UNKNOWN COMMAND
# =========================================================

async def unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.message:

        await update.message.reply_text(
            "❓ دستور شناخته نشد.\n"
            "برای مشاهده دستورات /help را بزن."
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # =====================================================
    # COMMANDS
    # =====================================================

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("id", id_command)
    )

    application.add_handler(
        CommandHandler("adminid", adminid_command)
    )

    application.add_handler(
        CommandHandler("members", members_command)
    )

    application.add_handler(
        CommandHandler("warn", warn_command)
    )

    application.add_handler(
        CommandHandler("warnings", warnings_command)
    )

    application.add_handler(
        CommandHandler("clearwarn", clearwarn_command)
    )

    application.add_handler(
        CommandHandler("mute", mute_command)
    )

    application.add_handler(
        CommandHandler("unmute", unmute_command)
    )

    application.add_handler(
        CommandHandler("ban", ban_command)
    )

    application.add_handler(
        CommandHandler("unban", unban_command)
    )

    application.add_handler(
        CommandHandler("kick", kick_command)
    )

    application.add_handler(
        CommandHandler("del", del_command)
    )

    application.add_handler(
        CommandHandler("rules", rules_command)
    )

    application.add_handler(
        CommandHandler("admins", admins_command)
    )

    application.add_handler(
        CommandHandler("panel", panel_command)
    )

    # =====================================================
    # CALLBACK BUTTONS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(panel_callback)
    )

    # =====================================================
    # NEW MEMBERS
    # =====================================================

    application.add_handler(
        ChatMemberHandler(
            new_member,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    # =====================================================
    # UNKNOWN COMMANDS
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            unknown_command,
        )
    )

    # =====================================================
    # ERROR HANDLER
    # =====================================================

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Cafe Film Bin + Zaraban Music Bot started"
    )

    logger.info(
        "ADMIN_ID = %s",
        ADMIN_ID,
    )

    # =====================================================
    # RUN
    # =====================================================

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
