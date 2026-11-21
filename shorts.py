"""Catalog shots, transcribe a narration take and render an authored vertical Short."""
from __future__ import annotations

import argparse
import html
import math
from pathlib import Path
import random
import re
import struct
import subprocess
import tempfile
import wave

from clipping import (CAPTION_FONTS, FONTS_DIR, ROOT, SAFE_ID, ass_sized,
                      ass_time, caption_groups, dump, ffmpeg_filter_path,
                      job_path, numeric, probe, read, resolve_caption_font, run,
                      source_for, space, transcribe_media)

FPS = 30
FRAME = 1 / FPS
SCENE_THRESHOLD = 0.34
FFMPEG = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
OVERLAYS = ("answer", "answer_wrong", "score", "time", "news")


def detect_cuts(source, start=0, duration=None):
    command = [*FFMPEG, "-ss", start]
    if duration is not None:
        command += ["-t", duration]
    result = run([*command, "-i", source, "-an", "-vf",
                  f"scale=480:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print:file=-",
                  "-f", "null", "NUL"], timeout=1800)
    return [start + float(value) for value in re.findall(r"pts_time:([\d.]+)", result)]


def contact_sheets(source, times, destination, prefix):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    for offset in range(0, len(times), 24):
        page = times[offset:offset + 24]
        sheet = Image.new("RGB", (960, 200 * math.ceil(len(page) / 3)), "#151923")
        draw = ImageDraw.Draw(sheet)
        with tempfile.TemporaryDirectory(dir=destination) as directory:
            frame = Path(directory) / "frame.jpg"
            for index, (label, stamp) in enumerate(page):
                run([*FFMPEG, "-ss", stamp, "-i", source, "-frames:v", 1,
                     "-vf", "scale=320:174:force_original_aspect_ratio=decrease", "-update", 1, frame])
                x, y = 320 * (index % 3), 200 * (index // 3)
                with Image.open(frame) as picture:
                    sheet.paste(picture, (x + (320 - picture.width) // 2, y))
                draw.text((x + 8, y + 178), f"{label}  |  {stamp:.3f}s", font=font, fill="white")
        sheet.save(destination / f"{prefix}-{offset // 24 + 1:02d}.jpg", quality=90)


def catalog(args):
    job = job_path(args.job)
    source, metadata = source_for(job)
    duration = metadata["media"]["duration"]
    bounds = [0, *(cut for cut in detect_cuts(source) if 0 < cut < duration), duration]
    shots = [{"id": i, "start": round(a, 4), "end": round(b, 4)}
             for i, (a, b) in enumerate(zip(bounds, bounds[1:]), 1)]
    out = job / "shots"
    out.mkdir(exist_ok=True)
    contact_sheets(source, [(f"Shot {s['id']}", (s["start"] + s["end"]) / 2) for s in shots],
                   out, "shots")
    dump(out / "index.json", {"source": str(source), "scene_threshold": SCENE_THRESHOLD,
                              "shots": shots, "sheets": [f"shots-{i + 1:02d}.jpg"
                                                          for i in range(math.ceil(len(shots) / 24))]})
    print(out / "index.json")


def analyze_voice(args):
    source = Path(args.audio).resolve(strict=True)
    media = probe(source)
    if not media["has_audio"] or not 0 < media["duration"] < 60:
        raise ValueError("Use one narration take with an audio track and duration under 60 seconds")
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    transcribe_media(args, out, source, {"media": media})
    print(out / "transcript.json")


def require_text(value, label, limit, *, optional=False):
    if (not isinstance(value, str) or len(value) > limit or not value.strip() and not optional
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{label}: expected single-line text up to {limit} characters")
    return value.strip()


def layout_filters(box, media, width, height):
    if box is None:
        scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        pad = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x10131b"
        return scale, pad
    if not isinstance(box, list) or len(box) != 4 or any(type(part) is not int or part % 2 for part in box):
        raise ValueError("crop must contain four even source pixel coordinates [x,y,width,height]")
    x, y, w, h = box
    if (min(x, y) < 0 or min(w, h) < 2 or x + w > media["width"] or y + h > media["height"]
            or abs(w / h - width / height) > 0.01):
        raise ValueError("crop must be in bounds and have a 9:16 aspect ratio")
    return f"crop={w}:{h}:{x}:{y}", f"scale={width}:{height}"


def prepare_timeline(manifest, voice_duration, width, height):
    beats = manifest.get("beats")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Expected a Shorts decisions file with schema_version 1")
    if not isinstance(beats, list) or not 1 <= len(beats) <= 12:
        raise ValueError("Choose between 1 and 12 beats")
    timeline = []
    sources = {}
    start_frame = 0
    previous_word_end = 0
    for beat in beats:
        if not isinstance(beat, dict):
            raise ValueError("Each beat must be an object")
        source_id = beat.get("source_job")
        if not isinstance(source_id, str) or not SAFE_ID.fullmatch(source_id):
            raise ValueError("source_job must name an ingested video")
        if source_id not in sources:
            sources[source_id] = source_for(job_path(source_id))
        source, metadata = sources[source_id]
        media = metadata["media"]
        start, stop = numeric(beat.get("source_start")), numeric(beat.get("source_end"))
        until = numeric(beat.get("until"))
        if not 0 < until < 60:
            raise ValueError("Each beat end must be between zero and 60 seconds")
        end_frame = round(until * FPS)
        duration = (end_frame - start_frame) / FPS
        if not 0.3 <= duration or not end_frame < 60 * FPS:
            raise ValueError("Beats must increase by at least 0.3 s and end before 60 s")
        if not 0 <= start < stop <= media["duration"] or stop - start < 0.2:
            raise ValueError("Selected source window must be at least 0.2 s, within the source")
        source_duration = min(stop - start, duration)
        speed = source_duration / duration
        if speed < 0.3 - 1e-6:
            raise ValueError("Selected shot requires speed under 0.3x; choose a longer clean shot")
        words = beat.get("words")
        if not isinstance(words, list) or not words:
            raise ValueError("Each narrated beat needs reviewed word timestamps")
        for word in words:
            if not isinstance(word, dict):
                raise ValueError("Each caption word needs text, start and end")
            require_text(word.get("text"), "Caption word", 50)
            a, b = numeric(word.get("start")), numeric(word.get("end"))
            if not max(start_frame / FPS, previous_word_end) <= a < b <= min(end_frame / FPS, voice_duration) + 1e-6:
                raise ValueError("Caption words must be ordered, within their beat and on the narration timeline")
            previous_word_end = b
        overlay = beat.get("overlay")
        if overlay is not None:
            if not isinstance(overlay, dict):
                raise ValueError("overlay needs text and a 1-based word number")
            require_text(overlay.get("text"), "Overlay text", 40)
            if type(overlay.get("word")) is not int or not 1 <= overlay["word"] <= len(words):
                raise ValueError("Overlay word number is outside the beat")
            if overlay.get("style", "score") not in OVERLAYS:
                raise ValueError("Choose an overlay style: " + ", ".join(OVERLAYS))
            if math.ceil(words[overlay["word"] - 1]["start"] * FPS - 1e-8) >= end_frame:
                raise ValueError("Overlay cue has no display frame before the next beat")
        crop, resize = layout_filters(beat.get("crop"), media, width, height)
        timeline.append({"source": source, "source_job": source_id, "start": start,
                         "source_duration": source_duration, "speed": speed,
                         "first_frame": start_frame, "end_frame": end_frame,
                         "crop": crop, "resize": resize,
                         "words": words, "overlay": overlay})
        start_frame = end_frame
    if start_frame / FPS + FRAME / 2 < voice_duration:
        raise ValueError("Extend the final beat to cover the full narration take")
    return timeline


def captions(timeline, hook, destination, width, height, *, font="Arial"):
    scale = width / 1080
    ass_font_name, _, _ = resolve_caption_font(font)
    styles = {
        "Caption": ("00FFFFFF", "00101010", 57, 2, 320, 1),
        "Hook": ("0000E7FF", "00101010", 72, 8, 175, 1),
        "answer": ("00FFFFFF", "00336D15", 70, 5, 0, 3),
        "answer_wrong": ("00FFFFFF", "001F1AC1", 70, 5, 0, 3),
        "score": ("0000E7FF", "00211909", 76, 5, 0, 3),
        "time": ("00FFFFFF", "00633325", 66, 8, 385, 3),
        "news": ("00FFFFFF", "001F1AC1", 58, 2, 480, 3),
    }
    header = [f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
              "WrapStyle: 0\nScaledBorderAndShadow: yes\n[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
              "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
              "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for name, (color, border, size, align, margin, box) in styles.items():
        header.append(f"Style: {name},{ass_font_name},{round(size*scale)},&H{color},&H{color},&H{border},&H{border},"
                      f"-1,0,0,0,100,100,0,0,{box},{max(1,round(6*scale))},0,{align},"
                      f"{round(76*scale)},{round(110*scale)},{round(margin*scale)},1")
    events, plain = [], []

    def event(layer, start, end, style, text):
        if ass_time(start) != ass_time(end):
            events.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    def srt_time(seconds):
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    event(2, 0, min(2, timeline[0]["end_frame"] / FPS), "Hook", ass_sized(hook, 72, 850, scale, font))
    for beat in timeline:
        words = beat["words"]
        end = beat["end_frame"] / FPS
        groups = math.ceil(len(words) / 4)
        phrases = []
        for index in range(groups):
            chunk = words[index * len(words) // groups:(index + 1) * len(words) // groups]
            phrases.extend(chunk[a:b] for a, b in caption_groups(
                [word["text"].strip() for word in chunk], 57, 850, font))
        for group in phrases:
            texts = [ass_sized(word["text"].strip(), 57, 850, scale, font) for word in group]
            plain.append(f"{len(plain)+1}\n{srt_time(group[0]['start'])} --> "
                         f"{srt_time(min(end, group[-1]['end']))}\n"
                         + " ".join(word["text"].strip() for word in group))
            for position, word in enumerate(group):
                next_time = group[position + 1]["start"] if position + 1 < len(group) else word["end"]
                highlighted = texts.copy()
                highlighted[position] = r"{\1c&H00E7FF&}" + texts[position] + r"{\r}"
                event(0, word["start"], min(next_time, end), "Caption", " ".join(highlighted))
        if overlay := beat["overlay"]:
            onset = words[overlay["word"] - 1]["start"]
            # ceil picks the first output frame at or after the reviewed word timestamp.
            onset = math.ceil(onset * FPS - 1e-8) / FPS
            ass_onset = math.floor(onset * 100 + 1e-8) / 100
            style = overlay.get("style", "score")
            event(1, ass_onset, min(onset + 1.5, end), style,
                  r"{\fscx115\fscy115\t(0,165,\fscx100\fscy100)}"
                  + ass_sized(overlay["text"], styles[style][2], 730, scale, font))
    header.append("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    (destination / "captions.ass").write_text("\n".join(header + events) + "\n", encoding="utf-8-sig")
    (destination / "captions.srt").write_text("\n\n".join(plain) + "\n", encoding="utf-8")


def render_beat(beat, index, destination, width, height, punch):
    start, duration = beat["start"], beat["source_duration"]
    cuts = detect_cuts(beat["source"], start, duration)
    if any(start < cut < start + duration - 1e-4 for cut in cuts):
        raise ValueError(f"Beat {index} crosses a detected hard cut; inspect the shot catalog")
    count = beat["end_frame"] - beat["first_frame"]
    speed = beat["speed"]
    filters = [beat["crop"], "setsar=1", "setpts=PTS-STARTPTS"]
    if speed < 0.999:
        filters.extend(["tpad=stop_mode=clone:stop_duration=0.12", f"setpts=PTS/{speed:.9f}",
                        "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir"])
    else:
        filters.append("fps=30")
    filters.extend([beat["resize"], "setsar=1"])
    filters.append("tpad=stop_mode=clone:stop_duration=0.12")
    if punch:
        filters.append("zoompan=z='1+0.10*max(0,1-on/5)':x='iw/2-iw/zoom/2':"
                       f"y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps=30")
    filters.append("format=yuv420p")
    name = f"beat-{index:02d}.mp4"
    run([*FFMPEG, "-ss", start, "-t", duration, "-i", beat["source"], "-an",
         "-vf", ",".join(filters), "-frames:v", count, "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", 16, name], cwd=destination)
    actual = probe(destination / name)
    if abs(actual["duration"] - count / FPS) > FRAME:
        raise RuntimeError(f"Beat {index} could not provide the requested frame count")
    return name


def write_whooshes(timeline, destination):
    sample_rate = 24000
    duration = timeline[-1]["end_frame"] / FPS
    audio = bytearray(round(duration * sample_rate) * 2)
    length = round(0.18 * sample_rate)
    noise = random.Random(0)
    for beat in timeline[1:]:
        offset = round(beat["first_frame"] / FPS * sample_rate)
        low = 0
        for i in range(min(length, len(audio) // 2 - offset)):
            raw = noise.uniform(-1, 1)
            low = 0.87 * low + 0.13 * raw
            envelope = math.sin(math.pi * i / length) ** 2
            value = round((raw - low) * envelope * 420)
            struct.pack_into("<h", audio, 2 * (offset + i), value)
    with wave.open(str(destination), "wb") as stream:
        stream.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        stream.writeframes(audio)


def render(args):
    from ffmpeg_utils import METADATA_SCRUB, QUALITY, video_encode_args

    decisions = Path(args.decisions).resolve(strict=True)
    manifest = read(decisions)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("voice"), str):
        raise ValueError("decisions.json needs a path to one narration take")
    voice = (decisions.parent / manifest["voice"]).resolve(strict=True)
    media = probe(voice)
    if not media["has_audio"] or not 0 < media["duration"] < 60:
        raise ValueError("Provide a narration take with audio and duration under 60 seconds")
    hook = require_text(manifest.get("hook"), "Hook", 65)
    title = require_text(manifest.get("title"), "Title", 150)
    description = manifest.get("description", "")
    tags = manifest.get("tags", [])
    if not isinstance(description, str) or not isinstance(tags, list) or len(tags) > 8:
        raise ValueError("Use a text description and up to eight tags")
    tags = [require_text(tag, "Tag", 80) for tag in tags]
    punch = manifest.get("punch", False)
    whoosh = manifest.get("whoosh", False)
    if type(punch) is not bool or type(whoosh) is not bool:
        raise ValueError("punch and whoosh must be true or false")
    cap_font = manifest.get("caption_font", "Arial")
    if not isinstance(cap_font, str) or cap_font not in CAPTION_FONTS:
        raise ValueError("caption_font must be one of: " + ", ".join(CAPTION_FONTS))
    width, height = args.width, args.width * 16 // 9
    timeline = prepare_timeline(manifest, media["duration"], width, height)
    duration = timeline[-1]["end_frame"] / FPS
    out = Path(args.output_dir).resolve()
    if out.exists():
        raise ValueError("Choose a new delivery folder for this render")
    space(2)
    out.parent.mkdir(parents=True, exist_ok=True)
    with (tempfile.TemporaryDirectory(prefix="shorts-", dir=ROOT / "temp") as scratch,
          tempfile.TemporaryDirectory(prefix=".shorts-", dir=out.parent) as delivery):
        stage = Path(scratch)
        bundle = Path(delivery)
        captions(timeline, hook, bundle, width, height, font=cap_font)
        files = [render_beat(beat, index, stage, width, height, punch)
                 for index, beat in enumerate(timeline, 1)]
        (stage / "beats.txt").write_text("\n".join(f"file '{file}'" for file in files), encoding="ascii")
        qa = run([*FFMPEG, "-xerror", "-f", "concat", "-safe", 1, "-i", stage / "beats.txt", "-vf",
                  "blackdetect=d=0.03:pix_th=0.10,freezedetect=n=-55dB:d=0.4,metadata=print:file=-",
                  "-f", "null", "NUL"])
        warnings = [line.strip() for line in qa.splitlines() if line.strip().startswith("lavfi.")]
        if any(line.startswith("lavfi.black_start=") and float(line.split("=")[1]) < FRAME
               for line in warnings):
            raise RuntimeError("Opening frame appears black; select a clean opening shot")
        output = bundle / "final.mp4"
        encoder = video_encode_args(QUALITY)
        audio_filter = "loudnorm=I=-14:TP=-1.5:LRA=7,aresample=48000,apad"
        audio = ["-map", "1:a:0", "-af", audio_filter]
        if whoosh and len(timeline) > 1:
            effect = stage / "whoosh.wav"
            write_whooshes(timeline, effect)
            audio = ["-i", effect, "-filter_complex",
                     "[1:a][2:a]amix=inputs=2:duration=first:normalize=0," + audio_filter + "[mix]",
                     "-map", "[mix]"]
        _, _, needs_fontsdir = resolve_caption_font(cap_font)
        ass_vf = (
            f"ass=filename=captions.ass:fontsdir='{ffmpeg_filter_path(FONTS_DIR)}'"
            if needs_fontsdir else "ass=filename=captions.ass"
        )
        run([*FFMPEG, "-f", "concat", "-safe", 1, "-i", stage / "beats.txt", "-i", voice, *audio,
             "-map", "0:v:0", "-vf", ass_vf,
             "-frames:v", timeline[-1]["end_frame"], "-t", duration, *encoder, "-r", FPS,
             "-c:a", "aac", "-b:a", "192k", *METADATA_SCRUB, "-movflags", "+faststart",
             output.name], cwd=bundle, log=stage / "render.log")
        check = probe(output)
        video = next((stream for stream in check["raw"]["streams"] if stream["codec_type"] == "video"), {})
        if (not check["has_audio"] or (check["width"], check["height"]) != (width, height)
                or video.get("r_frame_rate") != "30/1" or int(video.get("nb_frames", "0")) != timeline[-1]["end_frame"]
                or abs(check["duration"] - duration) > FRAME + 0.01):
            raise RuntimeError("Final media failed dimension, frame-count, audio or duration checks")
        measured = run([*FFMPEG, "-xerror", "-i", output,
                        "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.I:file=-", "-f", "null", "NUL"])
        levels = re.findall(r"lavfi\.r128\.I=(-?[\d.]+)", measured)
        if not levels or float(levels[-1]) <= -69:
            raise RuntimeError("Narration output is silent or too short to measure")
        integrated_lufs = float(levels[-1])
        if abs(integrated_lufs + 14) > 2:
            warnings.append(f"Integrated loudness is {integrated_lufs:.1f} LUFS; review against the -14 LUFS target")
        run([*FFMPEG, "-i", output, "-frames:v", 1, "-update", 1, bundle / "poster.jpg"])
        samples = [("First frame", 0.0)]
        for index, beat in enumerate(timeline, 1):
            samples.append((f"Beat {index}", beat["first_frame"] / FPS))
            if overlay := beat["overlay"]:
                onset = math.ceil(beat["words"][overlay["word"]-1]["start"] * FPS - 1e-8) / FPS
                if onset < beat["end_frame"] / FPS:
                    samples.append((f"Overlay {index}", onset))
        contact_sheets(output, samples, bundle, "review-frames")
        dump(bundle / "receipt.json", {
            "duration": check["duration"], "width": width, "height": height, "fps": FPS,
            "encoder": encoder[1], "audio": "narration with whooshes" if whoosh and len(timeline) > 1 else "narration only",
            "target_lufs": -14, "integrated_lufs": integrated_lufs,
            "qa_warnings": warnings, "beats": [{"source_job": beat["source_job"],
                "source_start": beat["start"], "source_end": beat["start"] + beat["source_duration"],
                "speed": beat["speed"], "first_frame": beat["first_frame"],
                "end_frame": beat["end_frame"]} for beat in timeline]})
        (bundle / "publish.txt").write_text(f"{title}\n\n{description}\n\n" + ", ".join(tags), encoding="utf-8")
        dump(bundle / "decisions.json", {**manifest, "voice": str(voice)})
        sheets = "".join(f'<img src="{sheet.name}" alt="فريمات من النسخة النهائية">'
                         for sheet in sorted(bundle.glob("review-frames-*.jpg")))
        (bundle / "review.html").write_text(
            '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body{background:#151923;color:white;font:18px Arial;margin:24px}a{color:#9bc9ff}'
            'video{width:min(100%,360px);max-height:75vh}img{width:min(100%,960px);display:block}</style>'
            f'<title>{html.escape(title)}</title><h1 dir="auto">{html.escape(title)}</h1>'
            '<video controls preload="metadata" poster="poster.jpg" src="final.mp4"></video>'
            '<p><a href="final.mp4" download>تحميل الفيديو</a> · '
            '<a href="publish.txt">بيانات النشر</a> · <a href="receipt.json">نتائج الفحص</a></p>'
            + sheets + '</html>', encoding="utf-8")
        bundle.rename(out)
    print(out / "review.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    shots = sub.add_parser("shots")
    shots.add_argument("job")
    shots.set_defaults(func=catalog)
    voice = sub.add_parser("voice")
    voice.add_argument("audio")
    voice.add_argument("--output-dir", required=True)
    voice.add_argument("--language", default="auto")
    voice.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    voice.add_argument("--prompt", help="Optional Whisper initial_prompt for domain/dialect terms")
    voice.add_argument("--no-prompt", action="store_true", help="Disable default Arabic Whisper prompt")
    voice.add_argument("--profile", help="Load Whisper prompt from profiles.json")
    voice.add_argument("--force", action="store_true")
    voice.set_defaults(func=analyze_voice, model=None)
    export = sub.add_parser("render")
    export.add_argument("decisions")
    export.add_argument("--output-dir", required=True)
    export.add_argument("--width", type=int, choices=[360, 720, 1080], default=1080)
    export.set_defaults(func=render)
    preview = sub.add_parser("preview")
    preview.add_argument("decisions")
    preview.add_argument("--output-dir", required=True)
    preview.set_defaults(func=render, width=360)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
