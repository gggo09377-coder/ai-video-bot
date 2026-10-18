import os
import asyncio
import logging
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# ═══════ أدوات مساعدة ═══════
async def notify_owner(context, text):
    owner = st.get_state().get("owner_id")
    if owner:
        try: await context.bot.send_message(chat_id=owner, text=text, parse_mode="HTML")
        except Exception: pass

# ═══════ الأوامر الأساسية ═══════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    # أول شخص يدخل يصير المالك
    if st.get_state().get("owner_id") == 0:
        st.set_owner(uid)
        st.add_user(uid, user.username or "")
        st.approve_user(uid)
        await update.message.reply_text(
            f"👑 مرحباً بك يا مالك البوت!\n"
            f"معرّفك: <code>{uid}</code>\n\n"
            f"أنت المالك — كل شي مفتوح لك.\n"
            f"اكتب /help للأوامر.",
            parse_mode="HTML"
        )
        return

    if st.is_owner(uid):
        await update.message.reply_text("👑 أهلاً بك يا مالك.\nاكتب /help للأوامر.")
        return

    if st.is_banned(uid):
        await update.message.reply_text("🚫 أنت محظور.")
        return

    u = st.get_user(uid)
    if not u:
        st.add_user(uid, user.username or "")
        await update.message.reply_text("👋 أهلاً بك!\nطلبك وصل للمالك. انتظر الموافقة.")
        await notify_owner(context, f"🔔 طلب انضمام جديد:\nالاسم: {user.full_name}\nID: <code>{uid}</code>\nللموافقة: /approve {uid}")
        return

    if u.get("status") == "pending":
        await update.message.reply_text("⏳ طلبك قيد المراجعة.")
    elif u.get("status") == "approved":
        await update.message.reply_text("✅ أنت مصرّح لك.\nأرسل نص أو صورة لتوليد فيديو.")
    else:
        await update.message.reply_text("حالة غير معروفة.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not st.is_approved(uid): return
    txt = (
        "📖 <b>الأوامر:</b>\n\n"
        "• أرسل <b>نص</b> = فيديو من نص\n"
        "• أرسل <b>صورة + نص</b> = فيديو من صورة\n"
        "• /status - حالة الطلبات\n"
    )
    if st.is_owner(uid):
        txt += "\n👑 <b>أوامر المالك:</b>\n• /admin - لوحة التحكم\n• /approve ID\n• /ban ID\n• /health"
    await update.message.reply_text(txt, parse_mode="HTML")

# ═══════ لوحة تحكم المالك ═══════
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_owner(update.effective_user.id): return
    kb = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("🏥 فحص APIs", callback_data="admin_health")],
    ]
    await update.message.reply_text("👑 <b>لوحة التحكم</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "admin_stats":
        s = st.get_state()["stats"]
        await q.edit_message_text(f"📊 <b>الإحصائيات</b>\n\n🎬 فيديوهات: {s.get('total_videos', 0)}\n👥 مستخدمين: {s.get('total_users', 0)}\n❌ أخطاء: {s.get('total_errors', 0)}", parse_mode="HTML")
    elif q.data == "admin_health":
        h = await apis.health_check()
        txt = "🏥 <b>حالة APIs:</b>\n\n"
        for k, v in h.items(): txt += f"{'✅' if v else '❌'} {k}\n"
        await q.edit_message_text(txt, parse_mode="HTML")

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_owner(update.effective_user.id) or not context.args: return
    uid = int(context.args[0])
    st.approve_user(uid)
    await update.message.reply_text(f"✅ تمت الموافقة على {uid}")
    try: await context.bot.send_message(uid, "✅ تمت الموافقة! اكتب /help.")
    except: pass

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not st.is_owner(update.effective_user.id) or not context.args: return
    uid = int(context.args[0])
    st.ban_user(uid)
    await update.message.reply_text(f"🚫 تم حظر {uid}")

# ═══════ معالجة الصور والنصوص ═══════
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not st.is_approved(uid): return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = BytesIO()
    await file.download_to_memory(bio)
    context.user_data["pending_image"] = bio.getvalue()
    await update.message.reply_text("📸 تم استلام الصورة.\nالآن أرسل وصف الفيديو (نص).")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    if not st.is_approved(uid):
        await update.message.reply_text("🚫 غير مصرّح. اكتب /start.")
        return

    image_bytes = context.user_data.pop("pending_image", None)
    job_id = st.new_job(uid, text)
    
    # نرسله لمعالجة في الخلفية عشان البوت ما يتوقف
    asyncio.create_task(process_job(context, job_id, text, image_bytes, update))

async def process_job(context, job_id, prompt, image_bytes, update):
    st.update_job(job_id, status="processing")
    await update.message.reply_text("⏳ جاري التوليد... قد يستغرق 30-180 ثانية.")

    try:
        image_url = None
        if image_bytes:
            await update.message.reply_text("📤 جاري رفع الصورة...")
            image_url = await apis.upload_image(image_bytes)

        video_url, provider, error = await apis.generate_video_with_fallback(prompt, image_url)

        if video_url:
            st.record_video(provider)
            st.update_job(job_id, status="done", video_url=video_url)
            await update.message.reply_video(video_url, caption=f"✅ تم التوليد عبر {provider}\n✨ البرومبت المحسن: {prompt[:50]}...")
        else:
            st.update_job(job_id, status="error", error=error)
            await update.message.reply_text("❌ فشل التوليد من جميع المحركات. جرب تغيير الوصف.")
            await notify_owner(context, f"❌ فشل job {job_id}\n{error}")
    except Exception as e:
        log.error(f"Job failed: {e}")
        st.update_job(job_id, status="error", error=str(e))
        await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")

# ═══════ التشغيل ═══════
def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN غير محدد")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CallbackQueryHandler(on_callback, pattern="^admin_"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("🤖 Bot polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
