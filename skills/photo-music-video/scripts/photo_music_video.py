from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    import numpy as np
except ImportError:  # Tempo detection falls back gracefully.
    np = None

from PIL import Image, ImageDraw, ImageFont, ImageOps


IMAGE_EXTS = {".heic", ".jpg", ".jpeg", ".png"}
MOTION_EXTS = {".mp4", ".mov", ".m4v"}
TRANSITIONS = [
    "fade",
    "wipeleft",
    "slideright",
    "circleopen",
    "smoothup",
    "dissolve",
    "hblur",
    "radial",
    "diagtr",
    "vertopen",
    "smoothleft",
    "circleclose",
    "wipebr",
    "fadeblack",
    "smoothdown",
]
DEFAULT_LINES = [
    "光落在眼前，时间慢慢安静下来",
    "风从画面里经过，留下温柔的回声",
    "这一刻不必说话，颜色已经替心开口",
    "远处有光，近处有可以停留的风景",
    "镜头轻轻靠近，世界也跟着放慢",
    "云影经过时，心事也变得柔软",
    "路在前方展开，像一封还没读完的信",
    "水光收住天空，也收住片刻宁静",
    "树影轻摇，把午后写成一首短诗",
    "山色不语，却把远方说得很深",
    "城市亮起时，夜色有了温度",
    "花叶之间，藏着一小片晴天",
    "风景停在这里，像等人慢慢看见",
    "光把边缘照亮，也把心放轻",
    "一帧一帧，都是时间留下的温柔",
    "远近之间，有风替我们翻页",
    "颜色缓缓铺开，像梦醒得很慢",
    "这一眼很轻，却足够被记住",
    "天空低下来，和大地说了一句悄悄话",
    "音乐往前走，画面把余光留下",
    "回看这一程，满眼都是被风收藏的光",
]


@dataclass
class Config:
    root: Path
    bgm: Path
    output: Path
    work: Path
    captions: Path | None
    motion_ratio: float
    target_segment: float
    transition_max: float
    kenburns: str
    limit: int | None
    no_captions: bool
    width: int = 1920
    height: int = 1080
    fps: int = 30

    @property
    def stills(self) -> Path:
        return self.work / "stills"

    @property
    def thumbs(self) -> Path:
        return self.work / "thumbs"

    @property
    def clips(self) -> Path:
        return self.work / "clips"

    @property
    def caption_images(self) -> Path:
        return self.work / "caption_images"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=True, text=True)


def capture(cmd: list[str], cwd: Path) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def ensure_dirs(cfg: Config) -> None:
    for path in (cfg.work, cfg.stills, cfg.thumbs, cfg.clips, cfg.caption_images):
        path.mkdir(parents=True, exist_ok=True)


