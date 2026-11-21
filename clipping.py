"""Local clipping workbench operated by the assistant. No LLM API required.

OpenShorts provides edit-list cuts, timeline mapping and caption segmentation.
Assistant decisions live in inspectable JSON manifests; source text is data.
"""
from __future__ import annotations

import argparse
import ctypes
from functools import lru_cache
import hashlib
import html
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("TEMP", str(ROOT / "temp"))
os.environ.setdefault("TMP", str(ROOT / "temp"))
os.environ.setdefault("FFMPEG_ENCODER", "auto")
VENDOR = ROOT / "vendor" / "openshorts"
sys.path.insert(0, str(VENDOR))
PIN = "8af4a48a2bdda96c9a02bb8df3f09d4e7ec99e0f"
MODEL = ROOT / "models" / "whisper-turbo"
FONTS_DIR = ROOT / "assets" / "fonts"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DEFAULT_ARABIC_PROMPT = (
    "كلام مصري طبيعي ومصطلحات إنجليزي وتقنية وجيمنج زي "
    "GTA, YouTube, Shorts, PC, GPU, AI, Python, FPS, Stream."
)
CAPTION_FONTS = {
    "Arial": ("Arial", None, "arialbd.ttf"),
    "Tajawal": ("Tajawal", "Tajawal-Bold.ttf", None),
    "Cairo": ("Cairo", "Cairo.ttf", None),
    "Segoe UI": ("Segoe UI", None, "segoeuib.ttf"),
    "Tahoma": ("Tahoma", None, "tahomabd.ttf"),
}
HIGHLIGHT_COLORS = {
    "yellow": "0000E7FF",
    "cyan": "00FFFF00",
    "green": "0050FF50",
    "pink": "00B469FF",
    "white": "00FFFFFF",
}
_DLL_HANDLES = []
if os.name == "nt":
    _cuda_bins = list((ROOT / ".venv" / "Lib" / "site-packages" / "nvidia").glob("*/bin"))
    for _cuda_bin in _cuda_bins:
        _DLL_HANDLES.append(os.add_dll_directory(str(_cuda_bin)))
    if _cuda_bins:
        os.environ["PATH"] = os.pathsep.join(str(p) for p in _cuda_bins) + os.pathsep + os.environ["PATH"]


def cuda_libraries():
    result = {}
    for name in ("cublas64_12.dll", "cudnn64_9.dll"):
        try:
            ctypes.WinDLL(name)
            result[name] = True
        except (OSError, AttributeError):
            result[name] = False
    return result


def asr_device(requested):
    if requested != "auto":
        return requested
    import ctranslate2
    return "cuda" if ctranslate2.get_cuda_device_count() > 0 and all(cuda_libraries().values()) else "cpu"


def installed_model():
    for name in ("whisper-turbo", "whisper-medium", "whisper-base"):
        path = ROOT / "models" / name
        if (path / "model.bin").is_file() and (path / "verified-sha256.txt").is_file():
            return path
    return None


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run(argv, *, cwd=None, log=None, timeout=1800):
    argv = [str(a) for a in argv]
    result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout,
                            encoding="utf-8", errors="replace", shell=False)
    if log:
        with Path(log).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(argv, ensure_ascii=False) + "\n" + result.stderr + "\n")
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv[0]}\n{result.stderr[-4500:]}")
    return result.stdout


def probe(path: Path):
    raw = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path]))
    video = next((s for s in raw["streams"] if s["codec_type"] == "video"), None)
    duration = float(raw["format"].get("duration", 0))
    return {"duration": duration, "width": video["width"] if video else 0,
            "height": video["height"] if video else 0,
            "has_audio": any(s["codec_type"] == "audio" for s in raw["streams"]),
            "has_video": video is not None, "raw": raw}


def space(required_gb=2):
    available = shutil.disk_usage(ROOT).free / 1024**3
    if available < required_gb:
        raise RuntimeError(f"Only {available:.2f} GiB free on workspace drive; need {required_gb:.2f} GiB.")
    return available


def job_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / "jobs" / value
    path = path.resolve()
    if not path.is_relative_to((ROOT / "jobs").resolve()):
        raise ValueError("Job must be inside workspace jobs directory")
    if not (path / "job.json").is_file():
        raise ValueError(f"Job not found: {path}")
    return path


def source_for(job):
    metadata = read(job / "job.json")
    source = Path(metadata["source"])
    if not source.is_file():
        raise ValueError(f"Source file missing: {source}")
    return source, metadata


