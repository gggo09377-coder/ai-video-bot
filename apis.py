
import os
import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.magichour.ai/v1"
MAGIC_HOUR_API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()

VIDEO_MODEL = "ltx-2.5"
VIDEO_RESOLUTION = "480p"
VIDEO_SECONDS = 10

POLL_INTERVAL = 8
MAX_POLL_ATTEMPTS = 150


def _headers():
    return {
        "Authorization": f"Bearer {MAGIC_HOUR_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _error(response):
    try:
        data = response.json()
        if isinstance(data, dict):
            message = data.get("message") or data.get("error") or data.get("detail")
            if isinstance(message, dict):
                message = message.get("message") or str(message)
            if message:
                return str(message)[:500]
    except Exception:
        pass

    return f"HTTP {response.status_code}: {response.text[:300]}"


async def enhance_prompt(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return "Cinematic scene, natural motion, smooth camera movement."

    return (
        f"{prompt}. Cinematic composition, realistic natural movement, "
        "smooth camera motion, consistent lighting, detailed scene."
    )


async def upload_image(image_bytes: bytes, filename: str = "img.jpg"):
    """Upload the Telegram image and return its public URL."""
    if not image_bytes:
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload", "userhash": ""},
                files={
                    "fileToUpload": (
                        filename,
                        image_bytes,
                        "image/jpeg",
                    )
                },
            )

            if response.status_code == 200:
                result = response.text.strip()
                if result.startswith("https://"):
                    return result

            log.error(
                "[Catbox] HTTP %s: %s",
                response.status_code,
                response.text[:300],
            )

    except Exception:
        log.exception("[Catbox] Upload failed")

    return None


def _video_url(data):
    if not isinstance(data, dict):
        return None

    download = data.get("download")
    if isinstance(download, dict):
        url = download.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            return url

    downloads = data.get("downloads")
    if isinstance(downloads, list):
        for item in downloads:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.startswith("https://"):
                    return url

    return None


async def try_magic_hour(prompt: str, image_url: str = None):
    if not MAGIC_HOUR_API_KEY:
        return None, None, (
            "المفتاح MAGIC_HOUR_API_KEY غير موجود في بيئة التشغيل. "
            "تحقق من Secrets ومن تمريره داخل run.yml."
        )

    prompt = (prompt or "").strip()
    if not prompt:
        return None, None, "وصف الفيديو فارغ."

    if image_url and not image_url.startswith("https://"):
        return None, None, "رابط الصورة غير صالح."

    if image_url:
        endpoint = f"{API_BASE}/image-to-video"
        payload = {
            "assets": {
                "image_file_path": image_url,
            },
            "end_seconds": VIDEO_SECONDS,
            "model": VIDEO_MODEL,
            "resolution": VIDEO_RESOLUTION,
            "style": {
                "prompt": prompt,
            },
        }
    else:
        endpoint = f"{API_BASE}/text-to-video"
        payload = {
            "end_seconds": VIDEO_SECONDS,
            "model": VIDEO_MODEL,
            "resolution": VIDEO_RESOLUTION,
            "style": {
                "prompt": prompt,
            },
        }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(90),
            follow_redirects=True,
        ) as client:

            # Create the video project
            response = await client.post(
                endpoint,
                headers=_headers(),
                json=payload,
            )

            if response.status_code not in (200, 201, 202):
                detail = _error(response)
                log.error(
                    "[Magic Hour] Create failed: %s",
                    detail,
                )
                return None, None, f"رفض إنشاء الفيديو: {detail}"

            try:
                created = response.json()
            except Exception:
                return None, None, "استجابة إنشاء المشروع غير صالحة."

            if not isinstance(created, dict) or not created.get("id"):
                log.error(
                    "[Magic Hour] Unexpected create response: %s",
                    str(created)[:500],
                )
                return None, None, "لم تُرجع الخدمة معرّف المشروع."

            project_id = created["id"]
            log.info("[Magic Hour] Project created: %s", project_id)

            # Poll until complete, failed, or timed out
            project_url = f"{API_BASE}/video-projects/{project_id}"

            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)

                try:
                    poll = await client.get(
                        project_url,
                        headers=_headers(),
                        timeout=30,
                    )
                except httpx.HTTPError as exc:
                    log.warning(
                        "[Magic Hour] Poll network error: %s",
                        str(exc)[:200],
                    )
                    continue

                if poll.status_code != 200:
                    log.warning(
                        "[Magic Hour] Poll HTTP %s: %s",
                        poll.status_code,
                        poll.text[:200],
                    )
                    continue

                try:
                    data = poll.json()
                except Exception:
                    continue

                if not isinstance(data, dict):
                    continue

                status = str(data.get("status", "")).lower()
                log.info(
                    "[Magic Hour] Project %s status: %s (%s/%s)",
                    project_id,
                    status,
                    attempt + 1,
                    MAX_POLL_ATTEMPTS,
                )

                if status == "complete":
                    url = _video_url(data)
                    if url:
                        return url, "Magic Hour", None

                    return (
                        None,
                        None,
                        "اكتمل التوليد لكن رابط التنزيل غير موجود "
                        "في استجابة المشروع.",
                    )

                if status in ("error", "failed", "canceled"):
                    detail = data.get("error") or data.get("message") or status
                    if isinstance(detail, dict):
                        detail = detail.get("message") or str(detail)

                    log.error(
                        "[Magic Hour] Project failed: %s",
                        str(detail)[:500],
                    )
                    return None, None, f"فشل المشروع: {str(detail)[:400]}"

            return (
                None,
                None,
                "انتهت مهلة الانتظار. قد يكون المشروع ما زال يعمل؛ "
                "تحقق من لوحة Magic Hour قبل إعادة المحاولة لتجنب "
                "خصم كريدت إضافية.",
            )

    except httpx.TimeoutException:
        log.exception("[Magic Hour] Request timed out")
        return None, None, "انتهت مهلة الاتصال بـ Magic Hour."

    except httpx.HTTPError:
        log.exception("[Magic Hour] HTTP error")
        return None, None, "حدث خطأ اتصال أثناء طلب الفيديو."

    except Exception:
        log.exception("[Magic Hour] Unexpected error")
        return None, None, "حدث خطأ غير متوقع أثناء توليد الفيديو."


async def generate_video_with_fallback(
    original_prompt: str,
    image_url: str = None,
):
    prompt = await enhance_prompt(original_prompt)

    video_url, provider, error = await try_magic_hour(
        prompt,
        image_url,
    )

    if video_url:
        return video_url, provider, None

    return None, None, error or "تعذر توليد الفيديو."


async def health_check() -> dict:
    return {
        "Magic Hour API key configured": bool(MAGIC_HOUR_API_KEY),
        "Model": VIDEO_MODEL,
        "Resolution": VIDEO_RESOLUTION,
        "Duration": f"{VIDEO_SECONDS} seconds",
    }
