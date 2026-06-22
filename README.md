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
