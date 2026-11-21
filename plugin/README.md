# Video Clipper Plugin

Unified Antigravity & Codex plugin for the local AI-Clipping vertical Shorts workbench.

## Features (v1.1.0)
- **Local Auto Face-Tracking (`face_track`)**: OpenCV YuNet face tracking with smoothing, deadband, cut-snap, and transparent center fallback.
- **Arabic Word-Highlight Captions & Bundled Fonts**: `style: "word_highlight"` with safe color presets (`yellow`, `cyan`, `green`, `pink`, `white`) and bundled `Tajawal` / `Cairo` fonts alongside Windows fonts.
- **Egyptian Arabic / Mixed-English ASR Tuning**: Automatic Arabic `initial_prompt` (overridable via `--prompt`, `--no-prompt`, or `--profile`) included in the ASR cache key.
- **Dynamic Path Resolution**: No hardcoded username paths; resolves `CLIPPING_ROOT` -> `~/.ai-clipping/config.json` -> current checkout.

## Syncing Updates
Run `.\sync-plugins.ps1` from the AI-Clipping root to sync this canonical plugin into both Antigravity and Codex.
