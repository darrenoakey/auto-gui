"""Tests for icon_generator module."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from icon_generator import (
    FAILURE_COOLDOWN_SCANS,
    MAX_CONSECUTIVE_FAILURES,
    _failure_counts,
    find_readme,
    generate_icon_async,
    generate_icon_for_process,
    generate_summary_async,
    get_change_version,
    get_icon_path,
    get_summary_path,
    has_icon,
    has_summary,
    increment_change_version,
    load_summary,
    queue_icon_generation,
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


class TestGenerateIconAsync:
    @pytest.mark.asyncio
    async def test_generates_transparent_icon(self, tmp_path):
        output_path = tmp_path / "icon.png"

        async def fake_image(prompt, *, width, height, output, transparent, provider):
            Path(output).write_bytes(b"png")
            assert provider == "spark"
            return MagicMock(path=Path(output))

        with patch("icon_generator.agent.image", side_effect=fake_image):
            result = await generate_icon_async("prompt", output_path)

        assert result is True
        assert output_path.read_bytes() == b"png"
        assert output_path.exists()

    @pytest.mark.asyncio
    async def test_returns_false_when_image_generation_fails(self, tmp_path):
        output_path = tmp_path / "icon.png"

        with patch("icon_generator.agent.image", side_effect=RuntimeError("generation failed")):
            result = await generate_icon_async("prompt", output_path)

        assert result is False
        assert not output_path.exists()


class TestFailureCooldown:
    def setup_method(self):
        """Clear failure state before each test."""
        _failure_counts.clear()

    def test_queue_allows_first_attempt(self):
        """Items with no failure history are queued normally."""
        with patch("icon_generator.get_icon_queue") as mock_queue:
            mock_q = MagicMock()
            mock_queue.return_value = mock_q
            queue_icon_generation("new-app")
            mock_q.put_nowait.assert_called_once()

    def test_queue_blocks_after_max_failures(self):
        """Items that failed MAX_CONSECUTIVE_FAILURES times are blocked."""
        _failure_counts[("bad-app", False)] = MAX_CONSECUTIVE_FAILURES
        with patch("icon_generator.get_icon_queue") as mock_queue:
            mock_q = MagicMock()
            mock_queue.return_value = mock_q
            queue_icon_generation("bad-app")
            mock_q.put_nowait.assert_not_called()

    def test_queue_allows_force_despite_failures(self):
        """force=True bypasses the cooldown."""
        _failure_counts[("bad-app", False)] = MAX_CONSECUTIVE_FAILURES
        with patch("icon_generator.get_icon_queue") as mock_queue:
            mock_q = MagicMock()
            mock_queue.return_value = mock_q
            queue_icon_generation("bad-app", force=True)
            mock_q.put_nowait.assert_called_once()

    def test_cooldown_expires_after_enough_scans(self):
        """After FAILURE_COOLDOWN_SCANS cycles, item is retried."""
        _failure_counts[("bad-app", False)] = MAX_CONSECUTIVE_FAILURES + FAILURE_COOLDOWN_SCANS
        with patch("icon_generator.get_icon_queue") as mock_queue:
            mock_q = MagicMock()
            mock_queue.return_value = mock_q
            queue_icon_generation("bad-app")
            mock_q.put_nowait.assert_called_once()
        # Counter should be reset
        assert _failure_counts[("bad-app", False)] == 0


class TestSaveSummaryRejectsEmpty:
    def test_rejects_empty_string(self, tmp_path):
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="empty summary"):
                save_summary("test", "")

    def test_rejects_whitespace_only(self, tmp_path):
        with patch("icon_generator.get_local_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="empty summary"):
                save_summary("test", "   \n  ")


class TestAgentRateLimitBackoff:
    @pytest.mark.asyncio
    async def test_generate_summary_retries_with_exponential_backoff(self):
        attempts = {"count": 0}

        async def fake_ask(prompt, *, tier=None, cwd=None):
            attempts["count"] += 1
            if attempts["count"] < 4:
                raise Exception("Unknown message type: rate_limit_event")
            response = MagicMock()
            response.text = "Recovered summary"
            return response

        sleep_mock = AsyncMock()
        with (
            patch("icon_generator.agent.ask", side_effect=fake_ask),
            patch("icon_generator.asyncio.sleep", sleep_mock),
            patch("icon_generator.RATE_LIMIT_MAX_RETRIES", 5),
            patch("icon_generator.RATE_LIMIT_INITIAL_BACKOFF_SECONDS", 1.0),
            patch("icon_generator.RATE_LIMIT_MAX_BACKOFF_SECONDS", 30.0),
        ):
            summary = await generate_summary_async("test-app", None, None)

        assert summary == "Recovered summary"
        assert attempts["count"] == 4
        assert [call.args[0] for call in sleep_mock.await_args_list] == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_generate_summary_does_not_retry_non_rate_limit_errors(self):
        attempts = {"count": 0}

        async def fake_ask(prompt, *, tier=None, cwd=None):
            attempts["count"] += 1
            raise Exception("upstream unavailable")

        sleep_mock = AsyncMock()
        with (
            patch("icon_generator.agent.ask", side_effect=fake_ask),
            patch("icon_generator.asyncio.sleep", sleep_mock),
        ):
            with pytest.raises(Exception, match="upstream unavailable"):
                await generate_summary_async("test-app", None, None)

        assert attempts["count"] == 1
        assert sleep_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_generate_summary_stops_after_max_rate_limit_retries(self):
        attempts = {"count": 0}

        async def fake_ask(prompt, *, tier=None, cwd=None):
            attempts["count"] += 1
            raise Exception("Unknown message type: rate_limit_event")

        sleep_mock = AsyncMock()
        with (
            patch("icon_generator.agent.ask", side_effect=fake_ask),
            patch("icon_generator.asyncio.sleep", sleep_mock),
            patch("icon_generator.RATE_LIMIT_MAX_RETRIES", 2),
            patch("icon_generator.RATE_LIMIT_INITIAL_BACKOFF_SECONDS", 0.5),
            patch("icon_generator.RATE_LIMIT_MAX_BACKOFF_SECONDS", 30.0),
        ):
            with pytest.raises(Exception, match="rate_limit_event"):
                await generate_summary_async("test-app", None, None)

        assert attempts["count"] == 3
        assert [call.args[0] for call in sleep_mock.await_args_list] == [0.5, 1.0]
