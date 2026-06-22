#!/usr/bin/env python
import argparse
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
CHINESE_LANGUAGE_HINTS = {"chinese", "zh", "mandarin", "cantonese", "yue"}
ENGLISH_PUNCT_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201b": "'",
    "\u2032": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2033": '"',
    "\u2026": "...",
    "\u00a0": " ",
}


def run(cmd, cwd=None):
    print(">", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def require_tool(name):
    if not shutil.which(name):
        raise SystemExit(f"Missing required tool: {name}. Install it and retry.")


def require_openai():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not configured.")
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise SystemExit("Python package 'openai' is missing. Run: python -m pip install openai") from exc
    return OpenAI


def find_video(path_arg):
    if path_arg:
        path = Path(path_arg).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Video not found: {path}")
        return path
    videos = sorted(
        [p for p in Path.cwd().iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not videos:
        raise SystemExit("No video file found in the current folder.")
    if len(videos) > 1:
        names = "\n".join(f"  - {p.name}" for p in videos)
        raise SystemExit(f"Multiple videos found. Pass --video explicitly:\n{names}")
    return videos[0].resolve()


def even(value):
    return int(math.ceil(value / 2.0) * 2)


def srt_time(seconds):
    ms_total = int(round(float(seconds or 0) * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1_000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def parse_time(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds.replace(",", "."))
    raise ValueError(f"Invalid time value: {value}")


def clean_text(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_english_punctuation(text):
    text = clean_text(text)
    for old, new in ENGLISH_PUNCT_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = text.replace("\u2013", " - ").replace("\u2014", " - ")
    text = re.sub(r"^\s*-\s*", "", text)
    text = re.sub(r"\s*-\s*$", "", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def has_cjk(text):
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")


def join_timed_words(words):
    text = ""
    for word in words:
        part = (word.get("word") or "").strip()
        if not part:
            continue
        if text and not (has_cjk(text[-1]) or has_cjk(part[0])):
            text += " "
        text += part
    return text


def apply_text_replacements(text, replacements):
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def parse_replacements(values):
    replacements = []
    for value in values or []:
        if "=" not in value:
            raise SystemExit(f"Replacement must use old=new syntax: {value}")
        old, new = value.split("=", 1)
        if not old:
            raise SystemExit(f"Replacement old value cannot be empty: {value}")
        replacements.append((old, new))
    return replacements


def normalize_translations(translations, replacements):
    normalized = []
    for item in translations:
        fixed = dict(item)
        fixed["index"] = int(fixed.get("index", len(normalized) + 1))
        fixed["en"] = apply_text_replacements(normalize_english_punctuation(fixed.get("en", "")), replacements)
        fixed["zh"] = apply_text_replacements(clean_text(fixed.get("zh", "")), replacements)
        normalized.append(fixed)
    return normalized


def build_word_timed_segments(words, replacements, max_duration=4.2, max_chars=34, pause_threshold=0.55):
    chunks = []
    current = []
    for word in words:
        if not current:
            current = [word]
            continue
        gap = float(word.get("start", 0)) - float(current[-1].get("end", 0))
        candidate = current + [word]
        text = join_timed_words(candidate)
        duration = float(word.get("end", 0)) - float(current[0].get("start", 0))
        if gap >= pause_threshold or duration > max_duration or len(text) > max_chars:
            chunks.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(current)

    segments = []
    for chunk in chunks:
        segments.append(
            {
                "start": max(0.0, float(chunk[0].get("start", 0))),
                "end": float(chunk[-1].get("end", 0)) + 0.10,
                "text": apply_text_replacements(join_timed_words(chunk), replacements),
            }
        )
    for idx in range(len(segments) - 1):
        next_start = segments[idx + 1]["start"]
        if segments[idx]["end"] > next_start - 0.04:
            segments[idx]["end"] = max(segments[idx]["start"] + 0.35, next_start - 0.04)
    return segments


def build_segment_timed_segments(raw_segments, replacements):
    segments = []
    for segment in raw_segments:
        segments.append(
            {
                "start": max(0.0, float(segment.get("start", 0))),
                "end": max(float(segment.get("end", 0)), float(segment.get("start", 0)) + 0.35),
                "text": apply_text_replacements(clean_text(segment.get("text", "")), replacements),
            }
        )
    return segments


def write_srt(path, segments, translations, selector):
    by_index = {int(item["index"]): item for item in translations}
    lines = []
    for idx, segment in enumerate(segments, 1):
        translated = by_index.get(idx, {})
        text = selector(translated, segment).strip()
        lines.extend([str(idx), f"{srt_time(segment['start'])} --> {srt_time(segment['end'])}", text, ""])
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def render_widescreen(input_video, output_video, width, height, zoom, blur, preset, crf):
    zoom_w = even(width * zoom)
    zoom_h = even(height * zoom)
    filter_complex = (
        f"[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={zoom_w}:{zoom_h}:force_original_aspect_ratio=increase,"
        f"crop={zoom_w}:{zoom_h},boxblur={blur}:2,crop={width}:{height}[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[v]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
    )


def extract_audio(video, audio_path):
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(audio_path),
        ]
    )


def transcribe(client, audio_path, model, granularities=None):
    with audio_path.open("rb") as audio:
        try:
            kwargs = {"model": model, "file": audio, "response_format": "verbose_json"}
            if granularities:
                kwargs["timestamp_granularities"] = granularities
            transcript = client.audio.transcriptions.create(**kwargs)
        except Exception:
            audio.seek(0)
            transcript = client.audio.transcriptions.create(model=model, file=audio, response_format="json")
    return transcript.model_dump() if hasattr(transcript, "model_dump") else dict(transcript)


def translate_segments(client, segments, source_language, model):
    source_is_chinese = any(hint in str(source_language).lower() for hint in CHINESE_LANGUAGE_HINTS)
    if not source_is_chinese:
        source_is_chinese = sum(1 for segment in segments if has_cjk(segment.get("text", ""))) > len(segments) / 2
    payload = {
        "source_language": source_language,
        "source_is_chinese": source_is_chinese,
        "segments": [
            {"index": idx, "start": segment["start"], "end": segment["end"], "text": clean_text(segment["text"])}
            for idx, segment in enumerate(segments, 1)
        ],
    }
    system = (
        "You prepare paired subtitles. Return only valid JSON. For each segment, produce natural "
        "subtitle English in 'en' and Simplified Chinese in 'zh'. If the source is already one of "
        "those languages, polish it lightly without changing meaning. Use plain ASCII punctuation "
        "for English apostrophes, quotation marks, and dashes. Preserve names, numbers, humor, and casual tone."
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False)
                + '\nReturn shape: {"segments":[{"index":1,"en":"...","zh":"..."}]}',
            },
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return data.get("segments", [])


def choose_highlight(client, segments, translations, duration, model):
    by_index = {int(item["index"]): item for item in translations}
    items = []
    for idx, segment in enumerate(segments, 1):
        translated = by_index.get(idx, {})
        items.append(
            {
                "index": idx,
                "start": round(segment["start"], 3),
                "end": round(segment["end"], 3),
                "en": translated.get("en", ""),
                "zh": translated.get("zh", ""),
            }
        )
    max_start = max(0.0, segments[-1]["end"] - duration)
    system = (
        "Select the strongest short social-video highlight based on timed subtitles. Prefer a funny, "
        "surprising, emotional, or self-contained moment. Avoid generic intros, outros, and setup-only passages. "
        "Return only valid JSON."
    )
    user = {
        "target_duration_seconds": duration,
        "valid_start_min": 0,
        "valid_start_max": max_start,
        "segments": items,
        "return_shape": {"start": "seconds", "reason": "short reason", "snippet": "short quoted summary"},
    }
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    start = min(max(0.0, float(data.get("start", 0.0))), max_start)
    return {"start": start, "duration": duration, "reason": data.get("reason", ""), "snippet": data.get("snippet", "")}


def mux_video(video, en_srt, zh_srt, output):
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(en_srt),
            "-i",
            str(zh_srt),
            "-map",
            "0:v",
            "-map",
            "0:a?",
            "-map",
            "1:0",
            "-map",
            "2:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:0",
            "title=English",
            "-metadata:s:s:1",
            "language=chi",
            "-metadata:s:s:1",
            "title=Chinese Simplified",
            "-disposition:s:0",
            "default",
            "-disposition:s:1",
            "0",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def build_burn_style(args):
    style_parts = [
        f"FontName={args.font_name}",
        f"FontSize={args.font_size}",
        f"PrimaryColour={args.primary_colour}",
        f"OutlineColour={args.outline_colour}",
        f"BackColour={args.back_colour}",
        "BorderStyle=1",
        f"Outline={args.outline}",
        f"Shadow={args.shadow}",
        f"Alignment={args.alignment}",
        f"MarginV={args.margin_v}",
        f"MarginL={args.margin_l}",
        f"MarginR={args.margin_r}",
    ]
    if args.bold:
        style_parts.append("Bold=1")
    if args.italic:
        style_parts.append("Italic=1")
    return ",".join(style_parts)


def filter_quote_filename(path):
    return path.name.replace("\\", "/").replace("'", "\\'")


def cut_bilingual_highlight(video, bilingual_srt, output, start, duration, style, preset, crf):
    filter_complex = (
        f"[0:v]subtitles='{filter_quote_filename(bilingual_srt)}':force_style='{style}',"
        f"trim=start={start:.3f}:duration={duration:.3f},setpts=PTS-STARTPTS[v];"
        f"[0:a]atrim=start={start:.3f}:duration={duration:.3f},asetpts=PTS-STARTPTS[a]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        cwd=bilingual_srt.parent,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Create a 16:9 blurred-background video, bilingual subtitles, and a bilingual highlight clip."
    )
    parser.add_argument("--video", help="Input video. Defaults to the only video in the current directory.")
    parser.add_argument("--output-dir", default=".", help="Directory for generated files.")
    parser.add_argument("--skip-widescreen", action="store_true", help="Use the input video as the 16:9 source.")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--background-zoom", type=float, default=1.10)
    parser.add_argument("--background-blur", default="25")
    parser.add_argument("--transcribe-model", default="gpt-4o-transcribe")
    parser.add_argument("--timing-transcribe-model", default="whisper-1")
    parser.add_argument("--translate-model", default="gpt-5.2")
    parser.add_argument("--timing", choices=["word", "segment"], default="word")
    parser.add_argument("--highlight-duration", type=float, default=10.0)
    parser.add_argument("--highlight-start", help="Manual highlight start, in seconds or HH:MM:SS.mmm.")
    parser.add_argument("--replace", action="append", default=[], help="Global subtitle correction in old=new form.")
    parser.add_argument("--keep-audio", action="store_true", help="Keep extracted transcription audio.")
    parser.add_argument("--font-name", default="Microsoft YaHei UI")
    parser.add_argument("--font-size", default="12")
    parser.add_argument("--primary-colour", default="&H00FFFFFF")
    parser.add_argument("--outline-colour", default="&H00000000")
    parser.add_argument("--back-colour", default="&H80000000")
    parser.add_argument("--outline", default="1.2")
    parser.add_argument("--shadow", default="0")
    parser.add_argument("--alignment", default="2")
    parser.add_argument("--margin-v", default="20")
    parser.add_argument("--margin-l", default="80")
    parser.add_argument("--margin-r", default="80")
    parser.add_argument("--bold", action="store_true")
    parser.add_argument("--italic", action="store_true")
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--crf", default="20")
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")
    input_video = find_video(args.video)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    replacements = parse_replacements(args.replace)

    if args.skip_widescreen:
        widescreen_video = input_video
        base = input_video.stem
    else:
        base = f"{input_video.stem}_16x9"
        widescreen_video = output_dir / f"{base}.mp4"
        render_widescreen(
            input_video,
            widescreen_video,
            args.width,
            args.height,
            args.background_zoom,
            args.background_blur,
            args.preset,
            args.crf,
        )

    OpenAI = require_openai()
    client = OpenAI()
    audio_path = output_dir / f"{base}.audio.mp3"
    transcript_path = output_dir / f"{base}.transcript.json"
    timing_path = output_dir / f"{base}.timing_transcript.json"
    translations_path = output_dir / f"{base}.translations.json"
    en_srt = output_dir / f"{base}.en.srt"
    zh_srt = output_dir / f"{base}.zh-Hans.srt"
    bilingual_srt = output_dir / f"{base}.bilingual.en-zh.srt"
    subtitled_video = output_dir / f"{base}.subtitled.mp4"
    highlight_video = output_dir / f"{base}_highlight_{int(args.highlight_duration)}s.mp4"
    highlight_json = output_dir / f"{base}.highlight.json"

    extract_audio(widescreen_video, audio_path)
    transcript = transcribe(client, audio_path, args.transcribe_model, ["word", "segment"])
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

    timing_transcript = transcript
    if args.timing == "word" and not transcript.get("words"):
        timing_transcript = transcribe(client, audio_path, args.timing_transcribe_model, ["word", "segment"])
        timing_path.write_text(json.dumps(timing_transcript, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.timing == "word" and timing_transcript.get("words"):
        segments = build_word_timed_segments(timing_transcript["words"], replacements)
    else:
        segments = build_segment_timed_segments(timing_transcript.get("segments") or transcript.get("segments") or [], replacements)
    if not segments:
        raise SystemExit("No timestamped transcript segments were returned.")

    translations = normalize_translations(
        translate_segments(client, segments, transcript.get("language", "unknown"), args.translate_model),
        replacements,
    )
    translations_path.write_text(json.dumps({"segments": translations}, ensure_ascii=False, indent=2), encoding="utf-8")

    write_srt(en_srt, segments, translations, lambda item, segment: item.get("en") or segment["text"])
    write_srt(zh_srt, segments, translations, lambda item, segment: item.get("zh") or segment["text"])
    write_srt(
        bilingual_srt,
        segments,
        translations,
        lambda item, segment: (item.get("en") or segment["text"]) + "\n" + (item.get("zh") or segment["text"]),
    )
    mux_video(widescreen_video, en_srt, zh_srt, subtitled_video)

    manual_start = parse_time(args.highlight_start)
    if manual_start is None:
        highlight = choose_highlight(client, segments, translations, args.highlight_duration, args.translate_model)
    else:
        highlight = {"start": manual_start, "duration": args.highlight_duration, "reason": "Manual start time.", "snippet": ""}
    highlight["end"] = highlight["start"] + highlight["duration"]
    highlight_json.write_text(json.dumps(highlight, ensure_ascii=False, indent=2), encoding="utf-8")

    cut_bilingual_highlight(
        widescreen_video,
        bilingual_srt,
        highlight_video,
        highlight["start"],
        highlight["duration"],
        build_burn_style(args),
        args.preset,
        args.crf,
    )

    if not args.keep_audio:
        audio_path.unlink(missing_ok=True)

    outputs = [
        widescreen_video,
        en_srt,
        zh_srt,
        bilingual_srt,
        transcript_path,
        translations_path,
        subtitled_video,
        highlight_json,
        highlight_video,
    ]
    if timing_path.exists():
        outputs.insert(5, timing_path)
    print("Created:")
    for path in outputs:
        print(f"  {path}")


if __name__ == "__main__":
    main()
