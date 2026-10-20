
import os
import asyncio
import logging
from io import BytesIO

from dotenv import load_dotenv
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
    ContextTypes,
    filters,
)

import storage as st
import apis

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

try:
    OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0").strip() or "0")
except ValueError:
    OWNER_ID = 0

MAX_PROMPT_LENGTH = 3000


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "بدء البوت"),
        BotCommand("help", "طريقة الاستخدام"),
        BotCommand("status", "حالة آخر طلب"),
        BotCommand("admin", "لوحة المالك"),
    ]
    await application.bot.set_my_commands(commands)


def is_allowed(user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    return st.is_approved(user_id)


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    user = update.effective_user
    uid = user.id

    # تثبيت المالك من GitHub Secret إذا تم ضبطه.
    if OWNER_ID:
        if st.get_state().get("owner_id") != OWNER_ID:
            st.set_owner(OWNER_ID)

        if uid == OWNER_ID:
            st.add_user(uid, user.username or "")
            st.approve_user(uid)

            await update.message.reply_text(
                "👑 أهلاً بك يا مالك البوت.\n\n"
                "أرسل صورة ثم وصف الفيديو، أو أرسل وصفاً فقط "
                "لتجربة توليد فيديو من النص."
            )
            return

    # طريقة التشغيل القديمة إذا لم يتم تعيين BOT_OWNER_ID.
    if st.get_state().get("owner_id", 0) == 0:
        st.set_owner(uid)
        st.add_user(uid, user.username or "")
        st.approve_user(uid)

        await update.message.reply_text(
            f"👑 أصبحت مالك البوت.\n"
            f"معرّف Telegram الخاص بك: {uid}\n\n"
            "أرسل صورة ثم وصف الفيديو."
        )
        return

    st.add_user(uid, user.username or "")

    if not is_allowed(uid):
        await update.message.reply_text(
            "🔒 حسابك غير مصرح له باستخدام البوت. "
            "تواصل مع المالك."
        )
        return

    await update.message.reply_text(
        "✅ البوت جاهز.\n\n"
        "1. أرسل صورة وانتظر تأكيد استلامها.\n"
        "2. أرسل وصف الفيديو.\n"
        "3. سيصلك الفيديو عند اكتمال التوليد.\n\n"
        "يمكنك أيضاً إرسال وصف فقط لتوليد فيديو من النص."
    )


async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    await update.message.reply_text(
        "📖 طريقة الاستخدام\n\n"
        "• صورة ثم وصف: توليد فيديو من صورتك.\n"
        "• وصف فقط: توليد فيديو من النص.\n"
        "• /status : حالة آخر طلب.\n"
        "• /admin : لوحة المالك.\n\n"
        "مدة الفيديو الافتراضية 10 ثوانٍ، "
        "والدقة 480p للمحافظة على الرصيد."
    )


async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id

    if not is_allowed(uid):
        await update.message.reply_text("🚫 غير مصرح لك.")
        return

    jobs = st.get_state().get("jobs", {})
    user_jobs = [
        (job_id, job)
        for job_id, job in jobs.items()
        if str(job.get("user_id")) == str(uid)
    ]

    if not user_jobs:
        await update.message.reply_text(
            "ما عندك طلبات مسجلة حالياً."
        )
        return

    user_jobs.sort(
        key=lambda item: item[1].get("created", 0),
        reverse=True,
    )

    job_id, job = user_jobs[0]
    status = job.get("status", "unknown")
    prompt = (job.get("prompt") or "").strip()

    status_ar = {
        "queued": "بالانتظار",
        "processing": "قيد المعالجة",
        "done": "مكتمل",
        "error": "فشل",
    }.get(status, status)

    await update.message.reply_text(
        f"📋 آخر طلب\n"
        f"الحالة: {status_ar}\n"
        f"رقم الطلب: {job_id}\n"
        f"الوصف: {prompt[:300] or 'غير متوفر'}"
    )


async def cmd_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id

    if not st.is_owner(uid) and not (OWNER_ID and uid == OWNER_ID):
        await update.message.reply_text("🚫 هذه اللوحة للمالك فقط.")
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
                "🏥 فحص الإعدادات",
                callback_data="admin_health",
            )
        ],
    ]

    await update.message.reply_text(
        "👑 لوحة التحكم",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    uid = query.from_user.id
    if not st.is_owner(uid) and not (OWNER_ID and uid == OWNER_ID):
        await query.edit_message_text("🚫 غير مصرح لك.")
        return

    if query.data == "admin_stats":
        stats = st.get_state().get("stats", {})

        await query.edit_message_text(
            "📊 إحصائيات البوت\n\n"
            f"🎬 الفيديوهات المكتملة: "
            f"{stats.get('total_videos', 0)}\n"
            f"👥 المستخدمون المسجلون: "
            f"{stats.get('total_users', 0)}\n"
            f"⚠️ الأخطاء المسجلة: "
            f"{stats.get('total_errors', 0)}"
        )

    elif query.data == "admin_health":
        health = await apis.health_check()
        lines = ["🏥 فحص إعدادات التوليد\n"]

        for name, ok in health.items():
            lines.append(f"{'✅' if ok else '❌'} {name}")

        await query.edit_message_text("\n".join(lines))


async def on_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id

    if not is_allowed(uid):
        await update.message.reply_text(
            "🚫 غير مصرح لك. اكتب /start أولاً."
        )
        return

    try:
        photo = update.message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)

        image_buffer = BytesIO()
        await telegram_file.download_to_memory(image_buffer)
        image_bytes = image_buffer.getvalue()

        if not image_bytes:
            await update.message.reply_text(
                "❌ وصلت رسالة الصورة لكن الملف فارغ. "
                "أعد إرسال الصورة."
            )
            return

        context.user_data["pending_image"] = image_bytes

        await update.message.reply_text(
            "📸 تم استلام الصورة بنجاح.\n"
            "الآن أرسل وصف الفيديو الذي تريد إنشاءه."
        )

    except Exception:
        log.exception("Failed to download Telegram photo.")
        await update.message.reply_text(
            "❌ تعذر تنزيل الصورة من Telegram. "
            "أعد إرسالها وحاول مرة أخرى."
        )


