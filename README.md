# video-skills

Reusable Codex skills for video-related workflows.

This repository is organized as a container for multiple independent skills. Each skill lives under `skills/<skill-name>/` and keeps the normal Codex skill layout:

```text
skills/
  <skill-name>/
    SKILL.md
    agents/openai.yaml
    scripts/
    references/
```

## Skills

### make-widescreen-highlight

Convert portrait or phone-shot talking videos into 16:9 widescreen videos with a zoomed blurred background, centered original video, English/Simplified Chinese subtitles, a selectable-subtitle full MP4, and a short bilingual burned-caption highlight selected from the timed subtitles.

Run the full pipeline:

```powershell
python skills/make-widescreen-highlight/scripts/make_widescreen_highlight.py --video "input.mp4"
```

If the video is already 16:9 and only needs subtitles plus a highlight:

```powershell
python skills/make-widescreen-highlight/scripts/make_widescreen_highlight.py --video "input_16x9.mp4" --skip-widescreen
```

Use `--highlight-start HH:MM:SS.mmm` to override the automatically selected 10-second highlight, or `--replace old=new` to apply simple subtitle name/term corrections before muxing and burn-in.

### photo-music-video

Create polished music-timed slideshow videos from local photo folders, HEIC/JPG/PNG images, matching Live Photo motion clips, and background music. It supports contact sheets, beat-aware timing, slow full-screen Ken Burns motion, captions/poetry, optional confident labels, preview sheets, and final MP4 export.

Quick draft:

```powershell
python skills/photo-music-video/scripts/photo_music_video.py build --input-dir . --bgm BGM.mp4 --output photo_music_video.mp4 --limit 5
```

Full render:

```powershell
python skills/photo-music-video/scripts/photo_music_video.py build --input-dir . --bgm BGM.mp4 --output photo_music_video.mp4 --captions captions.json
```

Use `--kenburns very-slow` when the footage should feel extra calm.
