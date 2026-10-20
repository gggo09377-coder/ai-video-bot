import os
import time
import asyncio
import httpx
import logging
import hashlib
import urllib.parse

log = logging.getLogger(__name__)

HF_API_KEY = os.getenv("HF_API_KEY", "").strip()
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "").strip()
FAL_API_KEY = os.getenv("FAL_API_KEY", "").strip()

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

# 1. Pollinations (بدون مفتاح)
async def try_pollinations(prompt, image_url):
    encoded = urllib.parse.quote(prompt)
    url = f"https://video.pollinations.ai/prompt/{encoded}"
    if image_url: url += f"?image={urllib.parse.quote(image_url)}"
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.get(url)
            if r.status_code == 200:
                if r.headers.get("content-type", "").startswith("video"):
                    files = {"fileToUpload": ("video.mp4", r.content, "video/mp4")}
                    data = {"reqtype": "fileupload", "userhash": ""}
                    r2 = await c.post("https://catbox.moe/user/api.php", data=data, files=files)
                    if r2.status_code == 200: return r2.text.strip(), "Pollinations"
                elif "url" in r.text:
                    import json
                    try: return json.loads(r.text).get("url"), "Pollinations"
                    except: pass
    except: pass
    return None, None

# 2. Hugging Face (بمفتاح)
async def try_hf(prompt, image_url):
    if not HF_API_KEY: return None, None
    try:
        headers = {"Authorization": f"Bearer {HF_API_KEY}"}
        url = "https://api-inference.huggingface.co/models/Lightricks/LTX-Video"
        payload = {"inputs": prompt}
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("video"):
                files = {"fileToUpload": ("video.mp4", r.content, "video/mp4")}
                data = {"reqtype": "fileupload", "userhash": ""}
                r2 = await c.post("https://catbox.moe/user/api.php", data=data, files=files)
                if r2.status_code == 200: return r2.text.strip(), "Hugging Face"
    except: pass
    return None, None

# 3. Agnes (بمفتاح)
async def try_agnes(prompt, image_url):
    if not AGNES_API_KEY: return None, None
    try:
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "agnes-video-v2.0", "prompt": prompt, "duration": 10}
        if image_url: payload["image_url"] = image_url
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post("https://apihub.agnes-ai.com/v1/videos", headers=headers, json=payload)
            if r.status_code == 200:
                data = r.json()
                return data.get("data", {}).get("video_url") or data.get("url"), "Agnes AI"
    except: pass
    return None, None

async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    # السلسلة: نجرب Pollinations ثم Hugging Face ثم Agnes
    providers = [
        try_pollinations(prompt, image_url),
        try_hf(prompt, image_url),
        try_agnes(prompt, image_url)
    ]
    
    for task in providers:
        url, name = await task
        if url: return url, name, None
        
    return None, None, "فشلت جميع المحركات. المواقع المجانية مزدحمة، جرب لاحقاً."

async def health_check() -> dict:
    return {"النظام": "متعدد المحركات"}
