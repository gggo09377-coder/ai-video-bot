
import os
import asyncio
import logging
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.magichour.ai/v1"
API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()

# إعدادات محافظة لتقليل استهلاك الرصيد.
VIDEO_SECONDS = 10
VIDEO_RESOLUTION = "480p"
VIDEO_MODEL = "ltx-2.5"

REQUEST_TIMEOUT = 45
POLL_INTERVAL = 10
MAX_WAIT_SECONDS = 720
MAX_VIDEO_BYTES = 49 * 1024 * 1024


def _headers():
    if not API_KEY:
        raise RuntimeError(
            "مفتاح MAGIC_HOUR_API_KEY غير موجود في GitHub Secrets."
        )

    return {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
    }


def _error_text(response):
    try:
        data = response.json()
        if isinstance(data, dict):
            message = (
                data.get("message")
                or data.get("error")
                or data.get("detail")
                or data.get("error_description")
            )
            if message:
                return str(message)[:700]
    except Exception:
        pass

    return (response.text or "لا توجد تفاصيل من الخدمة")[:700]


async def enhance_prompt(prompt: str) -> str:
    """
    تحسين محافظ للوصف دون إرسال النص إلى خدمة خارجية إضافية.
    """
    prompt = (prompt or "").strip()

    if not prompt:
        return ""

    cinematic_guidance = (
        "Create a coherent, natural-looking cinematic video based on "
        "the reference image. Preserve the main subject's identity, "
        "facial features, clothing, and important visual details. "
        "Use realistic motion, consistent anatomy, smooth camera movement, "
        "natural lighting, and temporal consistency. Avoid sudden cuts, "
        "warping, flicker, duplicated subjects, and unintended changes. "
        "Follow the user's requested action and camera direction."
    )

    return f"{prompt}\n\n{cinematic_guidance}"


async def upload_image(
    image_bytes: bytes,
    filename: str = "image.jpg",
) -> str | None:
    """
    يرفع الصورة إلى تخزين Magic Hour بالطريقة الرسمية:
    1) طلب رابط رفع مؤقت.
    2) رفع بايتات الصورة باستخدام PUT.
    3) إعادة file_path لاستخدامه في طلب التوليد.
    """
    if not API_KEY:
        log.error("MAGIC_HOUR_API_KEY is missing.")
        return None

    if not image_bytes:
        log.error("Image bytes are empty.")
        return None

    if len(image_bytes) > 20 * 1024 * 1024:
        log.error("Image is larger than the configured 20 MB limit.")
        return None

    extension = os.path.splitext(filename or "image.jpg")[1]
    extension = extension.lower().lstrip(".")

    allowed_extensions = {
        "jpg", "jpeg", "png", "webp", "bmp",
        "tif", "tiff", "heic", "heif", "avif",
    }

    if extension not in allowed_extensions:
        extension = "jpg"

    mime_types = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "tif": "image/tiff",
        "tiff": "image/tiff",
        "heic": "image/heic",
        "heif": "image/heif",
        "avif": "image/avif",
    }

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            # الخطوة الأولى: الحصول على رابط الرفع الرسمي.
            response = await client.post(
                f"{API_BASE}/files/upload-urls",
                headers={
                    **_headers(),
                    "Content-Type": "application/json",
                },
                json={
                    "items": [
                        {
                            "type": "image",
                            "extension": extension,
                        }
                    ]
                },
            )

            if response.status_code not in (200, 201):
                log.error(
                    "Magic Hour upload-URL request failed: HTTP %s: %s",
                    response.status_code,
                    _error_text(response),
                )
                return None

            data = response.json()
            items = data.get("items") or []

            if not items or not isinstance(items[0], dict):
                log.error("Upload URL response did not contain a valid item.")
                return None

            upload_url = items[0].get("upload_url")
            file_path = items[0].get("file_path")

            if not upload_url or not file_path:
                log.error("Upload URL response is missing required fields.")
                return None

            # لا نرسل مفتاح API إلى رابط الرفع المؤقت.
            parsed = urlparse(upload_url)
            if parsed.scheme != "https" or not parsed.netloc:
                log.error("Magic Hour returned an invalid upload URL.")
                return None

            # الخطوة الثانية: رفع الصورة إلى الرابط المؤقت.
            upload_response = await client.put(
                upload_url,
                content=image_bytes,
                headers={
                    "Content-Type": mime_types[extension],
                },
            )

            if upload_response.status_code not in (200, 201, 204):
                log.error(
                    "Image upload failed: HTTP %s: %s",
                    upload_response.status_code,
                    (upload_response.text or "")[:500],
                )
                return None

            log.info("Image uploaded successfully to Magic Hour storage.")
            return file_path

    except httpx.TimeoutException:
        log.exception("Timed out while uploading image.")
    except Exception:
        log.exception("Image upload failed unexpectedly.")

    return None


def _extract_video_url(data: dict) -> str | None:
    """
    تتعامل مع أكثر من شكل لرد مشروع الفيديو.
    """
    if not isinstance(data, dict):
        return None

    direct = data.get("video_url")
    if isinstance(direct, str) and direct.startswith("https://"):
        return direct

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

    outputs = data.get("outputs")
    if isinstance(outputs, list):
        for item in outputs:
            if isinstance(item, dict):
                url = item.get("url") or item.get("video_url")
                if isinstance(url, str) and url.startswith("https://"):
                    return url

    return None


