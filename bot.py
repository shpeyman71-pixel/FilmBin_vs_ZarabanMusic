import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone

import psycopg2

from telegram import Update, ChatPermissions
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ChatMemberHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# حذف پیام خوش‌آمدگویی بعد از 3 دقیقه
WELCOME_DELETE_SECONDS = 180

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
        (chat_id, user_id, admin_id, reason),
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
        WHERE chat_id = %s AND user_id = %s
        """,
        (chat_id, user_id),
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
        WHERE chat_id = %s AND user_id = %s
        """,
        (chat_id, user_id),
    )

    conn.commit()
    cur.close()
    conn.close()


# =========================================================
# ADMIN CHECK
# =========================================================

async def is_admin(update: Update, user_id: int = None):
    """
    بررسی مدیر بودن کاربر:

    1. ADMIN_ID در Railway = مدیر اصلی
    2. مدیران واقعی گروه نیز مدیر محسوب می‌شوند
    """

    if not update.effective_chat:
        return False

    if user_id is None:
        if not update.effective_user:
            return False

        user_id = update.effective_user.id

    # مدیر اصلی
    if ADMIN_ID and user_id == ADMIN_ID:
        return True

    try:
        member = await update.effective_chat.get_member(user_id)

        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as e:
        logger.error("Admin check error: %s", e)
        return False


async def require_admin(update: Update):
    if not await is_admin(update):

        if update.message:
            await update.message.reply_text(
                "⛔ این دستور فقط برای مدیران گروه است."
            )

        return False

    return True


# =========================================================
# TARGET USER
# =========================================================

def get_target_user(message):
    """
    کاربر هدف از طریق Reply مشخص می‌شود.
    """

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

/warn
اخطار به کاربر

/warnings
مشاهده تعداد اخطار

/clearwarn
پاک کردن اخطارها

/mute
میوت کاربر

/unmute
رفع میوت

/ban
مسدود کردن کاربر

/unban
رفع مسدودی

/kick
اخراج کاربر

/del
حذف پیام

/members
تعداد اعضای گروه

/admins
لیست مدیران

/rules
قوانین گروه

/id
نمایش آیدی

━━━━━━━━━━━━━━

📌 برای دستورات مدیریتی:
روی پیام کاربر Reply کنید.
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
        old_status
        in (
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED,
        )
        and new_status
        in (
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

    if await is_admin(update, target.id):

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

    # 3 اخطار = میوت 1 ساعت
    if count >= 3:

        until_date = (
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        )

        try:

            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target.id,
                permissions=ChatPermissions(
                    can_send_messages=False
                ),
                until_date=until_date,
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

            await message.reply_text(
                "⚠️ اخطار ثبت شد، اما میوت خودکار انجام نشد."
            )


# =========================================================
# WARNINGS
# =========================================================

async def warnings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

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

    if await is_admin(update, target.id):

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

    until_date = (
        datetime.now(timezone.utc)
        + timedelta(minutes=minutes)
    )

    try:

        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            permissions=ChatPermissions(
                can_send_messages=False
            ),
            until_date=until_date,
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

        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
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

    if await is_admin(update, target.id):

        await update.message.reply_text(
            "❌ نمی‌توان مدیر گروه را بن کرد."
        )

        return

    try:

        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
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

    if await is_admin(update, target.id):

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

    if not update.message:
        return

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

    # اتصال و ساخت جداول دیتابیس
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

    # =====================================================
    # NEW MEMBER
    # =====================================================

    application.add_handler(
        ChatMemberHandler(
            new_member,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    # =====================================================
    # UNKNOWN COMMAND
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

    # =====================================================
    # RUN
    # =====================================================

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# START BOT
# =========================================================

if __name__ == "__main__":
    main()
