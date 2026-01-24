"""Tests for icon_generator module."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from icon_generator import (
    find_readme,
    generate_icon_for_process,
    get_change_version,
    get_icon_path,
    get_summary_path,
    has_icon,
    has_summary,
    increment_change_version,
    load_summary,
    save_summary,
)


class TestFindReadme:
    def test_returns_none_when_no_workdir(self):
        result = find_readme(None)
        assert result is None

    def test_returns_none_when_readme_missing(self, tmp_path):
        result = find_readme(str(tmp_path))
        assert result is None

    def test_reads_uppercase_readme(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# Test App\n\nThis is a test.")
        result = find_readme(str(tmp_path))
        assert result == "# Test App\n\nThis is a test."

    def test_reads_lowercase_readme(self, tmp_path):
        readme = tmp_path / "readme.md"
        readme.write_text("# Lower Test")
        result = find_readme(str(tmp_path))
        assert result == "# Lower Test"


class TestSummaryFunctions:
    def test_get_summary_path(self, tmp_path):
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            path = get_summary_path("test-app")
            assert path == tmp_path / "test-app_summary.txt"

    def test_has_summary_false(self, tmp_path):
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            assert has_summary("nonexistent") is False

    def test_has_summary_true(self, tmp_path):
        (tmp_path / "test-app_summary.txt").write_text("Test summary")
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            assert has_summary("test-app") is True

    def test_save_and_load_summary(self, tmp_path):
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            save_summary("test-app", "This is a test summary")
            result = load_summary("test-app")
            assert result == "This is a test summary"

    def test_load_summary_returns_none_if_missing(self, tmp_path):
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            result = load_summary("nonexistent")
            assert result is None


class TestIconFunctions:
    def test_get_icon_path(self, tmp_path):
        with patch("icon_generator.get_icons_dir", return_value=tmp_path):
            path = get_icon_path("test-app")
            assert path == tmp_path / "test-app.png"

    def test_has_icon_false(self, tmp_path):
        with patch("icon_generator.get_icons_dir", return_value=tmp_path):
            assert has_icon("nonexistent") is False

    def test_has_icon_true(self, tmp_path):
        (tmp_path / "test-app.png").write_bytes(b"fake image")
        with patch("icon_generator.get_icons_dir", return_value=tmp_path):
            assert has_icon("test-app") is True


class TestChangeVersion:
    def test_get_and_increment(self):
        initial = get_change_version()
        increment_change_version()
        assert get_change_version() == initial + 1


class TestGenerateIconForProcess:
    @pytest.mark.asyncio
    async def test_returns_false_for_missing_process(self):
        with patch("icon_generator.get_process", return_value=None):
            result = await generate_icon_for_process("nonexistent")
            assert result is False