async def try_magic_hour(
    prompt: str,
    image_file_path: str | None = None,
):
    """
    يعيد: (video_url, provider, error)
    لا يعيد طلب التوليد تلقائياً عند الفشل، لتجنب خصم رصيد إضافي.
    """
    if not API_KEY:
        return None, None, (
            "مفتاح Magic Hour غير موجود. راجع GitHub Secrets."
        )

    prompt = (prompt or "").strip()
    if not prompt:
        return None, None, "وصف الفيديو فارغ."

    headers = {
        **_headers(),
        "Content-Type": "application/json",
    }

    payload = {
        "name": "Telegram AI Video",
        "end_seconds": VIDEO_SECONDS,
        "resolution": VIDEO_RESOLUTION,
        "model": VIDEO_MODEL,
        "style": {
            "prompt": prompt,
        },
    }

    if image_file_path:
        payload["assets"] = {
            "image_file_path": image_file_path,
        }
        endpoint = f"{API_BASE}/image-to-video"
    else:
        endpoint = f"{API_BASE}/text-to-video"

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            # إنشاء المشروع مرة واحدة فقط.
            response = await client.post(
                endpoint,
                headers=headers,
                json=payload,
            )

            if response.status_code not in (200, 201, 202):
                message = _error_text(response)
                log.error(
                    "Magic Hour create failed: HTTP %s: %s",
                    response.status_code,
                    message,
                )
                return None, None, (
                    f"Magic Hour رفض طلب التوليد "
                    f"(HTTP {response.status_code}). {message}"
                )

            try:
                data = response.json()
            except Exception:
                return None, None, (
                    "وصل رد غير مفهوم من Magic Hour عند إنشاء المشروع."
                )

            # بعض الردود قد تتضمن رابط النتيجة مباشرة.
            direct_url = _extract_video_url(data)
            if direct_url:
                return direct_url, "Magic Hour", None

            job_id = data.get("id") or data.get("project_id")
            if not job_id:
                log.error(
                    "Magic Hour response did not contain a project ID."
                )
                return None, None, (
                    "تم استلام رد من الخدمة لكن لم يظهر معرّف مشروع الفيديو."
                )

            project_url = f"{API_BASE}/video-projects/{job_id}"
            log.info("Magic Hour project created: %s", job_id)

            # انتظار النتيجة من المشروع نفسه دون إنشاء طلب جديد.
            elapsed = 0

            while elapsed < MAX_WAIT_SECONDS:
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL

                poll_response = await client.get(
                    project_url,
                    headers=_headers(),
                )

                if poll_response.status_code != 200:
                    log.warning(
                        "Project polling returned HTTP %s.",
                        poll_response.status_code,
                    )
                    continue

                try:
                    project = poll_response.json()
                except Exception:
                    continue

                status = str(project.get("status", "")).lower()

                if status in {
                    "complete", "completed", "success", "succeeded",
                }:
                    video_url = _extract_video_url(project)
                    if video_url:
                        return video_url, "Magic Hour", None

                    log.error(
                        "Project completed but no download URL was found."
                    )
                    return None, None, (
                        "اكتمل التوليد لكن رابط تنزيل الفيديو غير موجود "
                        "في رد الخدمة."
                    )

                if status in {
                    "error", "failed", "failure", "canceled", "cancelled",
                }:
                    details = (
                        project.get("error")
                        or project.get("message")
                        or project.get("failure_reason")
                        or status
                    )
                    log.error(
                        "Magic Hour project failed: %s",
                        str(details)[:700],
                    )
                    return None, None, (
                        f"فشل مشروع Magic Hour: {str(details)[:500]}"
                    )

            return None, None, (
                "التوليد ما اكتمل ضمن مهلة الانتظار. "
                "لم أرسل طلب توليد جديد حتى لا يتكرر خصم الرصيد. "
                "افحص المشروع في Magic Hour قبل إعادة المحاولة."
            )

    except httpx.TimeoutException:
        log.exception("Magic Hour request timed out.")
        return None, None, (
            "انتهت مهلة الاتصال بخدمة Magic Hour. "
            "افحص المشاريع في حسابك قبل إعادة الطلب."
        )
    except Exception as exc:
        log.exception("Magic Hour generation failed unexpectedly.")
        return None, None, (
            f"حدث خطأ تقني أثناء التوليد: {str(exc)[:400]}"
        )


async def generate_video_with_fallback(
    original_prompt: str,
    image_file_path: str | None = None,
):
    """
    نحافظ على اسم الدالة وتنسيق النتائج حتى يتوافق البوت معها.
    لا يوجد محرك مدفوع بديل يستهلك رصيداً دون موافقتك.
    """
    prompt = await enhance_prompt(original_prompt)

    return await try_magic_hour(
        prompt=prompt,
        image_file_path=image_file_path,
    )


async def download_video(video_url: str) -> bytes | None:
    """
    تنزيل الفيديو حتى يرسله البوت كملف إلى Telegram،
    بدل الاعتماد على أن Telegram يستطيع جلب الرابط بنفسه.
    """
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        log.error("Refusing an invalid video URL.")
        return None

    try:
        async with httpx.AsyncClient(
            timeout=120,
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", video_url) as response:
                if response.status_code != 200:
                    log.error(
                        "Video download failed: HTTP %s",
                        response.status_code,
                    )
                    return None

                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_VIDEO_BYTES:
                            log.error("Generated video exceeds size limit.")
                            return None
                    except ValueError:
                        pass

                chunks = []
                total = 0

                async for chunk in response.aiter_bytes(1024 * 256):
                    total += len(chunk)

                    if total > MAX_VIDEO_BYTES:
                        log.error("Generated video exceeded size limit.")
                        return None

                    chunks.append(chunk)

                result = b"".join(chunks)

                if not result:
                    log.error("Downloaded video is empty.")
                    return None

                return result

    except Exception:
        log.exception("Failed to download generated video.")
        return None


async def health_check() -> dict:
    return {
        "Magic Hour API key configured": bool(API_KEY),
        "Image upload endpoint configured": bool(API_KEY),
        "Image-to-video endpoint configured": bool(API_KEY),
    }
