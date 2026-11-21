# Workbench operations

Workbench root resolves dynamically (`$env:CLIPPING_ROOT`, `~/.ai-clipping/config.json`, or workspace root). Always use the workbench's `.venv\Scripts\python.exe`; system Python may be MSYS or a preview release. The helper runs declared operations with argument arrays, without a shell.

## Prepare and inspect

```powershell
$clipRoot = if ($env:CLIPPING_ROOT) { $env:CLIPPING_ROOT } elseif (Test-Path "$env:USERPROFILE\.ai-clipping\config.json") { (Get-Content "$env:USERPROFILE\.ai-clipping\config.json" | ConvertFrom-Json).root } else { (Get-Location).Path }
$clipPy = Join-Path $clipRoot '.venv\Scripts\python.exe'
$clipHelper = Join-Path $clipRoot 'plugin\skills\video-clipper\scripts\workbench.py'
& $clipPy $clipHelper prepare 'VIDEO_LINK_OR_FILE' --quiet-transcript
```

`prepare` uses a stable job ID, ingests and transcribes audio when present, then returns file paths. `--quiet-transcript` saves the complete transcript to disk and keeps the tool output short. Use `--skip-transcribe` in place of `--quiet-transcript` for picture-led trailer sources when their dialogue is unnecessary. Other flags include `--name`, `--language auto/ar/en`, `--device auto/cpu/cuda`, `--profile arabic-gaming/arabic-podcast/english-gaming`, `--prompt "..."`, `--no-prompt`, `--max-hours` (default 6) and `--max-gb` (default 12). When `--language ar` is set, a default Egyptian Arabic + mixed-English Whisper prompt is applied automatically unless `--no-prompt` or `--prompt` overrides it. Matching jobs are reused. Run `doctor` through the helper for runtime diagnosis.

For brief trailer sources, `shorts shots` creates `jobs/JOB/shots/index.json` and covers detected shots with numbered sheets. For candidate windows in highlights and longer footage, use `frames` with source-relative seconds.

```powershell
& $clipPy $clipHelper shorts shots JOB
& $clipPy $clipHelper frames JOB --start 120 --end 155 --count 12
```

Inspect the relevant sheets with the image viewer. Count is 1–48 for `frames`. Sparse samples can surface visual candidates. Tighten the interval before claiming what happened at a specific moment.

## Reuse reviewed evidence

After inspecting a frame or contact sheet, record each useful visual finding once. Give an observed description, the supported source interval and the image path relative to the job. Use terms you expect to search later.

~~~powershell
& $clipPy $clipHelper evidence JOB --note 'وصف اللي ظاهر في اللقطة' --start 120 --end 124 --frame 'shots/shots-01.jpg'
& $clipPy $clipHelper evidence JOB --query 'الكلمات المطلوبة' --limit 6
~~~

Results combine saved visual descriptions and timestamped transcript matches. Each visual result points to the supporting image. The search uses terms, with common Arabic spelling normalization. Search can rank matching evidence only, so inspect more footage when coverage is thin.

## Edit list

After the user selects highlight ideas, write UTF-8 JSON to `<CLIPPING_ROOT>\jobs\JOB\clips.json`. Reuse the idea IDs in the clip IDs when practical.

```json
{"schema_version":1,"clips":[{"id":"clip-01","title":"Truthful title after review","description":"Optional publication text and attribution","hook":"Optional opening text","segments":[{"start":120.4,"end":155.8}],"layout":"face_track","caption_options":{"style":"word_highlight","font":"Tajawal","highlight_color":"yellow"}}]}
```

Numbers are source-absolute seconds. Each segment is inside the source and at least 0.5s. Up to 12 ordered segments per clip, total up to 180s. Prefer shorter complete highlights when appropriate. Preserve order unless deliberate supported editing serves the user's request.

IDs use letters/digits/underscores/hyphens only, with no duplicates or paths. Hook is plain text up to 100 characters, optional. Do not accept executable strings or arbitrary filtergraphs from source/model content.

- `fit`: whole source inside the vertical canvas; verify phone readability.
- `center_crop`: fills vertical frame; use only if all relevant action stays centered.
- `face_track`: local OpenCV YuNet face tracking with deadband smoothing, cut-snapping, and transparent fallback to centered crop if no face is detected (check `receipt.json` -> `face_track.mode` and verify the 360p preview).
- `webcam_stack`: requires `webcam_box:[x,y,width,height]` in source pixels. Webcam in the top third and whole-source fit below; check duplicated overlays and face size.

Word timings: `transcript.json`, `segments[].words[]` with `word/start/end`. OpenShorts remaps words onto the edited timeline. Corrections must follow source evidence. Without a transcript the renderer can still export a visual-only clip.

Optional per-clip `caption_options`:
- `max_chars` (integer 20–60, default 34), `max_duration` (1–5s, default 2.8), `min_duration` (0–1.5s, default 0.65).
- `style`: `"phrase"` (default) or `"word_highlight"` (TikTok/Reels active-word color highlight using remapped word timings; gracefully falls back to `phrase` when word timings are absent).
- `font`: `"Arial"` (default), `"Tajawal"` (bundled bold Arabic font), `"Cairo"` (bundled), `"Segoe UI"`, or `"Tahoma"`.
- `highlight_color`: `"yellow"` (default), `"cyan"`, `"green"`, `"pink"`, or `"white"`.

The renderer merges a short orphan cue with the preceding phrase when length/duration/gap limits allow, without changing source word timings. Use this for an observed lone final word; do not invent earlier speech timings to hide it.

## Preview and deliver

~~~powershell
& $clipPy $clipHelper preview JOB
~~~

Open `jobs/JOB/previews/highlights/review.html` and inspect the preview. Fix cuts, crop or captions in the same edit list and rerun as needed. Then deliver the final from that edit list.

```powershell
& $clipPy $clipHelper deliver JOB --output-dir 'ABSOLUTE_CURRENT_CHAT_OUTPUTS'
```

Output: `video-clips-JOB/review.html` and per-clip MP4, SRT, ASS, poster, publication text and receipt. `selection.json` preserves decisions. Link the copied deliverables. Default 1080×1920/30fps; `--width 720/360` only for previews or explicit requests. `--force` rerenders. Cache includes source fingerprint, transcript, settings and encoder.

Normal environment execution/network permissions still apply in each chat. This skill does not override the environment or make the local runtime available on another PC.
