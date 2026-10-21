"""Match locally downloaded voice lines with their published transcripts.

Run with the GPT-SoVITS Python environment, which has requests, beautifulsoup4,
and imageio-ffmpeg installed. Original MP3 files are never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import subprocess
import wave
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import requests
from bs4 import BeautifulSoup


SOURCE_PAGE = "https://warfarin.wiki/cn/operators/zhuang-fangyi#dialogue"
FIELDS = (
    "filename",
    "title",
    "transcript",
    "duration_seconds",
    "training_status",
    "audio_url",
    "sha256",
)


def read_published_lines() -> dict[str, tuple[str, str, str]]:
    response = requests.get(SOURCE_PAGE, timeout=30)
    response.raise_for_status()
    page = BeautifulSoup(response.text, "html.parser")
    dialogue = page.find(id="dialogue")
    if dialogue is None:
        raise RuntimeError("The source page has no dialogue section")
    lines = {}
    for link in dialogue.select('a[href$=".mp3"]'):
        item = link.find_parent("li")
        spans = item.select("span") if item else []
        if len(spans) < 2:
            continue
        filename = Path(urlparse(link["href"]).path).name
        lines[filename] = (
            spans[0].get_text(" ", strip=True),
            spans[1].get_text(" ", strip=True),
            link["href"],
        )
    return lines


def convert_mp3(source: Path, destination: Path, ffmpeg: str) -> float:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_mtime < source.stat().st_mtime:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-map", "0:a:0", "-ac", "1", "-ar", "32000", "-c:a", "pcm_s16le", str(destination)],
            check=True,
        )
    with wave.open(str(destination), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Folder containing downloaded MP3 files")
    parser.add_argument("--output", type=Path, help="Prepared dataset folder (default: source/prepared)")
    args = parser.parse_args()
    source = args.source.resolve()
    output = (args.output or source / "prepared").resolve()
    published = read_published_lines()
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    rows = []
    for mp3 in sorted(source.glob("*.mp3")):
        title, transcript, url = published.get(mp3.name, ("", "", ""))
        wav = output / "wav" / f"{mp3.stem}.wav"
        duration = convert_mp3(mp3, wav, ffmpeg)
        if not transcript:
            status = "needs_transcript"
        elif duration < 1.0:
            status = "too_short"
        elif duration > 20.0:
            status = "review_long"
        else:
            status = "ready"
        rows.append({
            "filename": mp3.name,
            "title": title,
            "transcript": transcript,
            "duration_seconds": f"{duration:.2f}",
            "training_status": status,
            "audio_url": url,
            "sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
        })
    output.mkdir(parents=True, exist_ok=True)
    with (output / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (output / "training.list").open("w", encoding="utf-8") as stream:
        for row in rows:
            if row["training_status"] == "ready":
                wav = output / "wav" / f"{Path(row['filename']).stem}.wav"
                stream.write(f"{wav}|庄方宜|zh|{row['transcript']}\n")
    counts = {status: sum(row["training_status"] == status for row in rows)
              for status in {row["training_status"] for row in rows}}
    print(f"Source: {source}")
    print(f"Published lines: {len(published)}; local files: {len(rows)}")
    print(f"Prepared WAV: {output / 'wav'}")
    print(f"Manifest: {output / 'manifest.csv'}")
    print(f"Training list: {output / 'training.list'}")
    print(f"Status: {counts}")
    print(f"Total duration: {sum(float(row['duration_seconds']) for row in rows):.1f}s")


if __name__ == "__main__":
    main()
