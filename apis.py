import os
import asyncio
import httpx
import logging
import urllib.parse
import subprocess
import edge_tts

log = logging.getLogger(__name__)
MAGIC_HOUR_API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()

async def enhance_prompt(prompt: str) -> str:
    url = f"https://text.pollinations.ai/{prompt}?system=You are a cinematic director. Make this prompt highly detailed and strictly follow the user's script: {prompt}. Add 8k, photorealistic, cinematic lighting."
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            if r.status_code == 200 and len(r.text) > 10:
                return r.text.strip().replace('"', '')
    except Exception:
        pass
    return f"{prompt}, cinematic, 4K, ultra detailed"

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

# ═══════ معالجة الفيديو (صوت + نصوص + إزالة علامة) ═══════
async def process_video_with_audio_and_text(video_url: str, script_text: str) -> str:
    try:
        input_video = "input.mp4"
        output_video = "output.mp4"
        audio_file = "voice.mp3"
        text_file = "script.txt"
        
        # 1. تحميل الفيديو
        import urllib.request
        urllib.request.urlretrieve(video_url, input_video)

        # 2. توليد الصوت باستخدام Edge-TTS (صوت بشري مجاني)
        communicate = edge_tts.Communicate(script_text, "ar-SA-HamedNeural") # صوت عربي
        await communicate.save(audio_file)

        # 3. تجهيز النص لحرقه على الفيديو
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(script_text.replace("'", "\\'")) # تأمين النص

        # 4. استخدام FFmpeg لدمج الصوت، إضافة النص، وقص العلامة المائية
        # -vf: قص أسفل الفيديو (لإخفاء العلامة) + إضافة النص
        # -i: إدخال الفيديو والصوت
        cmd = [
            "ffmpeg", "-y",
            "-i", input_video, 
            "-i", audio_file,
            "-vf", "crop=iw:ih*0.85:0:0, drawtext=textfile=script.txt:fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:x=(w-text_w)/2:y=h-th-50",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-shortest", # استخدام أقصر مدة (الفيديو)
            output_video
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 5. رفع الفيديو النهائي
        with open(output_video, "rb") as f:
            video_bytes = f.read()
        import requests
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload", "userhash": ""},
            files={"fileToUpload": ("final_video.mp4", video_bytes, "video/mp4")},
            timeout=120
        )
        if r.status_code == 200:
            return r.text.strip()
    except Exception as e:
        log.error(f"[FFMPEG] Processing failed: {e}")
    return video_url # إذا فشل الدمج، نرجع الفيديو الأصلي

# ═══════ توليد الفيديو (Magic Hour) ═══════
async def try_magic_hour(prompt: str, image_url: str = None):
    if not MAGIC_HOUR_API_KEY: return None, None
    try:
        headers = {"Authorization": f"Bearer {MAGIC_HOUR_API_KEY}", "Content-Type": "application/json"}
        if image_url:
            url = "https://api.magichour.ai/v1/image-to-video"
            payload = {"image_url": image_url, "prompt": prompt, "duration": 10, "resolution": "720p", "model": "wan-2.2"}
        else:
            url = "https://api.magichour.ai/v1/text-to-video"
            payload = {"prompt": prompt, "duration": 10, "resolution": "720p", "model": "wan-2.2"}
            
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url, headers=headers, json=payload)
            if r.status_code not in (200, 201):
                return None, None
            data = r.json()
            job_id = data.get("id")
            if not job_id: return None, None
            
            poll_url = f"https://api.magichour.ai/v1/video-projects/{job_id}"
            for _ in range(60):
                await asyncio.sleep(10)
                poll_r = await c.get(poll_url, headers=headers)
                if poll_r.status_code == 200:
                    poll_data = poll_r.json()
                    status = poll_data.get("status", "").lower()
                    if status in ("complete", "completed"):
                        return poll_data.get("video_url") or poll_data.get("download", {}).get("url"), "Magic Hour"
                    elif status in ("error", "failed"):
                        return None, None
    except Exception as e:
        log.error(f"[Magic Hour] Exception: {e}")
    return None, None

async def generate_video_with_fallback(original_prompt: str, image_url: str = None, script_text: str = ""):
    prompt = await enhance_prompt(original_prompt)
    raw_url, name = await try_magic_hour(prompt, image_url)
    
    if raw_url:
        # نمرر الفيديو والنص على دالة المعالجة لإضافة الصوت والنص وإزالة العلامة
        final_url = await process_video_with_audio_and_text(raw_url, script_text)
        return final_url, f"{name} + AI Voice", None
        
    return None, None, "فشل التوليد. تأكد من الرصيد."

async def health_check() -> dict:
    return {"Magic Hour (أساسي)": bool(MAGIC_HOUR_API_KEY)}
