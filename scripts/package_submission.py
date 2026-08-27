"""Validate and create the exact two-file archive required for submission."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional


MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_VIDEO_SECONDS = 120.0
REPOSITORY_PATTERN = re.compile(
    r"https://(?:www\.)?(?:github\.com|gitee\.com)/[^\s/]+/[^\s/]+", re.IGNORECASE
)


class SubmissionError(ValueError):
    pass


def chinese_character_count(text: str) -> int:
    return sum("\u4e00" <= char <= "\u9fff" for char in text)


def probe_duration(video: Path) -> Optional[float]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    if completed.returncode != 0:
        raise SubmissionError("ffprobe could not read the video as a valid mp4")
    try:
        return float(completed.stdout.strip())
    except ValueError as exc:
        raise SubmissionError("ffprobe returned an invalid video duration") from exc


def validate_readme(readme: Path) -> str:
    if not readme.is_file():
        raise SubmissionError(f"README.txt not found: {readme}")
    text = readme.read_text(encoding="utf-8")
    count = chinese_character_count(text)
    if count > 1000:
        raise SubmissionError(f"README contains {count} Chinese characters; limit is 1000")
    if "[发布后在此填写" in text or not REPOSITORY_PATTERN.search(text):
        raise SubmissionError("replace the repository placeholder with a public GitHub/Gitee URL")
    return text


def validate_video(video: Path, *, check_duration: bool = True) -> Optional[float]:
    if not video.is_file():
        raise SubmissionError(f"video not found: {video}")
    if video.suffix.lower() != ".mp4":
        raise SubmissionError("video must use the .mp4 extension")
    size = video.stat().st_size
    if size > MAX_VIDEO_BYTES:
        raise SubmissionError(
            f"video is {size / 1024 / 1024:.1f} MB; limit is 200 MB"
        )
    with video.open("rb") as stream:
        signature = stream.read(16)
    if len(signature) < 12 or signature[4:8] != b"ftyp":
        raise SubmissionError("video does not have an MP4 ftyp signature")
    duration = probe_duration(video) if check_duration else None
    if duration is not None and duration > MAX_VIDEO_SECONDS:
        raise SubmissionError(
            f"video is {duration:.1f} seconds; limit is {MAX_VIDEO_SECONDS:.0f} seconds"
        )
    return duration


def build_submission(
    *,
    name: str,
    video: Path,
    readme: Path,
    output_directory: Path,
    check_duration: bool = True,
) -> Path:
    clean_name = name.strip()
    if not clean_name or re.search(r"[\\/:*?\"<>|]", clean_name):
        raise SubmissionError("name is empty or contains a character invalid in filenames")
    validate_readme(readme)
    validate_video(video, check_duration=check_duration)
    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / f"{clean_name}.zip"
    if target.exists():
        raise SubmissionError(f"refusing to overwrite existing archive: {target}")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(readme, arcname="README.txt")
        archive.write(video, arcname=video.name)
    with zipfile.ZipFile(target, "r") as archive:
        names = archive.namelist()
    if names != ["README.txt", video.name]:
        target.unlink(missing_ok=True)
        raise SubmissionError(f"archive content verification failed: {names}")
    return target


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create <name>.zip containing only README.txt and an mp4 video"
    )
    parser.add_argument("--name", required=True, help="your real name; also used as zip filename")
    parser.add_argument("--video", required=True, type=Path, help="path to the final mp4")
    parser.add_argument("--output", type=Path, default=Path("submission"))
    parser.add_argument(
        "--skip-duration-check",
        action="store_true",
        help="skip ffprobe duration validation (size/signature are still checked)",
    )
    return parser


def main(argv=None) -> int:
    args = make_parser().parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        target = build_submission(
            name=args.name,
            video=args.video.resolve(),
            readme=root / "README.txt",
            output_directory=args.output.resolve(),
            check_duration=not args.skip_duration_check,
        )
    except (OSError, SubmissionError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created: {target}")
    print("verified: archive contains exactly README.txt and the mp4 video")
    if not shutil.which("ffprobe") and not args.skip_duration_check:
        print("warning: ffprobe is unavailable; verify the video is no longer than 2 minutes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

