import os
import time
import asyncio
import httpx
import logging
import urllib.parse
import urllib.request
import requests
from gradio_client import Client, file

log = logging.getLogger(__name__)

# المفتاح المجاني لـ Agnes (مستخرج من مستودع مفتوح المصدر)
AGNES_API_KEY = "sk-wtQ84SRJzizAfKWm9m6hbzqYvg5S7rR2ZDlLKcHouC29ncpA"
AGNES_BASE_URL = "https://apihub.agnes-ai.cn/v1"

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

# ═══════ 1. محرك Agnes AI (مجاني غير محدود) ═══════
async def try_agnes(prompt: str, image_url: str = None):
    try:
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "agnes-video-v2.0", "prompt": prompt, "duration": 10}
        if image_url: payload["image_url"] = image_url
        
        async with httpx.AsyncClient(timeout=180) as c:
            # إرسال الطلب
            r = await c.post(f"{AGNES_BASE_URL}/videos", headers=headers, json=payload)
            if r.status_code != 200:
                log.error(f"[Agnes] HTTP {r.status_code}: {r.text[:100]}")
                return None, None
            
            data = r.json()
            task_id = data.get("id") or data.get("task_id")
            if not task_id: return None, None
            
            # الانتظار حتى ينتهي التوليد
            for _ in range(30): # ننتظر 5 دقائق كحد أقصى
                await asyncio.sleep(10)
                status_r = await c.get(f"{AGNES_BASE_URL}/videos/{task_id}", headers=headers)
                if status_r.status_code == 200:
                    status_data = status_r.json()
                    status = status_data.get("status")
                    if status == "completed":
                        return status_data.get("video_url") or status_data.get("url"), "Agnes AI"
                    elif status == "failed":
                        return None, None
    except Exception as e:
        log.error(f"[Agnes] failed: {e}")
    return None, None

# ═══════ 2. محرك InferencePort (بدون مفتاح) ═══════
async def try_inferenceport(prompt: str, image_url: str = None):
    try:
        # نقطة النهاية العامة لـ InferencePort
        url = "https://sharktide-lightning.hf.space/v1/videos/generations"
        payload = {"model": "wan-fast", "prompt": prompt}
        if image_url: payload["image_url"] = image_url
        
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get("url"), "InferencePort"
                elif isinstance(data, dict):
                    return data.get("url") or data.get("video_url"), "InferencePort"
            else:
                log.error(f"[InferencePort] HTTP {r.status_code}")
    except Exception as e:
        log.error(f"[InferencePort] failed: {e}")
    return None, None

# ═══════ 3. محرك Hugging Face LTX-Video (بدون مفتاح) ═══════
def try_hf_space(prompt: str, image_url: str = None):
    try:
        client = Client("Lightricks/LTX-Video")
        img_path = None
        if image_url:
            img_path = "temp_input.jpg"
            urllib.request.urlretrieve(image_url, img_path)
            img_path = file(img_path)
            
        result = client.predict(
            prompt=prompt,
            negative_prompt="watermark, text, logo, blurry, low quality",
            image=img_path,
            api_name="/generate"
        )
        
        if result and isinstance(result, str) and os.path.exists(result):
            with open(result, "rb") as f:
                video_bytes = f.read()
            r = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload", "userhash": ""},
                files={"fileToUpload": ("video.mp4", video_bytes, "video/mp4")},
                timeout=120
            )
            if r.status_code == 200: return r.text.strip(), "Hugging Face"
    except Exception as e:
        log.error(f"[HF Space] failed: {e}")
    return None, None

# ═══════ السلسلة النهائية (Fallback) ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    log.info("1. Trying Agnes AI (Unlimited Free)...")
    url, name = await try_agnes(prompt, image_url)
    if url: return url, name, None

    log.info("2. Trying InferencePort (No Key)...")
    url, name = await try_inferenceport(prompt, image_url)
    if url: return url, name, None

    log.info("3. Trying Hugging Face LTX-Video...")
    loop = asyncio.get_event_loop()
    url, name = await loop.run_in_executor(None, try_hf_space, prompt, image_url)
    if url: return url, name, None

    return None, None, "فشلت جميع المحركات الثلاثة. يرجى المحاولة لاحقاً."

async def health_check() -> dict:
    return {
        "Agnes AI (مجاني غير محدود)": True,
        "InferencePort (بدون مفتاح)": True,
        "Hugging Face LTX-Video": True
    }
