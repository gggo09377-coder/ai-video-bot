import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import textwrap
import time
from pathlib import Path
from urllib.parse import urlparse

import edge_tts
import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.magichour.ai/v1"
API_KEY = os.getenv("MAGIC_HOUR_API_KEY", "").strip()
VIDEO_SECONDS = max(10, int(os.getenv("VIDEO_SECONDS", "10")))
VIDEO_RESOLUTION = os.getenv("VIDEO_RESOLUTION", "480p").strip() or "480p"
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "ltx-2.5").strip() or "ltx-2.5"
VOICE_NAME = os.getenv("EDGE_TTS_VOICE", "ar-SA-HamedNeural").strip() or "ar-SA-HamedNeural"
REQUEST_TIMEOUT = 45
POLL_INTERVAL = 10
MAX_WAIT_SECONDS = 12 * 60
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_VIDEO_BYTES = 200 * 1024 * 1024
MAX_TELEGRAM_VIDEO_BYTES = 49 * 1024 * 1024
FONT_DIR = "/usr/share/fonts/truetype/noto"


class VideoProcessingError(RuntimeError):
    """A safe, user-displayable video processing error."""


def _headers(json_body: bool = False) -> dict[str, str]:
    if not API_KEY:
        raise RuntimeError("مفتاح MAGIC_HOUR_API_KEY غير مضبوط في GitHub Secrets.")
    result = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
    }
    if json_body:
        result["Content-Type"] = "application/json"
    return result


def _safe_detail(value: object, limit: int = 700) -> str:
    text = str(value or "لا توجد تفاصيل من الخدمة")
    text = re.sub(r"https?://[^\s?]+\?[^\s]+", "[رابط مؤقت مخفي]", text)
    return text[:limit]


def _response_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        data = None

    if isinstance(data, dict):
        detail = data.get("message") or data.get("error") or data.get("detail")
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
        if detail:
            return _safe_detail(detail)

    return _safe_detail(response.text)


async def enhance_prompt(prompt: str) -> str:
    """Add visual guidance locally; do not send a user's script to another API."""
    prompt = (prompt or "").strip()
    if not prompt:
        return ""

    guidance = (
        "Create a coherent, natural-looking cinematic video based on the "
        "reference image. Preserve the main subject's identity, facial features, "
        "clothing, and important visual details. Use realistic motion, consistent "
        "anatomy, smooth camera movement, natural lighting, and temporal consistency. "
        "Avoid sudden cuts, warping, flicker, duplicated subjects, and unintended "
        "changes. Follow the user's requested action and camera direction."
    )
    return f"{prompt}\n\n{guidance}"


async def upload_image(image_bytes: bytes, filename: str = "image.jpg") -> str | None:
    """Upload an image through Magic Hour's official temporary-upload flow."""
    if not API_KEY:
        log.error("MAGIC_HOUR_API_KEY is missing.")
        return None
    if not image_bytes:
        log.error("Image data is empty.")
        return None
    if len(image_bytes) > MAX_IMAGE_BYTES:
        log.error("Image exceeds the configured 20 MB limit.")
        return None

    extension = Path(filename or "image.jpg").suffix.lower().lstrip(".")
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
    if extension not in mime_types:
        extension = "jpg"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                f"{API_BASE}/files/upload-urls",
                headers=_headers(json_body=True),
                json={"items": [{"type": "image", "extension": extension}]},
            )
            if response.status_code not in (200, 201):
                log.error(
                    "Magic Hour upload-URL request failed (HTTP %s): %s",
                    response.status_code,
                    _response_error(response),
                )
                return None

            try:
                items = response.json().get("items", [])
            except Exception:
                log.error("Magic Hour returned invalid upload-URL JSON.")
                return None
            if not items or not isinstance(items[0], dict):
                log.error("Magic Hour upload response has no file entry.")
                return None

            upload_url = items[0].get("upload_url")
            file_path = items[0].get("file_path")
            parsed = urlparse(upload_url or "")
            if not file_path or parsed.scheme != "https" or not parsed.netloc:
                log.error("Magic Hour returned an invalid upload URL or file path.")
                return None

            # The signed upload URL is temporary; never send the API key to it.
            uploaded = await client.put(
                upload_url,
                content=image_bytes,
                headers={"Content-Type": mime_types[extension]},
            )
            if uploaded.status_code not in (200, 201, 204):
                log.error("Magic Hour image upload failed (HTTP %s).", uploaded.status_code)
                return None

            log.info("Image uploaded to Magic Hour storage.")
            return file_path
    except httpx.TimeoutException:
        log.error("Timed out while uploading the image to Magic Hour.")
    except httpx.HTTPError as exc:
        log.error("Image upload network error (%s).", type(exc).__name__)
    except Exception as exc:
        log.error("Image upload failed (%s).", type(exc).__name__)
    return None


