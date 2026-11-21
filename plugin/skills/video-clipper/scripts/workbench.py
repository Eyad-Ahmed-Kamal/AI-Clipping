"""Stable entry point for assistant-operated clipping; no cloud LLM calls."""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def resolve_root() -> Path:
    """Resolve the local AI-Clipping workbench root dynamically."""
    candidates: list[Path] = []
    for var in ("CLIPPING_ROOT", "AI_CLIPPING_ROOT"):
        val = os.environ.get(var)
        if val:
            candidates.append(Path(val).expanduser())

    cfg_path = Path.home() / ".ai-clipping" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            if isinstance(cfg, dict) and cfg.get("root"):
                candidates.append(Path(cfg["root"]).expanduser())
        except (OSError, json.JSONDecodeError):
            pass

    candidates.append(Path.cwd())
    here = Path(__file__).resolve()
    candidates.extend(here.parents)

    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if (resolved / "clipping.py").is_file():
            return resolved

    raise RuntimeError(
        "Clipping runtime not found. Set CLIPPING_ROOT (or run sync-plugins.ps1 to write "
        '~/.ai-clipping/config.json with {"root": "PATH_TO_AI_CLIPPING"}).'
    )


ROOT = resolve_root()
PYTHON = ROOT / ".venv/Scripts/python.exe"
CLI = ROOT / "clipping.py"


def environment():
    result = dict(os.environ)
    result.update({
        "CLIPPING_ROOT": str(ROOT),
        "HF_HOME": str(ROOT / "cache/huggingface"),
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "HF_HUB_DISABLE_XET": "1",
        "TEMP": str(ROOT / "temp"),
        "TMP": str(ROOT / "temp"),
        "UV_CACHE_DIR": str(ROOT / "cache/uv"),
        "PYTHONUTF8": "1",
        "FFMPEG_ENCODER": result.get("FFMPEG_ENCODER", "auto"),
    })
    return result


def call(*arguments, script=CLI):
    if not script.is_file() or not PYTHON.is_file():
        raise RuntimeError(f"Clipping runtime is incomplete at {ROOT}")
    completed = subprocess.run(
        [str(PYTHON), str(script), *[str(x) for x in arguments]],
        env=environment(),
        cwd=ROOT,
        shell=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Workbench {arguments[0]} failed (exit {completed.returncode}); inspect reported error"
        )


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_job(name):
    path = (ROOT / "jobs" / name).resolve()
    if not path.is_relative_to((ROOT / "jobs").resolve()) or not (path / "job.json").is_file():
        raise ValueError("Expected an existing job inside the clipping workspace")
    return path


