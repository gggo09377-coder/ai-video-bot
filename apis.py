import os
import time
import hashlib
import asyncio
import httpx
import logging

log = logging.getLogger(__name__)

AGNES_API_KEY = os.getenv("AGNES_API_KEY", "").strip()
HF_API_KEY = os.getenv("HF_API_KEY", "").strip()
FAL_API_KEY = os.getenv("FAL_API_KEY", "").strip()

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

# ═══════ 3. المحركات ═══════

async def hf_generate(prompt: str, image_url: str = None) -> str:
    """Hugging Face - المحرك الأساسي."""
    if not HF_API_KEY: return None
    try:
        # نستخدم موديل LTX-Video الشهير
        model = "Lightricks/LTX-Video"
        url = f"https://api-inference.huggingface.co/models/{model}"
        headers = {"Authorization": f"Bearer {HF_API_KEY}"}
        payload = {"inputs": prompt, "options": {"wait_for_model": True}}
        
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("video"):
                # إذا رجع فيديو، نرفعه على كات بوكس
                files = {"fileToUpload": ("video.mp4", r.content, "video/mp4")}
                data = {"reqtype": "fileupload", "userhash": ""}
                r2 = await c.post("https://catbox.moe/user/api.php", data=data, files=files)
                if r2.status_code == 200 and r2.text.startswith("http"):
                    return r2.text.strip()
    except Exception as e:
        log.error(f"[HF] failed: {e}")
    return None

async def agnes_generate(prompt: str, image_url: str = None) -> str:
    """Agnes AI - المحرك الثاني."""
    if not AGNES_API_KEY: return None
    try:
        url = "https://apihub.agnes-ai.com/v1/video/generations"
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        payload = {"prompt": prompt, "model": "agnes-video-v1", "duration": 5}
        if image_url: payload["image_url"] = image_url

        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code < 400:
                data = r.json()
                if "data" in data and len(data["data"]) > 0:
                    return data["data"][0].get("url")
    except Exception as e:
        log.error(f"[agnes] failed: {e}")
    return None

async def pollinations_generate(prompt: str, image_url: str = None) -> str:
    """Pollinations AI - المحرك الثالث (بدون مفتاح)."""
    try:
        encoded_prompt = httpx.URL(prompt).raw_path.decode()
        url = f"https://gen.pollinations.ai/video/{encoded_prompt}"
        if image_url:
            url += f"?image={image_url}"
            
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.get(url)
            if r.status_code == 200:
                if r.headers.get("content-type", "").startswith("video"):
                    files = {"fileToUpload": ("video.mp4", r.content, "video/mp4")}
                    data = {"reqtype": "fileupload", "userhash": ""}
                    r2 = await c.post("https://catbox.moe/user/api.php", data=data, files=files)
                    if r2.status_code == 200 and r2.text.startswith("http"):
                        return r2.text.strip()
    except Exception as e:
        log.error(f"[pollinations] failed: {e}")
    return None

# ═══════ 4. السلسلة الرباعية ═══════
async def generate_video_with_fallback(original_prompt: str, image_url: str = None):
    prompt = await enhance_prompt(original_prompt)
    
    providers = [
        ("Hugging Face", hf_generate),
        ("Agnes AI", agnes_generate),
        ("Pollinations", pollinations_generate)
    ]
    
    for name, func in providers:
        log.info(f"Trying provider: {name}...")
        try:
            video_url = await func(prompt, image_url)
            if video_url:
                return video_url, name, None
        except Exception as e:
            log.error(f"Provider {name} error: {e}")
        await asyncio.sleep(1)
        
    return None, None, "All providers failed"

async def health_check() -> dict:
    return {
        "Hugging Face": bool(HF_API_KEY),
        "Agnes AI": bool(AGNES_API_KEY),
        "Pollinations (بدون مفتاح)": True
    }

def prompt_hash(prompt: str, image_url: str = None) -> str:
    key = f"{prompt}|{image_url or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
