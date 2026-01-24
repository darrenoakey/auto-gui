"""Tests for server module."""
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_state(tmp_path):
    """Mock state manager to use temp directory."""
    with patch("server.get_project_root", return_value=tmp_path):
        # Create required directories
        (tmp_path / "static" / "css").mkdir(parents=True)
        (tmp_path / "static" / "js").mkdir(parents=True)
        (tmp_path / "static" / "img").mkdir(parents=True)
        (tmp_path / "templates").mkdir(parents=True)
        (tmp_path / "local" / "icons").mkdir(parents=True)

        # Create minimal CSS and JS files
        (tmp_path / "static" / "css" / "main.css").write_text("/* empty */")
        (tmp_path / "static" / "js" / "main.js").write_text("// empty")

        # Create minimal template
        (tmp_path / "templates" / "index.html").write_text("""
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<div id="process-list">
{% for process in processes %}
<div>{{ process.name }}</div>
{% endfor %}
</div>
<div id="last-scan">{{ last_scan }}</div>
</body>
</html>
""")
        yield tmp_path


@pytest.fixture
def mock_processes():
    """Mock process data."""
    return [
        {"name": "test-app", "port": 8080, "is_html": True, "visible": True, "icon_status": "pending"},
        {"name": "api-app", "port": 9000, "is_html": True, "visible": True, "icon_status": "ready"},
    ]


class TestIndexRoute:
    def test_renders_index_page(self, mock_state, mock_processes):
        with (
            patch("server.get_all_visible_items", return_value=mock_processes),
            patch("server.get_last_scan", return_value="2025-01-24T12:00:00"),
            patch("server.get_icons_dir", return_value=mock_state / "local" / "icons"),
            patch("server.scan_and_update_processes", new_callable=AsyncMock),
            patch("server.background_scanner", new_callable=AsyncMock),
        ):
            # Import after patching
            from server import app
            client = TestClient(app)
            response = client.get("/")
            assert response.status_code == 200
            assert "test-app" in response.text
            assert "api-app" in response.text


class TestApiProcesses:
    def test_returns_processes_json(self, mock_state, mock_processes):
        with (
            patch("server.get_all_visible_items", return_value=mock_processes),
            patch("server.get_last_scan", return_value="2025-01-24T12:00:00"),
            patch("server.get_icons_dir", return_value=mock_state / "local" / "icons"),
            patch("server.scan_and_update_processes", new_callable=AsyncMock),
            patch("server.background_scanner", new_callable=AsyncMock),
        ):
            from server import app
            client = TestClient(app)
            response = client.get("/api/processes")
            assert response.status_code == 200
            data = response.json()
            assert "processes" in data
            assert "last_scan" in data
            assert len(data["processes"]) == 2
            assert "server_pid" in data


class TestApiScan:
    def test_triggers_scan(self, mock_state):
        mock_scan = AsyncMock()
        with (
            patch("server.scan_and_update_processes", mock_scan),
            patch("server.get_last_scan", return_value="2025-01-24T12:30:00"),
            patch("server.get_visible_html_processes", return_value=[]),
            patch("server.get_icons_dir", return_value=mock_state / "local" / "icons"),
            patch("server.background_scanner", new_callable=AsyncMock),
        ):
            from server import app
            client = TestClient(app)
            response = client.post("/api/scan")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert "last_scan" in data


class TestScanAndUpdateProcesses:
    @pytest.mark.asyncio
    async def test_updates_state_for_html_processes(self, mock_state):
        mock_processes = [
            {"name": "html-app", "port": 8080, "workdir": "/path/to/app"},
            {"name": "api-app", "port": 9000, "workdir": None},
        ]

        with (
            patch("server.scan_processes", return_value=mock_processes),
            patch("server.check_port_returns_html", new_callable=AsyncMock) as mock_check,
            patch("server.get_process", return_value=None),
            patch("server.update_process") as mock_update,
            patch("server.get_visible_html_processes", return_value=[]),
            patch("server.update_last_scan"),
        ):
            # html-app serves HTML, api-app does not
            mock_check.side_effect = [True, False]

            from server import scan_and_update_processes
            await scan_and_update_processes()

            # Both processes should be updated
            assert mock_update.call_count == 2

            # Verify html-app was updated with is_html=True
            calls = mock_update.call_args_list
            html_call = next(c for c in calls if c.kwargs.get("name") == "html-app")
            assert html_call.kwargs["is_html"] is True

            # Verify api-app was updated with is_html=False
            api_call = next(c for c in calls if c.kwargs.get("name") == "api-app")
            assert api_call.kwargs["is_html"] is False

    @pytest.mark.asyncio
    async def test_marks_missing_processes_invisible(self, mock_state):
        # Process was visible before but no longer running
        visible_process = {"name": "old-app", "port": 7000, "is_html": True, "visible": True}

        with (
            patch("server.scan_processes", return_value=[]),  # No running processes
            patch("server.get_visible_html_processes", return_value=[visible_process]),
            patch("server.mark_process_invisible") as mock_mark,
            patch("server.update_last_scan"),
        ):
            from server import scan_and_update_processes
            await scan_and_update_processes()

            # old-app should be marked invisible
            mock_mark.assert_called_once_with("old-app")
