---
name: photo-music-video
description: Create polished music-timed slideshow videos from local photo folders, HEIC/JPG/PNG images, matching Live Photo motion clips, and background music. Use when Codex is asked to turn photos, flower pictures, travel/scenery shots, garden photos, event images, or mixed stills/videos into a cinematic video with beat-aware timing, transitions, slow Ken Burns pan/zoom, captions/poetry, optional confident subject labels, preview sheets, and final MP4 export.
---

# Photo Music Video

## Workflow

Use `scripts/photo_music_video.py` as the reusable pipeline. Start with a contact sheet, inspect the visuals, then render a short sample before committing to a full build.

When using this repository checkout directly:

```powershell
python skills/photo-music-video/scripts/photo_music_video.py contact --input-dir . --bgm BGM.mp4
python skills/photo-music-video/scripts/photo_music_video.py build --input-dir . --bgm BGM.mp4 --output photo_music_video.mp4 --captions captions.json --limit 5
python skills/photo-music-video/scripts/photo_music_video.py build --input-dir . --bgm BGM.mp4 --output photo_music_video.mp4 --captions captions.json
```

When the skill is installed into Codex, run the same script from the installed skill directory.

Default behavior:
- Use all top-level images: `.heic`, `.jpg`, `.jpeg`, `.png`.
- Prefer matching Live Photo clips by filename stem, such as `IMG_1234.HEIC` + `IMG_1234.MP4`.
- Select about 30% motion clips by default and render the rest as stills.
- Fit cuts to estimated music beats without requiring the full music length.
- Render 1920x1080 H.264 MP4 with AAC audio.
- Use slow full-screen Ken Burns motion by default; avoid fast pan/zoom because it can feel dizzying.

## Captions

Create a `captions.json` when captions matter. Keep one unique line per photo/video. For Chinese captions, prefer local rendered text layers over image generation so characters remain exact.

Schema:

```json
{
  "captions": [
    {
      "stem": "IMG_1234",
      "text": "风把光翻成柔软的一页",
      "name": "optional confident subject or cultivar name"
    }
  ]
}
```

Rules:
- Write captions after viewing the contact sheet or sample frames.
- Do not reuse the same caption on two different images.
- Keep each line short enough to read in 4-6 seconds.
- Match the subject: flowers, gardens, mountains, ocean, city night, family/event, etc.
- Show a subject/brand/cultivar/place label only when confidence is high. Omit it when unsure.
- For exact Chinese text, render with local fonts. Do not rely on generated-image text unless the user explicitly accepts possible text errors.

Optional detail: read `references/caption-guidance.md` when writing a large caption set or adapting to a non-flower theme.

## Visual Direction

Use full-screen framing unless the user asks for picture-in-picture or uncropped portrait photos.

Ken Burns defaults should be gentle:
- Still photos: total zoom increase around 1.5-2.5% per clip.
- Motion clips: nearly static, around 0.5-1.0% total zoom.
- Pan distance: small center-biased crop drift, not edge-to-edge travel.
- Segment target: around 6 seconds before transitions unless the music strongly suggests otherwise.

For portrait photos, full-screen crop may cut top/bottom. Inspect sample frames and reduce pan or switch selected clips to a softer crop if important subjects are clipped.

## Verification

Always produce and inspect:
- Contact sheet: confirms file order, selected motion clips, and missing images.
- Short sample video with `--limit 3` to `--limit 5`.
- Preview sheet from the final video.
- A few full-resolution extracted frames from early/middle/end and any labeled subject.

Use `ffprobe` to confirm the final has video and audio streams. If captions are too small in the preview sheet, inspect full-resolution frames before changing sizes.
