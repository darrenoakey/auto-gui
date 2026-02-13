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
<script>window.SELECTED_PROCESS = {{ selected_process | tojson }};</script>
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


class TestProcessPageRoute:
    def test_renders_with_selected_process(self, mock_state, mock_processes):
        with (
            patch("server.get_all_visible_items", return_value=mock_processes),
            patch("server.get_last_scan", return_value="2025-01-24T12:00:00"),
            patch("server.get_icons_dir", return_value=mock_state / "local" / "icons"),
            patch("server.scan_and_update_processes", new_callable=AsyncMock),
            patch("server.background_scanner", new_callable=AsyncMock),
        ):
            from server import app
            client = TestClient(app)
            response = client.get("/grafana")
            assert response.status_code == 200
            assert "SELECTED_PROCESS" in response.text
            assert '"grafana"' in response.text

    def test_api_processes_not_shadowed(self, mock_state, mock_processes):
        """Ensure /api/processes still returns JSON, not caught by /{name}."""
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

    def test_index_has_null_selected_process(self, mock_state, mock_processes):
        """Ensure GET / passes null for selected_process."""
        with (
            patch("server.get_all_visible_items", return_value=mock_processes),
            patch("server.get_last_scan", return_value="2025-01-24T12:00:00"),
            patch("server.get_icons_dir", return_value=mock_state / "local" / "icons"),
            patch("server.scan_and_update_processes", new_callable=AsyncMock),
            patch("server.background_scanner", new_callable=AsyncMock),
        ):
            from server import app
            client = TestClient(app)
            response = client.get("/")
            assert response.status_code == 200
            assert "SELECTED_PROCESS = null" in response.text


class TestScanInterval:
    def test_scan_interval_is_reasonable(self):
        """SCAN_INTERVAL should be at most 60 seconds for responsive dead detection."""
        from server import SCAN_INTERVAL
        assert SCAN_INTERVAL <= 60


class TestSmokeE2E:
    """Real E2E smoke tests against the live server at localhost:2000.

    These use Playwright to verify the actual running application works,
    including URL routing, process selection, and browser navigation.
    Skipped if the server isn't running.
    """

    BASE = "http://localhost:2000"

    @pytest.fixture(autouse=True)
    def _require_live_server(self):
        """Skip if the live server isn't reachable."""
        import urllib.request
        try:
            urllib.request.urlopen(self.BASE, timeout=3)
        except Exception:
            pytest.skip("Live server not running at localhost:2000")

    @pytest.fixture
    def browser_context(self):
        """Fresh Playwright browser context for each test."""
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        yield context
        context.close()
        browser.close()
        pw.stop()

    @pytest.fixture
    def first_process_name(self):
        """Get the name of the first process from the live API."""
        import json
        import urllib.request
        data = json.loads(
            urllib.request.urlopen(f"{self.BASE}/api/processes", timeout=5).read()
        )
        names = [p["name"] for p in data["processes"]]
        assert len(names) > 0, "No processes available for smoke test"
        return names[0]

    def test_index_shows_welcome(self, browser_context):
        """GET / shows the welcome screen with no process selected."""
        page = browser_context.new_page()
        page.goto(self.BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#welcome", state="visible", timeout=5000)
        assert page.locator("#welcome").is_visible()
        # No iframe should be active
        assert page.locator(".iframe-container.active").count() == 0
        page.screenshot(path="/Volumes/T9/darrenoakey/src/auto-gui/local/smoke_index.png")

    def test_direct_url_selects_process(self, browser_context, first_process_name):
        """GET /{name} auto-selects that process and hides the welcome screen."""
        page = browser_context.new_page()
        page.goto(f"{self.BASE}/{first_process_name}", wait_until="domcontentloaded")
        # Wait for JS to run and select the process
        page.wait_for_function(
            "() => document.querySelector('.iframe-container.active') !== null",
            timeout=10000,
        )
        # Welcome should be hidden
        assert not page.locator("#welcome").is_visible()
        # The correct button should be active
        active_btn = page.locator(f'button.process-button.active[data-name="{first_process_name}"]')
        assert active_btn.count() == 1
        page.screenshot(path="/Volumes/T9/darrenoakey/src/auto-gui/local/smoke_direct_url.png")

    def test_click_updates_url(self, browser_context, first_process_name):
        """Clicking a process in the sidebar pushes the URL to /{name}."""
        page = browser_context.new_page()
        page.goto(self.BASE, wait_until="domcontentloaded")
        page.wait_for_selector(f'[data-name="{first_process_name}"]', timeout=5000)
        # Click the process button (not the popout button)
        page.locator(f'[data-name="{first_process_name}"] .process-name').click()
        # URL should update
        page.wait_for_function(
            f"() => window.location.pathname === '/{first_process_name}'",
            timeout=5000,
        )
        assert page.url.endswith(f"/{first_process_name}")
        page.screenshot(path="/Volumes/T9/darrenoakey/src/auto-gui/local/smoke_click_url.png")

    def test_back_button_returns_to_welcome(self, browser_context, first_process_name):
        """Browser back button navigates from /{name} back to welcome."""
        page = browser_context.new_page()
        page.goto(self.BASE, wait_until="domcontentloaded")
        page.wait_for_selector(f'[data-name="{first_process_name}"]', timeout=5000)
        # Click to select a process
        page.locator(f'[data-name="{first_process_name}"] .process-name').click()
        page.wait_for_function(
            f"() => window.location.pathname === '/{first_process_name}'",
            timeout=5000,
        )
        # Go back
        page.go_back()
        page.wait_for_function(
            "() => window.location.pathname === '/'",
            timeout=5000,
        )
        # Welcome should be visible again
        page.wait_for_selector("#welcome", state="visible", timeout=5000)
        assert page.locator("#welcome").is_visible()
        page.screenshot(path="/Volumes/T9/darrenoakey/src/auto-gui/local/smoke_back_button.png")

    def test_refresh_preserves_selection(self, browser_context, first_process_name):
        """Refreshing on /{name} re-selects the same process."""
        page = browser_context.new_page()
        page.goto(f"{self.BASE}/{first_process_name}", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => document.querySelector('.iframe-container.active') !== null",
            timeout=10000,
        )
        # Refresh the page
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "() => document.querySelector('.iframe-container.active') !== null",
            timeout=10000,
        )
        # Process should still be selected
        active_btn = page.locator(f'button.process-button.active[data-name="{first_process_name}"]')
        assert active_btn.count() == 1
        assert not page.locator("#welcome").is_visible()
        page.screenshot(path="/Volumes/T9/darrenoakey/src/auto-gui/local/smoke_refresh.png")
