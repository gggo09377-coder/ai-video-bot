import os
import asyncio
import httpx
import logging
import urllib.parse

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

# ═══════ المحرك الوحيد: Pollinations Video API ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    # تشفير النص عشان يشتغل بالرابط
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://video.pollinations.ai/prompt/{encoded_prompt}"
    
    if image_url:
        url += f"?image={urllib.parse.quote(image_url)}"

    log.info(f"Trying Pollinations Video API...")
    
    try:
        # مهلة طويلة جداً عشان السيرفر ياخذ راحته
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.get(url)
            if r.status_code == 200:
                # إذا رجع ملف فيديو مباشر
                if r.headers.get("content-type", "").startswith("video"):
                    # نرفعه على كات بوكس عشان يشتغل بالتليكرام
                    files = {"fileToUpload": ("video.mp4", r.content, "video/mp4")}
                    data = {"reqtype": "fileupload", "userhash": ""}
                    r2 = await c.post("https://catbox.moe/user/api.php", data=data, files=files)
                    if r2.status_code == 200 and r2.text.startswith("http"):
                        return r2.text.strip(), "Pollinations AI", None
                # إذا رجع رابط فيديو
                elif "url" in r.text:
                    import json
                    try:
                        v_url = json.loads(r.text).get("url")
                        if v_url: return v_url, "Pollinations AI", None
                    except: pass
    except Exception as e:
        log.error(f"[Pollinations] failed: {e}")
        
    return None, None, "فشل التوليد من Pollinations. جرب وصف مختلف."

async def health_check() -> dict:
    return {"Pollinations Video": True}
