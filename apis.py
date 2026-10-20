import os
import time
import asyncio
import httpx
import logging
from gradio_client import Client, file

log = logging.getLogger(__name__)

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

# ═══════ السحر كله هنا: الاتصال المباشر بـ Hugging Face ═══════
def generate_via_hf_space(prompt, image_url=None):
    try:
        # هذا هو الرابط المباشر لمساحة توليد الفيديو المجانية
        client = Client("Lightricks/LTX-Video")
        
        img_path = None
        if image_url:
            import urllib.request
            img_path = "temp_input.jpg"
            urllib.request.urlretrieve(image_url, img_path)
            img_path = file(img_path)
        
        # نطلب الفيديو
        result = client.predict(
            prompt=prompt,
            negative_prompt="watermark, text, logo, blurry, low quality",
            image=img_path,
            api_name="/generate"
        )
        
        # إذا رجع الفيديو كملف، نرفعه على كات بوكس عشان يشتغل بالتليكرام
        if result and isinstance(result, str) and os.path.exists(result):
            with open(result, "rb") as f:
                video_bytes = f.read()
                
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

# ═══════ التشغيل ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    log.info("Trying Hugging Face Space directly (No API Key needed)...")
    loop = asyncio.get_event_loop()
    
    # نشغل الكود بالمتصفح الخلفي عشان البوت ما يعلق
    video_url = await loop.run_in_executor(None, generate_via_hf_space, prompt, image_url)
    
    if video_url:
        return video_url, "Hugging Face Space", None
        
    return None, None, "فشل الاتصال بمساحة توليد الفيديو. حاول مرة أخرى."

async def health_check() -> dict:
    return {"Hugging Face Space": True}
