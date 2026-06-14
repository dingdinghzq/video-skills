import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


DEFAULT_TRANSCRIBE_MODEL = os.environ.get(
    "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe-diarize"
)
DEFAULT_TIMING_MODEL = os.environ.get("OPENAI_SUBTITLE_TIMING_MODEL", "whisper-1")
DEFAULT_TRANSLATION_MODELS = "gpt-5.2,gpt-5.1,gpt-5,gpt-4.1,gpt-4o"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".mp4", ".mpeg", ".mpga", ".wav", ".webm"}


def load_windows_env_key():
    if os.environ.get("OPENAI_API_KEY"):
        return
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return

    locations = [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ]
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "OPENAI_API_KEY")
        except OSError:
            continue
        if value and str(value).strip():
            os.environ["OPENAI_API_KEY"] = str(value).strip()
            return


def clean_json_text(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def as_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return json.loads(obj.model_dump_json())


def srt_time(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        whole_seconds += 1
        millis = 0
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{millis:03}"


def srt_seconds(value):
    match = re.match(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = match.groups()
    millis = millis.ljust(3, "0")[:3]
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000
    )


def ass_time(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        whole_seconds += 1
        centis = 0
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centis:02}"


def cjk_len(text):
    return sum(2 if "\u4e00" <= ch <= "\u9fff" else 1 for ch in text)


def wrap_mixed(text, width=48):
    text = " ".join(str(text).split())
    if not text:
        return ""
    if cjk_len(text) > len(text) * 1.25:
        break_chars = "\uff0c\u3002\uff01\uff1f\uff1b\u3001,.!?; "
        lines = []
        line = ""
        score = 0
        for ch in text:
            line += ch
            score += 2 if "\u4e00" <= ch <= "\u9fff" else 1
            if score >= width and ch in break_chars:
                lines.append(line.strip())
                line = ""
                score = 0
            elif score >= width + 8:
                lines.append(line.strip())
                line = ""
                score = 0
        if line.strip():
            lines.append(line.strip())
        return "\n".join(lines)
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=False)) or text


def escape_ass(text):
    return (
        str(text)
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def find_video(path_arg):
    if path_arg:
        path = Path(path_arg).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    candidates = [
        p.resolve()
        for p in Path.cwd().iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError("No video file found in the current folder.")
    names = ", ".join(p.name for p in candidates)
    raise RuntimeError(f"Multiple videos found; pass one explicitly: {names}")


def require_tool(name):
    if not shutil.which(name):
        raise RuntimeError(f"Required command not found on PATH: {name}")


def run_command(args, cwd=None, allow_fail=False):
    print("Running:", " ".join(str(a) for a in args))
    result = subprocess.run(args, cwd=cwd)
    if result.returncode and not allow_fail:
        raise subprocess.CalledProcessError(result.returncode, args)
    return result


def run_json(args):
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout or "{}")


def probe_video_size(video_path):
    try:
        data = run_json(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(video_path),
            ]
        )
        stream = (data.get("streams") or [{}])[0]
        return int(stream.get("width") or 1920), int(stream.get("height") or 1080)
    except Exception:
        return 1920, 1080


def prepare_upload_media(video_path, base_path, max_upload_mb, keep_audio):
    max_bytes = int(max_upload_mb * 1024 * 1024)
    if video_path.suffix.lower() in AUDIO_EXTENSIONS and video_path.stat().st_size <= max_bytes:
        return video_path, None

    audio_path = base_path.with_suffix(".subtitle-audio.m4a")
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(audio_path),
        ]
    )
    if audio_path.stat().st_size > max_bytes:
        raise RuntimeError(
            f"Extracted audio is {audio_path.stat().st_size / 1024 / 1024:.1f} MB, "
            f"over the {max_upload_mb:.1f} MB upload target. Split the video/audio "
            "or lower the extraction bitrate before retrying."
        )
    return audio_path, None if keep_audio else audio_path


def normalize_segments(raw):
    segments = raw.get("segments") or []
    normalized = []
    for index, seg in enumerate(segments, start=1):
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or max(start + 2.5, start))
        if end <= start:
            end = start + 2.5
        normalized.append(
            {
                "id": index,
                "start": start,
                "end": end,
                "speaker": str(seg.get("speaker") or "").strip(),
                "text": text,
            }
        )
    if not normalized and raw.get("text"):
        normalized.append(
            {
                "id": 1,
                "start": 0.0,
                "end": 5.0,
                "speaker": "",
                "text": str(raw["text"]).strip(),
            }
        )
    return normalized


