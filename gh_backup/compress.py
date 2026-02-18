"""Streaming archive compression utilities."""

from __future__ import annotations

import logging
import os
import tarfile
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)


class ArchiveFormat(StrEnum):
    ZST = "zst"
    GZ = "gz"
    XZ = "xz"


def get_archive_suffix(fmt: ArchiveFormat) -> str:
    return f".tar.{fmt}"


def compress_directory(
    source_dir: Path,
    output_path: Path,
    fmt: ArchiveFormat = ArchiveFormat.ZST,
    level: int = 3,
    progress_callback: Callable[[int], None] | None = None,
) -> Path:
    """Stream `source_dir` into a compressed tar archive at `output_path`.

    For ZST: uses zstandard with multi-threaded encoding (threads=-1).
    For GZ/XZ: uses the standard tarfile module.
    Returns the output path on success.
    """
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Compressing %s → %s (format=%s, level=%d)", source_dir, output_path, fmt, level)

    try:
        if fmt == ArchiveFormat.ZST:
            _compress_zst(source_dir, output_path, level, progress_callback)
        else:
            _compress_stdlib(source_dir, output_path, fmt, progress_callback)
    except Exception:
        # Clean up partial archive on failure
        if output_path.exists():
            output_path.unlink()
        raise

    log.info(
        "Archive created: %s (%.1f MB)",
        output_path,
        output_path.stat().st_size / 1_048_576,
    )
    return output_path


def _compress_zst(
    source_dir: Path,
    output_path: Path,
    level: int,
    progress_callback: Callable[[int], None] | None,
) -> None:
    import zstandard

    cctx = zstandard.ZstdCompressor(level=level, threads=-1)
    bytes_written = 0

    with open(output_path, "wb") as out_file:
        with cctx.stream_writer(out_file, closefd=False) as compressor:
            with tarfile.open(fileobj=compressor, mode="w|") as tar:
                for root, _dirs, files in os.walk(source_dir):
                    for fname in files:
                        fpath = Path(root) / fname
                        arcname = fpath.relative_to(source_dir.parent)
                        tar.add(str(fpath), arcname=str(arcname))
                        bytes_written += fpath.stat().st_size
                        if progress_callback:
                            progress_callback(bytes_written)
                    # Also add empty directories
                    for dname in _dirs:
                        dpath = Path(root) / dname
                        if not any(dpath.iterdir()):
                            arcname = dpath.relative_to(source_dir.parent)
                            tar.add(str(dpath), arcname=str(arcname), recursive=False)


def verify_archive(archive_path: Path) -> int:
    """Open the archive and iterate all members to verify integrity.

    Returns the number of members verified.
    Raises tarfile.TarError or zstandard.ZstdError on corruption.
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    log.info("Verifying archive: %s", archive_path)
    name = archive_path.name

    if name.endswith(".tar.zst"):
        import zstandard

        with open(archive_path, "rb") as f:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(f) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    count = sum(1 for _ in tar)
    else:
        with tarfile.open(str(archive_path), "r:*") as tar:
            count = sum(1 for _ in tar)

    log.info("Verified %d members in %s", count, archive_path)
    return count


def _compress_stdlib(
    source_dir: Path,
    output_path: Path,
    fmt: ArchiveFormat,
    progress_callback: Callable[[int], None] | None,
) -> None:
    mode = f"w:{fmt}"
    bytes_written = 0

    with tarfile.open(str(output_path), mode) as tar:
        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                fpath = Path(root) / fname
                arcname = fpath.relative_to(source_dir.parent)
                tar.add(str(fpath), arcname=str(arcname))
                bytes_written += fpath.stat().st_size
                if progress_callback:
                    progress_callback(bytes_written)
