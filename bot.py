import os
import asyncio
import logging
from io import BytesIO

import base64
import requests

from dotenv import load_dotenv
from telegram import Update, BotCommand, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()
try:
    BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0").strip() or "0")
except ValueError:
    BOT_OWNER_ID = 0

MAX_PROMPT_LENGTH = 3000


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "بدء البوت"),
        BotCommand("help", "طريقة الاستخدام"),
        BotCommand("status", "حالة آخر طلب"),
        BotCommand("edit_file", "تعديل ملف في المستودع"),
    ]
    await application.bot.set_my_commands(commands)


def _configure_owner() -> None:
    if BOT_OWNER_ID:
        st.set_owner(BOT_OWNER_ID)
        st.add_user(BOT_OWNER_ID, "")
        st.approve_user(BOT_OWNER_ID)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user = update.effective_user
    uid = user.id

    if BOT_OWNER_ID:
        _configure_owner()
        if uid == BOT_OWNER_ID:
            await update.message.reply_text(
                "👑 أهلاً بك يا مالك البوت.\n\n"
                "أرسل صورة ثم وصف الفيديو، أو أرسل وصفاً فقط. "
                "سيُضاف صوت عربي ويُحرق النص داخل الفيديو."
            )
            return
        st.add_user(uid, user.username or "")
        if not st.is_approved(uid):
            await update.message.reply_text("🔒 حسابك غير مصرح له باستخدام البوت.")
            return
    elif st.get_state().get("owner_id", 0) == 0:
        st.set_owner(uid)
        st.add_user(uid, user.username or "")
        st.approve_user(uid)
        await update.message.reply_text(
            "👑 أهلاً بك يا مالك البوت.\n\n"
            "أرسل صورة ثم وصف الفيديو، أو أرسل وصفاً فقط."
        )
        return
    elif not st.is_approved(uid):
        st.add_user(uid, user.username or "")
        await update.message.reply_text("🚫 غير مصرح لك. تواصل مع مالك البوت.")
        return

    await update.message.reply_text(
        "✅ البوت جاهز.\n"
        "أرسل صورة وانتظر التأكيد، ثم أرسل وصف الفيديو والسكريبت. "
        "يمكنك أيضاً إرسال وصفاً فقط."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "طريقة الاستخدام:\n"
        "• صورة ثم وصف/سكريبت: فيديو من الصورة.\n"
        "• وصف فقط: فيديو من النص.\n"
        "• /status : حالة آخر طلب.\n"
        "• /edit_file : تعديل ملف في المستودع (للمالك فقط).\n\n"
        "المدة المطلوبة 10 ثوانٍ على الأقل، مع تعليق صوتي عربي ونص ظاهر في الفيديو."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not st.is_approved(uid) and not (BOT_OWNER_ID and uid == BOT_OWNER_ID):
        await update.message.reply_text("🚫 غير مصرح لك.")
        return

    jobs = st.get_state().get("jobs", {})
    user_jobs = [
        (job_id, job)
        for job_id, job in jobs.items()
        if str(job.get("user_id")) == str(uid)
    ]
    if not user_jobs:
        await update.message.reply_text("ما عندك طلبات مسجلة حالياً.")
        return

    user_jobs.sort(key=lambda item: item[1].get("created", 0), reverse=True)
    job_id, job = user_jobs[0]
    labels = {
        "queued": "بالانتظار",
        "processing": "قيد المعالجة",
        "done": "مكتمل",
        "error": "فشل",
    }
    status = labels.get(job.get("status", "unknown"), job.get("status", "unknown"))
    await update.message.reply_text(f"📋 آخر طلب\nالحالة: {status}\nرقم الطلب: {job_id}")


async def cmd_edit_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    if not st.is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 هذا الأمر للمالك فقط.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /edit_file <مسار_الملف> <المحتوى_الجديد>")
        return
    if not GITHUB_TOKEN or not GITHUB_REPO:
        await update.message.reply_text("❌ إعداد GITHUB_TOKEN أو GITHUB_REPO غير موجود.")
        return

    file_path = context.args[0]
    new_content = " ".join(context.args[1:])
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"

    try:
        response = await asyncio.to_thread(
            requests.get, url, headers=headers, timeout=30
        )
        if response.status_code != 200:
            await update.message.reply_text(
                f"❌ فشل جلب الملف من GitHub (HTTP {response.status_code})."
            )
            return

        sha = response.json().get("sha")
        if not sha:
            await update.message.reply_text("❌ لم يرجع GitHub بصمة الملف الحالية.")
            return
        data = {
            "message": f"Update {file_path} via Telegram Bot",
            "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": "main",
        }
        response = await asyncio.to_thread(
            requests.put, url, headers=headers, json=data, timeout=30
        )
        if response.status_code in (200, 201):
            await update.message.reply_text(f"✅ تم تحديث {file_path} في GitHub.")
        else:
            await update.message.reply_text(
                f"❌ فشل تحديث الملف (HTTP {response.status_code}): "
                f"{response.text[:500]}"
            )
    except requests.RequestException as exc:
        log.error("GitHub file edit failed (%s).", type(exc).__name__)
        await update.message.reply_text("❌ تعذر الاتصال بـ GitHub. راجع سجل Actions.")
    except Exception as exc:
        log.error("GitHub file edit failed (%s).", type(exc).__name__)
        await update.message.reply_text("❌ حدث خطأ أثناء تعديل الملف.")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not st.is_approved(uid) and not (BOT_OWNER_ID and uid == BOT_OWNER_ID):
        await update.message.reply_text("🚫 غير مصرح لك. اكتب /start أولاً.")
        return

    try:
        photo = update.message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        image_buffer = BytesIO()
        await telegram_file.download_to_memory(image_buffer)
        image_bytes = image_buffer.getvalue()
        if not image_bytes:
            await update.message.reply_text("❌ الصورة فارغة. أعد إرسالها.")
            return
        context.user_data["pending_image"] = image_bytes
        await update.message.reply_text(
            "📸 تم استلام الصورة. الآن أرسل وصف الفيديو والسكريبت الذي سيُقرأ صوتياً."
        )
    except Exception as exc:
        log.error("Telegram photo download failed (%s).", type(exc).__name__)
        await update.message.reply_text("❌ تعذر تنزيل الصورة من Telegram. أعد إرسالها.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    prompt = (update.message.text or "").strip()

    if not st.is_approved(uid) and not (BOT_OWNER_ID and uid == BOT_OWNER_ID):
        await update.message.reply_text("🚫 غير مصرح لك. اكتب /start أولاً.")
        return
    if not prompt:
        await update.message.reply_text("أرسل وصف الفيديو أولاً.")
        return
    if len(prompt) > MAX_PROMPT_LENGTH:
        await update.message.reply_text(
            f"الوصف طويل جداً. الحد الأقصى {MAX_PROMPT_LENGTH} حرف."
        )
        return

    image_bytes = context.user_data.pop("pending_image", None)
    if context.user_data.get("job_running"):
        if image_bytes:
            context.user_data["pending_image"] = image_bytes
        await update.message.reply_text("⏳ لديك طلب قيد المعالجة. انتظر حتى ينتهي.")
        return

    job_id = st.new_job(uid, prompt)
    context.user_data["job_running"] = True
    asyncio.create_task(
        process_job(context, job_id, prompt, image_bytes, update)
    )


async def process_job(context, job_id, prompt, image_bytes, update):
    message = update.effective_message
    if not message:
        context.user_data["job_running"] = False
        return

    st.update_job(job_id, status="processing")
    try:
        await message.reply_text(
            "🎬 بدأ تجهيز الفيديو. قد يستغرق التوليد عدة دقائق. "
            "لن أكرر طلب Magic Hour تلقائياً حتى لا يتكرر خصم الرصيد."
        )

        image_file_path = None
        if image_bytes:
            await message.reply_text("📤 جاري رفع الصورة إلى تخزين Magic Hour الرسمي...")
            image_file_path = await apis.upload_image(
                image_bytes,
                filename="telegram_image.jpg",
            )
            if not image_file_path:
                error = (
                    "فشل رفع الصورة إلى Magic Hour؛ لم يُرسل طلب توليد ولم يُخصم رصيد للتوليد. "
                    "راجع سجل GitHub Actions لتفاصيل الرفع."
                )
                st.update_job(job_id, status="error", error=error)
                await message.reply_text("❌ " + error)
                return

        await message.reply_text("🧠 جاري إنشاء الفيديو...")
        video_url, provider, error = await apis.generate_video_with_fallback(
            original_prompt=prompt,
            image_file_path=image_file_path,
            script_text=prompt,
        )
        if not video_url:
            error = error or "لم ترجع خدمة التوليد رابط فيديو."
            st.update_job(job_id, status="error", error=error)
            await message.reply_text("❌ لم يكتمل التوليد.\n\nالتفاصيل: " + error[:900])
            return

        await message.reply_text("🎙️ جاري إضافة الصوت العربي وحرق النص والتحقق من الفيديو...")
        final_video = await apis.process_video_with_audio_and_text(video_url, prompt)
        video_buffer = BytesIO(final_video)
        video_buffer.name = "telegram_ai_video.mp4"
        video_buffer.seek(0)

        await message.reply_video(
            video=InputFile(video_buffer, filename="telegram_ai_video.mp4"),
            caption=(
                f"✅ اكتمل الفيديو عبر {provider or 'Magic Hour'}\n"
                "الصوت العربي والنص أُضيفا، وتم التحقق من مدة لا تقل عن 10 ثوانٍ."
            ),
            supports_streaming=True,
            read_timeout=300,
            write_timeout=300,
            connect_timeout=60,
            pool_timeout=60,
        )
        st.update_job(job_id, status="done", provider=provider or "Magic Hour")
        st.record_video(provider or "Magic Hour")

    except Exception as exc:
        log.exception("Video job %s failed.", job_id)
        error = str(exc).strip() or type(exc).__name__
        st.update_job(job_id, status="error", error=error[:700])
        try:
            await message.reply_text(
                f"❌ تعذر تجهيز الفيديو.\nالتفاصيل: {error[:900]}\nرقم الطلب: {job_id}"
            )
        except Exception as send_exc:
            log.error("Failed to send Telegram error for job %s (%s).", job_id, type(send_exc).__name__)
    finally:
        context.user_data["job_running"] = False


def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN غير موجود في GitHub Secrets.")
    if not os.getenv("MAGIC_HOUR_API_KEY", "").strip():
        raise RuntimeError("MAGIC_HOUR_API_KEY غير موجود في GitHub Secrets.")

    _configure_owner()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("edit_file", cmd_edit_file))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
