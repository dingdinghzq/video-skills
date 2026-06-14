---
name: create-bilingual-subtitles
description: Generate English and Simplified Chinese bilingual subtitles for local video files using OpenAI transcription and translation, then create English SRT, Chinese SRT, bilingual SRT/ASS, a soft-subtitle MP4, and a burned-in subtitle MP4. Also reburn manually edited SRT or ASS subtitle files onto videos without calling OpenAI. Use when Codex is asked to add captions or subtitles to videos, transcribe speech into timed subtitles, translate English audio to Chinese or Chinese audio to English, create bilingual subtitle tracks, or reburn a video after the user edits an .srt file for .mp4, .mov, .mkv, .webm, or similar video files.
---

# Create Bilingual Subtitles

## Overview

Use this skill to turn a local video into bilingual English and Simplified Chinese subtitles, with both sidecar subtitle files and MP4 outputs.

## Workflow

1. Identify the target video in the current folder. If exactly one obvious video exists, use it; if multiple videos exist and the user did not name one, ask which file to process.
2. Before any OpenAI API call, follow the available OpenAI API key workflow: check for `OPENAI_API_KEY` without printing secrets, reuse an existing key only after the user has already authorized that, and never echo key values.
3. Verify tools: `ffmpeg`, `ffprobe`, Python, and the `openai` Python package. Install the package with `python -m pip install --user openai` if it is missing.
4. For timing-sensitive subtitles, use the script's default `--timing-source word`, which calls `whisper-1` with word-level timestamps and then translates fixed timed cues. Do not ask the translation model to invent cue timings. Current OpenAI docs state word-level timestamp granularities are supported by `whisper-1`; GPT transcription models can be used for text transcription, but not as the default timing source.
5. Resolve `scripts/create_bilingual_subtitles.py` relative to this `SKILL.md`, then run it with an absolute path:

```powershell
python "<skill-directory>\scripts\create_bilingual_subtitles.py" "C:\path\to\video.mp4"
```

If the current folder has exactly one video, the video argument can be omitted.

## Reburn Edited SRT

When the user manually edits an `.srt` file and asks to burn it into the video again, use `--reburn-srt`. This mode does not call OpenAI, does not require `OPENAI_API_KEY`, and does not alter transcription or translation caches.

```powershell
python "<skill-directory>\scripts\create_bilingual_subtitles.py" "C:\path\to\video.mp4" --reburn-srt "C:\path\to\video.bilingual.srt" --reburn-output "C:\path\to\video_bilingual_burned.mp4"
```

If `--reburn-output` is omitted and the SRT name is `<video>.bilingual.srt`, the script overwrites the usual `<video>_bilingual_burned.mp4` output.

## Outputs

The script writes files beside the video, using the video basename:

- `.timing.word.raw.json`: raw word-timestamp transcription response for audit/debugging
- `.segments.bilingual.json`: timed English and Simplified Chinese segment data
- `.en.srt`: English subtitle track
- `.zh-Hans.srt`: Simplified Chinese subtitle track
- `.bilingual.srt`: English and Chinese in each cue
- `.bilingual.ass`: styled bilingual subtitles for burn-in
- `_soft_subtitles.mp4`: MP4 with selectable English and Chinese subtitle tracks
- `_bilingual_burned.mp4`: MP4 with bilingual subtitles rendered onto the video

## Verification

After generation, perform a quick QA pass:

- Read the first few cues from `.bilingual.srt` and check that each cue has English plus Simplified Chinese.
- Run `ffprobe` on `_soft_subtitles.mp4` and confirm subtitle streams tagged `eng` and `zho`.
- Extract one frame from `_bilingual_burned.mp4` during a spoken cue and visually inspect the rendered subtitle placement.
- Remove temporary preview images after inspection.

## Script Notes

Use `scripts/create_bilingual_subtitles.py` directly for the normal workflow. Read or patch it only when the user needs different languages, styling, output naming, model choices, or behavior for unusually large/long videos.

Important flags:

- `--reburn-srt <path>`: burn an existing edited `.srt` or `.ass` onto the video and exit without OpenAI calls.
- `--reburn-output <path>`: output file for `--reburn-srt`; omit it to use the default burned-video name.
- `--timing-source word`: default timing mode; use `whisper-1` word timestamps for accurate subtitle cue times.
- `--timing-model <model>`: word timestamp model; default is `whisper-1`.
- `--transcribe-model <model>`: fallback diarized model used only with `--timing-source diarized`.
- `--translation-models <m1,m2,...>`: override translation fallback models.
- `--max-cue-seconds <seconds>`: maximum target duration for word-timestamp cue grouping.
- `--post-split-cues`: mechanically split remaining long cues after fixed-timing translation; use only as a fallback.
- `--force-transcribe`: ignore an existing raw transcript cache and call transcription again.
- `--skip-soft` or `--skip-burn`: skip one MP4 output when only subtitle sidecars are needed.