def subtitle_widths(width, height):
    if width < height:
        return 34, 34
    return 54, 42


def rough_text_len(text):
    return max(1, cjk_len(text))


def split_english_sentences(text):
    text = " ".join(str(text).split())
    if not text:
        return []
    chunks = re.split(r"(?<=[.!?])\s+", text)
    parts = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or [text]


def split_chinese_sentences(text):
    text = " ".join(str(text).split())
    if not text:
        return []
    parts = re.findall(r"[^。！？；]+[。！？；]?", text)
    parts = [part.strip() for part in parts if part.strip()]
    return parts or [text]


def split_part_once(text, width, chinese=False):
    text = str(text).strip()
    if not text:
        return [text]
    halfway = len(text) // 2
    if chinese:
        break_chars = "，、, "
    else:
        break_chars = ",;: "
    best = None
    best_distance = len(text)
    for index, char in enumerate(text):
        if char in break_chars:
            distance = abs(index - halfway)
            if distance < best_distance:
                best = index + 1
                best_distance = distance
    if best is None or best < 4 or best > len(text) - 4:
        if chinese:
            score = 0
            best = 0
            for index, char in enumerate(text):
                score += 2 if "\u4e00" <= char <= "\u9fff" else 1
                if score >= width:
                    best = index + 1
                    break
            if not best:
                best = halfway
        else:
            wrapped = textwrap.wrap(text, width=max(16, width), break_long_words=False)
            if len(wrapped) > 1:
                return [wrapped[0], " ".join(wrapped[1:])]
            best = halfway
    return [text[:best].strip(), text[best:].strip()]


def fit_parts_to_count(parts, count, width, chinese=False):
    parts = [part.strip() for part in parts if part.strip()]
    if not parts:
        return [""] * count

    guard = 0
    while len(parts) < count and guard < count * 4:
        guard += 1
        index = max(range(len(parts)), key=lambda i: rough_text_len(parts[i]))
        if rough_text_len(parts[index]) < width * 1.2:
            break
        left, right = split_part_once(parts[index], width, chinese=chinese)
        if not left or not right:
            break
        parts[index : index + 1] = [left, right]

    while len(parts) > count:
        best_index = min(
            range(len(parts) - 1),
            key=lambda i: rough_text_len(parts[i]) + rough_text_len(parts[i + 1]),
        )
        joiner = "" if chinese else " "
        merged = f"{parts[best_index]}{joiner}{parts[best_index + 1]}".strip()
        parts[best_index : best_index + 2] = [merged]

    if len(parts) < count:
        parts.extend([""] * (count - len(parts)))
    return parts[:count]


