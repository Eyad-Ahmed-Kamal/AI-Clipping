# Video Clipper

A Windows workbench for making vertical Shorts with Antigravity or Codex. Give the assistant a local video or an accessible video URL. It transcribes speech locally, inspects frames and pitches timestamped ideas. Choose the ideas you want, then review the MP4s, captions and edit decisions. Narrated edits use a recording you supply.

## First install

Use 64-bit Windows, PowerShell, Git, uv, and a full FFmpeg build with ffprobe, libass and libx264 on PATH. For YouTube sources, also install Deno or Node. Open the assistant application you intend to use at least once. Allow several GB on the checkout drive for Python, libraries, the 1.62 GB Whisper download and working video. The setup script uses the current user's profile for plugin registration.

If prerequisites are missing, Windows Package Manager can install them. Reopen PowerShell afterwards.

~~~powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
winget install --id DenoLand.Deno -e
~~~

Copy the repository's HTTPS clone URL from GitHub and substitute it below.

~~~powershell
git clone <REPOSITORY-URL> AI-Clipping
cd AI-Clipping
.\setup.ps1
~~~

The default target is Antigravity. For Codex use `.\setup.ps1 -Target Codex`, for both use `-Target Both`, or use `-Target None` for the CLI only. For local NVIDIA speech inference add `-Gpu` on a machine with a compatible driver. The default installation supports CPU transcription. The video encoder separately probes for NVENC and otherwise uses libx264.

Setup installs Python 3.12 with uv, creates a local virtual environment, fetches four OpenShorts modules and their license at a pinned Git commit, then verifies Whisper's weights, tokenizer, configuration and the YuNet face detector by SHA-256. It checks installed assets again on update. If setup stops, fix the reported error and rerun the same command.

Reload the selected application and choose Video Clipper. Try “Review this video and give me three timestamped ideas to choose from.” Confirm the source times, choose an idea and inspect its preview before approving the final edit.

## Update an existing install

From the same checkout, run

~~~powershell
git pull --ff-only
.\setup.ps1 -Update
~~~

Reload the application. Setup remembers the target and GPU option from the previous successful setup in this checkout. Jobs, source media and exports live outside Git. A changed plugin is staged before replacement. The previous plugin files are retained in `~/.ai-clipping/backups/`. Codex registration preserves unrelated configuration. If you maintain multiple checkouts, the last plugin installation selects the workbench in `~/.ai-clipping/config.json`.

## Verify and troubleshoot

~~~powershell
.\clip.ps1 doctor
. .\Activate-Clipping.ps1
.\.venv\Scripts\python.exe -m unittest -v test_clipping
~~~

The suite generates its own test media. To check actual speech inference on your hardware, run the following with a short video containing speech and read the resulting timestamped transcript. Use a fresh job name for a different source. The model supports Egyptian Arabic prompts and mixed-language captions. Check proper names and timings against the recording.

~~~powershell
.\clip.ps1 ingest 'C:\Videos\example.mp4' --name first-video
.\clip.ps1 transcribe first-video --language ar
Get-Content -Encoding UTF8 .\jobs\first-video\transcript.txt
~~~

A failed URL download can be retried with an accessible local file and a new job name. Use `doctor.json` from this computer to diagnose missing tools or libraries. Subtitle fonts include Tajawal and Cairo. Face tracking follows a visually prominent face, so check framing manually when speakers alternate.

## Project layout

- `plugin/` contains the canonical manifests, skill, helper and [editing reference](plugin/skills/video-clipper/references/workbench.md). Run `.\sync-plugins.ps1` to copy changes to both local hosts during development.
- `clipping.py`, `shorts.py`, `face_track.py` and `clip_evidence.py` implement the local pipeline. Publication requires the user's explicit instruction.
- `jobs/NAME/review.html` opens the highlight review page. Each export includes a video, captions, poster, suggested publication text and receipt.
- `models/`, `vendor/`, `tools/python/`, `.venv/`, `cache/`, `temp/` and `jobs/` are local installation or production data. Commit the installer sources in `tools/` when updating the release.

## Third-party components

OpenShorts ships its MIT license in the pinned checkout. The installer obtains Whisper from dropbox-dash/faster-whisper-large-v3-turbo and YuNet from opencv/opencv_zoo. See the pinned revisions and hashes in [the asset installer](tools/install_assets.py) and [provenance record](provenance.json). Font notices accompany [Tajawal](assets/fonts/OFL.txt) and [Cairo](assets/fonts/Cairo-OFL.txt).
