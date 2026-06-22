---
name: make-widescreen-highlight
description: Convert portrait or phone-shot talking videos into 16:9 widescreen videos with a zoomed blurred background, centered original video, English/Simplified Chinese subtitles, a selectable-subtitle full MP4, and a short bilingual burned-caption highlight. Use when Codex is asked to reformat vertical MP4/MOV videos for widescreen, generate bilingual subtitles, pick the most interesting 10-second segment from subtitles/transcript, or create social-ready highlight clips.
---

# Make Widescreen Highlight

## Workflow

Use `scripts/make_widescreen_highlight.py` as the reusable pipeline. It performs the same end-to-end process:

1. Render a 1920x1080 copy with a blurred, zoomed version of the video as background and the original video centered on top.
2. Transcribe speech and create English, Simplified Chinese, and bilingual SRT files.
3. Remux the full widescreen video with selectable English and Chinese subtitle tracks.
4. Select the most interesting self-contained highlight from the timed subtitles, defaulting to 10 seconds.
5. Export the highlight with burned-in bilingual captions.

Run from a repository checkout:

```powershell
python skills/make-widescreen-highlight/scripts/make_widescreen_highlight.py --video "input.mp4"
```

If the input is already the final 16:9 layout, skip the layout render:

```powershell
python skills/make-widescreen-highlight/scripts/make_widescreen_highlight.py --video "input_16x9.mp4" --skip-widescreen
```

For known name or term fixes, pass replacements before subtitle files and the highlight are finalized:

```powershell
python skills/make-widescreen-highlight/scripts/make_widescreen_highlight.py --video "input.mp4" --replace "wrong name=right name"
```

## Outputs

For `input.mp4`, the script creates files like:

- `input_16x9.mp4`
- `input_16x9.en.srt`
- `input_16x9.zh-Hans.srt`
- `input_16x9.bilingual.en-zh.srt`
- `input_16x9.transcript.json`
- `input_16x9.translations.json`
- `input_16x9.subtitled.mp4`
- `input_16x9_highlight_10s.mp4`
- `input_16x9.highlight.json`

If `--skip-widescreen` is used, output stems are based on the input stem instead.

## Review Loop

Always inspect the generated subtitles before treating the work as finished:

- Correct obvious names, places, school names, and visible/contextual terms.
- Prefer `--replace old=new` for simple global corrections, then rerun the script.
- If the highlight is not the desired moment, rerun with `--highlight-start HH:MM:SS.mmm`.
- Extract a frame near an active caption and visually check bilingual caption size, spacing, and Chinese font rendering.

Useful verification commands:

```powershell
ffprobe -v error -show_entries format=duration:stream=codec_type,width,height -of default=noprint_wrappers=1 "input_16x9_highlight_10s.mp4"
ffmpeg -y -ss 4 -i "input_16x9_highlight_10s.mp4" -frames:v 1 "highlight_preview.jpg"
```

## Requirements

- `ffmpeg` and `ffprobe` must be available on `PATH`.
- `OPENAI_API_KEY` and the `openai` Python package are required for transcription, translation, and automatic highlight selection.
- The script defaults to `gpt-4o-transcribe` for primary transcription, `whisper-1` for word timing fallback, and `gpt-5.2` for subtitle translation/highlight selection. Override model names if the local account uses different models.
