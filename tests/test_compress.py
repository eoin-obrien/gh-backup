"""Tests for gh_backup/compress.py."""

from __future__ import annotations

import tarfile

import pytest

from gh_backup.compress import (
    ArchiveFormat,
    compress_directory,
    get_archive_suffix,
    verify_archive,
)

# ── get_archive_suffix ────────────────────────────────────────────────────────


class TestGetArchiveSuffix:
    @pytest.mark.parametrize(
        "fmt,expected",
        [
            (ArchiveFormat.ZST, ".tar.zst"),
            (ArchiveFormat.GZ, ".tar.gz"),
            (ArchiveFormat.XZ, ".tar.xz"),
        ],
    )
    def test_returns_correct_extension(self, fmt, expected):
        assert get_archive_suffix(fmt) == expected


# ── compress_directory ────────────────────────────────────────────────────────


class TestCompressDirectory:
    def test_raises_file_not_found_for_missing_source(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Source directory not found"):
            compress_directory(
                source_dir=tmp_path / "nonexistent",
                output_path=tmp_path / "out.tar.zst",
            )

    def test_creates_nested_parent_dirs_for_output(self, source_dir, tmp_path):
        output = tmp_path / "nested" / "deep" / "out.tar.zst"
        compress_directory(source_dir=source_dir, output_path=output)
        assert output.parent.exists()

    def test_returns_output_path(self, source_dir, tmp_path):
        output = tmp_path / "out.tar.zst"
        result = compress_directory(source_dir=source_dir, output_path=output)
        assert result == output

    def test_produces_non_empty_archive(self, source_dir, tmp_path):
        output = tmp_path / "out.tar.zst"
        compress_directory(source_dir=source_dir, output_path=output)
        assert output.stat().st_size > 0

    def test_cleans_up_partial_archive_on_failure(self, source_dir, tmp_path, mocker):
        """If _compress_zst raises mid-compression, the partial output file is removed."""
        output = tmp_path / "out.tar.zst"
        output.write_bytes(b"partial")
        mocker.patch(
            "gh_backup.compress._compress_zst",
            side_effect=RuntimeError("disk full"),
        )
        with pytest.raises(RuntimeError, match="disk full"):
            compress_directory(source_dir=source_dir, output_path=output)
        assert not output.exists()

    def test_progress_callback_invoked_with_increasing_values(self, source_dir, tmp_path):
        """Progress callback is called at least once with non-decreasing byte counts."""
        calls = []
        compress_directory(
            source_dir=source_dir,
            output_path=tmp_path / "out.tar.zst",
            progress_callback=calls.append,
        )
        assert len(calls) > 0
        assert all(calls[i] <= calls[i + 1] for i in range(len(calls) - 1))

    def test_none_progress_callback_does_not_raise(self, source_dir, tmp_path):
        compress_directory(
            source_dir=source_dir,
            output_path=tmp_path / "out.tar.zst",
            progress_callback=None,
        )


# ── ZST format ────────────────────────────────────────────────────────────────


class TestCompressZst:
    def test_archive_contains_source_files(self, source_dir, tmp_path):
        """Produced .tar.zst contains the source files at expected paths."""
        import zstandard

        output = tmp_path / "out.tar.zst"
        compress_directory(source_dir=source_dir, output_path=output, fmt=ArchiveFormat.ZST)

        dctx = zstandard.ZstdDecompressor()
        with open(output, "rb") as f:
            with dctx.stream_reader(f) as reader:
                # Use streaming mode "r|" because the zstd reader is non-seekable
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    names = [m.name for m in tar]

        assert any("file1.txt" in n for n in names)
        assert any("file2.txt" in n for n in names)

    @pytest.mark.parametrize("level", [1, 3, 9])
    def test_various_compression_levels_produce_valid_archive(self, source_dir, tmp_path, level):
        output = tmp_path / f"out-level{level}.tar.zst"
        compress_directory(
            source_dir=source_dir,
            output_path=output,
            fmt=ArchiveFormat.ZST,
            level=level,
        )
        assert output.exists()
        assert output.stat().st_size > 0


# ── GZ format ─────────────────────────────────────────────────────────────────


class TestCompressGz:
    def test_archive_contains_source_files(self, source_dir, tmp_path):
        output = tmp_path / "out.tar.gz"
        compress_directory(source_dir=source_dir, output_path=output, fmt=ArchiveFormat.GZ)
        with tarfile.open(str(output), "r:gz") as tar:
            names = tar.getnames()
        assert any("file1.txt" in n for n in names)
        assert any("file2.txt" in n for n in names)

    def test_produces_non_empty_archive(self, source_dir, tmp_path):
        output = tmp_path / "out.tar.gz"
        compress_directory(source_dir=source_dir, output_path=output, fmt=ArchiveFormat.GZ)
        assert output.stat().st_size > 0


# ── XZ format ─────────────────────────────────────────────────────────────────


class TestCompressXz:
    def test_archive_contains_source_files(self, source_dir, tmp_path):
        output = tmp_path / "out.tar.xz"
        compress_directory(source_dir=source_dir, output_path=output, fmt=ArchiveFormat.XZ)
        with tarfile.open(str(output), "r:xz") as tar:
            names = tar.getnames()
        assert any("file1.txt" in n for n in names)

    def test_produces_non_empty_archive(self, source_dir, tmp_path):
        output = tmp_path / "out.tar.xz"
        compress_directory(source_dir=source_dir, output_path=output, fmt=ArchiveFormat.XZ)
        assert output.stat().st_size > 0


# ── verify_archive ────────────────────────────────────────────────────────────


class TestVerifyArchive:
    def test_raises_file_not_found_for_missing_archive(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            verify_archive(tmp_path / "nonexistent.tar.zst")

    @pytest.mark.parametrize("fmt", [ArchiveFormat.ZST, ArchiveFormat.GZ, ArchiveFormat.XZ])
    def test_returns_positive_member_count_for_valid_archive(self, source_dir, tmp_path, fmt):
        suffix = get_archive_suffix(fmt)
        output = tmp_path / f"out{suffix}"
        compress_directory(source_dir=source_dir, output_path=output, fmt=fmt)
        count = verify_archive(output)
        assert count > 0


# ── edge cases ────────────────────────────────────────────────────────────────


class TestCompressEdgeCases:
    def test_empty_source_directory_produces_valid_archive(self, tmp_path):
        """An empty source directory produces a valid (minimal) archive."""
        src = tmp_path / "empty_src"
        src.mkdir()
        output = tmp_path / "out.tar.zst"
        compress_directory(source_dir=src, output_path=output)
        assert output.exists()

    @pytest.mark.parametrize("fmt", [ArchiveFormat.ZST, ArchiveFormat.GZ, ArchiveFormat.XZ])
    def test_all_formats_produce_non_empty_archive(self, source_dir, tmp_path, fmt):
        suffix = get_archive_suffix(fmt)
        output = tmp_path / f"out{suffix}"
        compress_directory(source_dir=source_dir, output_path=output, fmt=fmt)
        assert output.stat().st_size > 0