def image_files(cfg: Config) -> list[Path]:
    files = [
        p
        for p in cfg.root.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    files.sort(key=lambda p: p.name.lower())
    if cfg.limit:
        files = files[: cfg.limit]
    return files


def motion_for_stem(cfg: Config, stem: str) -> Path | None:
    for path in cfg.root.iterdir():
        if path.is_file() and path.stem == stem and path.suffix.lower() in MOTION_EXTS:
            return path
    return None


def media_stems(cfg: Config) -> list[str]:
    return [p.stem for p in image_files(cfg)]


def is_jpeg_bytes(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(2) == b"\xff\xd8"


def quote_ps(value: Path) -> str:
    return str(value).replace("'", "''")


def convert_with_wic(src: Path, dst: Path, cwd: Path) -> None:
    ps = rf"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$src = '{quote_ps(src)}'
$dst = '{quote_ps(dst)}'
$stream = [IO.File]::OpenRead($src)
try {{
  $decoder = [Windows.Media.Imaging.BitmapDecoder]::Create(
    $stream,
    [Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,
    [Windows.Media.Imaging.BitmapCacheOption]::OnLoad
  )
}} finally {{
  $stream.Close()
}}
$frame = $decoder.Frames[0]
$encoder = New-Object Windows.Media.Imaging.PngBitmapEncoder
$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($frame))
$out = [IO.File]::Create($dst)
try {{
  $encoder.Save($out)
}} finally {{
  $out.Close()
}}
"""
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], cwd)


def convert_still(src: Path, dst: Path, cfg: Config) -> None:
    if dst.exists():
        return
    try:
        im = Image.open(src)
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.save(dst)
        return
    except Exception:
        if src.suffix.lower() != ".heic":
            raise
    convert_with_wic(src, dst, cfg.root)


def duration(path: Path, cfg: Config) -> float:
    value = capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        cfg.root,
    )
    return float(value)


def make_video_thumb(src: Path, dst: Path, cfg: Config) -> None:
    dur = duration(src, cfg)
    ss = max(0.0, min(dur * 0.45, dur - 0.08))
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ss:.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "2",
            str(dst),
        ],
        cfg.root,
    )


def letterbox_thumb(src: Path, size: tuple[int, int]) -> Image.Image:
    im = Image.open(src)
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (24, 24, 24))
    x = (size[0] - im.width) // 2
    y = (size[1] - im.height) // 2
    canvas.paste(im, (x, y))
    return canvas


def font_from(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def font_for(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    fonts = {
        "ui": ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"],
        "bold": ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/arialbd.ttf"],
        "kai": ["C:/Windows/Fonts/STKAITI.TTF", "C:/Windows/Fonts/simkai.ttf"],
        "xingkai": ["C:/Windows/Fonts/STXINGKA.TTF", "C:/Windows/Fonts/STKAITI.TTF"],
        "song": ["C:/Windows/Fonts/STSONG.TTF", "C:/Windows/Fonts/simsun.ttc"],
        "latin": ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"],
    }
    return font_from(fonts.get(kind, fonts["ui"]), size)


def select_motion_stems(cfg: Config, stems: list[str]) -> set[str]:
    candidates = [stem for stem in stems if motion_for_stem(cfg, stem)]
    target = min(len(candidates), max(0, round(len(stems) * cfg.motion_ratio)))
    if target <= 0:
        return set()
    if target >= len(candidates):
        return set(candidates)
    picks: list[str] = []
    for i in range(target):
        pos = round(i * (len(candidates) - 1) / max(1, target - 1))
        picks.append(candidates[pos])
    return set(picks)


def make_contact_sheet(cfg: Config) -> Path:
    ensure_dirs(cfg)
    files = image_files(cfg)
    stems = [p.stem for p in files]
    motion_stems = select_motion_stems(cfg, stems)
    for src in files:
        convert_still(src, cfg.stills / f"{src.stem}.png", cfg)

    thumbs: list[tuple[str, Path, str]] = []
    for stem in stems:
        thumb = cfg.thumbs / f"{stem}.jpg"
        motion = motion_for_stem(cfg, stem)
        if stem in motion_stems and motion:
            make_video_thumb(motion, thumb, cfg)
            label = "selected motion"
        else:
            letterbox_thumb(cfg.stills / f"{stem}.png", (360, 250)).save(thumb, quality=92)
            label = "still slow Ken Burns"
        thumbs.append((stem, thumb, label))

    cols = 5
    cell_w, cell_h = 400, 318
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (244, 241, 236))
    draw = ImageDraw.Draw(sheet)
    font = font_for("latin", 21)
    small = font_for("latin", 17)
    for idx, (stem, thumb, label) in enumerate(thumbs):
        x = (idx % cols) * cell_w
        y = (idx // cols) * cell_h
        im = letterbox_thumb(thumb, (cell_w - 24, 240))
        sheet.paste(im, (x + 12, y + 12))
        draw.text((x + 18, y + 260), stem, fill=(35, 35, 35), font=font)
        draw.text((x + 18, y + 286), label, fill=(84, 84, 84), font=small)
    out = cfg.work / "contact_sheet.jpg"
    sheet.save(out, quality=92)
    print(f"contact_sheet={out}")
    return out


def extract_audio(cfg: Config) -> tuple[Path, Path]:
    audio = cfg.work / "bgm_extracted.m4a"
    wav = cfg.work / "bgm_analysis.wav"
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(cfg.bgm),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(audio),
        ],
        cfg.root,
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio),
            "-t",
            "90",
            "-ac",
            "1",
            "-ar",
            "22050",
            str(wav),
        ],
        cfg.root,
    )
    return audio, wav


def read_wav_mono(path: Path) -> tuple[int, object]:
    with wave.open(str(path), "rb") as f:
        sr = f.getframerate()
        channels = f.getnchannels()
        frames = f.readframes(f.getnframes())
    if np is None:
        return sr, frames
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return sr, data


def estimate_tempo(path: Path) -> tuple[float, float]:
    if np is None:
        return 100.0, 0.6
    sr, samples = read_wav_mono(path)
    frame = 2048
    hop = 512
    if len(samples) < frame * 8:
        return 100.0, 0.6
    energies = []
    for start in range(0, len(samples) - frame, hop):
        chunk = samples[start : start + frame]
        energies.append(float(np.sqrt(np.mean(chunk * chunk) + 1e-12)))
    energy = np.asarray(energies, dtype=np.float32)
    novelty = np.maximum(0.0, np.diff(np.log1p(80.0 * energy)))
    smooth_n = max(3, int(0.45 * sr / hop))
    kernel = np.ones(smooth_n, dtype=np.float32) / smooth_n
    baseline = np.convolve(novelty, kernel, mode="same")
    novelty = np.maximum(0.0, novelty - baseline)
    novelty[: int(1.0 * sr / hop)] = 0
    ac = np.correlate(novelty, novelty, mode="full")[len(novelty) - 1 :]

    best_bpm = 100.0
    best_score = -1.0
    for bpm in np.linspace(70.0, 170.0, 401):
        lag = int(round(sr * 60.0 / (bpm * hop)))
        if lag <= 1 or lag >= len(ac):
            continue
        score = float(ac[lag])
        if score > best_score:
            best_score = score
            best_bpm = float(bpm)
    return best_bpm, 60.0 / best_bpm


def load_caption_map(path: Path | None) -> dict[str, dict[str, str]]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("captions", data) if isinstance(data, dict) else data
    result: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict) or "stem" not in item:
            continue
        stem = str(item["stem"])
        result[stem] = {
            "text": str(item.get("text", "")).strip(),
            "name": str(item.get("name", "")).strip(),
        }
    return result


def default_caption(index: int) -> str:
    if index < len(DEFAULT_LINES):
        return DEFAULT_LINES[index]
    return f"第{index + 1}个瞬间，也有自己的光"


def wrap_cjk(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split = max_chars
        for mark in ("，", "。", "；", "、", " "):
            pos = remaining.rfind(mark, 0, max_chars + 1)
            if pos >= int(max_chars * 0.55):
                split = pos + 1
                break
        chunks.append(remaining[:split])
        remaining = remaining[split:]
    if remaining:
        chunks.append(remaining)
    return "\n".join(chunks)


def text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    spacing: int = 6,
    stroke_width: int = 0,
) -> tuple[int, int]:
    bbox = draw.multiline_textbbox(
        (0, 0),
        text,
        font=font,
        spacing=spacing,
        stroke_width=stroke_width,
    )
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    spacing: int = 6,
) -> None:
    draw.multiline_text(
        xy,
        text,
        font=font,
        fill=(255, 255, 255, 236),
        spacing=spacing,
        stroke_width=2,
        stroke_fill=(0, 0, 0, 145),
    )


def vertical_text(text: str) -> str:
    return "\n".join(char for char in text if char != " ")


def draw_name_label(canvas: Image.Image, name: str, cfg: Config) -> None:
    draw = ImageDraw.Draw(canvas)
    font = font_for("latin", 34)
    tw, th = text_size(draw, name, font, stroke_width=1)
    pad_x, pad_y = 24, 14
    x2 = cfg.width - 70
    y1 = 52
    x1 = max(60, x2 - tw - pad_x * 2)
    y2 = y1 + th + pad_y * 2
    draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=(0, 0, 0, 96))
    draw.text(
        (x1 + pad_x, y1 + pad_y - 2),
        name,
        font=font,
        fill=(255, 255, 255, 235),
        stroke_width=1,
        stroke_fill=(0, 0, 0, 130),
    )


def caption_image(
    cfg: Config,
    stem: str,
    index: int,
    caption_map: dict[str, dict[str, str]],
) -> Path:
    out = cfg.caption_images / f"{index:03d}_{stem}.png"
    canvas = Image.new("RGBA", (cfg.width, cfg.height), (0, 0, 0, 0))
    if cfg.no_captions:
        canvas.save(out)
        return out

    item = caption_map.get(stem, {})
    text = item.get("text") or default_caption(index)
    name = item.get("name", "")
    draw = ImageDraw.Draw(canvas)
    accent = [
        (255, 216, 186, 185),
        (234, 190, 255, 185),
        (195, 230, 255, 175),
        (255, 238, 190, 180),
        (206, 255, 220, 170),
        (255, 201, 223, 180),
    ][index % 6]
    style = index % 7

    if style == 0:
        font = font_for("kai", 55)
        wrapped = wrap_cjk(text, 18)
        spacing = 10
        tw, _ = text_size(draw, wrapped, font, spacing=spacing, stroke_width=2)
        draw.rectangle((0, cfg.height - 168, cfg.width, cfg.height), fill=(0, 0, 0, 76))
        draw.rectangle((0, cfg.height - 168, cfg.width, cfg.height - 164), fill=accent)
        draw_text(draw, ((cfg.width - tw) // 2, cfg.height - 132), wrapped, font, spacing)
    elif style == 1:
        font = font_for("song", 48)
        wrapped = wrap_cjk(text, 16)
        spacing = 8
        x, y = 92, 100
        tw, th = text_size(draw, wrapped, font, spacing=spacing, stroke_width=2)
        draw.rounded_rectangle((x - 28, y - 22, x + tw + 34, y + th + 28), 22, fill=(0, 0, 0, 78))
        draw.rectangle((x - 28, y - 22, x - 20, y + th + 28), fill=accent)
        draw_text(draw, (x, y), wrapped, font, spacing)
    elif style == 2:
        font = font_for("bold", 47)
        wrapped = wrap_cjk(text, 16)
        spacing = 8
        tw, th = text_size(draw, wrapped, font, spacing=spacing, stroke_width=2)
        x, y = cfg.width - tw - 116, 108
        draw.rounded_rectangle((x - 30, y - 22, x + tw + 34, y + th + 28), 20, fill=(0, 0, 0, 76))
        draw.line((x - 5, y + th + 21, x + tw + 12, y + th + 21), fill=accent, width=3)
        draw_text(draw, (x, y), wrapped, font, spacing)
    elif style in (3, 4):
        font = font_for("xingkai", 41 if len(text) <= 20 else 37)
        vertical = vertical_text(text)
        spacing = 3
        tw, th = text_size(draw, vertical, font, spacing=spacing, stroke_width=2)
        x = cfg.width - 158 - tw if style == 3 else 120
        y = max(72, (cfg.height - th) // 2)
        draw.rounded_rectangle((x - 28, y - 30, x + tw + 30, y + th + 32), 28, fill=(0, 0, 0, 68), outline=accent, width=2)
        draw_text(draw, (x, y), vertical, font, spacing)
    elif style == 5:
        font = font_for("kai", 50)
        wrapped = wrap_cjk(text, 15)
        spacing = 8
        tw, th = text_size(draw, wrapped, font, spacing=spacing, stroke_width=2)
        x, y = 92, cfg.height - th - 170
        draw.rounded_rectangle((x - 28, y - 22, x + tw + 34, y + th + 28), 24, fill=(0, 0, 0, 78), outline=accent, width=2)
        draw_text(draw, (x, y), wrapped, font, spacing)
    else:
        font = font_for("xingkai", 58)
        wrapped = wrap_cjk(text, 14)
        spacing = 11
        x, y = 118, 150
        tw, th = text_size(draw, wrapped, font, spacing=spacing, stroke_width=2)
        draw.rounded_rectangle((x - 30, y - 24, x + tw + 38, y + th + 32), 30, fill=(0, 0, 0, 58))
        draw.line((x - 4, y + th + 24, x + min(tw, 520), y + th + 24), fill=accent, width=4)
        draw_text(draw, (x, y), wrapped, font, spacing)

    if name:
        draw_name_label(canvas, name, cfg)
    canvas.save(out)
    return out


def kenburns_values(kind: str, source_is_motion: bool) -> tuple[float, float, float]:
    values = {
        "very-slow": ((1.012, 0.010, 0.10), (1.003, 0.004, 0.06)),
        "slow": ((1.020, 0.018, 0.15), (1.006, 0.006, 0.08)),
        "medium": ((1.028, 0.032, 0.22), (1.010, 0.012, 0.12)),
    }
    still, motion = values[kind]
    return motion if source_is_motion else still


def crop_points(index: int, span: float) -> tuple[float, float, float, float]:
    dirs = [
        (-0.6, 0.6, -0.2, 0.3),
        (0.6, -0.6, -0.3, 0.2),
        (-0.2, 0.2, -0.6, 0.6),
        (0.4, -0.4, 0.4, -0.4),
        (-0.5, 0.5, 0.3, -0.3),
        (0.2, -0.2, -0.4, 0.4),
    ][index % 6]
    x0 = 0.5 + dirs[0] * span
    x1 = 0.5 + dirs[1] * span
    y0 = 0.5 + dirs[2] * span
    y1 = 0.5 + dirs[3] * span
    return tuple(max(0.05, min(0.95, v)) for v in (x0, x1, y0, y1))


def visual_filter(cfg: Config, clip_len: float, index: int, source_is_motion: bool) -> str:
    zoom_start, zoom_delta, pan_span = kenburns_values(cfg.kenburns, source_is_motion)
    x0, x1, y0, y1 = crop_points(index, pan_span)
    zoom = f"{zoom_start:.4f}+{zoom_delta:.4f}*t/{clip_len:.3f}"
    ratio = f"{cfg.width / cfg.height:.6f}"
    scale = (
        f"scale=w='if(gt(a,{ratio}),trunc({cfg.height}*({zoom})*a/2)*2,"
        f"trunc({cfg.width}*({zoom})/2)*2)':"
        f"h='if(gt(a,{ratio}),trunc({cfg.height}*({zoom})/2)*2,"
        f"trunc({cfg.width}*({zoom})/a/2)*2)':eval=frame"
    )
    crop = (
        f"crop={cfg.width}:{cfg.height}:"
        f"x='(iw-ow)*({x0:.3f}+({x1 - x0:.3f})*t/{clip_len:.3f})':"
        f"y='(ih-oh)*({y0:.3f}+({y1 - y0:.3f})*t/{clip_len:.3f})'"
    )
    caption_start = 0.55
    caption_end = max(caption_start + 2.4, clip_len - 0.72)
    fade_out = max(caption_start + 0.8, caption_end - 0.58)
    return (
        f"[0:v]fps={cfg.fps},setsar=1,{scale},{crop},"
        "setsar=1,eq=saturation=1.06:contrast=1.012,unsharp=5:5:0.30[base];"
        "[1:v]format=rgba,"
        f"fade=t=in:st={caption_start:.3f}:d=0.430:alpha=1,"
        f"fade=t=out:st={fade_out:.3f}:d=0.580:alpha=1[cap];"
        f"[base][cap]overlay=0:0:enable='between(t,{caption_start:.3f},{caption_end:.3f})',"
        "format=yuv420p[v]"
    )


def chosen_source(cfg: Config, stem: str, motion_stems: set[str]) -> Path:
    motion = motion_for_stem(cfg, stem)
    if motion and stem in motion_stems:
        return motion
    return cfg.stills / f"{stem}.png"


def build_clip(
    cfg: Config,
    stem: str,
    source: Path,
    clip_len: float,
    index: int,
    caption_map: dict[str, dict[str, str]],
) -> Path:
    out = cfg.clips / f"{index:03d}_{stem}.mp4"
    if out.exists():
        out.unlink()
    caption = caption_image(cfg, stem, index, caption_map)
    source_is_motion = source.suffix.lower() in MOTION_EXTS
    vf = visual_filter(cfg, clip_len, index, source_is_motion)
    if source_is_motion:
        input_args = ["-stream_loop", "-1", "-i", str(source)]
    else:
        input_args = ["-loop", "1", "-framerate", str(cfg.fps), "-i", str(source)]
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *input_args,
            "-loop",
            "1",
            "-framerate",
            str(cfg.fps),
            "-i",
            str(caption),
            "-t",
            f"{clip_len:.3f}",
            "-an",
            "-filter_complex",
            vf,
            "-map",
            "[v]",
            "-r",
            str(cfg.fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(out),
        ],
        cfg.root,
    )
    return out


def render_final(cfg: Config, clips: list[Path], audio: Path, segment_spacing: float, transition_len: float) -> Path:
    if not clips:
        raise ValueError("No clips to render")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for clip in clips:
        cmd += ["-i", str(clip)]
    cmd += ["-i", str(audio)]

    if len(clips) == 1:
        filter_complex = "[0:v]format=yuv420p[v]"
    else:
        parts = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]" for i in range(len(clips))]
        current = "v0"
        for i in range(1, len(clips)):
            out_label = f"x{i}"
            transition = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
            offset = segment_spacing * i
            parts.append(
                f"[{current}][v{i}]xfade=transition={transition}:"
                f"duration={transition_len:.3f}:offset={offset:.3f}[{out_label}]"
            )
            current = out_label
        parts.append(f"[{current}]format=yuv420p[v]")
        filter_complex = ";".join(parts)

    final_duration = segment_spacing * len(clips) + transition_len
    fade_start = max(0.0, final_duration - 4.0)
    run(
        [
            *cmd,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            f"{len(clips)}:a:0",
            "-t",
            f"{final_duration:.3f}",
            "-af",
            f"volume=0.82,afade=t=out:st={fade_start:.3f}:d=4.000",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(cfg.output),
        ],
        cfg.root,
    )
    return cfg.output


def make_preview_sheet(cfg: Config, video: Path) -> Path:
    preview = cfg.work / "preview_sheet.jpg"
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            "fps=1/8,scale=360:-1,tile=5x8",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(preview),
        ],
        cfg.root,
    )
    print(f"preview_sheet={preview}")
    return preview


def build_video(cfg: Config) -> Path:
    ensure_dirs(cfg)
    files = image_files(cfg)
    if not files:
        raise ValueError(f"No image files found in {cfg.root}")
    for src in files:
        convert_still(src, cfg.stills / f"{src.stem}.png", cfg)

    stems = [p.stem for p in files]
    motion_stems = select_motion_stems(cfg, stems)
    make_contact_sheet(cfg)
    audio, wav = extract_audio(cfg)
    bpm, beat = estimate_tempo(wav)
    beats_per_segment = max(4, min(16, int(round(cfg.target_segment / beat))))
    if beats_per_segment % 2 == 1:
        beats_per_segment += 1
    segment_spacing = beats_per_segment * beat
    transition_len = min(cfg.transition_max, max(0.55, beat * 1.15))
    clip_len = segment_spacing + transition_len
    (cfg.work / "edit_timing.txt").write_text(
        "\n".join(
            [
                f"estimated_bpm={bpm:.2f}",
                f"beat_seconds={beat:.4f}",
                f"beats_per_segment={beats_per_segment}",
                f"segment_spacing_seconds={segment_spacing:.3f}",
                f"transition_seconds={transition_len:.3f}",
                f"clip_seconds={clip_len:.3f}",
                f"kenburns={cfg.kenburns}",
                f"motion_selected={len(motion_stems)}",
                f"total_items={len(stems)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print((cfg.work / "edit_timing.txt").read_text(encoding="utf-8"))

    caption_map = load_caption_map(cfg.captions)
    clips = []
    for index, stem in enumerate(stems):
        source = chosen_source(cfg, stem, motion_stems)
        clips.append(build_clip(cfg, stem, source, clip_len, index, caption_map))
    out = render_final(cfg, clips, audio, segment_spacing, transition_len)
    make_preview_sheet(cfg, out)
    print(f"video={out}")
    return out


def resolved(root: Path, value: str | None, default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else root / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a beat-timed photo/music slideshow video.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--input-dir", default=".", help="Folder containing images, motion clips, and BGM.")
        p.add_argument("--bgm", default="BGM.mp4", help="Background music/video file.")
        p.add_argument("--work-dir", default="_photo_music_video_work", help="Intermediate output folder.")
        p.add_argument("--motion-ratio", type=float, default=0.30, help="Approximate ratio of matching motion clips to use.")
        p.add_argument("--limit", type=int, default=None, help="Use only the first N images for a fast draft.")

    contact = sub.add_parser("contact", help="Create a contact sheet and show selected motion clips.")
    add_common(contact)

    build = sub.add_parser("build", help="Render the final MP4.")
    add_common(build)
    build.add_argument("--output", default="photo_music_video.mp4", help="Final MP4 path.")
    build.add_argument("--captions", default=None, help="Optional captions JSON path.")
    build.add_argument("--target-segment", type=float, default=6.0, help="Target seconds per photo before beat rounding.")
    build.add_argument("--transition-max", type=float, default=0.85, help="Max transition duration in seconds.")
    build.add_argument("--kenburns", choices=["very-slow", "slow", "medium"], default="slow")
    build.add_argument("--no-captions", action="store_true", help="Render without text overlays.")
    return parser.parse_args()


def make_config(args: argparse.Namespace) -> Config:
    root = Path(args.input_dir).resolve()
    work = resolved(root, args.work_dir, "_photo_music_video_work")
    return Config(
        root=root,
        bgm=resolved(root, args.bgm, "BGM.mp4"),
        output=resolved(root, getattr(args, "output", None), "photo_music_video.mp4"),
        work=work,
        captions=resolved(root, args.captions, "") if getattr(args, "captions", None) else None,
        motion_ratio=max(0.0, min(1.0, args.motion_ratio)),
        target_segment=getattr(args, "target_segment", 6.0),
        transition_max=getattr(args, "transition_max", 0.85),
        kenburns=getattr(args, "kenburns", "slow"),
        limit=args.limit,
        no_captions=getattr(args, "no_captions", False),
    )


def main() -> None:
    args = parse_args()
    cfg = make_config(args)
    ensure_dirs(cfg)
    if args.command == "contact":
        make_contact_sheet(cfg)
    elif args.command == "build":
        build_video(cfg)


if __name__ == "__main__":
    main()
