"""The Video Editor's toolkit: look at, transcribe and cut videos, and render edits with ffmpeg.

ffmpeg comes from the imageio-ffmpeg package (no separate install), PyAV reads a file's details, and faster-whisper
(already used for Jarvis's hearing) writes the transcripts that captions and cut-by-what-was-said are built from.

The source files are never changed: every render is a new file in Videos\\Jarvis Edits. Long jobs (renders,
transcripts) run in the background; if one takes longer than a tool call may wait, the tool says so and the agent
gets a message when it's done.
"""
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid

from team import messages
from tools import files

CACHE = os.path.join(messages.FOLDER, "video_cache")   # transcripts, keyed by file and its modified time
WAIT_S = 200            # how long a tool waits for a job before handing it to the background (calls time out at 300)
MAX_CLIPS = 200
MAX_LOOK_FRAMES = 12
PHONE_MB = 45           # "phone" quality aims under Telegram's 50 MB limit for bots
WHISPER_MODEL = os.environ.get("JARVIS_VIDEO_WHISPER", "small.en")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".mts", ".m2ts", ".3gp")
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "4:3": (4, 3)}
CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_whisper = None
_whisper_lock = threading.Lock()


def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def output_folder() -> str:
    folder = os.path.join(files.resolve("Videos"), "Jarvis Edits")
    os.makedirs(folder, exist_ok=True)
    return folder


def _source(path: str) -> str:
    """A file the agent may read: it must exist and be in a folder Jarvis is allowed to use."""
    real = files.resolve(path)
    if not os.path.isfile(real):
        raise ValueError(f"No file at {real}. Use find_files to locate it.")
    if files.is_sensitive(real) or not files.is_allowed(real):
        raise ValueError(f"{real} is outside the folders Jarvis may use. Ask the user to move it into Videos, "
                         "Downloads or Documents, or to grant Jarvis access to its folder.")
    return real


def _output(name: str, ext: str) -> str:
    """A new file in the edits folder (never overwrites anything)."""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", os.path.splitext(name or "")[0]).strip(" .") or "edit"
    stem = stem[:80]
    folder = output_folder()
    path = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return path


def _clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return (f"{h}:{m:02d}:{s:05.2f}" if h else f"{m}:{s:05.2f}")