def _extract_video_url(data: dict) -> str | None:
    """Read the documented downloads[].url field, with old response compatibility."""
    if not isinstance(data, dict):
        return None

    downloads = data.get("downloads")
    if isinstance(downloads, list):
        for item in downloads:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.startswith("https://"):
                    return url

    for key in ("video_url", "url"):
        url = data.get(key)
        if isinstance(url, str) and url.startswith("https://"):
            return url

    download = data.get("download")
    if isinstance(download, dict):
        url = download.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return None


async def try_magic_hour(
    prompt: str,
    image_file_path: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Create one Magic Hour job, poll it, and return its official download URL."""
    if not API_KEY:
        return None, None, "مفتاح Magic Hour غير موجود في GitHub Secrets."
    prompt = (prompt or "").strip()
    if not prompt:
        return None, None, "وصف الفيديو فارغ."

    payload = {
        "name": "Telegram AI Video",
        "end_seconds": VIDEO_SECONDS,
        "resolution": VIDEO_RESOLUTION,
        "model": VIDEO_MODEL,
        "style": {"prompt": prompt},
    }
    if image_file_path:
        payload["assets"] = {"image_file_path": image_file_path}
        endpoint = f"{API_BASE}/image-to-video"
    else:
        payload["aspect_ratio"] = "16:9"
        endpoint = f"{API_BASE}/text-to-video"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                endpoint,
                headers=_headers(json_body=True),
                json=payload,
            )
            if response.status_code not in (200, 201, 202):
                detail = _response_error(response)
                log.error("Magic Hour create failed (HTTP %s): %s", response.status_code, detail)
                return None, None, f"Magic Hour رفض الطلب (HTTP {response.status_code}): {detail}"

            try:
                project = response.json()
            except Exception:
                return None, None, "وصل رد غير صالح من Magic Hour عند إنشاء المشروع."

            direct_url = _extract_video_url(project)
            if direct_url:
                return direct_url, "Magic Hour", None

            project_id = project.get("id") or project.get("project_id")
            if not project_id:
                log.error("Magic Hour create response has no project ID.")
                return None, None, "قبلت الخدمة الطلب لكن لم ترجع معرّف مشروع الفيديو."

            project_url = f"{API_BASE}/video-projects/{project_id}"
            log.info("Magic Hour project created: %s", project_id)
            deadline = time.monotonic() + MAX_WAIT_SECONDS

            while time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                try:
                    polled = await client.get(project_url, headers=_headers())
                except httpx.TimeoutException:
                    log.warning("Magic Hour status poll timed out; will retry polling.")
                    continue
                except httpx.HTTPError as exc:
                    log.warning("Magic Hour status poll network error (%s).", type(exc).__name__)
                    continue

                if polled.status_code in (429, 500, 502, 503, 504):
                    log.warning("Magic Hour status poll returned HTTP %s.", polled.status_code)
                    continue
                if polled.status_code != 200:
                    detail = _response_error(polled)
                    log.error("Magic Hour status check failed (HTTP %s): %s", polled.status_code, detail)
                    return None, None, f"تعذر فحص حالة مشروع Magic Hour (HTTP {polled.status_code}): {detail}"

                try:
                    details = polled.json()
                except Exception:
                    log.warning("Magic Hour returned invalid status JSON.")
                    continue

                status = str(details.get("status", "")).lower()
                if status == "complete":
                    video_url = _extract_video_url(details)
                    if video_url:
                        return video_url, "Magic Hour", None
                    return None, None, "اكتمل التوليد لكن لم يرجع Magic Hour رابط التنزيل في downloads[].url."

                if status in ("error", "canceled", "cancelled", "failed", "failure"):
                    detail = details.get("error") or status
                    if isinstance(detail, dict):
                        detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
                    safe_detail = _safe_detail(detail, 500)
                    log.error("Magic Hour project finished with status %s: %s", status, safe_detail)
                    return None, None, f"انتهى مشروع Magic Hour بالحالة {status}: {safe_detail}"

            return (
                None,
                None,
                "لم يكتمل التوليد خلال مهلة الانتظار. لم أرسل طلباً جديداً حتى لا يتكرر خصم الرصيد؛ افحص المشروع في Magic Hour قبل إعادة المحاولة.",
            )
    except httpx.TimeoutException:
        log.error("Magic Hour create request timed out; no automatic retry was sent.")
        return None, None, "انتهت مهلة طلب Magic Hour. افحص المشاريع في حسابك قبل إعادة المحاولة حتى لا يتكرر خصم الرصيد."
    except httpx.HTTPError as exc:
        log.error("Magic Hour request failed (%s).", type(exc).__name__)
        return None, None, f"تعذر الاتصال بـ Magic Hour ({type(exc).__name__})."
    except Exception as exc:
        log.error("Magic Hour request failed (%s).", type(exc).__name__)
        return None, None, f"حدث خطأ أثناء طلب Magic Hour ({type(exc).__name__})."


async def generate_video_with_fallback(
    original_prompt: str,
    image_file_path: str | None = None,
    script_text: str = "",
) -> tuple[str | None, str | None, str | None]:
    """Keep the existing bot function name and return shape for compatibility."""
    prompt = (original_prompt or script_text or "").strip()
    if not prompt:
        return None, None, "وصف الفيديو فارغ."
    enhanced = await enhance_prompt(prompt)
    return await try_magic_hour(enhanced, image_file_path=image_file_path)


async def _download_to_file(url: str, destination: Path) -> None:
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.netloc:
        raise VideoProcessingError("رابط تنزيل الفيديو غير صالح.")

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise VideoProcessingError(
                        f"فشل تنزيل فيديو Magic Hour (HTTP {response.status_code})."
                    )
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > MAX_SOURCE_VIDEO_BYTES:
                    raise VideoProcessingError("حجم فيديو Magic Hour أكبر من الحد المسموح للمعالجة.")
                total = 0
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        total += len(chunk)
                        if total > MAX_SOURCE_VIDEO_BYTES:
                            raise VideoProcessingError("حجم فيديو Magic Hour تجاوز الحد المسموح للمعالجة.")
                        output.write(chunk)
                if total == 0:
                    raise VideoProcessingError("ملف الفيديو الذي نزل من Magic Hour فارغ.")
    except VideoProcessingError:
        raise
    except httpx.TimeoutException:
        raise VideoProcessingError("انتهت مهلة تنزيل الفيديو من Magic Hour.") from None
    except httpx.HTTPError as exc:
        raise VideoProcessingError(
            f"تعذر تنزيل الفيديو من Magic Hour ({type(exc).__name__})."
        ) from None


async def _run_process(command: list[str], label: str) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise VideoProcessingError("FFmpeg أو ffprobe غير مثبت في GitHub Actions.") from None

    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-1800:].strip()
        log.error("%s failed (exit %s): %s", label, process.returncode, detail or "no stderr")
        raise VideoProcessingError(
            f"فشل {label} (رمز {process.returncode}). "
            f"التفاصيل: {detail or 'لا توجد تفاصيل من FFmpeg.'}"
        )
    return stdout


async def _probe_media(path: Path) -> dict:
    output = await _run_process(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height,duration",
            "-of", "json", str(path),
        ],
        "فحص ملف الوسائط",
    )
    try:
        data = json.loads(output.decode("utf-8", errors="replace"))
        streams = data.get("streams", [])
        duration = float((data.get("format") or {}).get("duration") or 0)
        if duration <= 0:
            values = [float(item.get("duration", 0)) for item in streams if item.get("duration")]
            duration = max(values, default=0)
        video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    except (ValueError, TypeError, json.JSONDecodeError):
        raise VideoProcessingError("تعذر قراءة معلومات ملف الفيديو أو الصوت.") from None

    if duration <= 0:
        raise VideoProcessingError("ملف الوسائط لا يحتوي على مدة قابلة للقراءة.")
    return {
        "duration": duration,
        "video": video_stream,
        "has_audio": bool(audio_streams),
        "width": int((video_stream or {}).get("width") or 0),
        "height": int((video_stream or {}).get("height") or 0),
    }


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _ass_text(script_text: str, chars_per_line: int) -> str:
    normalized = (script_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = normalized.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    lines = []
    for paragraph in normalized.split("\n"):
        wrapped = textwrap.wrap(
            paragraph,
            width=chars_per_line,
            break_long_words=True,
            break_on_hyphens=False,
        )
        lines.extend(wrapped or [""])
    return r"\N".join(lines)


def _write_ass(path: Path, script_text: str, width: int, height: int, duration: float) -> None:
    width = max(width, 320)
    height = max(height, 240)
    font_size = max(22, int(min(width, height) * 0.055))
    chars_per_line = max(18, int(width / (font_size * 0.62)))
    caption = _ass_text(script_text, chars_per_line)
    content = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans Arabic,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,3,1,0,8,30,30,36,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{_ass_timestamp(duration)},Default,,0,0,0,,{caption}
"""
    path.write_text(content, encoding="utf-8")


def _filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


async def process_video_with_audio_and_text(video_url: str, script_text: str) -> bytes:
    """Download, add Arabic TTS and burned-in captions, then verify the MP4."""
    script_text = (script_text or "").strip()
    if not script_text:
        raise VideoProcessingError("النص المطلوب للصوت والترجمة فارغ.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise VideoProcessingError("FFmpeg أو ffprobe غير مثبت في GitHub Actions.")
    if not Path(FONT_DIR).exists():
        raise VideoProcessingError("خط Noto العربي غير مثبت؛ تحقق من خطوة تثبيت الخطوط في run.yml.")

    with tempfile.TemporaryDirectory(prefix="telegram-video-") as temporary_directory:
        work = Path(temporary_directory)
        source_path = work / "source.mp4"
        voice_path = work / "voice.mp3"
        caption_path = work / "captions.ass"
        output_path = work / "final.mp4"

        await _download_to_file(video_url, source_path)
        try:
            communicate = edge_tts.Communicate(script_text, VOICE_NAME)
            await communicate.save(str(voice_path))
        except Exception as exc:
            log.error("Edge TTS failed (%s).", type(exc).__name__)
            raise VideoProcessingError(
                "تعذر توليد التعليق الصوتي عبر Edge TTS. تحقق من اتصال GitHub Actions وحاول مرة أخرى."
            ) from None

        if not voice_path.exists() or voice_path.stat().st_size == 0:
            raise VideoProcessingError("خدمة الصوت لم تُرجع ملفاً صوتياً.")

        source_info = await _probe_media(source_path)
        voice_info = await _probe_media(voice_path)
        if not source_info["video"]:
            raise VideoProcessingError("ملف Magic Hour لا يحتوي على مسار فيديو.")
        if not voice_info["has_audio"]:
            raise VideoProcessingError("ملف التعليق الصوتي لا يحتوي على مسار صوت.")

        target_duration = max(
            float(VIDEO_SECONDS),
            source_info["duration"],
            voice_info["duration"],
        )
        extra_video = max(0.0, target_duration - source_info["duration"])
        _write_ass(
            caption_path,
            script_text,
            source_info["width"],
            source_info["height"],
            target_duration,
        )

        subtitle_filter = (
            f"subtitles=filename={_filter_path(str(caption_path))}:"
            f"fontsdir={_filter_path(FONT_DIR)}"
        )
        video_filter = f"tpad=stop_mode=clone:stop_duration={extra_video:.3f},{subtitle_filter}"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_path),
            "-i", str(voice_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", video_filter,
            "-af", f"apad=whole_dur={target_duration:.3f}",
            "-t", f"{target_duration:.3f}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        await _run_process(command, "معالجة FFmpeg")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise VideoProcessingError("FFmpeg لم يُنشئ ملف فيديو نهائياً.")
        if output_path.stat().st_size > MAX_TELEGRAM_VIDEO_BYTES:
            raise VideoProcessingError(
                "حجم الفيديو النهائي أكبر من حد Telegram الآمن (49 MB). "
                "اخفض الدقة في إعداد VIDEO_RESOLUTION ثم أعد المحاولة."
            )

        final_info = await _probe_media(output_path)
        if not final_info["video"]:
            raise VideoProcessingError("الملف النهائي لا يحتوي على مسار فيديو.")
        if not final_info["has_audio"]:
            raise VideoProcessingError("الملف النهائي لا يحتوي على مسار صوت.")
        if final_info["duration"] < VIDEO_SECONDS - 0.05:
            raise VideoProcessingError(
                f"مدة الملف النهائي {final_info['duration']:.2f} ثانية، وهي أقل من {VIDEO_SECONDS} ثوانٍ."
            )

        return output_path.read_bytes()


async def health_check() -> dict[str, bool]:
    return {
        "مفتاح Magic Hour مضبوط": bool(API_KEY),
        "FFmpeg مثبت": shutil.which("ffmpeg") is not None,
        "ffprobe مثبت": shutil.which("ffprobe") is not None,
        "خط Noto العربي مثبت": Path(FONT_DIR).exists(),
        "إعدادات الصوت والنص جاهزة": True,
        }