async def on_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id
    prompt = (update.message.text or "").strip()

    if not is_allowed(uid):
        await update.message.reply_text(
            "🚫 غير مصرح لك. اكتب /start أولاً."
        )
        return

    if not prompt:
        await update.message.reply_text("أرسل وصف الفيديو أولاً.")
        return

    if len(prompt) > MAX_PROMPT_LENGTH:
        await update.message.reply_text(
            f"الوصف طويل جداً. الحد الأقصى "
            f"{MAX_PROMPT_LENGTH} حرف."
        )
        return

    image_bytes = context.user_data.pop("pending_image", None)

    # منع طلبات متزامنة من المستخدم نفسه.
    if context.user_data.get("job_running"):
        if image_bytes:
            context.user_data["pending_image"] = image_bytes

        await update.message.reply_text(
            "⏳ عندك طلب قيد المعالجة. انتظر حتى ينتهي أولاً."
        )
        return

    job_id = st.new_job(uid, prompt)
    context.user_data["job_running"] = True

    asyncio.create_task(
        process_job(
            context=context,
            job_id=job_id,
            prompt=prompt,
            image_bytes=image_bytes,
            update=update,
        )
    )


async def process_job(
    context: ContextTypes.DEFAULT_TYPE,
    job_id: str,
    prompt: str,
    image_bytes: bytes | None,
    update: Update,
):
    message = update.effective_message
    uid = update.effective_user.id if update.effective_user else 0

    try:
        st.update_job(job_id, status="processing")

        await message.reply_text(
            "🎬 بدأ تجهيز طلبك.\n"
            "قد يستغرق التوليد عدة دقائق. "
            "لن أكرر طلب التوليد تلقائياً حتى لا يتكرر خصم الرصيد."
        )

        image_file_path = None

        if image_bytes:
            await message.reply_text(
                "📤 جاري رفع الصورة إلى خدمة التوليد الرسمية..."
            )

            image_file_path = await apis.upload_image(
                image_bytes,
                filename="telegram_image.jpg",
            )

            if not image_file_path:
                error = (
                    "فشل رفع الصورة إلى Magic Hour. "
                    "لم يتم إرسال طلب توليد الفيديو، "
                    "حتى لا يُستهلك الرصيد على طلب غير مكتمل. "
                    "راجع سجل GitHub Actions لمعرفة تفاصيل الرفع."
                )
                st.update_job(
                    job_id,
                    status="error",
                    error=error,
                )

                await message.reply_text("❌ " + error)
                return

        await message.reply_text(
            "🧠 تم تجهيز المدخلات. جاري إنشاء الفيديو..."
        )

        video_url, provider, error = (
            await apis.generate_video_with_fallback(
                original_prompt=prompt,
                image_file_path=image_file_path,
            )
        )

        if not video_url:
            error = error or "لم ترجع خدمة التوليد رابط فيديو."
            st.update_job(
                job_id,
                status="error",
                error=error,
            )
            await message.reply_text(
                "❌ لم يكتمل التوليد.\n\n"
                f"التفاصيل: {error}"
            )
            return

        await message.reply_text(
            "⬇️ اكتمل التوليد. جاري تنزيل الفيديو لإرساله..."
        )

        video_bytes = await apis.download_video(video_url)

        if not video_bytes:
            error = (
                "اكتمل التوليد لكن تعذر تنزيل الفيديو لإرساله إلى Telegram. "
                "لم أعد طلب التوليد حتى لا يتكرر خصم الرصيد."
            )
            st.update_job(
                job_id,
                status="error",
                video_url=video_url,
                error=error,
            )
            await message.reply_text(
                "⚠️ " + error + "\n"
                "يمكن فحص رابط النتيجة في سجل المشروع لدى Magic Hour."
            )
            return

        video_file = BytesIO(video_bytes)
        video_file.name = "ai_video.mp4"
        video_file.seek(0)

        await message.reply_video(
            video=video_file,
            caption=(
                f"✅ اكتمل الفيديو بنجاح\n"
                f"المحرك: {provider or 'Magic Hour'}\n"
                f"المدة المطلوبة: 10 ثوانٍ\n"
                f"الدقة المطلوبة: 480p"
            ),
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
            pool_timeout=30,
        )

        st.update_job(
            job_id,
            status="done",
            provider=provider or "Magic Hour",
        )
        st.record_video(provider or "Magic Hour")

    except Exception as exc:
        log.exception("Job %s failed.", job_id)

        error = (
            "حدث خطأ تقني أثناء معالجة الطلب. "
            "راجع سجل GitHub Actions للحصول على التفاصيل."
        )

        try:
            st.update_job(
                job_id,
                status="error",
                error=str(exc)[:700],
            )
        except Exception:
            log.exception("Failed to save job error.")

        try:
            await message.reply_text(
                f"❌ {error}\n"
                f"رقم الطلب: {job_id}"
            )
        except Exception:
            log.exception("Failed to send error message to Telegram.")

    finally:
        # إزالة حالة الانشغال حتى يستطيع المستخدم طلب فيديو آخر.
        try:
            for user_id, user_data in context.application.user_data.items():
                if str(user_id) == str(uid):
                    user_data["job_running"] = False
                    break
        except Exception:
            log.exception("Failed to reset user job state.")


async def on_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    log.error(
        "Unhandled Telegram update error: %s",
        context.error,
        exc_info=context.error,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN غير موجود في GitHub Secrets."
        )

    if not os.getenv("MAGIC_HOUR_API_KEY", "").strip():
        raise RuntimeError(
            "MAGIC_HOUR_API_KEY غير موجود في GitHub Secrets."
        )

    # تثبيت المالك عبر Secret يحمي صلاحياته من ضياع الحالة
    # عند إعادة تشغيل GitHub Actions.
    if OWNER_ID:
        st.set_owner(OWNER_ID)
        st.add_user(OWNER_ID, "")
        st.approve_user(OWNER_ID)

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
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_text,
        )
    )

    app.add_error_handler(on_error)

    log.info("Telegram AI Video Bot is starting.")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
