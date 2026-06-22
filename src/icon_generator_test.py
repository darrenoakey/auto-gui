"""Tests for icon_generator module."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from icon_generator import (
    PNG_SIGNATURE,
    fetch_image_job,
    FAILURE_COOLDOWN_SCANS,
    get_image_job_status,
    MAX_CONSECUTIVE_FAILURES,
    _failure_counts,
    find_readme,
    generate_icon_async,
    submit_image_job,
    generate_icon_for_process,
    generate_summary_async,
    get_change_version,
    get_icon_path,
    get_summary_path,
    has_icon,
    has_summary,
    increment_change_version,
    load_summary,
    normalize_icon_png,
    queue_icon_generation,
    save_summary,
    wait_for_image_job,
)


class ImageServiceHarness:
    def __init__(self, statuses=None, image_data=None, image_content_type="image/png"):
        self.statuses = list(statuses or [{"id": "job-123", "status": "done"}])
        self.image_data = image_data or PNG_SIGNATURE + b"payload"
        self.image_content_type = image_content_type
        self.requests = []
        self.server = None
        self.thread = None

    def __enter__(self):
        harness = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", "0")))
                harness.requests.append((self.command, self.path, body))
                if self.path != "/jobs":
                    self.send_error(404)
                    return
                self.send_response(202)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "job-123"}).encode("utf-8"))

            def do_GET(self):
                harness.requests.append((self.command, self.path, b""))
                if self.path == "/jobs/job-123":
                    payload = harness.statuses.pop(0) if harness.statuses else {"id": "job-123", "status": "done"}
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(payload).encode("utf-8"))
                    return
                if self.path == "/jobs/job-123/image":
                    self.send_response(200)
                    self.send_header("content-type", harness.image_content_type)
                    self.end_headers()
                    self.wfile.write(harness.image_data)
                    return
                self.send_error(404)

            def log_message(self, _format, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"


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
    @pytest.mark.asyncio
    async def test_generates_icon_from_image_service(self, tmp_path):
        output_path = tmp_path / "icon.png"
        png_data = make_png_bytes()

        with ImageServiceHarness(
            statuses=[
                {"id": "job-123", "status": "queued"},
                {"id": "job-123", "status": "running"},
                {"id": "job-123", "status": "done"},
            ],
            image_data=png_data,
        ) as service:
            with (
                patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url),
                patch("icon_generator.ICON_IMAGE_POLL_SECONDS", 0.001),
            ):
                result = await generate_icon_async("prompt", output_path)

        assert json.loads(service.requests[0][2]) == {
            "prompt": "prompt",
            "width": 128,
            "height": 128,
            "transparent": True,
        }
        assert [request[1] for request in service.requests] == [
            "/jobs",
            "/jobs/job-123",
            "/jobs/job-123",
            "/jobs/job-123",
            "/jobs/job-123/image",
        ]
        assert result is True
        assert output_path.exists()
        with Image.open(output_path) as image:
            assert image.size == (128, 128)
            assert image.mode == "RGBA"

    @pytest.mark.asyncio
    async def test_returns_false_when_image_generation_fails(self, tmp_path):
        output_path = tmp_path / "icon.png"

        with ImageServiceHarness(statuses=[{"id": "job-123", "status": "failed", "error": "service broke"}]) as service:
            with patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url):
                result = await generate_icon_async("prompt", output_path)

        assert result is False
        assert not output_path.exists()


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


class TestImageServiceClient:
    @pytest.mark.asyncio
    async def test_submit_image_job_posts_prompt_and_dimensions(self):
        with ImageServiceHarness() as service:
            async with httpx.AsyncClient() as client:
                with patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url):
                    job_id = await submit_image_job(client, "make an icon")

        assert job_id == "job-123"
        assert service.requests[0][0] == "POST"
        assert service.requests[0][1] == "/jobs"
        assert json.loads(service.requests[0][2]) == {
            "prompt": "make an icon",
            "width": 128,
            "height": 128,
            "transparent": True,
        }

    @pytest.mark.asyncio
    async def test_get_image_job_status_decodes_json(self):
        with ImageServiceHarness(statuses=[{"id": "job-123", "status": "done"}]) as service:
            async with httpx.AsyncClient() as client:
                with patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url):
                    status = await get_image_job_status(client, "job-123")

        assert status == {"id": "job-123", "status": "done"}

    @pytest.mark.asyncio
    async def test_wait_for_image_job_polls_until_done(self):
        with ImageServiceHarness(
            statuses=[
                {"id": "job-123", "status": "queued"},
                {"id": "job-123", "status": "running"},
                {"id": "job-123", "status": "done"},
            ]
        ) as service:
            async with httpx.AsyncClient() as client:
                with (
                    patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url),
                    patch("icon_generator.ICON_IMAGE_POLL_SECONDS", 0.001),
                ):
                    await wait_for_image_job(client, "job-123")

        assert [request[1] for request in service.requests] == [
            "/jobs/job-123",
            "/jobs/job-123",
            "/jobs/job-123",
        ]

    @pytest.mark.asyncio
    async def test_wait_for_image_job_raises_on_failure(self):
        with ImageServiceHarness(
            statuses=[{"id": "job-123", "status": "failed", "attempts": 2, "error": "service broke"}]
        ) as service:
            async with httpx.AsyncClient() as client:
                with patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url):
                    with pytest.raises(RuntimeError, match="service broke"):
                        await wait_for_image_job(client, "job-123")

    @pytest.mark.asyncio
    async def test_fetch_image_job_returns_png_bytes(self):
        png_data = PNG_SIGNATURE + b"payload"

        with ImageServiceHarness(image_data=png_data) as service:
            async with httpx.AsyncClient() as client:
                with patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url):
                    image_data = await fetch_image_job(client, "job-123")

        assert image_data == png_data

    @pytest.mark.asyncio
    async def test_fetch_image_job_rejects_non_png_content_type(self):
        with ImageServiceHarness(image_content_type="text/plain") as service:
            async with httpx.AsyncClient() as client:
                with patch("icon_generator.ICON_IMAGE_SERVER_URL", service.url):
                    with pytest.raises(ValueError, match="content type"):
                        await fetch_image_job(client, "job-123")


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
