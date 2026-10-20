
import os
import asyncio
import logging
from io import BytesIO

from dotenv import load_dotenv

load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

import storage as st
import apis

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "بدء البوت والقائمة الرئيسية"),
        BotCommand("help", "طريقة استخدام البوت"),
        BotCommand("status", "حالة البوت"),
        BotCommand("admin", "لوحة تحكم المالك"),
    ]
    await application.bot.set_my_commands(commands)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return

    uid = user.id

    if st.get_state().get("owner_id") == 0:
        st.set_owner(uid)
        st.add_user(uid, user.username or "")
        st.approve_user(uid)

        await update.message.reply_text(
            f"👑 مرحباً بك يا مالك البوت!\n"
            f"معرّفك: {uid}\n\n"
            "أرسل وصفاً أو صورة ثم وصف الفيديو."
        )
        return

    st.add_user(uid, user.username or "")

    if not st.is_approved(uid):
        await update.message.reply_text(
            "🚫 غير مصرّح لك باستخدام البوت."
        )
        return

    await update.message.reply_text(
        "✅ البوت جاهز.\n"
        "أرسل وصف الفيديو، أو صورة ثم وصفاً."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "📖 طريقة الاستخدام:\n\n"
            "1. أرسل وصف الفيديو فقط.\n"
            "2. أو أرسل صورة وانتظر تأكيد الاستلام.\n"
            "3. أرسل وصف الحركة المطلوبة.\n"
            "4. انتظر اكتمال التوليد.\n\n"
            "مدة الفيديو المستهدفة: 10 ثوانٍ."
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "🤖 البوت يعمل. هذا الأمر لا يؤكد أن خدمة التوليد متاحة."
        )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    if not st.is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 هذا الأمر للمالك فقط.")
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 الإحصائيات",
                callback_data="admin_stats",
            )
        ],
        [
            InlineKeyboardButton(
                "🏥 فحص إعدادات API",
                callback_data="admin_health",
            )
        ],
    ]

    await update.message.reply_text(
        "👑 لوحة تحكم المالك",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or not st.is_owner(user.id):
        await query.edit_message_text("🚫 هذا الأمر للمالك فقط.")
        return

    if query.data == "admin_stats":
        state = st.get_state()
        stats = state.get("stats", {})

        await query.edit_message_text(
            "📊 الإحصائيات\n\n"
            f"🎬 الفيديوهات: {stats.get('total_videos', 0)}\n"
            f"👥 المستخدمون: {stats.get('total_users', 0)}\n"
            f"❌ الأخطاء: {stats.get('total_errors', 0)}"
        )

    elif query.data == "admin_health":
        health = await apis.health_check()
        lines = ["🏥 إعدادات خدمة التوليد:\n"]

        for key, value in health.items():
            lines.append(f"{'✅' if value else '❌'} {key}: {value}")

        await query.edit_message_text("\n".join(lines))


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id

    if not st.is_approved(uid):
        await update.message.reply_text(
            "🚫 غير مصرّح لك. اكتب /start."
        )
        return

    try:
        photo = update.message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)

        image_buffer = BytesIO()
        await telegram_file.download_to_memory(image_buffer)

        context.user_data["pending_image"] = image_buffer.getvalue()

        await update.message.reply_text(
            "📸 تم استلام الصورة. أرسل وصف الفيديو الآن."
        )

    except Exception:
        log.exception("Failed to download Telegram image")
        await update.message.reply_text(
            "❌ تعذر تنزيل الصورة من تيليجرام. أعد إرسالها."
        )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id
    prompt = (update.message.text or "").strip()

    if not prompt:
        return

    if not st.is_approved(uid):
        await update.message.reply_text(
            "🚫 غير مصرّح لك. اكتب /start."
        )
        return

    image_bytes = context.user_data.pop("pending_image", None)

    job_id = st.new_job(uid, prompt)

    await process_job(
        context,
        job_id,
        prompt,
        image_bytes,
        update,
    )


async def process_job(context, job_id, prompt, image_bytes, update):
    st.update_job(job_id, status="processing")

    message = update.message
    if not message:
        st.update_job(job_id, status="error", error="Missing message")
        return

    try:
        await message.reply_text(
            "⏳ بدأت معالجة طلبك. قد يستغرق التوليد عدة دقائق."
        )

        image_url = None

        if image_bytes:
            await message.reply_text("📤 جاري رفع الصورة...")

            image_url = await apis.upload_image(image_bytes)

            if not image_url:
                error = (
                    "تعذر رفع الصورة إلى خدمة الاستضافة. "
                    "لم يتم إرسال طلب توليد الفيديو."
                )
                st.update_job(
                    job_id,
                    status="error",
                    error=error,
                )
                await message.reply_text(f"❌ {error}")
                return

        video_url, provider, error = (
            await apis.generate_video_with_fallback(
                prompt,
                image_url,
            )
        )

        if not video_url:
            st.update_job(
                job_id,
                status="error",
                error=error or "Generation failed",
            )
            await message.reply_text(
                f"❌ فشل التوليد:\n{error or 'خطأ غير معروف'}"
            )
            return

        st.record_video(provider)
        st.update_job(
            job_id,
            status="done",
            video_url=video_url,
        )

        await message.reply_video(
            video=video_url,
            caption=f"✅ تم التوليد عبر {provider}",
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
            pool_timeout=30,
        )

    except Exception as exc:
        log.exception("Video job failed")

        st.update_job(
            job_id,
            status="error",
            error=str(exc)[:500],
        )

        try:
            await message.reply_text(
                f"❌ حدث خطأ أثناء المعالجة: {str(exc)[:300]}"
            )
        except Exception:
            log.exception("Could not send error message")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(
        "Unhandled Telegram update error",
        exc_info=context.error,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN غير موجود في إعدادات التشغيل."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("admin", cmd_admin))

    app.add_handler(
        CallbackQueryHandler(on_callback, pattern="^admin_")
    )
    app.add_handler(
        MessageHandler(filters.PHOTO, on_photo)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )

    app.add_error_handler(on_error)

    log.info("Telegram bot starting")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