def prepare(args):
    name = args.name or "video-" + hashlib.sha256(args.source.encode()).hexdigest()[:12]
    call("ingest", args.source, "--name", name, "--max-hours", args.max_hours, "--max-gb", args.max_gb)
    job = safe_job(name)
    metadata = read(job / "job.json")
    if metadata["media"]["has_audio"] and not args.skip_transcribe:
        transcribe = ["transcribe", name, "--language", args.language, "--device", args.device]
        if args.prompt is not None:
            transcribe.extend(["--prompt", args.prompt])
        if args.no_prompt:
            transcribe.append("--no-prompt")
        if args.profile:
            transcribe.extend(["--profile", args.profile])
        if args.quiet_transcript:
            transcribe.append("--quiet-transcript")
        call(*transcribe)
    result = {
        "job": name,
        "job_dir": str(job),
        "metadata": str(job / "job.json"),
        "media": metadata["media"],
        "transcript_json": str(job / "transcript.json") if (job / "transcript.json").exists() else None,
        "transcript_text": str(job / "transcript.txt") if (job / "transcript.txt").exists() else None,
        "next": (
            "Run shorts shots to catalog footage."
            if args.skip_transcribe
            else "Review the transcript when available and inspect candidate frames for the shortlist."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def deliver(args):
    job = safe_job(args.job)
    output = Path(args.output_dir).resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        raise ValueError("Use current chat's deliverables folder outside the clipping runtime")
    arguments = ["render", job.name, "--width", args.width]
    if args.force:
        arguments.append("--force")
    call(*arguments)
    exports = read(job / "exports.json")
    dest = output / ("video-clips-" + job.name)
    dest.mkdir(parents=True, exist_ok=True)
    cards, files = [], []
    for item in exports:
        source = Path(item["path"]).resolve()
        if not source.is_relative_to((job / "exports").resolve()) or source.name != "final.mp4":
            raise ValueError("Export receipt points outside this job")
        decode = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(source), "-f", "null", "-"],
            env=environment(),
            capture_output=True,
            text=True,
            timeout=1800,
            shell=False,
        )
        if decode.returncode:
            raise RuntimeError("Media decode failed: " + decode.stderr[-1500:])
        cid = item["clip_id"]
        if not cid or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in cid):
            raise ValueError("Invalid clip ID in receipt")
        target = dest / cid
        target.mkdir(exist_ok=True)
        for filename in ("final.mp4", "captions.srt", "captions.ass", "poster.jpg", "publish.txt", "receipt.json"):
            path = source.parent / filename
            if path.exists():
                shutil.copy2(path, target / filename)
        cards.append(
            f'<article><h2 dir="auto">{html.escape(item.get("title") or cid)}</h2>'
            f'<video controls preload="metadata" poster="{cid}/poster.jpg" src="{cid}/final.mp4"></video>'
            f'<p>{item["duration"]:.1f}s · {item["width"]}×{item["height"]}</p>'
            f'<a href="{cid}/final.mp4" download>تحميل الفيديو</a></article>'
        )
        files.append({"id": cid, "video": str(target / "final.mp4"), "captions": str(target / "captions.srt")})
    shutil.copy2(job / "clips.json", dest / "selection.json")
    page = (
        '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>مراجعة المقاطع</title><style>body{background:#10131b;color:#eee;font:18px Arial;margin:24px}'
        'main{display:flex;gap:28px;flex-wrap:wrap}article{max-width:360px}video{width:100%;max-height:640px}'
        'a{color:#a6ccff}h1{font-size:26px}</style><h1>المقاطع الجاهزة للمراجعة</h1><main>'
        + "".join(cards)
        + "</main></html>"
    )
    (dest / "review.html").write_text(page, encoding="utf-8")
    print(json.dumps({"review": str(dest / "review.html"), "files": files}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("doctor")
    p = subs.add_parser("prepare")
    p.add_argument("source")
    p.add_argument("--name")
    p.add_argument("--language", default="auto")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    p.add_argument("--prompt")
    p.add_argument("--no-prompt", action="store_true")
    p.add_argument("--profile")
    p.add_argument("--max-hours", type=float, default=6)
    p.add_argument("--max-gb", type=float, default=12)
    p.add_argument("--skip-transcribe", action="store_true")
    p.add_argument("--quiet-transcript", action="store_true")
    p = subs.add_parser("frames")
    p.add_argument("job")
    p.add_argument("--start", type=float, required=True)
    p.add_argument("--end", type=float, required=True)
    p.add_argument("--count", type=int, default=12)
    p = subs.add_parser("deliver")
    p.add_argument("job")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--width", type=int, choices=[360, 720, 1080], default=1080)
    p.add_argument("--force", action="store_true")
    p = subs.add_parser("shorts", help="Run a narrated Shorts operation")
    p.add_argument("operation", choices=["shots", "voice", "preview", "render"])
    p.add_argument("arguments", nargs=argparse.REMAINDER)
    p = subs.add_parser("evidence", help="Search speech and reviewed scenes or save a visual note")
    p.add_argument("job")
    p.add_argument("arguments", nargs=argparse.REMAINDER)
    p = subs.add_parser("preview", help="Render an internal 360p highlights preview")
    p.add_argument("job")
    p.add_argument("--manifest")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            call("doctor")
        elif args.command == "prepare":
            prepare(args)
        elif args.command == "frames":
            call("frames", safe_job(args.job).name, "--start", args.start, "--end", args.end, "--count", args.count)
        elif args.command == "shorts":
            call(args.operation, *args.arguments, script=ROOT / "shorts.py")
        elif args.command == "evidence":
            call("evidence", safe_job(args.job).name, *args.arguments)
        elif args.command == "preview":
            arguments = ["preview", safe_job(args.job).name]
            if args.manifest:
                arguments.extend(["--manifest", args.manifest])
            call(*arguments)
        else:
            deliver(args)
    except (ValueError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
