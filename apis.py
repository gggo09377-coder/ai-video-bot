import os
import time
import hashlib
import asyncio
import httpx
import logging

log = logging.getLogger(__name__)

# المفاتيح (إذا موجودة، وإذا لا راح يتخطاها)
FAL_API_KEY = os.getenv("FAL_API_KEY", "").strip()
MAGIC_HOUR_API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "").strip()
HF_API_KEY = os.getenv("HF_API_KEY", "").strip()

# ═══════ 1. تحسين البرومبت تلقائياً (مجاني بدون مفتاح) ═══════
async def enhance_prompt(prompt: str) -> str:
    """يستخدم Pollinations AI لتحسين البرومبت وإضافة تفاصيل سينمائية."""
    url = f"https://text.pollinations.ai/{prompt}?system=Act as a professional cinematic video prompt engineer. Enhance this prompt with lighting, camera angles, 4k, 8k, highly detailed, masterpiece. Output only the enhanced prompt in English."
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            if r.status_code == 200 and len(r.text) > 10:
                enhanced = r.text.strip().replace('"', '')
                log.info(f"Prompt enhanced: {prompt[:20]}... -> {enhanced[:20]}...")
                return enhanced
    except Exception as e:
        log.warning(f"Prompt enhance failed, using original: {e}")
    return f"{prompt}, cinematic, 4K, ultra detailed, professional lighting"

# ═══════ 2. رفع الصور تلقائياً (مجاني بدون مفتاح) ═══════
async def upload_image(image_bytes: bytes, filename: str = "img.jpg") -> str:
    """يرفع صورة على catbox.moe ويرجع الرابط المباشر."""
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            files = {"fileToUpload": (filename, image_bytes, "image/jpeg")}
            data = {"reqtype": "fileupload", "userhash": ""}
            r = await c.post("https://catbox.moe/user/api.php", data=data, files=files)
            if r.status_code == 200 and r.text.startswith("http"):
                return r.text.strip()
    except Exception as e:
        log.error(f"[catbox] upload failed: {e}")
    return None

# ═══════ 3. محركات توليد الفيديو ═══════

async def agnes_generate(prompt: str, image_url: str = None) -> str:
    """Agnes AI (أساسي - مجاني)."""
    if not AGNES_API_KEY:
        return None
    try:
        url = "https://apihub.agnes-ai.com/v1/video/generations"
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        payload = {"prompt": prompt, "model": "agnes-video-v1", "duration": 5}
        if image_url: payload["image_url"] = image_url

        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code >= 400:
                log.error(f"[agnes] HTTP {r.status_code}")
                return None
            data = r.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0].get("url")
    except Exception as e:
        log.error(f"[agnes] failed: {e}")
    return None

async def magichour_generate(prompt: str, image_url: str = None) -> str:
    """Magic Hour (احتياطي - 100 فيديو يومياً)."""
    if not MAGIC_HOUR_API_KEY:
        return None
    try:
        url = "https://api.magichour.ai/v1/image-to-video" if image_url else "https://api.magichour.ai/v1/text-to-video"
        headers = {"Authorization": f"Bearer {MAGIC_HOUR_API_KEY}", "Content-Type": "application/json"}
        payload = {"prompt": prompt, "duration": 5, "resolution": "720p"}
        if image_url: payload["image_url"] = image_url

        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code >= 400: return None
            data = r.json()
            if "video_url" in data: return data["video_url"]
            
            # إذا كان async، ننتظر النتيجة
            job_id = data.get("id")
            if job_id: return await _magichour_poll(job_id)
    except Exception as e:
        log.error(f"[magichour] failed: {e}")
    return None

async def _magichour_poll(job_id: str) -> str:
    headers = {"Authorization": f"Bearer {MAGIC_HOUR_API_KEY}"}
    url = f"https://api.magichour.ai/v1/video-projects/{job_id}"
    for _ in range(30):  # ننتظر 2.5 دقيقة كحد أقصى
        await asyncio.sleep(5)
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(url, headers=headers)
                data = r.json()
                if data.get("status") in ("complete", "completed"):
                    return data.get("video_url") or data.get("download", {}).get("url")
                if data.get("status") == "failed": return None
        except: pass
    return None

async def fal_generate(prompt: str, image_url: str = None) -> str:
    """fal.ai (احتياطي - 5 فيديوهات يومياً)."""
    if not FAL_API_KEY:
        return None
    try:
        model = "fal-ai/kling-video/v1.6/standard/image-to-video" if image_url else "fal-ai/minimax/video-01"
        url = f"https://fal.run/{model}"
        headers = {"Authorization": f"Key {FAL_API_KEY}", "Content-Type": "application/json"}
        payload = {"prompt": prompt}
        if image_url: payload["image_url"] = image_url

        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code >= 400: return None
            data = r.json()
            video = data.get("video", {})
            return video.get("url") if isinstance(video, dict) else video
    except Exception as e:
        log.error(f"[fal] failed: {e}")
    return None

# ═══════ 4. السلسلة الكاملة (Fallback System) ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    """يجرب كل المحركات بالترتيب حتى ينجح واحد منها."""
    
    # 1. نحسن البرومبت أولاً
    prompt = await enhance_prompt(original_prompt)
    
    # 2. ترتيب المحركات حسب الأفضلية
    providers = [
        ("Agnes AI", agnes_generate),
        ("Magic Hour", magichour_generate),
        ("fal.ai", fal_generate)
    ]
    
    last_error = "All providers failed"
    for name, func in providers:
        log.info(f"Trying provider: {name}...")
        try:
            video_url = await func(prompt, image_url)
            if video_url:
                return video_url, name, None
        except Exception as e:
            last_error = f"{name}: {str(e)}"
            log.error(f"Provider {name} error: {e}")
        await asyncio.sleep(1) # تأخير بسيط بين المحاولات
        
    return None, None, last_error

# ═══════ 5. فحص حالة المفاتيح ═══════
async def health_check() -> dict:
    return {
        "Agnes AI (أساسي)": bool(AGNES_API_KEY),
        "Magic Hour (احتياطي)": bool(MAGIC_HOUR_API_KEY),
        "fal.ai (احتياطي)": bool(FAL_API_KEY),
        "HuggingFace (احتياطي)": bool(HF_API_KEY)
    }

def prompt_hash(prompt: str, image_url: str = None) -> str:
    key = f"{prompt}|{image_url or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
