"""Tests for icon_generator module."""
from io import BytesIO
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from daz_agent_sdk import agent
import pytest
from PIL import Image

from icon_generator import (
    PNG_SIGNATURE,
    FAILURE_COOLDOWN_SCANS,
    MAX_CONSECUTIVE_FAILURES,
    _failure_counts,
    atomic_swap,
    find_readme,
    generate_icon_async,
    generate_icon_for_process,
    generate_summary_async,
    get_change_version,
    get_icon_image_operation,
    get_icon_path,
    get_summary_path,
    has_icon,
    has_summary,
    increment_change_version,
    load_summary,
    normalize_icon_png,
    queue_icon_generation,
    save_summary,
)


def make_png_bytes(mode="RGBA", size=(16, 16), colour=(20, 120, 220, 255)) -> bytes:
    image = Image.new(mode, size, colour)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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
    def test_real_igs_submit_recovery_and_png_validation(self):
        output_path = Path(__file__).resolve().parent.parent / "output" / "testing" / "durable-icon.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = (
            "A bold simple 3D isometric blue toolbox app icon on a completely flat coral background, "
            "one substantial object, no text, fill the frame"
        )
        operation = get_icon_image_operation(prompt, output_path)
        state_path = Path(operation["operation_state"])

        assert inspect.signature(agent.image).parameters["timeout"].default is None
        script = """
import asyncio
import json
import sys
from pathlib import Path
from icon_generator import generate_icon_async, get_icon_image_operation

async def run():
    prompt = sys.argv[1]
    output = Path(sys.argv[2])
    operation = get_icon_image_operation(prompt, output)
    state_path = Path(operation["operation_state"])
    assert await generate_icon_async(prompt, output)
    first = json.loads(state_path.read_text(encoding="utf-8"))
    output.unlink()
    assert await generate_icon_async(prompt, output)
    recovered = json.loads(state_path.read_text(encoding="utf-8"))
    print(json.dumps({"first": first, "recovered": recovered}, separators=(",", ":")))

asyncio.run(run())
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, prompt, str(output_path)],
            cwd=Path(__file__).resolve().parent,
            check=True,
            capture_output=True,
            text=True,
        )
        evidence = json.loads(completed.stdout.strip().splitlines()[-1])
        first_state = evidence["first"]
        first_job_id = first_state["job_id"]
        recovered_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert recovered_state == evidence["recovered"]
        assert first_job_id
        assert first_state["idempotency_key"] == operation["idempotency_key"]
        assert recovered_state["job_id"] == first_job_id
        assert recovered_state["idempotency_key"] == operation["idempotency_key"]
        with Image.open(output_path) as image:
            assert image.format == "PNG"
            assert image.size == (128, 128)
            assert image.mode == "RGBA"

    @pytest.mark.asyncio
    async def test_returns_false_for_conflicting_durable_state(self, tmp_path):
        output_path = tmp_path / "icon.png"
        prompt = "durable identity conflict proof"
        operation = get_icon_image_operation(prompt, output_path)
        state_path = Path(operation["operation_state"])
        state_path.write_text("{}", encoding="utf-8")
        state_path.chmod(0o600)
        try:
            result = await generate_icon_async(prompt, output_path)
        finally:
            state_path.unlink(missing_ok=True)
            state_path.with_name(state_path.name + ".lock").unlink(missing_ok=True)

        assert result is False
        assert not output_path.exists()

    def test_operation_identity_is_exact_and_deterministic(self, tmp_path):
        output_path = tmp_path / "icon.png"
        first = get_icon_image_operation("exact prompt", output_path)
        replay = get_icon_image_operation("exact prompt", output_path)
        changed_prompt = get_icon_image_operation("changed prompt", output_path)
        changed_output = get_icon_image_operation("exact prompt", tmp_path / "other.png")

        assert first == replay
        assert first["idempotency_key"] != changed_prompt["idempotency_key"]
        assert first["idempotency_key"] != changed_output["idempotency_key"]
        assert first["operation_state"] != changed_prompt["operation_state"]

    def test_invalid_temporary_icon_preserves_previous_icon(self, tmp_path):
        final_path = tmp_path / "icon.png"
        temporary_path = tmp_path / "icon.tmp.png"
        previous = make_png_bytes(size=(128, 128), colour=(10, 20, 30, 255))
        final_path.write_bytes(previous)
        temporary_path.write_bytes(PNG_SIGNATURE + b"truncated")

        assert atomic_swap(temporary_path, final_path) is False
        assert final_path.read_bytes() == previous


class TestNormalizeIconPng:
    def test_preserves_existing_alpha_and_resizes(self):
        image = Image.new("RGBA", (16, 16), (200, 20, 20, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        source = BytesIO()
        image.save(source, format="PNG")

        normalized = normalize_icon_png(source.getvalue())

        with Image.open(BytesIO(normalized)) as result:
            assert result.size == (128, 128)
            assert result.mode == "RGBA"
            assert result.getchannel("A").getextrema()[0] < 255

    def test_removes_connected_checkerboard_background(self):
        image = Image.new("RGB", (12, 12), (255, 255, 255))
        pixels = image.load()
        for y in range(12):
            for x in range(12):
                pixels[x, y] = (225, 225, 225) if (x // 3 + y // 3) % 2 else (255, 255, 255)
        for y in range(4, 8):
            for x in range(4, 8):
                pixels[x, y] = (220, 20, 20)
        source = BytesIO()
        image.save(source, format="PNG")

        normalized = normalize_icon_png(source.getvalue())

        with Image.open(BytesIO(normalized)) as result:
            alpha = result.getchannel("A")
            assert result.size == (128, 128)
            assert alpha.getextrema()[0] == 0
            assert result.convert("RGBA").getpixel((64, 64))[3] > 200


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
