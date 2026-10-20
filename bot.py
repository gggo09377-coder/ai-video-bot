import os
import asyncio
import logging
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)
from dotenv import load_dotenv
import base64
import requests
import json

import storage as st
import apis

load_dotenv()
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()

async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "بدء البوت"),
        BotCommand("help", "طريقة الاستخدام"),
        BotCommand("edit_file", "تعديل ملف في المستودع"),
    ]
    await application.bot.set_my_commands(commands)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    if st.get_state().get("owner_id") == 0:
        st.set_owner(uid); st.add_user(uid, user.username or ""); st.approve_user(uid)
        await update.message.reply_text("👑 مرحباً بك يا مالك البوت! البوت جاهز.")
        return
    if not st.is_approved(uid): return await update.message.reply_text("🚫 غير مصرّح لك.")
    await update.message.reply_text("✅ البوت جاهز. أرسل نصاً أو صورة + نص.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل صورة ثم وصف، أو أرسل وصفاً فقط.")

# ═══════ أوامر التحكم بـ GitHub ═══════
async def cmd_edit_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_owner(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /edit_file <مسار_الملف> <المحتوى_الجديد>")
        return
    
    file_path = context.args[0]
    new_content = " ".join(context.args[1:])
    
    # جلب الـ SHA الحالي
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        await update.message.reply_text(f"❌ فشل جلب الملف: {r.status_code}")
        return
    
    sha = r.json()["sha"]
    encoded_content = base64.b64encode(new_content.encode()).decode()
    
    data = {
        "message": f"Update {file_path} via Telegram Bot",
        "content": encoded_content,
        "sha": sha,
        "branch": "main"
    }
    
    r = requests.put(url, headers=headers, data=json.dumps(data))
    if r.status_code in (200, 201):
        await update.message.reply_text(f"✅ تم تحديث {file_path} بنجاح! جاري إعادة التشغيل...")
        # هنا يمكن إضافة أمر لإعادة تشغيل الـ Workflow
    else:
        await update.message.reply_text(f"❌ فشل التحديث: {r.text}")

# ═══════ استقبال الصور والنصوص ═══════
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_approved(update.effective_user.id): return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = BytesIO()
    await file.download_to_memory(bio)
    context.user_data["pending_image"] = bio.getvalue()
    await update.message.reply_text("📸 تم استلام الصورة. الآن أرسل وصف الفيديو (والسكربت المطلوب).")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not st.is_approved(uid): return await update.message.reply_text("🚫 غير مصرّح. اكتب /start.")
    image_bytes = context.user_data.pop("pending_image", None)
    job_id = st.new_job(uid, text)
    asyncio.create_task(process_job(context, job_id, text, image_bytes, update))

async def process_job(context, job_id, prompt, image_bytes, update):
    st.update_job(job_id, status="processing")
    await update.message.reply_text("⏳ جاري التوليد (فيديو + صوت + نصوص)...")
    try:
        image_url = None
        if image_bytes:
            image_url = await apis.upload_image(image_bytes)
        
        # نمرر النص الأصلي كسكربت للصوت
        video_url, provider, error = await apis.generate_video_with_fallback(prompt, image_url, script_text=prompt)

        if video_url:
            st.update_job(job_id, status="done", video_url=video_url)
            await update.message.reply_video(video_url, caption=f"✅ تم التوليد عبر {provider}")
        else:
            await update.message.reply_text(f"❌ فشل: {error}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)[:50]}")

def main():
    if not BOT_TOKEN: return
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("edit_file", cmd_edit_file))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
