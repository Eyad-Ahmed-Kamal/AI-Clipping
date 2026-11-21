# Narrated Shorts

Use this workflow for commentary and trailer-led formats. Use [workbench.md](workbench.md) for highlights that preserve source dialogue.

## Develop the selected idea

Start here after the user selects an idea from the source-backed shortlist. Reopen the selected source windows from the saved shortlist. Use the existing shot catalog and inspect tighter windows to confirm each clip.

- Give each beat one job such as setting up, revealing, or closing. Reuse the look of an approved video in the same format when supplied.
- Aim for 15–25 seconds with five or six beats of roughly three to four seconds. Start with the most readable visual and a hook of up to five words. Write one natural spoken reaction or story sentence per beat, around ten words, and a specific closing question when it suits the format.
- Quiz, POV timeline, short story, mock news and rankings can use answer, time, news and score overlays. Choose the exact spoken cue for each payoff. In Egyptian Arabic, write numbers as spoken and review mixed Arabic and English captions.
- Use confirmed official trailer sources for Rockstar trailer edits. Treat plot speculation as speculation. Credit each source in the publication text. Muting source audio addresses the audio mix. Use footage appropriate to the user's intended publication.
- A production sheet states the hook, shot and source for every beat, spoken line, visual reveal and cue word, voice direction, proposed title/description and up to eight tags. Work from actual shot timestamps as soon as footage is available.

## Produce

In PowerShell, resolve the local workbench root dynamically. Supply concrete paths and job IDs in place of the examples.

~~~powershell
$clipRoot = if ($env:CLIPPING_ROOT) { $env:CLIPPING_ROOT } elseif (Test-Path "$env:USERPROFILE\.ai-clipping\config.json") { (Get-Content "$env:USERPROFILE\.ai-clipping\config.json" | ConvertFrom-Json).root } else { (Get-Location).Path }
$clipPy = Join-Path $clipRoot '.venv\Scripts\python.exe'
$clipHelper = Join-Path $clipRoot 'plugin\skills\video-clipper\scripts\workbench.py'
& $clipPy $clipHelper shorts voice (Join-Path $clipRoot 'jobs\production-01\voice.wav') --language ar --profile arabic-gaming --output-dir (Join-Path $clipRoot 'jobs\production-01\voice-analysis')
& $clipPy $clipHelper shorts preview (Join-Path $clipRoot 'jobs\production-01\decisions.json') --output-dir (Join-Path $clipRoot 'jobs\production-01\previews\idea-1-v1')
& $clipPy $clipHelper shorts render (Join-Path $clipRoot 'jobs\production-01\decisions.json') --output-dir 'ABSOLUTE_NEW_CURRENT_CHAT_OUTPUT_FOLDER'
~~~

1. Start from the selected idea's shot map. Check windows at tighter frame intervals for flashes, embedded logos/text, black or a missed cut. Pick a crop per beat or use fit when filling the canvas loses the event.
2. Use one continuous voice take for consistent tone. Use an available authorized voice source, a user recording or a supplied TTS file. Mako with a short, format-specific style direction is the guide's example. When a voice take is unavailable, finish the script, shot map and voice direction so production can resume with the recording. Keep existing media local unless the user authorizes an external workflow that requires it.
3. Use the narration transcript as timing evidence. Review the supplied take against its script and correct ASR substitutions or bad boundaries. Assign the reviewed words to beats by the script lines. Keep the audio continuous. Set each edit boundary on the narration timeline in a pause or before the next line, with natural breathing room when available. A window shorter than its beat is interpolated down to 0.3x. Choose a longer shot when the result needs more stretch or shows interpolation artifacts.
4. Render the internal preview to a fresh project preview folder. Check frame zero, cuts, mobile crop, text width and direction, and each reveal. Fix the edit decisions, render the final, and check its contact sheets and playback. Read receipt warnings and investigate black/freeze candidates. Link the reviewed MP4 and its review page.

## Decisions file

One video per UTF-8 JSON. Source windows use seconds relative to the ingested video. The first beat starts at zero. Each until is the beat end on the final narration timeline and is rounded to the nearest video frame. Word times are seconds on that same timeline. The end of the final beat must cover the voice take.

~~~json
{
  "schema_version": 1,
  "voice": "voice.wav",
  "hook": "تفتكر دي أنهي GTA؟",
  "title": "تعرف دي GTA كام؟ #shorts",
  "description": "اللقطات من تريلر Rockstar الرسمي. التعليق والمونتاج أصليين.",
  "tags": ["GTA 6", "GTA 5", "Rockstar", "Shorts"],
  "caption_font": "Tajawal",
  "punch": true,
  "whoosh": true,
  "beats": [
    {
      "source_job": "trailer-1",
      "source_start": 12.4,
      "source_end": 14.0,
      "until": 3.2,
      "crop": [754, 180, 360, 640],
      "words": [
        {"text": "تفتكر", "start": 0.05, "end": 0.30},
        {"text": "دي", "start": 0.32, "end": 0.49},
        {"text": "ستة؟", "start": 0.50, "end": 0.93}
      ],
      "overlay": {"style": "answer", "text": "GTA 6", "word": 3}
    }
  ]
}
~~~

The example demonstrates the data shape. Use source evidence and the actual audio to fill it. Optional `caption_font` accepts `"Arial"` (default), `"Tajawal"`, `"Cairo"`, `"Segoe UI"`, or `"Tahoma"`. The optional crop is even source-pixel coordinates `[x, y, width, height]` with a 9:16 aspect ratio. Omit it for fit. Overlay word is the 1-based index in that beat. Supported styles are `answer` (green), `answer_wrong` (red), `score`, `time` and `news`. Set `punch` only when safe for the crop. The optional `whoosh` adds a quiet synthesized sound at transitions. Source sound is excluded. The normal render is 1080×1920 at 30 fps. The narration mix targets −14 LUFS and the receipt measures actual output loudness. Preview widths are 360 and 720.
