import os
import time
import asyncio
import httpx
import logging
import urllib.parse

log = logging.getLogger(__name__)

MAGIC_HOUR_API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()

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

# ═══════ المحرك الأساسي: Magic Hour ═══════
async def try_magic_hour(prompt: str, image_url: str = None):
    if not MAGIC_HOUR_API_KEY:
        return None, None
        
    try:
        headers = {"Authorization": f"Bearer {MAGIC_HOUR_API_KEY}", "Content-Type": "application/json"}
        
        # تحديد نوع الفيديو (من صورة أو من نص)
        if image_url:
            url = "https://api.magichour.ai/v1/image-to-video"
            payload = {"image_url": image_url, "prompt": prompt, "duration": 5, "resolution": "720p"}
        else:
            url = "https://api.magichour.ai/v1/text-to-video"
            payload = {"prompt": prompt, "duration": 5, "resolution": "720p"}
            
        async with httpx.AsyncClient(timeout=60) as c:
            # 1. إرسال الطلب
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code not in (200, 201):
                log.error(f"[Magic Hour] HTTP {r.status_code}: {r.text[:100]}")
                return None, None
                
            data = r.json()
            job_id = data.get("id")
            if not job_id:
                return None, None
                
            log.info(f"[Magic Hour] Job created: {job_id}. Waiting for completion...")
            
            # 2. الانتظار حتى ينتهي التوليد (Polling)
            poll_url = f"https://api.magichour.ai/v1/video-projects/{job_id}"
            for _ in range(40): # ننتظر حتى 6 دقائق كحد أقصى
                await asyncio.sleep(10)
                poll_r = await c.get(poll_url, headers=headers)
                if poll_r.status_code == 200:
                    poll_data = poll_r.json()
                    status = poll_data.get("status", "").lower()
                    
                    if status in ("complete", "completed"):
                        return poll_data.get("video_url") or poll_data.get("download", {}).get("url"), "Magic Hour"
                    elif status in ("error", "failed"):
                        log.error(f"[Magic Hour] Job failed.")
                        return None, None
    except Exception as e:
        log.error(f"[Magic Hour] Exception: {e}")
    return None, None

# ═══════ السلسلة النهائية ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    log.info("Trying Magic Hour (Primary)...")
    url, name = await try_magic_hour(prompt, image_url)
    if url: 
        return url, name, None

    return None, None, "فشل التوليد عبر Magic Hour. تأكد من الرصيد أو المفتاح."

async def health_check() -> dict:
    return {
        "Magic Hour (أساسي)": bool(MAGIC_HOUR_API_KEY)
    }
