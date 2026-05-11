#!/usr/bin/env python3
"""
artifact_pipeline.py – upgraded version (Cobalt + yt-dlp hybrid)

- PRIMARY: Downloads YouTube videos via Cobalt API (if reachable).
- FALLBACK: Uses yt-dlp if Cobalt fails.
- Generic HTTP download remains available via --url.
- Compresses as .zip, splits into <=95 MiB chunks, and optionally commits.

Requirements (runner):
    pip install requests yt-dlp
    sudo apt-get update && sudo apt-get install -y ffmpeg
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse
import zipfile

import requests


# ----------------- Constants -----------------

ARTIFACT_ROOT = Path("large_artifacts")
DEFAULT_CHUNK_SIZE_MB = 95
HTTP_STREAM_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB

# Cobalt API endpoint (can be overridden via env)
COBALT_API_URL = os.getenv("COBALT_API_URL", "https://co.wuk.sh/api/json")


# ----------------- Data structures -----------------

@dataclass
class PipelineResult:
    url: str
    output_prefix: str
    raw_file: Path
    zip_file: Path
    chunk_files: List[Path]


# ----------------- Helpers (core) -----------------

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def slug_from_url(url: str, max_len: int = 40) -> str:
    parsed = urlparse(url)
    candidate = Path(parsed.path).name or parsed.netloc or "artifact"
    safe = "".join(c for c in candidate if c.isalnum() or c in "-_.")
    if not safe:
        safe = "artifact"
    if len(safe) > max_len:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        safe = safe[:max_len - 9] + "_" + h
    return safe


def slug_from_text(text: str, max_len: int = 60) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    safe = "".join(c for c in text if c.isalnum() or c in "-_.")
    if not safe:
        return ""
    return safe[:max_len] if len(safe) > max_len else safe


def download_stream(url: str, dest_path: Path) -> None:
    print(f"[download] Streaming {url} -> {dest_path}")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        if total:
            print(f"[download] Size: {total/(1024*1024):.2f} MiB")
        downloaded = 0
        with dest_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=HTTP_STREAM_CHUNK_SIZE):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 / total
                    sys.stdout.write(
                        f"\r[download] {downloaded/(1024*1024):.2f} MiB ({pct:.1f}%)"
                    )
                    sys.stdout.flush()
        if total:
            print()
    print(f"[download] Saved {dest_path} ({dest_path.stat().st_size/(1024*1024):.2f} MiB)")


def compress_to_zip(src_path: Path, zip_path: Path) -> None:
    print(f"[zip] Compressing {src_path} -> {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(src_path, arcname=src_path.name)
    print(f"[zip] Done. Zip size: {zip_path.stat().st_size/(1024*1024):.2f} MiB")


def split_file(path: Path, chunk_size_mb: int) -> List[Path]:
    chunk_size_bytes = int(chunk_size_mb * 1024 * 1024)
    if chunk_size_bytes <= 0:
        raise ValueError("chunk_size_mb must be > 0")

    print(f"[split] Splitting {path} into <= {chunk_size_mb} MiB chunks")
    chunk_paths: List[Path] = []
    idx = 1
    total_written = 0

    with path.open("rb") as src:
        while True:
            data = src.read(chunk_size_bytes)
            if not data:
                break
            chunk_path = path.with_name(path.name + f".{idx:03d}")
            with chunk_path.open("wb") as dst:
                dst.write(data)
            size_mb = chunk_path.stat().st_size / (1024 * 1024)
            print(f"[split]   {chunk_path} ({size_mb:.2f} MiB)")
            chunk_paths.append(chunk_path)
            total_written += len(data)
            idx += 1

    print(f"[split] Total chunks: {len(chunk_paths)} (~{total_written/(1024*1024):.2f} MiB)")
    return chunk_paths


def git_commit_files(files: Iterable[Path], message: str) -> None:
    files = list(files)
    if not files:
        print("[git] No files to commit.")
        return

    rel_paths = [f.as_posix() for f in files]
    print(f"[git] Staging {len(rel_paths)} file(s)")
    subprocess.run(["git", "add", "--"] + rel_paths, check=True)

    print(f"[git] Committing: {message!r}")
    result = subprocess.run(
        ["git", "commit", "-m", message],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[git] git commit returned", result.returncode)
        if result.stdout:
            print("[git] stdout:\n", result.stdout)
        if result.stderr:
            print("[git] stderr:\n", result.stderr)
        print("[git] Continuing (likely nothing to commit).")
    else:
        print("[git] Commit created.")


# ----------------- Shared compression/splitting pipeline -----------------

def _compress_split_commit(
    src_path: Path,
    output_prefix: str,
    chunk_size_mb: int,
    do_commit: bool,
    cleanup_src: bool,
    url_meta: str,
) -> PipelineResult:
    ensure_dir(ARTIFACT_ROOT)
    zip_path = ARTIFACT_ROOT / f"{output_prefix}.zip"
    compress_to_zip(src_path, zip_path)

    if cleanup_src:
        try:
            src_path.unlink()
            print(f"[cleanup] Removed source file {src_path}")
        except FileNotFoundError:
            pass

    chunk_files = split_file(zip_path, chunk_size_mb)
    try:
        zip_path.unlink()
        print(f"[cleanup] Removed zip archive {zip_path}")
    except FileNotFoundError:
        pass

    if do_commit:
        msg = f"Add artifact chunks for {output_prefix}"
        git_commit_files(chunk_files, msg)

    return PipelineResult(
        url=url_meta,
        output_prefix=output_prefix,
        raw_file=src_path,
        zip_file=zip_path,
        chunk_files=chunk_files,
    )


def process_large_artifact(
    url: str,
    output_prefix: Optional[str] = None,
    chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
    do_commit: bool = False,
) -> PipelineResult:
    """Generic HTTP/HTTPS download."""
    if not output_prefix:
        output_prefix = slug_from_url(url)
    ensure_dir(ARTIFACT_ROOT)
    raw_path = ARTIFACT_ROOT / f"{output_prefix}.bin"
    download_stream(url, raw_path)
    return _compress_split_commit(
        raw_path,
        output_prefix,
        chunk_size_mb,
        do_commit,
        cleanup_src=True,
        url_meta=url,
    )


def process_large_artifact_from_local(
    src_path: Path,
    output_prefix: str,
    chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
    do_commit: bool = False,
    cleanup_src: bool = False,
    url_meta: Optional[str] = None,
) -> PipelineResult:
    """Start from an already downloaded file (e.g., YouTube video)."""
    return _compress_split_commit(
        src_path,
        output_prefix,
        chunk_size_mb,
        do_commit,
        cleanup_src=cleanup_src,
        url_meta=url_meta or f"file://{src_path}",
    )


# ---------------------- YouTube ingestion via Cobalt API (primary) ----------------------

def normalize_youtube_url(url: str) -> str:
    """Clean and normalize common YouTube URL forms."""
    s = url.strip().replace(" ", "")
    patterns = [
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:.*&)?v=([^&?#]+)",
        r"(?:https?://)?(?:www\.)?youtu\.be/([^&?#]+)",
        r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([^&?#]+)",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    return s


def _cobalt_download(
    youtube_url: str,
    output_prefix: Optional[str],
    tmp_root: Optional[Path],
) -> Tuple[Path, str]:
    """
    Use Cobalt API to retrieve a direct download link, then stream it.
    Raises RuntimeError on failure.
    """
    youtube_url = normalize_youtube_url(youtube_url)
    print(f"[cobalt] Endpoint: {COBALT_API_URL}")
    print(f"[cobalt] Requesting download link for: {youtube_url}")

    payload = {
        "url": youtube_url,
        "aFormat": "mp4",
        "vQuality": "max",
        "filenamePattern": "classic",
        "isAudioOnly": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; artifact_pipeline/1.0)",
    }

    try:
        resp = requests.post(
            COBALT_API_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        # این همان چیزی است که الآن در لاگ دیدی (NameResolutionError و ...)
        raise RuntimeError(f"Cobalt API HTTP error: {e}") from e
    except ValueError as e:
        raise RuntimeError(f"Cobalt API returned invalid JSON: {e}") from e

    status = data.get("status")
    direct_url = data.get("url")

    if status == "error" or not direct_url:
        # طبق داکیومنت ممکن است text پیام خطا باشد
        raise RuntimeError(f"Cobalt API logical error: status={status}, text={data.get('text')}")

    # Determine effective prefix (from suggested filename if present, else fallback)
    base_name = output_prefix or slug_from_url(youtube_url)
    suggested = data.get("filename") or ""
    if not output_prefix and suggested:
        maybe_title = Path(suggested).stem
        if maybe_title:
            base_name = slug_from_text(maybe_title) or base_name

    if tmp_root is None:
        tmp_root = ARTIFACT_ROOT / "youtube_tmp" / base_name
    ensure_dir(tmp_root)

    out_path = tmp_root / f"{base_name}.mp4"
    download_stream(direct_url, out_path)
    return out_path, base_name


# ---------------------- YouTube fallback (yt-dlp) ----------------------

def _ytdlp_download(
    youtube_url: str,
    output_prefix: Optional[str],
    tmp_root: Optional[Path],
) -> Tuple[Path, str]:
    """Fallback method using yt-dlp command line."""
    youtube_url = normalize_youtube_url(youtube_url)
    base_name = output_prefix or slug_from_url(youtube_url)

    if tmp_root is None:
        tmp_root = ARTIFACT_ROOT / "youtube_tmp" / base_name
    ensure_dir(tmp_root)

    out_path = tmp_root / f"{base_name}.mp4"
    cmd = [
        "yt-dlp",
        "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        str(out_path),
        youtube_url,
    ]
    print("[yt-dlp] Running fallback command:", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "yt-dlp not found. Install it with `pip install yt-dlp` "
            "or ensure it is available in PATH."
        ) from e

    print(f"[yt-dlp] exit code: {proc.returncode}")
    if proc.stdout:
        print("[yt-dlp] stdout:\n", proc.stdout)
    if proc.stderr:
        print("[yt-dlp] stderr:\n", proc.stderr)

    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"yt-dlp failed. exit={proc.returncode}, output exists={out_path.exists()}"
        )

    return out_path, base_name


def ingest_from_youtube(
    youtube_url: str,
    output_prefix: Optional[str] = None,
    tmp_root: Optional[Path] = None,
) -> Tuple[Path, str]:
    """
    Primary: Cobalt API.
    Fallback: yt-dlp.

    Returns:
        (final_video_path, effective_prefix)
    Raises:
        RuntimeError only اگر هر دو روش fail شوند.
    """
    last_error = None

    # ---- Primary: Cobalt ----
    try:
        print("[yt] PRIMARY method: Cobalt API")
        path, prefix = _cobalt_download(youtube_url, output_prefix, tmp_root)
        print("[yt] Cobalt succeeded.")
        return path, prefix
    except Exception as e:  # noqa: BLE001
        last_error = e
        print(f"[yt] Cobalt failed: {e!r}")

    # ---- Fallback: yt-dlp ----
    try:
        print("[yt] FALLBACK method: yt-dlp")
        path, prefix = _ytdlp_download(youtube_url, output_prefix, tmp_root)
        print("[yt] yt-dlp succeeded.")
        return path, prefix
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Both methods failed. Cobalt: {last_error!r}; yt-dlp: {e!r}"
        ) from e


# ----------------- CLI -----------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Download, zip, chunk, and optionally commit a large artifact.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Generic HTTP/HTTPS URL")
    group.add_argument("--youtube-url", help="YouTube video URL")
    parser.add_argument("--output-prefix", help="Base name for output files")
    parser.add_argument(
        "--chunk-size-mb",
        type=int,
        default=DEFAULT_CHUNK_SIZE_MB,
        help=f"Max chunk size in MiB (default {DEFAULT_CHUNK_SIZE_MB})",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Stage and commit chunks into the repo",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        if args.youtube_url:
            final_path, eff_prefix = ingest_from_youtube(
                args.youtube_url,
                args.output_prefix,
            )
            result = process_large_artifact_from_local(
                src_path=final_path,
                output_prefix=eff_prefix,
                chunk_size_mb=args.chunk_size_mb,
                do_commit=args.commit,
                cleanup_src=True,
                url_meta=args.youtube_url,
            )
        else:
            result = process_large_artifact(
                url=args.url,
                output_prefix=args.output_prefix,
                chunk_size_mb=args.chunk_size_mb,
                do_commit=args.commit,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Pipeline failed: {e}")
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
            print(f"    - {p} (missing)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