def split_long_segments(segments, width, height, max_cue_seconds):
    en_width, zh_width = subtitle_widths(width, height)
    split_segments = []
    new_id = 1
    for seg in segments:
        duration = max(0.5, float(seg["end"]) - float(seg["start"]))
        en_parts = split_english_sentences(seg["english"])
        zh_parts = split_chinese_sentences(seg["chinese"])
        en_need = max(1, (rough_text_len(seg["english"]) + en_width - 1) // en_width)
        zh_need = max(1, (rough_text_len(seg["chinese"]) + zh_width - 1) // zh_width)
        time_need = max(1, int((duration + max_cue_seconds - 0.001) // max_cue_seconds))
        max_by_duration = max(1, int(duration // 1.25))
        count = max(time_need, en_need, zh_need, len(en_parts), len(zh_parts))
        count = min(max_by_duration, count)
        count = max(1, count)

        if count == 1:
            split_segments.append({**seg, "id": new_id})
            new_id += 1
            continue

        en_parts = fit_parts_to_count(en_parts, count, en_width, chinese=False)
        zh_parts = fit_parts_to_count(zh_parts, count, zh_width, chinese=True)
        weights = [
            rough_text_len(en_parts[index]) + rough_text_len(zh_parts[index])
            for index in range(count)
        ]
        total_weight = sum(weights) or count
        current = float(seg["start"])
        for index in range(count):
            if index == count - 1:
                end = float(seg["end"])
            else:
                end = current + duration * weights[index] / total_weight
            split_segments.append(
                {
                    **seg,
                    "id": new_id,
                    "start": current,
                    "end": max(current + 0.35, end),
                    "english": en_parts[index],
                    "chinese": zh_parts[index],
                }
            )
            current = split_segments[-1]["end"]
            new_id += 1
    return split_segments


def append_word_text(text, token):
    token = str(token).strip()
    if not token:
        return text
    no_space_before = ".,!?;:%)]}\uff0c\u3002\uff01\uff1f\uff1b\u3001"
    if not text:
        return token
    if token[0] in no_space_before:
        return text + token
    if ("\u4e00" <= token[0] <= "\u9fff") or ("\u4e00" <= text[-1] <= "\u9fff"):
        return text + token
    return text + " " + token


def join_word_tokens(words):
    text = ""
    for word in words:
        text = append_word_text(text, word.get("word") or word.get("text") or "")
    return text.strip()


def ends_sentence(text):
    return str(text).strip().endswith((".", "!", "?", ";", "\u3002", "\uff01", "\uff1f", "\uff1b"))


def normalize_words(raw, max_cue_seconds, en_width, zh_width):
    words = []
    for item in raw.get("words") or []:
        token = str(item.get("word") or item.get("text") or "").strip()
        if not token:
            continue
        start = item.get("start")
        end = item.get("end")
        if start is None or end is None:
            continue
        words.append({"word": token, "start": float(start), "end": float(end)})

    if not words:
        return normalize_segments(raw)

    max_units = max(32, min(76, max(en_width * 2, zh_width * 2)))
    min_sentence_seconds = 1.1
    segments = []
    cue_words = []
    cue_start = None
    for word in words:
        if cue_start is None:
            cue_start = word["start"]
        cue_words.append(word)
        text = join_word_tokens(cue_words)
        duration = word["end"] - cue_start
        should_break = False
        if duration >= max_cue_seconds:
            should_break = True
        elif rough_text_len(text) >= max_units:
            should_break = True
        elif duration >= min_sentence_seconds and ends_sentence(text):
            should_break = True

        if should_break:
            segments.append(
                {
                    "id": len(segments) + 1,
                    "start": cue_start,
                    "end": max(word["end"], cue_start + 0.35),
                    "speaker": "",
                    "text": text,
                }
            )
            cue_words = []
            cue_start = None

    if cue_words:
        end = cue_words[-1]["end"]
        segments.append(
            {
                "id": len(segments) + 1,
                "start": cue_start,
                "end": max(end, cue_start + 0.35),
                "speaker": "",
                "text": join_word_tokens(cue_words),
            }
        )
    return segments


def transcribe_diarized(client, media_path, model):
    print(f"Transcribing with {model}...")
    with media_path.open("rb") as media:
        result = client.audio.transcriptions.create(
            model=model,
            file=media,
            response_format="diarized_json",
            chunking_strategy="auto",
        )
    return as_dict(result)


def transcribe_word_timestamps(client, media_path, model, language=None):
    print(f"Aligning subtitle timing with {model} word timestamps...")
    kwargs = {
        "model": model,
        "file": None,
        "response_format": "verbose_json",
        "timestamp_granularities": ["word", "segment"],
    }
    if language:
        kwargs["language"] = language
    with media_path.open("rb") as media:
        kwargs["file"] = media
        result = client.audio.transcriptions.create(**kwargs)
    return as_dict(result)


def iter_batches(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def translation_prompt(segments):
    payload = [
        {
            "id": seg["id"],
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        }
        for seg in segments
    ]
    return (
        "For each timed transcript segment, produce both English and Simplified "
        "Chinese subtitle text. The Chinese field must always use Simplified "
        "Chinese characters, even when the source transcript contains "
        "Traditional Chinese, Cantonese wording, or mixed writing systems. If "
        "the original segment is English, keep the English faithful and "
        "translate to Simplified Chinese. If the original segment is Chinese, "
        "convert the Chinese transcript to Simplified Chinese and translate it "
        "to English. If it is mixed, produce complete natural subtitles in both "
        "languages. Do not split, merge, reorder, omit, or change the timing "
        "of any segment. Each returned English and Chinese pair must express "
        "the exact same content as that input segment. Preserve names, numbers, "
        "and meaning. Do not add speaker labels. Return valid JSON only in this "
        "shape: "
        '{"segments":[{"id":1,"english":"...","chinese":"..."}]}.\n\n'
        + json.dumps({"segments": payload}, ensure_ascii=False)
    )


def translate_batch(client, batch, models):
    prompt = translation_prompt(batch)
    last_error = None
    for model in models:
        try:
            print(f"Translating {len(batch)} segment(s) with {model}...")
            response = client.responses.create(
                model=model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are a careful bilingual subtitle translator. "
                            "Return only machine-readable JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            data = json.loads(clean_json_text(response.output_text))
            by_id = {}
            for item in data.get("segments", []):
                by_id[int(item["id"])] = item
            translated = []
            for seg in batch:
                item = by_id.get(seg["id"], {})
                english = str(item.get("english") or seg["text"]).strip()
                chinese = str(item.get("chinese") or seg["text"]).strip()
                translated.append({**seg, "english": english, "chinese": chinese})
            return translated, model
        except Exception as exc:
            last_error = exc
            print(f"Translation with {model} failed; trying next fallback.")
    raise RuntimeError(f"Translation failed with all configured models: {last_error}")


def translate_segments(client, segments, models, batch_size):
    all_segments = []
    used_models = []
    for batch in iter_batches(segments, batch_size):
        translated, model = translate_batch(client, batch, models)
        all_segments.extend(translated)
        if model not in used_models:
            used_models.append(model)
    for index, segment in enumerate(all_segments, start=1):
        segment["id"] = index
    return all_segments, used_models


def write_srt(path, segments, mode, en_width=58, zh_width=36):
    chunks = []
    for index, seg in enumerate(segments, start=1):
        if mode == "en":
            text = wrap_mixed(seg["english"], width=en_width)
        elif mode == "zh":
            text = wrap_mixed(seg["chinese"], width=zh_width)
        else:
            en = wrap_mixed(seg["english"], width=en_width)
            zh = wrap_mixed(seg["chinese"], width=zh_width)
            text = f"{en}\n{zh}".strip()
        chunks.append(
            f"{index}\n{srt_time(seg['start'])} --> {srt_time(seg['end'])}\n{text}\n"
        )
    path.write_text("\n".join(chunks), encoding="utf-8-sig")


def write_ass(path, segments, width, height, font):
    en_width, zh_width = subtitle_widths(width, height)
    base_dimension = min(width, height)
    font_size = max(28, round(base_dimension * 0.039))
    margin_v = max(42, round(base_dimension * 0.054))
    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default, {font}, {font_size}, &H00FFFFFF, &H000000FF, &H00000000, &H8C000000, 0, 0, 0, 0, 100, 100, 0, 0, 1, 3, 1, 2, 80, 80, {margin_v}, 1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for seg in segments:
        en = wrap_mixed(seg["english"], width=en_width)
        zh = wrap_mixed(seg["chinese"], width=zh_width)
        text = escape_ass(f"{en}\n{zh}".strip())
        lines.append(
            f"Dialogue: 0,{ass_time(seg['start'])},{ass_time(seg['end'])},"
            f"Default,,0,0,0,,{text}\n"
        )
    path.write_text("".join(lines), encoding="utf-8-sig")


def parse_srt(path):
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise RuntimeError(f"SRT file is empty: {path}")

    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip("\ufeff") for line in block.splitlines()]
        lines = [line for line in lines if line.strip()]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index]
        start_text, end_text = [part.strip().split()[0] for part in timing.split("-->", 1)]
        cue_text = "\n".join(lines[timing_index + 1 :]).strip()
        if not cue_text:
            continue
        cues.append(
            {
                "start": srt_seconds(start_text),
                "end": srt_seconds(end_text),
                "text": cue_text,
            }
        )
    if not cues:
        raise RuntimeError(f"No subtitle cues found in SRT file: {path}")
    return cues


def write_text_ass(path, cues, width, height, font):
    base_dimension = min(width, height)
    font_size = max(28, round(base_dimension * 0.039))
    margin_v = max(42, round(base_dimension * 0.054))
    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default, {font}, {font_size}, &H00FFFFFF, &H000000FF, &H00000000, &H8C000000, 0, 0, 0, 0, 100, 100, 0, 0, 1, 3, 1, 2, 80, 80, {margin_v}, 1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for cue in cues:
        text = escape_ass(cue["text"])
        lines.append(
            f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},"
            f"Default,,0,0,0,,{text}\n"
        )
    path.write_text("".join(lines), encoding="utf-8-sig")


def make_soft_video(video_path, en_srt, zh_srt, output_path):
    cwd = video_path.parent
    copy_args = [
        "ffmpeg",
        "-y",
        "-i",
        video_path.name,
        "-i",
        en_srt.name,
        "-i",
        zh_srt.name,
        "-map",
        "0",
        "-map",
        "1",
        "-map",
        "2",
        "-c",
        "copy",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=eng",
        "-metadata:s:s:0",
        "title=English",
        "-metadata:s:s:1",
        "language=zho",
        "-metadata:s:s:1",
        "title=Chinese",
        str(output_path),
    ]
    if run_command(copy_args, cwd=cwd, allow_fail=True).returncode == 0:
        return
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path.name,
            "-i",
            en_srt.name,
            "-i",
            zh_srt.name,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map",
            "1",
            "-map",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:1",
            "language=zho",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        cwd=cwd,
    )


def make_burned_video(video_path, ass_path, output_path):
    cwd = video_path.parent
    burn_filter = f"ass={ass_path.name}"
    copy_audio_args = [
        "ffmpeg",
        "-y",
        "-i",
        video_path.name,
        "-vf",
        burn_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    if run_command(copy_audio_args, cwd=cwd, allow_fail=True).returncode == 0:
        return
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path.name,
            "-vf",
            burn_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        cwd=cwd,
    )


def default_reburn_output(video_path, subtitle_path):
    base = video_path.with_suffix("")
    subtitle_stem = subtitle_path.stem
    if subtitle_stem == f"{video_path.stem}.bilingual":
        return base.with_name(f"{base.name}_bilingual_burned.mp4")
    if subtitle_stem == video_path.stem:
        return base.with_name(f"{base.name}_burned.mp4")
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", subtitle_stem)
    return base.with_name(f"{base.name}_{safe_stem}_burned.mp4")


def make_burned_subtitle_video(video_path, subtitle_path, output_path, font):
    if not subtitle_path.exists():
        raise FileNotFoundError(subtitle_path)
    width, height = probe_video_size(video_path)
    cleanup_path = None

    if subtitle_path.suffix.lower() == ".ass":
        if subtitle_path.parent == video_path.parent:
            ass_path = subtitle_path
        else:
            temp = tempfile.NamedTemporaryFile(
                suffix=".ass", dir=video_path.parent, delete=False
            )
            temp.close()
            ass_path = Path(temp.name)
            shutil.copyfile(subtitle_path, ass_path)
            cleanup_path = ass_path
    elif subtitle_path.suffix.lower() == ".srt":
        cues = parse_srt(subtitle_path)
        temp = tempfile.NamedTemporaryFile(
            suffix=".ass", dir=video_path.parent, delete=False
        )
        temp.close()
        ass_path = Path(temp.name)
        write_text_ass(ass_path, cues, width, height, font)
        cleanup_path = ass_path
    else:
        raise RuntimeError("Reburn input must be an .srt or .ass subtitle file.")

    try:
        make_burned_video(video_path, ass_path, output_path)
    finally:
        if cleanup_path and cleanup_path.exists():
            cleanup_path.unlink()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create English and Simplified Chinese subtitles for a video."
    )
    parser.add_argument("video", nargs="?", help="Video path. Omit if one video is in cwd.")
    parser.add_argument(
        "--reburn-srt",
        help="Burn an existing edited .srt or .ass file onto the video and exit.",
    )
    parser.add_argument(
        "--reburn-output",
        help="Output path for --reburn-srt. Defaults to the usual burned-video name.",
    )
    parser.add_argument(
        "--transcribe-model",
        default=DEFAULT_TRANSCRIBE_MODEL,
        help="Fallback diarized transcription model used when --timing-source=diarized.",
    )
    parser.add_argument(
        "--timing-source",
        choices=["word", "diarized"],
        default="word",
        help="Use whisper word timestamps for accurate cue timing, or diarized segments.",
    )
    parser.add_argument(
        "--timing-model",
        default=DEFAULT_TIMING_MODEL,
        help="OpenAI model for word-level timestamp alignment. Default: whisper-1.",
    )
    parser.add_argument(
        "--language",
        help="Optional ISO language code for the timing transcription, such as zh or en.",
    )
    parser.add_argument(
        "--translation-models",
        default=DEFAULT_TRANSLATION_MODELS,
        help="Comma-separated OpenAI response models to try for translation.",
    )
    parser.add_argument("--translation-batch-size", type=int, default=80)
    parser.add_argument("--max-upload-mb", type=float, default=24.0)
    parser.add_argument("--output-prefix", help="Output basename/path without extension.")
    parser.add_argument("--font", default="Microsoft YaHei")
    parser.add_argument("--max-cue-seconds", type=float, default=3.8)
    parser.add_argument(
        "--post-split-cues",
        action="store_true",
        help="Apply a mechanical length-based split after model cue generation.",
    )
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument("--skip-soft", action="store_true")
    parser.add_argument("--skip-burn", action="store_true")
    parser.add_argument("--keep-audio", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    require_tool("ffmpeg")
    require_tool("ffprobe")
    video_path = find_video(args.video)
    base = Path(args.output_prefix).resolve() if args.output_prefix else video_path.with_suffix("")
    width, height = probe_video_size(video_path)
    en_width, zh_width = subtitle_widths(width, height)

    if args.reburn_srt:
        subtitle_path = Path(args.reburn_srt).resolve()
        output_path = (
            Path(args.reburn_output).resolve()
            if args.reburn_output
            else default_reburn_output(video_path, subtitle_path)
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        make_burned_subtitle_video(video_path, subtitle_path, output_path, args.font)
        print("\nCreated:")
        print(output_path)
        return

    if OpenAI is None:
        raise RuntimeError(
            "The openai package is missing. Install it with: python -m pip install --user openai"
        )

    load_windows_env_key()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not available to this process.")

    raw_suffix = (
        ".timing.word.raw.json"
        if args.timing_source == "word"
        else ".transcript.raw.json"
    )
    raw_path = base.with_suffix(raw_suffix)
    segments_path = base.with_suffix(".segments.bilingual.json")
    en_srt = base.with_suffix(".en.srt")
    zh_srt = base.with_suffix(".zh-Hans.srt")
    bilingual_srt = base.with_suffix(".bilingual.srt")
    ass_path = base.with_suffix(".bilingual.ass")
    soft_output = base.with_name(f"{base.name}_soft_subtitles.mp4")
    burned_output = base.with_name(f"{base.name}_bilingual_burned.mp4")

    cleanup_path = None
    if raw_path.exists() and not args.force_transcribe:
        print(f"Using cached transcript: {raw_path}")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        upload_path, cleanup_path = prepare_upload_media(
            video_path, base, args.max_upload_mb, args.keep_audio
        )
        client = OpenAI()
        if args.timing_source == "word":
            raw = transcribe_word_timestamps(
                client, upload_path, args.timing_model, args.language
            )
        else:
            raw = transcribe_diarized(client, upload_path, args.transcribe_model)
        raw_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        if args.timing_source == "word":
            segments = normalize_words(raw, args.max_cue_seconds, en_width, zh_width)
        else:
            segments = normalize_segments(raw)
        if not segments:
            raise RuntimeError("No transcript segments were returned.")

        client = OpenAI()
        models = [m.strip() for m in args.translation_models.split(",") if m.strip()]
        translated, translation_models = translate_segments(
            client,
            segments,
            models,
            args.translation_batch_size,
        )
        if args.post_split_cues:
            translated = split_long_segments(
                translated, width, height, args.max_cue_seconds
            )

        segments_path.write_text(
            json.dumps(
                {
                    "timing_source": args.timing_source,
                    "timing_model": (
                        args.timing_model
                        if args.timing_source == "word"
                        else args.transcribe_model
                    ),
                    "translation_models": translation_models,
                    "segments": translated,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        write_srt(en_srt, translated, "en", en_width=en_width, zh_width=zh_width)
        write_srt(zh_srt, translated, "zh", en_width=en_width, zh_width=zh_width)
        write_srt(
            bilingual_srt, translated, "both", en_width=en_width, zh_width=zh_width
        )
        write_ass(ass_path, translated, width, height, args.font)

        if not args.skip_soft:
            make_soft_video(video_path, en_srt, zh_srt, soft_output)
        if not args.skip_burn:
            make_burned_video(video_path, ass_path, burned_output)
    finally:
        if cleanup_path and cleanup_path.exists():
            cleanup_path.unlink()

    print("\nCreated:")
    outputs = [raw_path, segments_path, en_srt, zh_srt, bilingual_srt, ass_path]
    if not args.skip_soft:
        outputs.append(soft_output)
    if not args.skip_burn:
        outputs.append(burned_output)
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
