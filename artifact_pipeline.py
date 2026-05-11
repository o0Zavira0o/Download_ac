#!/usr/bin/env python3
"""
artifact_pipeline.py

Phase 1:
    - Generic pipeline for handling large binary artifacts:
      * Download from HTTP/HTTPS URL
      * Compress as .zip
      * Split into <=100MB numbered chunks
      * Optionally commit chunks into the git repo

Phase 2 (YouTube‑specialized ingestion):
    - ingest_from_youtube(youtube_url, ...):
      * Use pytube to pick the best available stream
      * Prefer progressive (video+audio) mp4 at highest resolution
      * Fallback: best video‑only + best audio‑only, then merge via ffmpeg
      * Feed the final merged video into the same compression/splitting pipeline

CLI modes:
    * HTTP generic:
        python artifact_pipeline.py \
            --url "https://example.com/large-file.bin" \
            --output-prefix my_artifact \
            --chunk-size-mb 95 \
            --commit

    * YouTube specialized:
        python artifact_pipeline.py \
            --youtube-url "https://www.youtube.com/watch?v=XXXX" \
            --output-prefix my_video \
            --chunk-size-mb 95 \
            --commit

Requirements:
    pip install requests pytube
    # و روی runner (مثلاً ubuntu-latest):
    sudo apt-get update && sudo apt-get install -y ffmpeg
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple
from urllib.parse import urlparse

import requests
import zipfile


# ----------------- Constants & configuration -----------------

# Base directory under the repo where artifacts will be stored
ARTIFACT_ROOT = Path("large_artifacts")

# Default maximum size for each chunk (MiB); must stay below GitHub's 100 MB limit
DEFAULT_CHUNK_SIZE_MB = 95

# Streaming download chunk size for HTTP (in bytes)
HTTP_STREAM_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB


# ----------------- Data structures -----------------


@dataclass
class PipelineResult:
    """
    Represents the result of processing a single large artifact.

    Notes:
        raw_file and zip_file may have been removed during cleanup
        (we keep the paths for metadata/debugging).
    """
    url: str
    output_prefix: str
    raw_file: Path
    zip_file: Path
    chunk_files: List[Path]


# ----------------- Helper functions -----------------


def ensure_dir(path: Path) -> Path:
    """Create directory if needed and return the Path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def slug_from_url(url: str, max_len: int = 40) -> str:
    """
    Derive a filesystem‑safe slug from a URL.

    - Uses last path component if present; otherwise hostname.
    - Falls back to a short hash if necessary.
    """
    parsed = urlparse(url)
    candidate = Path(parsed.path).name or parsed.netloc or "artifact"

    # Very simple slugify: keep ASCII letters, digits, underscore, dash, dot
    safe = "".join(c for c in candidate if c.isalnum() or c in "-_.")
    if not safe:
        safe = "artifact"

    if len(safe) > max_len:
        # Shorten but append a small hash to keep uniqueness
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        safe = safe[: max_len - 9] + "_" + h

    return safe


def slug_from_text(text: str, max_len: int = 60) -> str:
    """Filesystem‑safe slug from arbitrary text (e.g., YouTube title)."""
    text = (text or "").strip()
    if not text:
        return ""
    safe = "".join(c for c in text if c.isalnum() or c in "-_.")
    if not safe:
        return ""
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe


def download_stream(url: str, dest_path: Path) -> None:
    """
    Stream‑download `url` into `dest_path` using HTTP.

    The entire file is never held in memory at once; we write incremental chunks.
    """
    print(f"[download] Starting download from: {url}")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        if total:
            print(f"[download] Reported size: {total / (1024*1024):.2f} MiB")

        bytes_downloaded = 0
        with dest_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=HTTP_STREAM_CHUNK_SIZE):
                if not chunk:
                    continue
                f.write(chunk)
                bytes_downloaded += len(chunk)

                # Optional progress logging
                if total:
                    pct = bytes_downloaded * 100 / total
                    sys.stdout.write(
                        f"\r[download] Downloaded {bytes_downloaded / (1024*1024):.2f} MiB "
                        f"({pct:.1f}%)"
                    )
                    sys.stdout.flush()
        if total:
            print()  # newline after progress
    print(
        f"[download] Finished. Saved to: {dest_path} "
        f"({dest_path.stat().st_size / (1024*1024):.2f} MiB)"
    )