def doctor(args):
    result = {"workspace": str(ROOT), "free_gib": round(space(0), 2), "python": sys.version,
              "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
              "node": shutil.which("node"), "model_ready": installed_model() is not None,
              "default_asr_model": str(installed_model()) if installed_model() else None,
              "turbo_ready": (MODEL / "model.bin").is_file() and (MODEL / "verified-sha256.txt").is_file()}
    result["cuda_libraries"] = cuda_libraries()
    for package in ("faster-whisper", "ctranslate2", "yt-dlp", "yt-dlp-ejs", "Pillow", "opencv-python-headless", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "MISSING"
    result["face_track_ready"] = (
        result.get("opencv-python-headless") != "MISSING"
        and (ROOT / "models" / "face-detect" / "face_detection_yunet_2023mar.onnx").is_file()
    )
    result["bundled_fonts"] = sorted(p.name for p in FONTS_DIR.glob("*.ttf")) if FONTS_DIR.is_dir() else []
    try:
        import ctranslate2
        result["cuda_device_count"] = ctranslate2.get_cuda_device_count()
        result["selected_asr_device"] = asr_device("auto")
    except Exception as exc:
        result["cuda_error"] = str(exc)
    try:
        from ffmpeg_utils import nvenc_available
        result["nvenc_available"] = nvenc_available()
        result["openshorts_commit"] = run(["git", "-C", VENDOR, "rev-parse", "HEAD"]).strip()
        result["openshorts_pin_matches"] = result["openshorts_commit"] == PIN
    except Exception as exc:
        result["openshorts_error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    dump(ROOT / "doctor.json", result)


def ingest(args):
    space(3)
    digest = hashlib.sha256(args.source.encode()).hexdigest()[:8]
    name = args.name or datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + digest
    if not SAFE_ID.fullmatch(name):
        raise ValueError("Job name must use letters, digits, underscores and hyphens")
    job = ROOT / "jobs" / name
    if job.exists():
        request_path = job / "request.json"
        if not request_path.exists() or read(request_path).get("source") != args.source:
            raise ValueError("Job already exists with a different source")
        if (job / "job.json").exists():
            print(f"Using existing job: {job}")
            return
    job.mkdir(parents=True, exist_ok=True)
    dump(job / "request.json", {"source": args.source, "created_at": datetime.now(timezone.utc).isoformat()})
    is_url = args.source.startswith(("https://", "http://"))
    source = Path(args.source)
    if is_url:
        import yt_dlp
        from yt_dlp.networking.exceptions import TransportError
        options = {"noplaylist": True, "quiet": True, "noprogress": True, "no_warnings": False,
                   "js_runtimes": {"deno": {}, "node": {}}, "cachedir": str(ROOT / "cache" / "ytdlp"),
                   "socket_timeout": 30, "retries": 3, "fragment_retries": 3,
                   "windowsfilenames": True, "restrictfilenames": True,
                   "outtmpl": str(job / "source.%(ext)s"), "merge_output_format": "mp4",
                   "format": "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=?1080]/bv*[height<=1080]+ba",
                   "max_filesize": args.max_gb * 1024**3}
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(args.source, download=False)
                if not info or "entries" in info or info.get("is_live"):
                    raise ValueError("Use a single completed video, not a channel, playlist or active livestream")
                duration = info.get("duration")
                if duration and duration > args.max_hours * 3600:
                    raise ValueError(f"Source exceeds requested {args.max_hours} hour ceiling")
                estimate = info.get("filesize") or info.get("filesize_approx") or 0
                if estimate:
                    space(max(3, estimate / 1024**3 * 2 + 1))
                dump(job / "source-info.json", {k: info.get(k) for k in
                     ("id", "title", "uploader", "duration", "webpage_url", "license", "extractor")})
                try:
                    ydl.download([args.source])
                except yt_dlp.utils.DownloadError as error:
                    # An available progressive rendition can work when a
                    # separate DASH media URL is refused. One bounded retry;
                    # never use cookies or claim the fallback is full HD.
                    if "403" not in str(error) or "youtube" not in str(info.get("extractor", "")).lower():
                        raise
                    print("Separate media URL refused; trying one progressive rendition. Source quality will be recorded.", flush=True)
                    fallback = {**options, "format": "b[height<=?1080]/b"}
                    with yt_dlp.YoutubeDL(fallback) as progressive:
                        progressive.download([args.source])
                    dump(job / "download-fallback.json", {"reason": "HTTP 403 on preferred media",
                        "format": fallback["format"], "quality": "Probe source media; progressive may be lower resolution"})
        except (yt_dlp.utils.DownloadError, TransportError) as exc:
            dump(job / "download-error.json", {"error": str(exc), "retry_same_job": True})
            raise RuntimeError("Download unavailable. Details saved in download-error.json; retry same job after resolving access.") from exc
        files = [p for p in job.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov") and p.stem == "source"]
        if len(files) != 1:
            raise RuntimeError("Download did not produce exactly one source video")
        source = files[0]
    else:
        source = source.resolve()
        if not source.is_file():
            raise ValueError("Local source does not exist")
    media = probe(source)
    if not media["has_video"] or media["duration"] <= 0:
        raise ValueError("Source must be a video with a known duration")
    if not is_url:
        dump(job / "source-info.json", {"title": source.stem, "local": True})
    metadata = {"schema_version": 1, "source": str(source), "original": args.source,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "media": {k: v for k, v in media.items() if k != "raw"}, "status": "ingested"}
    dump(job / "job.json", metadata)
    print(str(job))


def decode_pcm(job, source):
    """Preserve the audio PTS timeline, including lead-in and discontinuities."""
    pcm_path = job / "audio-16k.f32"
    audio_key = f"v2:{source.stat().st_size}:{source.stat().st_mtime_ns}"
    receipt = job / "audio-cache.json"
    if not pcm_path.is_file() or not receipt.exists() or read(receipt).get("key") != audio_key:
        temp = job / "audio-16k.f32.partial"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", source,
             "-vn", "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1", "-ar", "16000", "-f", "f32le", temp], timeout=7200)
        temp.replace(pcm_path)
        dump(receipt, {"key":audio_key,"sample_rate":16000,"channels":1,"preserve_audio_timeline":True})
    if pcm_path.stat().st_size == 0:
        raise ValueError("Decoded audio is empty")
    return pcm_path


def transcribe(args):
    job = job_path(args.job)
    source, metadata = source_for(job)
    if transcribe_media(args, job, source, metadata):
        metadata["status"] = "transcribed"
        dump(job / "job.json", metadata)


def resolve_asr_prompt(args):
    """Resolve optional Whisper initial_prompt from CLI flags, profiles.json, or Arabic default."""
    if getattr(args, "no_prompt", False):
        return ""
    explicit = getattr(args, "prompt", None)
    if explicit is not None:
        text = str(explicit).strip()
        if len(text) > 400 or any(ord(ch) < 32 for ch in text):
            raise ValueError("ASR prompt must be single-line text up to 400 characters")
        return text
    profile_name = getattr(args, "profile", None)
    if profile_name:
        profiles_path = ROOT / "profiles.json"
        if not profiles_path.is_file():
            raise ValueError(f"Profile config missing: {profiles_path}")
        profiles = read(profiles_path)
        if profile_name not in profiles:
            raise ValueError(f"Unknown profile: {profile_name} (available: {', '.join(sorted(profiles))})")
        return str(profiles[profile_name].get("initial_prompt", "")).strip()
    if getattr(args, "language", "auto") == "ar":
        return DEFAULT_ARABIC_PROMPT
    return ""


def transcribe_media(args, job, source, metadata):
    from faster_whisper import WhisperModel
    import numpy as np
    if not metadata["media"]["has_audio"]:
        raise ValueError("No audio track; use visual review and a manual edit manifest")
    model_path = Path(args.model) if args.model else installed_model()
    if model_path is None or not (model_path / "model.bin").is_file():
        raise ValueError(f"Model not installed: {model_path}")
    device = asr_device(args.device)
    compute = getattr(args, "compute_type", "auto")
    if compute == "auto":
        compute = "int8" if device == "cpu" else "float16"
    initial_prompt = resolve_asr_prompt(args)
    print(f"ASR model: {model_path.name}; device: {device}; precision: {compute}", flush=True)
    settings = {"model": str(model_path.resolve()), "language": args.language,
                "device": device, "compute_type": compute,
                "beam_size": 5, "word_timestamps": True, "condition_on_previous_text": False, "vad_filter": True,
                "initial_prompt": initial_prompt, "audio_decoder_version": 2}
    key = hashlib.sha256(json.dumps({"source_size": source.stat().st_size,
        "source_mtime_ns": source.stat().st_mtime_ns, "settings": settings}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    dest = job / "transcript.json"
    if dest.exists() and read(dest).get("cache_key") == key and not args.force:
        print(f"Using cached transcript: {dest}")
        return
    space(1)
    started = time.monotonic()
    # Decode with our tested FFmpeg binary. Supplying PCM also avoids PyAV
    # keyword/API changes between releases and memory-maps long recordings.
    pcm_path = decode_pcm(job, source)
    audio = np.memmap(pcm_path, dtype="float32", mode="r")
    model = WhisperModel(str(model_path), device=device,
                         compute_type=settings["compute_type"], cpu_threads=min(10, os.cpu_count() or 4),
                         num_workers=1, local_files_only=True)
    segments, info = model.transcribe(audio, language=None if args.language == "auto" else args.language,
        beam_size=5, word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
        initial_prompt=initial_prompt or None)
    output = []
    for segment in segments:
        words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3),
                  "probability": round(w.probability, 4)} for w in (segment.words or [])]
        output.append({"start": round(segment.start, 3), "end": round(segment.end, 3),
                       "text": segment.text.strip(), "words": words,
                       "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob})
        dump(job / "transcript.partial.json", {"complete": False, "segments": output})
        if not getattr(args, "quiet_transcript", False):
            print(f"[{segment.start:.1f} - {segment.end:.1f}] {segment.text}", flush=True)
    result = {"schema_version": 1, "cache_key": key, "settings": settings, "language": info.language,
              "language_probability": info.language_probability, "segments": output,
              "processing_seconds": round(time.monotonic() - started, 2), "complete": True}
    dump(dest, result)
    from subtitles import generate_srt
    (job / "transcript.srt").write_text("", encoding="utf-8")
    generate_srt(result, 0, metadata["media"]["duration"], str(job / "transcript.srt"),
                 max_chars=42, max_duration=4)
    text = "\n".join(f"[{s['start']:.3f} --> {s['end']:.3f}] {s['text']}" for s in output)
    (job / "transcript.txt").write_text(text, encoding="utf-8")
    print(f"Saved {len(output)} segments in {result['processing_seconds']} seconds")
    return True


def frames(args):
    from PIL import Image, ImageDraw, ImageFont
    job = job_path(args.job)
    source, metadata = source_for(job)
    end = args.end if args.end is not None else metadata["media"]["duration"]
    if not (0 <= args.start < end <= metadata["media"]["duration"] + 0.05):
        raise ValueError("Frame interval outside source")
    count = args.count
    if not 1 <= count <= 48:
        raise ValueError("Choose 1–48 frames")
    dest = job / "frames" / f"{args.start:.1f}-{end:.1f}"
    dest.mkdir(parents=True, exist_ok=True)
    tiles = []
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    for i in range(count):
        stamp = args.start + (end - args.start) * (i + 0.5) / count
        path = dest / f"frame_{i:02d}.jpg"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", stamp,
             "-i", source, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", path])
        with Image.open(path) as picture:
            tile = Image.new("RGB", (480, 300), "#10131b")
            thumb = picture.copy()
            thumb.thumbnail((480, 270))
            tile.paste(thumb, ((480-thumb.width)//2, 0))
            ImageDraw.Draw(tile).text((10, 276), f"{stamp:.2f}s", font=font, fill="white")
            tiles.append(tile)
    columns = min(3, count)
    sheet = Image.new("RGB", (columns*480, math.ceil(count/columns)*300), "#10131b")
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % columns)*480, (i//columns)*300))
    sheet.save(dest / "contact-sheet.jpg", quality=90)
    print(dest / "contact-sheet.jpg")


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Timestamps must be finite numbers")
    return float(value)


def validate_manifest(manifest, media):
    from recut import normalize_segments
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not 1 <= len(clips) <= 50:
        raise ValueError("Manifest must contain 1–50 clips")
    seen = set()
    normalized = []
    for clip in clips:
        if not isinstance(clip, dict):
            raise ValueError("Each clip must be an object")
        cid = clip.get("id", "")
        if not isinstance(cid, str) or not SAFE_ID.fullmatch(cid) or cid in seen:
            raise ValueError("Clip IDs must be unique safe names")
        seen.add(cid)
        segments = clip.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ValueError("Each clip needs source-absolute segments")
        for seg in segments:
            if not isinstance(seg, dict) or "start" not in seg or "end" not in seg:
                raise ValueError("Each segment needs start and end")
            start, end = numeric(seg["start"]), numeric(seg["end"])
            if not (0 <= start < end <= media["duration"] + 0.05):
                raise ValueError(f"{cid}: segment outside source duration")
        segments = normalize_segments(segments, source_duration=media["duration"])
        layout = clip.get("layout", "fit")
        if layout not in ("fit", "center_crop", "webcam_stack", "face_track"):
            raise ValueError("Supported layouts: fit, center_crop, webcam_stack, face_track")
        if layout == "webcam_stack":
            box = clip.get("webcam_box")
            if not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box):
                raise ValueError("webcam_box must be [x,y,width,height] in source pixels")
            x, y, w, h = box
            if min(x, y) < 0 or min(w, h) < 2 or x+w > media["width"] or y+h > media["height"]:
                raise ValueError("webcam_box exceeds source image")
        for field in ("title", "description", "hook"):
            if not isinstance(clip.get(field, ""), str):
                raise ValueError(f"{field} must be text")
        if len(clip.get("hook", "")) > 100:
            raise ValueError("Hook must be 100 characters or shorter")
        caption_options = clip.get("caption_options", {})
        allowed_caption_keys = {"max_chars", "max_duration", "min_duration", "style", "font", "highlight_color"}
        if not isinstance(caption_options, dict) or set(caption_options) - allowed_caption_keys:
            raise ValueError("caption_options supports max_chars, max_duration, min_duration, style, font, highlight_color")
        max_chars = caption_options.get("max_chars", 34)
        if type(max_chars) is not int or not 20 <= max_chars <= 60:
            raise ValueError("caption max_chars must be an integer between 20 and 60")
        for field, default, low, high in (("max_duration",2.8,1,5),("min_duration",0.65,0,1.5)):
            value = numeric(caption_options.get(field, default))
            if not low <= value <= high:
                raise ValueError(f"caption {field} outside supported range")
        style = caption_options.get("style", "phrase")
        if style not in ("phrase", "word_highlight"):
            raise ValueError("caption style must be phrase or word_highlight")
        font = caption_options.get("font", "Arial")
        if not isinstance(font, str) or font not in CAPTION_FONTS:
            raise ValueError("caption font must be one of: " + ", ".join(CAPTION_FONTS))
        highlight_color = caption_options.get("highlight_color", "yellow")
        if not isinstance(highlight_color, str) or highlight_color not in HIGHLIGHT_COLORS:
            raise ValueError("caption highlight_color must be one of: " + ", ".join(HIGHLIGHT_COLORS))
        normalized.append({**clip, "segments": segments, "layout": layout})
    return normalized


def ass_time(seconds):
    centis = max(0, round(seconds*100))
    return f"{centis//360000}:{centis//6000%60:02d}:{centis//100%60:02d}.{centis%100:02d}"


def ass_escape(text):
    return str(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def resolve_caption_font(font_name="Arial"):
    """Return (ass_font_name, ttf_path, needs_fontsdir) from the safe font registry."""
    if font_name not in CAPTION_FONTS:
        raise ValueError("Unsupported caption font: " + str(font_name))
    ass_name, bundled_file, win_file = CAPTION_FONTS[font_name]
    win_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    if bundled_file:
        bundled = FONTS_DIR / bundled_file
        if bundled.is_file():
            return ass_name, bundled, True
        return "Arial", win_fonts / "arialbd.ttf", False
    target = win_fonts / win_file
    if target.is_file():
        return ass_name, target, False
    return "Arial", win_fonts / "arialbd.ttf", False


def ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter option value."""
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


@lru_cache(maxsize=32)
def caption_font(size, font_name="Arial"):
    from PIL import ImageFont

    _, ttf_path, _ = resolve_caption_font(font_name)
    return ImageFont.truetype(str(ttf_path), size)


def caption_size(text, size, budget, font_name="Arial"):
    font = caption_font(size, font_name)
    left, _, right, _ = font.getbbox(text)
    # Leave room for stroke and layout differences between FreeType and libass.
    measured = max(right - left, font.getlength(text)) * 1.08
    return max(1, math.floor(size * min(1, budget / max(measured, 1))))


def caption_groups(words, size, budget, font_name="Arial"):
    start = 0
    for end in range(1, len(words)):
        if caption_size(" ".join(words[start:end + 1]), size, budget, font_name) < size:
            yield start, end
            start = end
    if words:
        yield start, len(words)


def ass_sized(text, size, budget, scale, font_name="Arial"):
    fitted = caption_size(text, size, budget, font_name)
    prefix = rf"{{\fs{max(1, round(fitted * scale))}}}" if fitted < size else ""
    return prefix + ass_escape(text)


def smooth_short_captions(path, max_chars=34, min_duration=0.65, max_duration=2.8):
    """Merge orphaned short cues without altering transcript word timings."""
    def stamp(seconds):
        millis = round(seconds*1000)
        return f"{millis//3600000:02d}:{millis//60000%60:02d}:{millis//1000%60:02d},{millis%1000:03d}"
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        match = re.fullmatch(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not match:
            raise ValueError("Invalid caption timing")
        n = [int(v) for v in match.groups()]
        start = n[0]*3600+n[1]*60+n[2]+n[3]/1000
        end = n[4]*3600+n[5]*60+n[6]+n[7]/1000
        text = " ".join(lines[2:])
        if cues and end-start < min_duration:
            prev = cues[-1]
            merged = prev[2]+" "+text
            if start-prev[1] <= 0.5 and end-prev[0] <= max_duration+0.6 and len(merged) <= max_chars+10:
                cues[-1] = (prev[0],end,merged)
                continue
        cues.append((start,end,text))
    path.write_text("\n".join(f"{i+1}\n{stamp(s)} --> {stamp(e)}\n{t}\n" for i,(s,e,t) in enumerate(cues)), encoding="utf-8")


def _collect_timed_words(virtual):
    """Extract ordered (text, start, end) words from a remapped virtual transcript."""
    words = []
    for seg in (virtual or {}).get("segments", []):
        for w in seg.get("words") or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            start = w.get("start")
            end = w.get("end")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start >= 0:
                words.append({"text": text, "start": float(start), "end": float(end)})
    return words


def _group_highlight_words(words, max_chars=34, max_duration=2.8, min_duration=0.65, font_name="Arial"):
    """Partition timed words into mobile-safe phrases for word-by-word highlighting."""
    groups = []
    cur = []
    for w in words:
        cand = cur + [w]
        text = " ".join(item["text"] for item in cand)
        dur = cand[-1]["end"] - cand[0]["start"]
        gap = w["start"] - cur[-1]["end"] if cur else 0.0
        fits_width = caption_size(text, 54, 868, font_name) >= 54
        if cur and (gap > 0.55 or dur > max_duration or len(text) > max_chars or not fits_width):
            groups.append(cur)
            cur = [w]
        else:
            cur = cand
    if cur:
        groups.append(cur)
    # Merge short orphaned single-word tail if it fits cleanly within limits.
    if len(groups) >= 2 and len(groups[-1]) == 1:
        tail = groups[-1][0]
        prev = groups[-2]
        merged_text = " ".join(item["text"] for item in prev + [tail])
        if (tail["end"] - tail["start"] < min_duration
                and tail["start"] - prev[-1]["end"] <= 0.5
                and tail["end"] - prev[0]["start"] <= max_duration + 0.6
                and len(merged_text) <= max_chars + 10
                and caption_size(merged_text, 54, 868, font_name) >= 48):
            groups[-2].append(tail)
            groups.pop()
    return groups


def make_ass(srt, dest, width, height, hook="", *, font="Arial", style="phrase",
             highlight_color="yellow", virtual=None, max_chars=34, max_duration=2.8, min_duration=0.65):
    scale = width/1080
    ass_font_name, _, _ = resolve_caption_font(font)
    hi_bgr = HIGHLIGHT_COLORS[highlight_color]
    outline_px = max(1, round((5 if style == "word_highlight" else 3) * scale))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{ass_font_name},{round(54*scale)},&H00FFFFFF,&H00FFFFFF,&H00101010,&H99000000,-1,0,0,0,100,100,0,0,1,{outline_px},1,2,{round(65*scale)},{round(65*scale)},{round(310*scale)},1
Style: Hook,{ass_font_name},{round(60*scale)},&H0000DFFF,&H0000DFFF,&H00101010,&H99000000,-1,0,0,0,100,100,0,0,1,{outline_px},1,8,{round(70*scale)},{round(70*scale)},{round(175*scale)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    timed_words = _collect_timed_words(virtual) if style == "word_highlight" else []
    if style == "word_highlight" and timed_words:
        for group in _group_highlight_words(timed_words, max_chars, max_duration, min_duration, font):
            group_end = group[-1]["end"]
            line_text = " ".join(w["text"] for w in group)
            fitted = caption_size(line_text, 54, 868, font)
            fs_prefix = rf"{{\fs{max(1, round(fitted * scale))}}}" if fitted < 54 else ""
            escaped = [ass_escape(w["text"]) for w in group]
            for pos, word in enumerate(group):
                next_t = group[pos + 1]["start"] if pos + 1 < len(group) else group_end
                end_t = max(word["end"], next_t) if pos + 1 == len(group) else max(word["start"] + 0.02, next_t)
                if ass_time(word["start"]) == ass_time(end_t):
                    continue
                parts = escaped.copy()
                parts[pos] = rf"{{\1c&H{hi_bgr}&}}{escaped[pos]}{{\r}}{fs_prefix}"
                events.append(f"Dialogue: 0,{ass_time(word['start'])},{ass_time(end_t)},Caption,,0,0,0,,{fs_prefix}{' '.join(parts)}")
    else:
        for block in re.split(r"\r?\n\s*\r?\n", srt.read_text(encoding="utf-8-sig").strip()):
            lines = block.splitlines()
            if len(lines) < 3:
                continue
            match = re.fullmatch(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
            if not match:
                raise ValueError("Invalid generated SRT timing")
            nums = [int(n) for n in match.groups()]
            start = nums[0]*3600+nums[1]*60+nums[2]+nums[3]/1000
            end = nums[4]*3600+nums[5]*60+nums[6]+nums[7]/1000
            words = " ".join(lines[2:]).split()
            text = r"\N".join(
                ass_sized(" ".join(words[a:b]), 54, 868, scale, font) + r"{\r}"
                for a, b in caption_groups(words, 54, 868, font)
            )
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{text}")
    if hook:
        events.append(f"Dialogue: 1,0:00:00.00,0:00:03.00,Hook,,0,0,0,,{ass_sized(hook, 60, 868, scale, font)}")
    dest.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def render(args):
    from recut import run_cut_concat, virtual_transcript
    from subtitles import generate_srt
    from ffmpeg_utils import video_encode_args, QUALITY, METADATA_SCRUB
    job = job_path(args.job)
    source, metadata = source_for(job)
    manifest_path = Path(args.manifest) if args.manifest else job / "clips.json"
    manifest = read(manifest_path)
    clips = validate_manifest(manifest, metadata["media"])
    transcript = read(job / "transcript.json") if (job / "transcript.json").is_file() else {"segments": []}
    width = args.width
    if width not in (360, 720, 1080):
        raise ValueError("Width must be 360, 720 or 1080")
    height = width*16//9
    if height % 2:
        height += 1
    space(2)
    preview = getattr(args, "preview", False)
    result_dir = job / "previews" / "highlights" if preview else job
    dest = result_dir / "exports"
    dest.mkdir(parents=True, exist_ok=True)
    log = result_dir / "render.log"
    log.touch()
    exports = []
    for clip in clips:
        folder = dest / clip["id"]
        folder.mkdir(exist_ok=True)
        output = folder / "final.mp4"
        cache_key = hashlib.sha256(json.dumps({"clip": clip, "width": width,
            "source_size": source.stat().st_size, "source_mtime_ns": source.stat().st_mtime_ns,
            "transcript": transcript, "renderer_version": 5,
            "encoder": video_encode_args(QUALITY)[1]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        receipt_path = folder / "receipt.json"
        if output.exists() and receipt_path.exists() and read(receipt_path).get("cache_key") == cache_key and not args.force:
            print(f"Using existing render: {output}")
            exports.append(read(receipt_path))
            continue
        def cutter(command):
            command = [str(v) for v in command]
            # Neutral source/job names; POSIX separators also work in Windows concat lists.
            if not metadata["media"]["has_audio"] and "-af" in command:
                idx = command.index("-af")
                del command[idx:idx+2]
            run(command, log=log)
        intermediate = folder / "cut.mp4"
        run_cut_concat(source.as_posix(), clip["segments"], intermediate.as_posix(), folder.as_posix(), runner=cutter)
        virtual = virtual_transcript(transcript, clip["segments"])
        duration = sum(s["end"]-s["start"] for s in clip["segments"])
        srt = folder / "captions.srt"
        srt.write_text("", encoding="utf-8")
        captions = clip.get("caption_options", {})
        max_chars, max_duration = captions.get("max_chars",34), captions.get("max_duration",2.8)
        min_duration = captions.get("min_duration", 0.65)
        cap_style = captions.get("style", "phrase")
        cap_font = captions.get("font", "Arial")
        cap_color = captions.get("highlight_color", "yellow")
        generate_srt(virtual, 0, duration, str(srt), max_chars=max_chars, max_duration=max_duration)
        smooth_short_captions(srt, max_chars, min_duration, max_duration)
        ass = folder / "captions.ass"
        make_ass(srt, ass, width, height, clip.get("hook", ""),
                 font=cap_font, style=cap_style, highlight_color=cap_color,
                 virtual=virtual, max_chars=max_chars, max_duration=max_duration, min_duration=min_duration)
        face_track_meta = None
        if clip["layout"] == "fit":
            graph = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x10131b,setsar=1[v]"
        elif clip["layout"] == "center_crop":
            graph = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={width}:{height},setsar=1[v]"
        elif clip["layout"] == "face_track":
            import face_track
            plan = face_track.analyze_video(intermediate, width, height)
            face_track_meta = {k: v for k, v in plan.items() if k != "filter"}
            graph = f"[0:v]{plan['filter']}[v]"
        else:
            x,y,w,h = clip["webcam_box"]
            top = (height//3)//2*2
            bottom = height-top
            graph = (f"[0:v]split=2[cam][game];[cam]crop={w}:{h}:{x}:{y},"
                     f"scale={width}:{top}:force_original_aspect_ratio=increase:force_divisible_by=2,"
                     f"crop={width}:{top},setsar=1[c];[game]scale={width}:{bottom}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                     f"pad={width}:{bottom}:(ow-iw)/2:(oh-ih)/2:color=0x10131b,setsar=1[g];[c][g]vstack=inputs=2[v]")
        _, _, needs_fontsdir = resolve_caption_font(cap_font)
        ass_filter = (
            f"ass=filename=captions.ass:fontsdir='{ffmpeg_filter_path(FONTS_DIR)}'"
            if needs_fontsdir else "ass=filename=captions.ass"
        )
        graph += f";[v]{ass_filter},format=yuv420p[out]"
        temp_output = folder / "rendering.mp4"
        run(["ffmpeg", "-hide_banner", "-y", "-i", "cut.mp4", "-filter_complex", graph,
             "-map", "[out]", "-map", "0:a?", *video_encode_args(QUALITY), "-r", "30",
             "-c:a", "copy", *METADATA_SCRUB, "-movflags", "+faststart", temp_output.name],
            cwd=folder, log=log)
        check = probe(temp_output)
        if check["width"] != width or check["height"] != height or abs(check["duration"]-duration) > 0.6:
            raise RuntimeError(f"Render validation failed: {clip['id']}")
        if metadata["media"]["has_audio"] and not check["has_audio"]:
            raise RuntimeError("Audio track lost during rendering")
        temp_output.replace(output)
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", 0 if preview else min(1,duration/2),
             "-i", output, "-frames:v", "1", "-vf", "scale=360:-2", folder / "poster.jpg"])
        (folder / "publish.txt").write_text(clip.get("title", "") + "\n\n" + clip.get("description", ""), encoding="utf-8")
        receipt = {"clip_id": clip["id"], "path": str(output), "duration": check["duration"],
                   "width": width, "height": height, "has_audio": check["has_audio"],
                   "title": clip.get("title", ""), "cache_key": cache_key,
                   "openshorts_commit": PIN, "source_segments": clip["segments"],
                   "video_encoder": video_encode_args(QUALITY)[1],
                   "caption_style": cap_style, "caption_font": cap_font}
        if face_track_meta is not None:
            receipt["face_track"] = face_track_meta
        dump(receipt_path, receipt)
        exports.append(receipt)
        print(f"Rendered: {output}", flush=True)
    dump(result_dir / "exports.json", exports)
    cards = []
    for item in exports:
        cid = item["clip_id"]
        cards.append(f'<article><h2 dir="auto">{html.escape(item["title"] or cid)}</h2>'
                     f'<video controls preload="metadata" poster="exports/{cid}/poster.jpg" src="exports/{cid}/final.mp4"></video>'
                     f'<p>{item["duration"]:.1f}s · {item["width"]}×{item["height"]}</p></article>')
    page = '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>مراجعة المقاطع</title><style>body{background:#10131b;color:#eee;font:18px Arial;margin:30px}main{display:flex;gap:28px;flex-wrap:wrap}article{max-width:360px}video{width:100%;max-height:640px}h1{font-size:26px}</style><h1>المقاطع الجاهزة للمراجعة</h1><main>' + ''.join(cards) + '</main></html>'
    (result_dir / "review.html").write_text(page, encoding="utf-8")
    if not preview:
        metadata["status"] = "rendered"
        dump(job / "job.json", metadata)
    print(result_dir / "review.html")


def evidence(args):
    from clip_evidence import save_note, search, visual_note

    job = job_path(args.job)
    source, metadata = source_for(job)
    duration = metadata["media"]["duration"]
    if args.note is not None:
        if args.frame is None or args.start is None or args.end is None:
            raise ValueError("--note requires --start, --end and --frame")
        entry = visual_note(job, duration, args.start, args.end, args.frame, args.note)
        print(save_note(job, source, entry))
    else:
        if not 1 <= args.limit <= 20:
            raise ValueError("--limit must be between 1 and 20")
        matches = search(job, source, duration, args.query, args.limit)
        print(json.dumps({"job": job.name, "matches": matches}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("doctor"); p.set_defaults(func=doctor)
    p = sub.add_parser("ingest")
    p.add_argument("source"); p.add_argument("--name")
    p.add_argument("--max-gb", type=float, default=12); p.add_argument("--max-hours", type=float, default=6)
    p.set_defaults(func=ingest)
    p = sub.add_parser("transcribe")
    p.add_argument("job"); p.add_argument("--language", default="auto")
    p.add_argument("--device", choices=["auto","cpu","cuda"], default="auto")
    p.add_argument("--compute-type", choices=["auto","int8","float16","int8_float16","float32"], default="auto")
    p.add_argument("--model"); p.add_argument("--force", action="store_true")
    p.add_argument("--prompt", help="Optional Whisper initial_prompt for domain/dialect terms")
    p.add_argument("--no-prompt", action="store_true", help="Disable default Arabic Whisper prompt")
    p.add_argument("--profile", help="Load Whisper prompt from profiles.json")
    p.add_argument("--quiet-transcript", action="store_true", help="Save transcript without echoing all segments")
    p.set_defaults(func=transcribe)
    p = sub.add_parser("frames")
    p.add_argument("job"); p.add_argument("--start", type=float, default=0)
    p.add_argument("--end", type=float); p.add_argument("--count", type=int, default=12); p.set_defaults(func=frames)
    p = sub.add_parser("render")
    p.add_argument("job"); p.add_argument("--manifest"); p.add_argument("--width", type=int, default=1080)
    p.add_argument("--force", action="store_true"); p.set_defaults(func=render)
    preview = sub.add_parser("preview")
    preview.add_argument("job")
    preview.add_argument("--manifest")
    preview.set_defaults(func=render, width=360, force=False, preview=True)
    index = sub.add_parser("evidence")
    index.add_argument("job")
    mode = index.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--note")
    index.add_argument("--start", type=float)
    index.add_argument("--end", type=float)
    index.add_argument("--frame")
    index.add_argument("--limit", type=int, default=8)
    index.set_defaults(func=evidence)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
