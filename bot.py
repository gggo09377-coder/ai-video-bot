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

import storage as st
import apis

load_dotenv()
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# ═══════ القائمة الجانبية (Menu) ═══════
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "بدء البوت والقائمة الرئيسية"),
        BotCommand("help", "طريقة استخدام البوت"),
        BotCommand("status", "حالة طلباتك الحالية"),
        BotCommand("admin", "لوحة تحكم المالك فقط"),
    ]
    await application.bot.set_my_commands(commands)

# ═══════ الأوامر ═══════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    if st.get_state().get("owner_id") == 0:
        st.set_owner(uid); st.add_user(uid, user.username or ""); st.approve_user(uid)
        await update.message.reply_text(f"👑 مرحباً بك يا مالك البوت!\nمعرّفك: <code>{uid}</code>\n\nالبوت جاهز. أرسل نص أو صورة لتبدأ.", parse_mode="HTML")
        return
    if not st.is_approved(uid): return await update.message.reply_text("🚫 غير مصرّح لك.")
    await update.message.reply_text("✅ البوت جاهز. أرسل نصاً أو صورة + نص.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 <b>طريقة الاستخدام:</b>\n\n1. أرسل وصف الفيديو (نص فقط).\n2. أو أرسل صورة، ثم أرسل الوصف.\n3. انتظر 30-180 ثانية، وسيصلك الفيديو.\n\n👑 <b>للمالك:</b>\n/admin - لوحة التحكم", parse_mode="HTML")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت يعمل بشكل طبيعي.")

# ═══════ المالك ═══════
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_owner(update.effective_user.id): return
    kb = [[InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")], [InlineKeyboardButton("🏥 فحص APIs", callback_data="admin_health")]]
    await update.message.reply_text("👑 <b>لوحة التحكم</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "admin_stats":
        s = st.get_state()["stats"]
        await q.edit_message_text(f"📊 <b>الإحصائيات</b>\n\n🎬 فيديوهات: {s.get('total_videos', 0)}\n👥 مستخدمين: {s.get('total_users', 0)}", parse_mode="HTML")
    elif q.data == "admin_health":
        h = await apis.health_check()
        txt = "🏥 <b>حالة APIs:</b>\n\n"
        for k, v in h.items(): txt += f"{'✅' if v else '❌'} {k}\n"
        await q.edit_message_text(txt, parse_mode="HTML")

# ═══════ استقبال الصور والنصوص ═══════
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_approved(update.effective_user.id): return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = BytesIO()
    await file.download_to_memory(bio)
    context.user_data["pending_image"] = bio.getvalue()
    await update.message.reply_text("📸 تم استلام الصورة. الآن أرسل وصف الفيديو.")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not st.is_approved(uid): return await update.message.reply_text("🚫 غير مصرّح. اكتب /start.")
    image_bytes = context.user_data.pop("pending_image", None)
    job_id = st.new_job(uid, text)
    asyncio.create_task(process_job(context, job_id, text, image_bytes, update))

async def process_job(context, job_id, prompt, image_bytes, update):
    st.update_job(job_id, status="processing")
    await update.message.reply_text("⏳ جاري التوليد... قد يستغرق 30-180 ثانية.")
    try:
        image_url = None
        if image_bytes:
            await update.message.reply_text("📤 جاري رفع الصورة...")
            image_url = await apis.upload_image(image_bytes)

        # هنا يتم استدعاء المحرك الجديد
        video_url, provider, error = await apis.generate_video_with_fallback(prompt, image_url)

        if video_url:
            st.record_video(provider)
            st.update_job(job_id, status="done", video_url=video_url)
            await update.message.reply_video(video_url, caption=f"✅ تم التوليد عبر {provider}")
        else:
            st.update_job(job_id, status="error", error=error)
            await update.message.reply_text(f"❌ فشل التوليد: {error}")
    except Exception as e:
        log.error(f"Job failed: {e}")
        await update.message.reply_text(f"❌ خطأ تقني: {str(e)[:50]}")

# ═══════ التشغيل ═══════
def main():
    if not BOT_TOKEN: return
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^admin_"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    print("🤖 Bot polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