def compress_to_zip(src_path: Path, zip_path: Path) -> None:
    """
    Compress `src_path` into a zip archive at `zip_path`.

    The artifact is stored with `arcname` equal to its basename (no directories).
    """
    print(f"[zip] Compressing {src_path} -> {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(src_path, arcname=src_path.name)
    print(f"[zip] Done. Zip size: {zip_path.stat().st_size / (1024*1024):.2f} MiB")


def split_file(path: Path, chunk_size_mb: int) -> List[Path]:
    """
    Split `path` into numbered chunks with maximum size `chunk_size_mb` MiB.

    Chunks are created in the same directory as `path` and named:

        {path.name}.001, {path.name}.002, ...

    Returns:
        List of chunk file paths (sorted by index).
    """
    chunk_size_bytes = int(chunk_size_mb * 1024 * 1024)
    if chunk_size_bytes <= 0:
        raise ValueError("chunk_size_mb must be > 0")

    print(f"[split] Splitting {path} into chunks of at most {chunk_size_mb} MiB")

    chunk_paths: List[Path] = []
    index = 1
    bytes_written_total = 0

    with path.open("rb") as src:
        while True:
            chunk_data = src.read(chunk_size_bytes)
            if not chunk_data:
                break

            suffix = f".{index:03d}"
            chunk_path = path.with_name(path.name + suffix)

            with chunk_path.open("wb") as dst:
                dst.write(chunk_data)

            size_mb = chunk_path.stat().st_size / (1024 * 1024)
            print(f"[split]   -> {chunk_path} ({size_mb:.2f} MiB)")
            chunk_paths.append(chunk_path)

            bytes_written_total += len(chunk_data)
            index += 1

    print(
        f"[split] Completed. Total chunks: {len(chunk_paths)} "
        f"(~{bytes_written_total / (1024*1024):.2f} MiB in chunks)"
    )
    return chunk_paths


def git_commit_files(files: Iterable[Path], message: str) -> None:
    """
    Stage and commit the specified files using the local git repo.

    Assumes:
        - We are inside a valid git working tree.
        - User/Action has already configured user.name / user.email if needed.
    """
    files = list(files)
    if not files:
        print("[git] No files to commit; skipping git operations.")
        return

    # Convert to relative POSIX paths for nicer git output
    rel_paths = [f.as_posix() for f in files]

    print(f"[git] Staging {len(rel_paths)} file(s)")
    subprocess.run(["git", "add", "--"] + rel_paths, check=True)

    print(f"[git] Committing with message: {message!r}")
    result = subprocess.run(
        ["git", "commit", "-m", message],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Common case: "nothing to commit"
        print("[git] git commit exited with code", result.returncode)
        if result.stdout:
            print("[git] stdout:\n", result.stdout)
        if result.stderr:
            print("[git] stderr:\n", result.stderr)
        print("[git] Continuing without raising; assume no-op commit.")
    else:
        print("[git] Commit created successfully.")


# ----------------- Core compression/splitting pipeline -----------------


def _compress_split_commit(
    src_path: Path,
    output_prefix: str,
    chunk_size_mb: int,
    do_commit: bool,
    cleanup_src: bool,
    url_meta: str,
) -> PipelineResult:
    """
    Internal helper that:
      - Compresses src_path to {output_prefix}.zip under ARTIFACT_ROOT
      - Splits the zip into <=chunk_size_mb MiB chunks
      - Optionally commits the chunks
      - Optionally removes src_path and the zip

    This is the common core used by:
      * process_large_artifact (HTTP)
      * process_large_artifact_from_local (e.g., YouTube ingestion)
    """
    ensure_dir(ARTIFACT_ROOT)

    zip_path = ARTIFACT_ROOT / f"{output_prefix}.zip"
    compress_to_zip(src_path, zip_path)

    if cleanup_src:
        try:
            size_mb = src_path.stat().st_size / (1024 * 1024)
            src_path.unlink()
            print(f"[cleanup] Removed source file ({size_mb:.2f} MiB) at {src_path}")
        except FileNotFoundError:
            pass

    chunk_files = split_file(zip_path, chunk_size_mb=chunk_size_mb)

    # Optionally remove the single zip archive and keep only chunks
    try:
        zip_size = zip_path.stat().st_size
        zip_path.unlink()
        print(
            f"[cleanup] Removed zip archive "
            f"({zip_size / (1024*1024):.2f} MiB) at {zip_path}"
        )
    except FileNotFoundError:
        pass

    if do_commit:
        msg = f"Add artifact chunks for {output_prefix}"
        git_commit_files(chunk_files, msg)

    return PipelineResult(
        url=url_meta or str(src_path),
        output_prefix=output_prefix,
        raw_file=src_path,
        zip_file=zip_path,
        chunk_files=chunk_files,
    )


def process_large_artifact(
    url: str,
    output_prefix: str | None = None,
    chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
    do_commit: bool = False,
) -> PipelineResult:
    """
    Full end‑to‑end pipeline for a generic HTTP/HTTPS artifact:

      1. Download URL into local workspace
      2. Zip the downloaded file
      3. Split the zip into <=chunk_size_mb MiB chunks
      4. Optionally git‑commit the resulting chunks

    Args:
        url:             Direct HTTP/HTTPS URL to the large binary resource.
        output_prefix:   Base name for all generated files (without extension).
                         If None, derived automatically from the URL.
        chunk_size_mb:   Max size of each chunk in mebibytes (MiB).
        do_commit:       If True, stage & commit chunk files into git repo.
    """
    if not output_prefix:
        output_prefix = slug_from_url(url)

    ensure_dir(ARTIFACT_ROOT)
    raw_path = ARTIFACT_ROOT / f"{output_prefix}.bin"

    download_stream(url, raw_path)

    # Use shared core
    return _compress_split_commit(
        src_path=raw_path,
        output_prefix=output_prefix,
        chunk_size_mb=chunk_size_mb,
        do_commit=do_commit,
        cleanup_src=True,
        url_meta=url,
    )


def process_large_artifact_from_local(
    src_path: Path,
    output_prefix: str,
    chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
    do_commit: bool = False,
    cleanup_src: bool = False,
    url_meta: str | None = None,
) -> PipelineResult:
    """
    Same compression/splitting pipeline, but starting from an existing local file.

    This is what YouTube ingestion uses once it has produced a final merged video.

    Args:
        src_path:        Path to existing local file (e.g., merged mp4).
        output_prefix:   Base name to use for the zip/chunk artifacts.
        chunk_size_mb:   Max size of each chunk (MiB).
        do_commit:       If True, stage & commit chunk files into git repo.
        cleanup_src:     If True, delete src_path after successful zipping.
        url_meta:        Optional "source" string to store in PipelineResult.url
                         (e.g. original YouTube URL).
    """
    return _compress_split_commit(
        src_path=src_path,
        output_prefix=output_prefix,
        chunk_size_mb=chunk_size_mb,
        do_commit=do_commit,
        cleanup_src=cleanup_src,
        url_meta=url_meta or f"file://{src_path}",
    )


# ----------------- YouTube specialized ingestion -----------------


def ingest_from_youtube(
    youtube_url: str,
    output_prefix: str | None = None,
    tmp_root: Path | None = None,
) -> Tuple[Path, str]:
    """
    Specialized ingestion for a YouTube video URL using pytube + ffmpeg.

    Logic:
      1. Build pytube.YouTube object from the URL
      2. If available, select the best progressive mp4 stream
         (contains both audio & video) with highest resolution
      3. Otherwise:
         * select highest resolution video‑only mp4 stream
         * select best audio‑only stream (prefer mp4, else any)
         * download both and merge them using `ffmpeg -c copy` into
           a single mp4 file
      4. Return the path to the final video file + the effective output_prefix

    The returned video path can be passed directly into
    process_large_artifact_from_local(...) to enter the main pipeline.
    """
    try:
        from pytube import YouTube
    except ImportError as e:
        raise RuntimeError(
            "pytube is required for YouTube ingestion; "
            "install it with `pip install pytube`."
        ) from e

    print(f"[yt] Initializing pytube for URL: {youtube_url}")
    try:
        yt = YouTube(youtube_url)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Failed to initialize YouTube object: {e}") from e

    base_name = (
        output_prefix
        or slug_from_text(yt.title)
        or slug_from_url(youtube_url)
    )

    if tmp_root is None:
        tmp_root = ARTIFACT_ROOT / "youtube_tmp" / base_name
    ensure_dir(tmp_root)

    print(f"[yt] Video title: {yt.title}")
    print(f"[yt] Output base name: {base_name}")
    print(f"[yt] Temporary directory: {tmp_root}")

    # 1) Try progressive highest resolution mp4
    progressive = (
        yt.streams.filter(progressive=True, file_extension="mp4")
        .order_by("resolution")
        .desc()
        .first()
    )

    if progressive:
        print(
            f"[yt] Using progressive stream: itag={progressive.itag}, "
            f"res={progressive.resolution}, mime={progressive.mime_type}"
        )
        out_path_str = progressive.download(
            output_path=str(tmp_root),
            filename=base_name,
        )
        final_video_path = Path(out_path_str)
        print(f"[yt] Downloaded progressive stream to {final_video_path}")
        return final_video_path, base_name

    # 2) Fallback: separate video + audio
    print("[yt] No suitable progressive mp4 stream found; "
          "downloading video and audio separately...")

    video_stream = (
        yt.streams.filter(only_video=True, file_extension="mp4")
        .order_by("resolution")
        .desc()
        .first()
    )
    if video_stream is None:
        raise RuntimeError("No suitable video‑only mp4 stream found for this YouTube URL.")

    # Prefer audio in mp4 container, fall back to any audio otherwise
    audio_stream = (
        yt.streams.filter(only_audio=True, file_extension="mp4")
        .order_by("abr")
        .desc()
        .first()
        or yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    )
    if audio_stream is None:
        raise RuntimeError("No suitable audio‑only stream found for this YouTube URL.")

    print(
        f"[yt] Selected video‑only stream: itag={video_stream.itag}, "
        f"res={video_stream.resolution}, mime={video_stream.mime_type}"
    )
    print(
        f"[yt] Selected audio‑only stream: itag={audio_stream.itag}, "
        f"abr={audio_stream.abr}, mime={audio_stream.mime_type}"
    )

    video_path = Path(
        video_stream.download(output_path=str(tmp_root), filename=f"{base_name}_video")
    )
    audio_path = Path(
        audio_stream.download(output_path=str(tmp_root), filename=f"{base_name}_audio")
    )

    print(f"[yt] Downloaded video to: {video_path}")
    print(f"[yt] Downloaded audio to: {audio_path}")

    merged_path = tmp_root / f"{base_name}.mp4"

    # Merge with ffmpeg
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-loglevel",
        "error",
        str(merged_path),
    ]
    print("[yt] Merging video and audio using ffmpeg:")
    print("     ", " ".join(ffmpeg_cmd))

    try:
        subprocess.run(ffmpeg_cmd, check=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "ffmpeg not found. Install it in the runner environment "
            "e.g. `sudo apt-get install -y ffmpeg`."
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg merge failed with exit code {e.returncode}") from e

    print(f"[yt] Final merged video at: {merged_path}")

    # Optional cleanup of intermediate streams
    for p in (video_path, audio_path):
        try:
            p.unlink()
            print(f"[yt] Removed intermediate file: {p}")
        except FileNotFoundError:
            pass

    return merged_path, base_name


# ----------------- CLI entry point -----------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download/ingest, zip, chunk, and (optionally) commit a large artifact.",
    )

    # URL source: either a generic HTTP URL OR a YouTube URL (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--url",
        help="HTTP/HTTPS URL of the large file to ingest (generic mode).",
    )
    group.add_argument(
        "--youtube-url",
        help="YouTube video URL to ingest via pytube + ffmpeg (specialized mode).",
    )

    parser.add_argument(
        "--output-prefix",
        help=(
            "Base name for generated files (without extension). "
            "If omitted: for --url, derived from URL; for --youtube-url, "
            "derived from the video title/URL."
        ),
    )
    parser.add_argument(
        "--chunk-size-mb",
        type=int,
        default=DEFAULT_CHUNK_SIZE_MB,
        help=(
            f"Maximum size of each chunk in MiB (default: {DEFAULT_CHUNK_SIZE_MB}). "
            "Must be less than GitHub's 100 MB file limit."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="If set, stage and commit generated chunk files into the repo.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        if args.youtube_url:
            # Phase 2 path: YouTube specialized ingestion
            final_video_path, effective_prefix = ingest_from_youtube(
                youtube_url=args.youtube_url,
                output_prefix=args.output_prefix,
            )
            result = process_large_artifact_from_local(
                src_path=final_video_path,
                output_prefix=effective_prefix,
                chunk_size_mb=args.chunk_size_mb,
                do_commit=args.commit,
                cleanup_src=True,  # remove merged mp4 after pipeline
                url_meta=args.youtube_url,
            )
        else:
            # Phase 1 path: generic HTTP URL
            result = process_large_artifact(
                url=args.url,
                output_prefix=args.output_prefix,
                chunk_size_mb=args.chunk_size_mb,
                do_commit=args.commit,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Pipeline failed: {exc}")
        return 1

    print("\n[summary]")
    print(f"  Source:       {result.url}")
    print(f"  Output prefix:{result.output_prefix}")
    print("  Chunk files:")
    for p in result.chunk_files:
        try:
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"    - {p} ({size_mb:.2f} MiB)")
        except FileNotFoundError:
            print(f"    - {p} (file missing at summary time)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
