# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yt-dlp[default,curl-cffi]",
#     "faster-whisper",
# ]
# ///
"""Transcribe Augusta, GA government meeting streams from YouTube.

Downloads audio with yt-dlp and transcribes locally with faster-whisper.

Usage:
    .venv\\Scripts\\python augusta-commission\\fetch_transcripts.py --limit 5
    .venv\\Scripts\\python augusta-commission\\fetch_transcripts.py --video Iq43RtAF4ZM
    # GPU (needs nvidia-cublas-cu12 and nvidia-cudnn-cu12==9.* installed):
    .venv\\Scripts\\python augusta-commission\\fetch_transcripts.py --device cuda
"""

import argparse
import os
import re
import tempfile
import time
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel

CHANNEL_STREAMS = "https://www.youtube.com/user/augustagagov/streams"
OUT_DIR = Path(__file__).parent / "transcripts"
MODEL = "large-v3-turbo"
YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "js_runtimes": {"node": {}},
}


def add_cuda_dlls() -> None:
    # CTranslate2 finds cuBLAS/cuDNN via PATH; pip installs them under site-packages/nvidia/*/bin.
    import nvidia

    dirs = [str(p) for base in nvidia.__path__ for p in Path(base).glob("*/bin")]
    os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])


def list_streams(limit: int) -> list[str]:
    opts = {**YDL_BASE, "extract_flat": True, "playlistend": limit}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL_STREAMS, download=False)
    return [e["id"] for e in info.get("entries", []) if e.get("id")]


def already_done(video_id: str) -> bool:
    return any(OUT_DIR.glob(f"*-{video_id}.md"))


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def download_audio(video_id: str, work_dir: Path) -> tuple[dict, Path]:
    opts = {
        **YDL_BASE,
        "format": "bestaudio/best",
        "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}")
    return info, Path(ydl.prepare_filename(info))


def drop_repeats(segments: list[dict], min_run: int = 3) -> list[dict]:
    """Drop runs of identical segments, Whisper's signature hallucination over silence."""
    kept, run = [], []
    for seg in segments + [{"text": None}]:
        if run and seg["text"] != run[0]["text"]:
            if len(run) < min_run:
                kept += run
            run = []
        run.append(seg)
    return kept


def write_transcript(info: dict, segments: list[dict]) -> Path:
    date = info.get("release_date") or info.get("upload_date") or "unknown"
    pretty_date = f"{date[:4]}-{date[4:6]}-{date[6:]}" if date.isdigit() else date
    path = OUT_DIR / f"{pretty_date}-{slugify(info['title'])}-{info['id']}.md"
    lines = [
        f"# {info['title']}",
        "",
        f"* Date: {pretty_date}",
        f"* Video: <{info['webpage_url']}>",
        f"* Duration: {info.get('duration_string', '')}",
        f"* Transcribed with: {MODEL} (machine-generated, unreviewed)",
        "",
        "## Transcript",
        "",
    ]
    lines += [
        f"[{timestamp(s['start'])}] {s['text'].strip()}" for s in drop_repeats(segments)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=5, help="most recent N streams")
    parser.add_argument("--video", action="append", help="specific video ID (repeatable)")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    video_ids = args.video or list_streams(args.limit)
    if args.device == "cuda":
        add_cuda_dlls()
    compute_type = "float16" if args.device == "cuda" else "int8"
    model = WhisperModel(MODEL, device=args.device, compute_type=compute_type)

    for video_id in video_ids:
        if already_done(video_id):
            print(f"skip {video_id} (already transcribed)")
            continue
        start = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                print(f"download {video_id}", flush=True)
                info, audio = download_audio(video_id, Path(tmp))
                print(f"transcribe {info['title']} ({info.get('duration_string', '?')})", flush=True)
                # Streams open with long silent/music pre-roll that Whisper hallucinates over.
                segments, _ = model.transcribe(
                    str(audio),
                    language="en",
                    condition_on_previous_text=False,
                    word_timestamps=True,
                    hallucination_silence_threshold=2.0,
                )
                # segments is a lazy generator; consume it before the temp audio is deleted.
                segments = [{"start": s.start, "text": s.text} for s in segments]
            path = write_transcript(info, segments)
        except Exception as e:  # keep the batch going past unavailable/scheduled streams
            print(f"FAILED {video_id}: {e}", flush=True)
            continue
        elapsed = time.monotonic() - start
        print(f"wrote {path.name} in {elapsed / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
