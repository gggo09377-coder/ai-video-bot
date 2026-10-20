import os
import time
import hashlib
import asyncio
import httpx
import logging
from gradio_client import Client, file

log = logging.getLogger(__name__)

HF_API_KEY = os.getenv("HF_API_KEY", "").strip()

# ═══════ 1. تحسين البرومبت ═══════
async def enhance_prompt(prompt: str) -> str:
    url = f"https://text.pollinations.ai/{prompt}?system=Act as a professional cinematic video prompt engineer. Enhance this prompt with lighting, camera angles, 4k, 8k, highly detailed, masterpiece. Output only the enhanced prompt in English."
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            if r.status_code == 200 and len(r.text) > 10:
                return r.text.strip().replace('"', '')
    except Exception:
        pass
    return f"{prompt}, cinematic, 4K, ultra detailed, professional lighting"

# ═══════ 2. رفع الصور ═══════
async def upload_image(image_bytes: bytes, filename: str = "img.jpg") -> str:
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

# ═══════ 3. توليد الفيديو (عبر مساحة Hugging Face مباشرة) ═══════
def generate_video_hf_space(prompt: str, image_url: str = None) -> str:
    """يستخدم مساحة LTX-Video على Hugging Face لتوليد الفيديو."""
    try:
        # الاتصال بالمساحة المجانية
        client = Client("Lightricks/LTX-Video", hf_token=HF_API_KEY)
        
        # تحميل الصورة كملف إذا وجدت
        img_path = None
        if image_url:
            import urllib.request
            img_path = "temp_input.jpg"
            urllib.request.urlretrieve(image_url, img_path)
            img_path = file(img_path)
        
        # طلب التوليد
        result = client.predict(
            prompt=prompt,
            negative_prompt="watermark, text, logo, blurry, low quality",
            image=img_path,
            api_name="/generate"
        )
        
        # النتيجة عادة تكون مسار ملف، نحوله لرابط
        if result and isinstance(result, str):
            # إذا كان مسار محلي، نرفعه على كات بوكس
            if os.path.exists(result):
                with open(result, "rb") as f:
                    video_bytes = f.read()
                
                # رفع الفيديو
                import requests
                r = requests.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload", "userhash": ""},
                    files={"fileToUpload": ("video.mp4", video_bytes, "video/mp4")},
                    timeout=120
                )
                if r.status_code == 200:
                    return r.text.strip()
    except Exception as e:
        log.error(f"[HF Space] failed: {e}")
    return None

# ═══════ 4. السلسلة (فقط Hugging Face Space) ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    log.info("Trying Hugging Face Space (LTX-Video)...")
    # نستخدم دالة متزامنة (sync) ونحولها لـ async
    loop = asyncio.get_event_loop()
    video_url = await loop.run_in_executor(None, generate_video_hf_space, prompt, image_url)
    
    if video_url:
        return video_url, "Hugging Face (LTX-Video)", None
        
    return None, None, "All providers failed"

async def health_check() -> dict:
    return {"Hugging Face Space": bool(HF_API_KEY)}

def prompt_hash(prompt: str, image_url: str = None) -> str:
    key = f"{prompt}|{image_url or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