def _run(args: list[str], cancel: threading.Event | None = None, progress=None, total: float = 0,
         cwd: str | None = None) -> str:
    """Run ffmpeg; returns its log (stderr). Stops early if `cancel` is set. `progress(fraction)` while it runs."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y"] + (["-progress", "pipe:1"] if progress else []) + args
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_FLAGS, text=True, encoding="utf-8", errors="replace")
    err: list[str] = []
    reader = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
    reader.start()
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            break
        if progress and total and line.startswith("out_time_us="):
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / total))
            except ValueError:
                pass
    proc.wait()
    reader.join(5)
    log = "".join(err)
    if cancel is not None and cancel.is_set():
        raise RuntimeError("stopped")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{_ffmpeg_error(log)}")
    return log


def _ffmpeg_error(log: str) -> str:
    """The lines of an ffmpeg log that say what went wrong (not its banner and stream listing)."""
    lines = [l.strip() for l in log.splitlines() if l.strip()]
    wanted = [l for l in lines if re.search(r"error|invalid|no such|not found|unable|cannot|could not|failed|"
                                            r"unconnected|does not|unknown", l, re.IGNORECASE)]
    return "\n".join((wanted or lines)[-8:])[-1500:]


# ---------- details ----------

def probe(path: str) -> dict:
    import av

    with av.open(path) as box:
        info = {"duration": (box.duration or 0) / 1e6, "size_mb": os.path.getsize(path) / 2**20, "video": None,
                "audio": None}
        v = next(iter(box.streams.video), None)
        if v is not None:
            rotation = 0
            try:
                rotation = int(float(v.metadata.get("rotate", 0) or 0)) or int(getattr(v, "side_data", {}).get("DISPLAYMATRIX", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                pass
            w, h = v.codec_context.width, v.codec_context.height
            if abs(rotation) in (90, 270):
                w, h = h, w
            fps = float(v.average_rate) if v.average_rate else 0.0
            info["video"] = {"width": w, "height": h, "fps": round(fps, 2), "codec": v.codec_context.name}
            if not info["duration"] and v.duration and v.time_base:
                info["duration"] = float(v.duration * v.time_base)
        a = next(iter(box.streams.audio), None)
        if a is not None:
            info["audio"] = {"codec": a.codec_context.name, "rate": a.codec_context.sample_rate,
                             "channels": a.codec_context.channels}
    return info


def _describe(path: str, info: dict) -> str:
    v, a = info["video"], info["audio"]
    parts = [f"{os.path.basename(path)}: {_clock(info['duration'])} long, {info['size_mb']:.1f} MB"]
    if v:
        parts.append(f"video {v['width']}x{v['height']} at {v['fps']} fps ({v['codec']})")
    if a:
        parts.append(f"audio {a['codec']}, {a['channels']} ch")
    else:
        parts.append("no audio")
    return ", ".join(parts)


# ---------- background jobs ----------

def _start_job(agent, kind: str, label: str, work) -> str:
    """Run `work(job)` in the background. Wait up to WAIT_S for it; if it isn't done, say so (and message the agent
    when it is)."""
    job = {"id": uuid.uuid4().hex[:6], "kind": kind, "label": label, "agent": agent.name, "status": "running",
           "progress": 0.0, "result": "", "started": time.time(), "detached": False}
    with _jobs_lock:
        _jobs[job["id"]] = job
    done = threading.Event()

    def runner():
        try:
            job["result"] = work(job)
            job["status"] = "done"
        except Exception as e:
            job["status"], job["result"] = "failed", f"{label} failed: {e}"
        done.set()
        if job["detached"] and not agent.cancelled.is_set():
            agent.put(f"(Background job {job['id']} finished) {job['result']}")

    threading.Thread(target=runner, daemon=True, name=f"video-{kind}").start()
    if done.wait(WAIT_S):
        return job["result"]
    job["detached"] = True
    if done.is_set():  # finished in the instant between
        job["detached"] = False
        return job["result"]
    return (f"{label} is still running ({job['progress']:.0%} so far; job {job['id']}). End your turn: you'll get a "
            "message when it's done. video_jobs shows progress.")


def _samples(path: str):
    """The audio as 16 kHz mono floats, decoded by ffmpeg (what whisper expects; faster-whisper's own decoder
    doesn't match the installed PyAV)."""
    import numpy as np

    proc = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                           "-f", "f32le", "-"], capture_output=True, creationflags=CREATE_FLAGS)
    if proc.returncode != 0:
        raise RuntimeError("couldn't read the audio: " + _ffmpeg_error(proc.stderr.decode("utf-8", "replace")))
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _transcript(path: str, cancel: threading.Event | None = None) -> dict:
    """{"language", "words": [[start, end, word], ...], "segments": [[start, end, text], ...]} for a file, cached."""
    stat = os.stat(path)
    key = hashlib.sha1(f"{os.path.normcase(path)}|{stat.st_size}|{int(stat.st_mtime)}".encode()).hexdigest()[:20]
    cached = os.path.join(CACHE, f"{key}.json")   # short: long paths break on Windows
    try:
        with open(cached, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel

            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, info = _whisper.transcribe(_samples(path), word_timestamps=True, vad_filter=True)
        words, lines = [], []
        for seg in segments:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("stopped")
            lines.append([round(seg.start, 2), round(seg.end, 2), seg.text.strip()])
            words.extend([round(w.start, 2), round(w.end, 2), w.word.strip()] for w in (seg.words or []) if w.word.strip())
    result = {"language": info.language, "words": words, "segments": lines}
    os.makedirs(CACHE, exist_ok=True)
    with open(cached, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return result


# ---------- looking at a video ----------

def contact_sheet(path: str, times: list[float], width: int = 360) -> bytes:
    """A grid of frames at the given times, each labelled with its timestamp, as JPEG."""
    from PIL import Image, ImageDraw, ImageFont

    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            out = os.path.join(tmp, f"{i}.jpg")
            try:
                _run(["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", out])
                frames.append((t, Image.open(out).convert("RGB")))
            except RuntimeError:
                continue
    if not frames:
        raise RuntimeError("couldn't read any frames from that video")
    cols = 3 if len(frames) > 4 else 2 if len(frames) > 1 else 1
    rows = -(-len(frames) // cols)
    fw, fh = frames[0][1].size
    sheet = Image.new("RGB", (cols * fw, rows * fh), "black")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", max(14, fw // 16))
    except OSError:
        font = ImageFont.load_default()
    for i, (t, frame) in enumerate(frames):
        x, y = (i % cols) * fw, (i // cols) * fh
        sheet.paste(frame.resize((fw, fh)), (x, y))
        label = _clock(t)
        box = draw.textbbox((0, 0), label, font=font)
        draw.rectangle((x + 4, y + 4, x + 12 + box[2], y + 10 + box[3]), fill=(0, 0, 0))
        draw.text((x + 8, y + 6), label, fill=(255, 220, 60), font=font)
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")


def _text(path: str) -> str | None:
    """A plain text file's contents (cut at files.MAX_READ_CHARS), or None if it isn't text."""
    with open(path, "rb") as f:
        data = f.read(files.MAX_READ_CHARS * 4 + 1)
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", "replace")
    elif b"\x00" in data[:8192]:
        return None   # binary
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            if e.start < len(data) - 4:  # not just a character cut in half at the end
                text = data.decode("cp1252", "replace")
            else:
                text = data[:e.start].decode("utf-8-sig")
    if len(text) > files.MAX_READ_CHARS or os.path.getsize(path) > len(data):
        text = text[:files.MAX_READ_CHARS] + f"\n[... cut after {files.MAX_READ_CHARS} characters]"
    return text


def view_file(path: str) -> list | str:
    """What any agent sees of a file: an image (the picture), a video (frames across it) or plain text (its text),
    for the view_file tool."""
    src = _source(path)
    lower = src.lower()
    if not lower.endswith(IMAGE_EXTS + VIDEO_EXTS + (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")):
        text = _text(src)
        if text is not None:
            return f"{os.path.basename(src)} ({os.path.getsize(src):,} bytes):\n\n{text}"
    if src.lower().endswith(IMAGE_EXTS) and not src.lower().endswith(".gif"):
        from PIL import Image

        try:
            image = Image.open(src)
            image.load()
        except Exception as e:
            return f"Couldn't open {os.path.basename(src)} as an image ({e})."
        size = image.size
        image = image.convert("RGB")
        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return _image(buffer.getvalue(), f"{os.path.basename(src)} ({size[0]}x{size[1]})")
    try:
        info = probe(src)
    except Exception:
        info = {"video": None}
    if not info["video"]:
        return (f"{os.path.basename(src)} isn't an image, video or plain text file, so I can't show it (PDFs and "
                "Word documents aren't supported yet).")
    duration = info["duration"]
    count = 1 if duration < 1 else 6
    times = [duration * (n + .5) / count for n in range(count)]
    return _image(contact_sheet(src, times), f"{_describe(src, info)}. {count} frames across it, timestamps in the "
                                             "corners.")


def _image(data: bytes, caption: str) -> list:
    return [{"type": "text", "text": caption},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(data).decode("ascii")}}]


# ---------- finding cuts ----------

def speech_parts(path: str, min_silence: float, threshold_db: float, pad: float) -> tuple[list, float]:
    """The parts with sound, between silences (ffmpeg's silencedetect), slightly padded."""
    duration = probe(path)["duration"]
    log = _run(["-i", path, "-vn", "-af", f"silencedetect=n={threshold_db}dB:d={min_silence}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", log)]
    keep, cursor = [], 0.0
    for i, s in enumerate(starts):
        if s - cursor > 0.05:
            keep.append([max(0.0, cursor - pad), min(duration, s + pad)])
        cursor = ends[i] if i < len(ends) else duration
    if duration - cursor > 0.05:
        keep.append([max(0.0, cursor - pad), duration])
    merged = []
    for part in keep:
        if merged and part[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], part[1])
        else:
            merged.append(part)
    return [[round(a, 2), round(b, 2)] for a, b in merged], duration


def scene_cuts(path: str, threshold: float, cancel=None) -> list[float]:
    log = _run(["-i", path, "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
               cancel)
    return [round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", log)]


# ---------- rendering ----------

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


CAPTION_STYLES = {
    # name: (font, size as a fraction of the height, bold, primary, outline colour, outline, highlight colour)
    "bold": ("Arial Black", .062, 1, "&H00FFFFFF", "&H00000000", 4, "&H0000E6FF"),
    "clean": ("Segoe UI Semibold", .055, 0, "&H00FFFFFF", "&H80000000", 3, None),
    "boxed": ("Segoe UI Semibold", .052, 0, "&H00FFFFFF", "&H60000000", 10, None),   # the outline is the box's padding
}


def _captions(words: list, style: str, position: str, per_line: int, uppercase: bool, w: int, h: int) -> list[str]:
    """ASS dialogue lines for captions: a few words at a time, the spoken word highlighted in the bold style."""
    font, size, bold, primary, outline_c, outline, highlight = CAPTION_STYLES[style]
    chunks, current = [], []
    for word in words:
        if current and (len(current) >= per_line or word[0] - current[-1][1] > .6
                        or current[-1][2].endswith((".", "?", "!"))):
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)
    lines = []
    for n, chunk in enumerate(chunks):
        end = chunk[-1][1]
        if n + 1 < len(chunks):
            end = min(end + .35, chunks[n + 1][0][0])   # stay up a moment, but never overlap the next one
        texts = [(c[2].upper() if uppercase else c[2]) for c in chunk]
        if highlight:
            for i, word in enumerate(chunk):
                stop = chunk[i + 1][0] if i + 1 < len(chunk) else end
                text = " ".join((f"{{\\c{highlight}&}}{_ass_text(t)}{{\\c{primary}&}}" if j == i else _ass_text(t))
                                for j, t in enumerate(texts))
                lines.append(f"Dialogue: 0,{_ass_time(word[0])},{_ass_time(stop)},Caption,,0,0,0,,{text}")
        else:
            lines.append(f"Dialogue: 0,{_ass_time(chunk[0][0])},{_ass_time(end)},Caption,,0,0,0,,"
                         + _ass_text(" ".join(texts)))
    return lines


def _ass_file(path: str, w: int, h: int, caption_style: str, caption_position: str, caption_lines: list[str],
              texts: list[dict]) -> None:
    font, size, bold, primary, outline_c, outline, _ = CAPTION_STYLES.get(caption_style, CAPTION_STYLES["bold"])
    align = {"bottom": 2, "middle": 5, "top": 8}.get(caption_position, 2)
    margin = int(h * (.16 if h > w else .08)) if align != 5 else 0
    border = 3 if caption_style == "boxed" else 1
    head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding",
            f"Style: Caption,{font},{int(h * size)},{primary},&H000000FF,{outline_c},{outline_c},{-bold},0,0,0,100,"
            f"100,0,0,{border},{outline},0,{align},{int(w * .06)},{int(w * .06)},{margin},1",
            f"Style: Title,Arial Black,{int(h * .07)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,"
            f"0,1,{max(3, h // 270)},2,8,{int(w * .05)},{int(w * .05)},{int(h * .08)},1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    events = list(caption_lines)
    for t in texts:
        where = {"top": 8, "center": 5, "middle": 5, "bottom": 2}.get(str(t.get("position", "top")).lower(), 8)
        scale = {"small": .7, "medium": 1, "large": 1.35}.get(str(t.get("size", "medium")).lower(), 1)
        tags = f"{{\\an{where}\\fad(250,250)" + (f"\\fs{int(h * .07 * scale)}" if scale != 1 else "") + "}"
        events.append(f"Dialogue: 1,{_ass_time(float(t.get('start', 0)))},{_ass_time(float(t['end']))},Title,,0,0,0,,"
                      + tags + _ass_text(str(t.get("text", ""))))
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(head + events) + "\n")


def _atempo(speed: float) -> str:
    steps, s = [], speed
    while s > 2.0:
        steps.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        steps.append("atempo=0.5")
        s /= 0.5
    steps.append(f"atempo={s:.4f}")
    return ",".join(steps)


def _size(aspect: str, short: int, first: dict) -> tuple[int, int]:
    """Frame size: `short` is the short side, so 9:16 at 1080 is 1080x1920 and 16:9 at 1080 is 1920x1080."""
    if aspect in ASPECTS:
        aw, ah = ASPECTS[aspect]
        w, h = (short, round(short * ah / aw)) if aw <= ah else (round(short * aw / ah), short)
    else:
        height = short
        v = first["video"]
        scale = height / min(v["width"], v["height"])
        w, h = int(v["width"] * scale), int(v["height"] * scale)
    return w - w % 2, h - h % 2


def render(plan: dict, cancel: threading.Event | None, progress) -> str:
    clips = plan.get("clips") or []
    if not clips:
        raise ValueError("The plan has no clips. Give at least one {file, start, end}.")
    if len(clips) > MAX_CLIPS:
        raise ValueError(f"Too many clips ({len(clips)}); the limit is {MAX_CLIPS}.")
    sources, infos = [], {}
    for c in clips:
        real = _source(str(c.get("file", "")))
        if real not in infos:
            infos[real] = probe(real)
            sources.append(real)
        c["_file"] = real
    first = infos[clips[0]["_file"]]
    if not first["video"]:
        raise ValueError(f"{os.path.basename(clips[0]['_file'])} has no video.")
    fmt = str(plan.get("format", "mp4")).lower().lstrip(".")
    aspect = str(plan.get("aspect", "original"))
    height = int(plan.get("height") or 1080)
    height = max(240, min(height, 2160))
    w, h = _size(aspect, height, first)
    fps = float(plan.get("fps") or min(60.0, first["video"]["fps"] or 30.0) or 30.0)
    fit = str(plan.get("fit", "blur" if aspect in ("9:16", "4:5", "1:1") else "crop")).lower()

    # the timeline, and the captions mapped onto it
    timeline, cursor = [], 0.0
    for c in clips:
        info = infos[c["_file"]]
        start = max(0.0, float(c.get("start", 0)))
        end = min(float(c.get("end", info["duration"])), info["duration"])
        if end - start < 0.05:
            raise ValueError(f"A clip from {os.path.basename(c['_file'])} is empty ({start}-{end}).")
        speed = max(0.25, min(float(c.get("speed", 1) or 1), 4.0))
        timeline.append((c, start, end, speed, cursor))
        cursor += (end - start) / speed
    total = cursor
    captions = plan.get("captions") or {}
    if isinstance(captions, bool):
        captions = {"style": "bold"} if captions else {}
    caption_lines = []
    style = str(captions.get("style", "bold")).lower()
    if captions and style != "none":
        style = style if style in CAPTION_STYLES else "bold"
        words = []
        for c, start, end, speed, at in timeline:
            for ws, we, word in _transcript(c["_file"], cancel)["words"]:
                if ws >= start - .05 and we <= end + .05:
                    words.append([at + (max(ws, start) - start) / speed, at + (min(we, end) - start) / speed, word])
        caption_lines = _captions(words, style, str(captions.get("position", "bottom")),
                                  max(1, min(int(captions.get("words_per_line", 3 if h > w else 6)), 12)),
                                  bool(captions.get("uppercase", style == "bold")), w, h)
    texts = [t for t in (plan.get("texts") or []) if str(t.get("text", "")).strip() and "end" in t]

    out_ext = {"mp4": ".mp4", "mov": ".mov", "mp3": ".mp3", "gif": ".gif"}.get(fmt, ".mp4")
    out = _output(plan.get("name") or os.path.splitext(os.path.basename(sources[0]))[0] + " edit", out_ext)
    with tempfile.TemporaryDirectory() as tmp:
        args, graph = [], []
        for src in sources:
            args += ["-i", src]
        index = {src: i for i, src in enumerate(sources)}
        music = plan.get("music") or {}
        music_file = _source(str(music["file"])) if music.get("file") else None
        if music_file:
            args += ["-stream_loop", "-1", "-i", music_file]
        for k, (c, start, end, speed, _) in enumerate(timeline):
            i = index[c["_file"]]
            pts = f"(PTS-STARTPTS)/{speed}" if speed != 1 else "PTS-STARTPTS"
            v = f"[{i}:v]trim=start={start:.3f}:end={end:.3f},setpts={pts},fps={fps:g},"
            if fit == "pad":
                v += f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
            elif fit == "blur":
                graph.append(v + f"split[bg{k}][fg{k}]")
                graph.append(f"[bg{k}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=24:4,"
                             f"eq=brightness=-0.08[b{k}]")
                graph.append(f"[fg{k}]scale={w}:{h}:force_original_aspect_ratio=decrease[f{k}]")
                v = f"[b{k}][f{k}]overlay=(W-w)/2:(H-h)/2"
            else:
                v += f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
            graph.append(v + f",setsar=1,format=yuv420p[v{k}]")
            vol = 0 if c.get("mute") else float(c.get("volume", 1) or 1)
            if infos[c["_file"]]["audio"]:
                a = f"[{i}:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
                if speed != 1:
                    a += "," + _atempo(speed)
            else:
                a = f"anullsrc=r=48000:cl=stereo,atrim=duration={(end - start) / speed:.3f}"
            graph.append(a + f",volume={vol},aformat=sample_rates=48000:channel_layouts=stereo[a{k}]")
        graph.append("".join(f"[v{k}][a{k}]" for k in range(len(timeline)))
                     + f"concat=n={len(timeline)}:v=1:a=1[cv][ca]")
        video = "[cv]"
        if caption_lines or texts:
            _ass_file(os.path.join(tmp, "text.ass"), w, h, style, str(captions.get("position", "bottom")),
                      caption_lines, texts)
            graph.append(f"{video}subtitles=text.ass[sv]")   # a relative name: ffmpeg runs in the temp folder
            video = "[sv]"
        fade_in, fade_out = float(plan.get("fade_in", 0) or 0), float(plan.get("fade_out", 0) or 0)
        if fade_in or fade_out:
            f = []
            if fade_in:
                f.append(f"fade=t=in:st=0:d={fade_in:.2f}")
            if fade_out:
                f.append(f"fade=t=out:st={max(0.0, total - fade_out):.2f}:d={fade_out:.2f}")
            graph.append(f"{video}{','.join(f)}[fv]")
            video = "[fv]"
        audio = "[ca]"
        if music_file:
            m = len(sources)
            level = max(0.0, min(float(music.get("volume", 0.15)), 1.0))
            graph.append(f"[{m}:a]atrim=duration={total:.3f},volume={level},"
                         "aformat=sample_rates=48000:channel_layouts=stereo[mu]")
            if music.get("duck", True):
                graph.append("[ca]asplit[voice][key]")
                graph.append("[mu][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[ducked]")
                graph.append("[voice][ducked]amix=inputs=2:duration=first:normalize=0[mx]")
            else:
                graph.append("[ca][mu]amix=inputs=2:duration=first:normalize=0[mx]")
            audio = "[mx]"
        a_filters = []
        if plan.get("normalize", True):
            a_filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        if fade_in:
            a_filters.append(f"afade=t=in:st=0:d={fade_in:.2f}")
        if fade_out:
            a_filters.append(f"afade=t=out:st={max(0.0, total - fade_out):.2f}:d={fade_out:.2f}")
        if a_filters:
            graph.append(f"{audio}{','.join(a_filters)},aresample=48000[fa]")
            audio = "[fa]"
        if out_ext == ".gif":
            graph.append(f"{video}fps=12,scale='min(480,iw)':-2,split[g1][g2]")
            graph.append("[g1]palettegen=stats_mode=diff[pal]")
            graph.append("[g2][pal]paletteuse=dither=sierra2_4a[gif]")
            graph.append(f"{audio}anullsink")   # nothing may be left unconnected
            maps = ["-map", "[gif]"]
        elif out_ext == ".mp3":
            graph.append(f"{video}nullsink")
            maps = ["-map", audio, "-c:a", "libmp3lame", "-b:a", "192k"]
        else:
            quality = str(plan.get("quality", "high")).lower()
            if quality == "phone":
                kbps = max(400, min(6000, int(PHONE_MB * 8 * 1024 / max(total, 1) - 160)))  # small, still sharp
                vcodec = ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 2}k",
                          "-bufsize", f"{kbps * 2}k"]
            else:
                crf = {"draft": "28", "high": "20", "best": "17"}.get(quality, "20")
                vcodec = ["-c:v", "libx264", "-preset", "veryfast" if quality == "draft" else "medium", "-crf", crf]
            maps = ["-map", video, "-map", audio] + vcodec + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        script = os.path.join(tmp, "graph.txt")
        with open(script, "w", encoding="utf-8") as f:
            f.write(";\n".join(graph))
        # ffmpeg works in the temp folder so the subtitles filter finds text.ass by a plain relative name
        # (a Windows path there would need escaping)
        try:
            _run(args + ["-/filter_complex", script] + maps + ["-t", f"{total:.3f}", out],
                 cancel, progress, total, cwd=tmp)
        except Exception:
            if os.path.exists(out):  # no half-written file left behind
                os.remove(out)
            raise
    info = probe(out)
    notes = []
    if captions and style != "none" and not caption_lines:
        notes.append("no speech was found for captions")
    if out_ext not in (".gif", ".mp3") and info["size_mb"] > 50:
        notes.append("over 50 MB, too big to send by Telegram (render again with quality 'phone' to send it)")
    return (f"Rendered {out}\n{_describe(out, info)}" + (f"\nNote: {'; '.join(notes)}." if notes else ""))


# ---------- the tools ----------

def video_tools(agent, tool, text_prop) -> dict:
    """The toolkit for standing.py: {name: (schema, handler)}."""
    TEXT = text_prop

    def info(i: dict) -> str:
        path = i.get("path", "").strip()
        real = files.resolve(path)
        if os.path.isdir(real):
            found = [os.path.join(real, f) for f in sorted(os.listdir(real)) if f.lower().endswith(VIDEO_EXTS)]
            if not found:
                return f"No videos in {real}."
            out = []
            for f in found[:40]:
                try:
                    out.append(_describe(f, probe(_source(f))))
                except Exception as e:
                    out.append(f"{os.path.basename(f)}: couldn't read ({e})")
            return f"Videos in {real}:\n" + "\n".join(out)
        src = _source(path)
        return _describe(src, probe(src))

    def look(i: dict):
        src = _source(i.get("path", ""))
        duration = probe(src)["duration"]
        start = max(0.0, float(i.get("start", 0) or 0))
        end = min(float(i.get("end") or duration), duration)
        times = [float(t) for t in (i.get("times") or [])][:MAX_LOOK_FRAMES]
        if not times:
            count = max(1, min(int(i.get("count", 6) or 6), MAX_LOOK_FRAMES))
            span = max(0.0, end - start)
            times = [start + span * (n + .5) / count for n in range(count)]
        times = [min(max(0.0, t), max(0.0, duration - .05)) for t in times]
        sheet = contact_sheet(src, times)
        return _image(sheet, f"{len(times)} frames from {os.path.basename(src)} (timestamps in the corners).")

    def transcribe(i: dict) -> str:
        src = _source(i.get("path", ""))
        detail = str(i.get("detail", "lines")).lower()

        def work(job):
            t = _transcript(src, agent.cancelled)
            if not t["segments"]:
                return f"No speech found in {os.path.basename(src)}."
            if detail == "words":
                body = " ".join(f"[{w[0]:.1f}]{w[2]}" for w in t["words"])
            else:
                body = "\n".join(f"[{_clock(a)} - {_clock(b)}] {text}" for a, b, text in t["segments"])
            if len(body) > 30000:
                body = body[:30000] + "\n[... cut: ask for a time range with video_look, or work in parts ...]"
            return f"Transcript of {os.path.basename(src)} ({t['language']}):\n{body}"

        return _start_job(agent, "transcribe", f"Transcribing {os.path.basename(src)}", work)

    def silences(i: dict) -> str:
        src = _source(i.get("path", ""))
        parts, duration = speech_parts(src, float(i.get("min_silence", .6) or .6),
                                       float(i.get("threshold_db", -35) or -35), float(i.get("pad", .12) or .12))
        kept = sum(b - a for a, b in parts)
        listed = json.dumps(parts[:400])
        return (f"{len(parts)} parts with sound, {_clock(kept)} of {_clock(duration)} "
                f"({duration - kept:.1f}s of silence would be cut). As clips ([start, end] seconds):\n{listed}"
                + ("\n(first 400 shown)" if len(parts) > 400 else ""))

    def scenes(i: dict) -> str:
        src = _source(i.get("path", ""))
        threshold = max(.1, min(float(i.get("threshold", .3) or .3), .9))

        def work(job):
            cuts = scene_cuts(src, threshold, agent.cancelled)
            return (f"{len(cuts)} scene changes in {os.path.basename(src)} (seconds): {cuts[:300]}"
                    if cuts else f"No clear scene changes in {os.path.basename(src)} at threshold {threshold}.")

        return _start_job(agent, "scenes", f"Finding scenes in {os.path.basename(src)}", work)

    def do_render(i: dict) -> str:
        plan = i.get("plan") if isinstance(i.get("plan"), dict) else i
        if isinstance(plan, str):
            plan = json.loads(plan)
        name = plan.get("name") or "edit"

        def work(job):
            return render(plan, agent.cancelled, lambda p: job.update(progress=p))

        return _start_job(agent, "render", f"Rendering '{name}'", work)

    def thumbnail(i: dict) -> str:
        src = _source(i.get("path", ""))
        t = float(i.get("time", 0) or 0)
        out = _output(i.get("name") or os.path.splitext(os.path.basename(src))[0] + " thumbnail", ".jpg")
        title = str(i.get("title", "")).strip()
        _run(["-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-q:v", "2", out])
        if title:
            from PIL import Image, ImageDraw, ImageFont

            image = Image.open(out).convert("RGB")
            draw = ImageDraw.Draw(image)
            size = max(24, image.width // 11)
            try:
                font = ImageFont.truetype("ariblk.ttf", size)
            except OSError:
                font = ImageFont.load_default()
            box = draw.multiline_textbbox((0, 0), title, font=font, stroke_width=size // 10)
            x = (image.width - box[2]) // 2
            y = image.height - box[3] - image.height // 12
            draw.multiline_text((x, y), title, font=font, fill="white", stroke_width=size // 10, stroke_fill="black",
                                align="center")
            image.save(out, quality=92)
        return f"Saved {out}"

    def jobs(i: dict) -> str:
        with _jobs_lock:
            mine = [j for j in _jobs.values() if j["agent"] == agent.name][-10:]
        if not mine:
            return "No video jobs yet."
        return "\n".join(f"{j['id']}: {j['label']} - {j['status']}"
                         + (f" {j['progress']:.0%}" if j["status"] == "running" else "")
                         + (f" ({int(time.time() - j['started'])}s)" if j["status"] == "running" else "")
                         + (f"\n  {j['result'][:300]}" if j["status"] != "running" else "") for j in mine)

    path_prop = {"type": "string", "description": "Path to a video file (find it with find_files)"}
    clip = {"type": "object", "properties": {
        "file": TEXT, "start": {"type": "number", "description": "seconds"}, "end": {"type": "number"},
        "speed": {"type": "number", "description": "0.25-4, default 1"}, "volume": {"type": "number"},
        "mute": {"type": "boolean"}}, "required": ["file"]}
    overlay = {"type": "object", "properties": {
        "text": TEXT, "start": {"type": "number", "description": "seconds on the finished video"},
        "end": {"type": "number"}, "position": {"type": "string", "description": "top, center or bottom"},
        "size": {"type": "string", "description": "small, medium or large"}}, "required": ["text", "end"]}
    plan = {"type": "object", "properties": {
        "name": {"type": "string", "description": "File name for the result (no folder)"},
        "clips": {"type": "array", "items": clip, "description": "In order; cut a long video by listing the parts "
                  "to keep. Same file many times is fine."},
        "aspect": {"type": "string", "description": "original, 16:9, 9:16 (Shorts, Reels, TikTok), 1:1, 4:5"},
        "fit": {"type": "string", "description": "crop (fill, cut edges), pad (black bars) or blur (blurred "
                "background; the default for vertical and square)"},
        "height": {"type": "integer", "description": "short side in pixels: 720, 1080 (default) or 2160"},
        "fps": {"type": "number"},
        "captions": {"type": "object", "description": "Burned-in captions from the speech. {style: bold (big, "
                     "word-by-word highlight, for Shorts) | clean | boxed | none, position: bottom | middle | top, "
                     "words_per_line, uppercase}",
                     "properties": {"style": TEXT, "position": TEXT, "words_per_line": {"type": "integer"},
                                    "uppercase": {"type": "boolean"}}},
        "texts": {"type": "array", "items": overlay, "description": "Titles and text on screen"},
        "music": {"type": "object", "description": "{file, volume (0-1, default 0.15), duck (lower it under "
                  "speech, default true)}", "properties": {"file": TEXT, "volume": {"type": "number"},
                                                           "duck": {"type": "boolean"}}},
        "fade_in": {"type": "number", "description": "seconds"}, "fade_out": {"type": "number"},
        "normalize": {"type": "boolean", "description": "even out loudness for social media (default true)"},
        "quality": {"type": "string", "description": "draft (fast preview), high (default), best, or phone "
                    "(sized to send by Telegram, under 50 MB)"},
        "format": {"type": "string", "description": "mp4 (default), mov, gif or mp3 (audio only)"}},
        "required": ["clips"]}
    return {
        "video_info": (tool("video_info", "Length, resolution, frame rate and audio of a video, or of every video in "
                            "a folder.", {"path": {"type": "string", "description": "A video file or a folder"}},
                            ["path"]), info),
        "video_look": (tool("video_look", "See frames from a video as one image with timestamps: evenly spread "
                            "(count, optionally between start and end) or at exact times. Use it to check shots, "
                            "framing, where scenes change, and your own renders.",
                            {"path": path_prop, "count": {"type": "integer", "description": "1-12, default 6"},
                             "start": {"type": "number"}, "end": {"type": "number"},
                             "times": {"type": "array", "items": {"type": "number"}}}, ["path"]), look),
        "video_transcribe": (tool("video_transcribe", "What's said in a video, with timestamps (cached, so asking "
                                  "again is instant). detail: lines (default) or words.",
                                  {"path": path_prop, "detail": TEXT}, ["path"]), transcribe),
        "video_find_silences": (tool("video_find_silences", "The parts of a video that have sound, as [start, end] "
                                     "clips, to cut out pauses (jump cuts) by rendering just these.",
                                     {"path": path_prop, "min_silence": {"type": "number", "description":
                                      "shortest pause to cut, seconds (default 0.6)"},
                                      "threshold_db": {"type": "number", "description": "quieter than this is "
                                                       "silence (default -35; -30 cuts more)"},
                                      "pad": {"type": "number", "description": "seconds kept around speech "
                                              "(default 0.12)"}}, ["path"]), silences),
        "video_find_scenes": (tool("video_find_scenes", "The times where the shot changes in a video.",
                                   {"path": path_prop, "threshold": {"type": "number", "description":
                                    "0.1-0.9, lower finds more (default 0.3)"}}, ["path"]), scenes),
        "video_render": (tool("video_render", "Render an edit to a NEW file in Videos\\Jarvis Edits (sources are "
                              "never changed): clips in order (trims, speed), aspect ratio for each platform, "
                              "burned-in captions from the speech, titles, background music that ducks under "
                              "speech, fades and loudness. Long renders finish in the background and message you.",
                              {"plan": plan}, ["plan"]), do_render),
        "video_thumbnail": (tool("video_thumbnail", "Save a frame as a JPEG thumbnail, optionally with a big title "
                                 "on it.", {"path": path_prop, "time": {"type": "number", "description": "seconds"},
                                            "title": TEXT, "name": TEXT}, ["path"]), thumbnail),
        "video_jobs": (tool("video_jobs", "Your renders and transcripts: running (with progress), done or failed."),
                       jobs),
    }
