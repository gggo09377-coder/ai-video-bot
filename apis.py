import os
import time
import asyncio
import httpx
import logging
from magic_hour import Client as MagicHourClient
import fal_client

log = logging.getLogger(__name__)

MAGIC_HOUR_API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()
FAL_API_KEY = os.getenv("FAL_API_KEY", "").strip()
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "").strip()

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

# ═══════ المحرك 1: Magic Hour ═══════
def _magic_hour_sync(prompt, image_url):
    if not MAGIC_HOUR_API_KEY: return None
    try:
        client = MagicHourClient(token=MAGIC_HOUR_API_KEY)
        if image_url:
            res = client.v1.image_to_video.create(
                image_url=image_url, prompt=prompt, duration_seconds=10, model="wan-2.2"
            )
        else:
            res = client.v1.text_to_video.create(
                prompt=prompt, duration_seconds=10, model="wan-2.2"
            )
        # Magic Hour يعيد كائن يحتوي على رابط الفيديو
        return res.video_url if hasattr(res, 'video_url') else res.get("video_url")
    except Exception as e:
        log.error(f"[Magic Hour] failed: {e}")
    return None

# ═══════ المحرك 2: fal.ai (Kling) ═══════
def _fal_sync(prompt, image_url):
    if not FAL_API_KEY: return None
    try:
        os.environ["FAL_KEY"] = FAL_API_KEY
        if image_url:
            result = fal_client.subscribe(
                "fal-ai/kling-video/v2.1/standard/image-to-video",
                arguments={"prompt": prompt, "image_url": image_url, "duration": "10"}
            )
        else:
            result = fal_client.subscribe(
                "fal-ai/kling-video/v3/pro/text-to-video",
                arguments={"prompt": prompt, "duration": "10"}
            )
        return result.get("video", {}).get("url")
    except Exception as e:
        log.error(f"[fal.ai] failed: {e}")
    return None

# ═══════ المحرك 3: Agnes AI ═══════
async def _agnes_async(prompt, image_url):
    if not AGNES_API_KEY: return None
    try:
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "agnes-video-v2.0", "prompt": prompt, "duration": 10}
        if image_url: payload["image_url"] = image_url
        
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post("https://apihub.agnes-ai.com/v1/videos", headers=headers, json=payload)
            if r.status_code == 200:
                data = r.json()
                return data.get("data", {}).get("video_url") or data.get("url")
    except Exception as e:
        log.error(f"[Agnes] failed: {e}")
    return None

# ═══════ السلسلة النهائية ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    loop = asyncio.get_event_loop()

    # 1. Magic Hour
    log.info("Trying Magic Hour (10s)...")
    url = await loop.run_in_executor(None, _magic_hour_sync, prompt, image_url)
    if url: return url, "Magic Hour", None

    # 2. fal.ai
    log.info("Trying fal.ai (Kling 10s)...")
    url = await loop.run_in_executor(None, _fal_sync, prompt, image_url)
    if url: return url, "fal.ai", None

    # 3. Agnes AI
    log.info("Trying Agnes AI (10s)...")
    url = await _agnes_async(prompt, image_url)
    if url: return url, "Agnes AI", None

    return None, None, "فشلت جميع المحركات. تأكد من مفاتيح API."

async def health_check() -> dict:
    return {
        "Magic Hour": bool(MAGIC_HOUR_API_KEY),
        "fal.ai": bool(FAL_API_KEY),
        "Agnes AI": bool(AGNES_API_KEY),
    }
