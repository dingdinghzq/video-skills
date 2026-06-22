# Caption Guidance

Use these patterns when creating `captions.json` for `photo-music-video`.

## General Style

- Prefer concrete sensory details over generic praise.
- Use one image-specific line per item.
- Let captions vary in rhythm: some can be lyrical, some quiet and plain.
- Avoid repeating the same opening phrase across adjacent photos.
- Keep the text human-readable at video speed; one short sentence is usually enough.

## Subject Adaptation

Flowers and gardens:
- Mention light, petals, wind, leaves, paths, fragrance, color, stillness.
- Use cultivar or species names only when confidently identified.

Scenery and travel:
- Mention horizon, clouds, road, water, mountain lines, city light, weather, distance.
- Use place names only when known from filenames, metadata, signs, or user context.

Events and people:
- Keep captions respectful and less ornate.
- Avoid identifying private people unless the user provided names.

## Chinese Caption Examples

```json
{
  "captions": [
    {"stem": "IMG_0001", "text": "云影贴着山脊，风把远处慢慢推近"},
    {"stem": "IMG_0002", "text": "水面收住天光，也收住这一刻的安静"},
    {"stem": "IMG_0003", "text": "小路转弯时，春天忽然有了回声"}
  ]
}
```

## Avoid

- Do not use the same caption for multiple photos.
- Do not over-label uncertain subjects.
- Do not make every line the same length or position if the video should feel crafted.
- Do not use fast moving text or dense paragraphs.
