"""Tests for state_manager module."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from state_manager import (
    _load_state_file,
    _save_state_file,
    add_website,
    get_all_visible_items,
    get_icons_dir,
    get_last_scan,
    get_process,
    get_project_root,
    get_state_path,
    get_visible_html_processes,
    get_website,
    list_websites,
    load_state,
    mark_process_invisible,
    remove_website,
    save_state,
    update_last_scan,
    update_process,
    update_website,
)


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create a temporary state directory and patch get_project_root."""
    state_dir = tmp_path / "local"
    state_dir.mkdir(parents=True)
    with patch("state_manager.get_project_root", return_value=tmp_path):
        yield tmp_path


class TestGetProjectRoot:
    def test_returns_path(self):
        result = get_project_root()
        assert isinstance(result, Path)
        assert result.is_absolute()


class TestGetStatePath:
    def test_returns_local_state_json(self):
        result = get_state_path()
        assert result.name == "state.json"
        assert result.parent.name == "local"


class TestGetIconsDir:
    def test_creates_and_returns_icons_dir(self, temp_state_dir):
        icons_dir = get_icons_dir()
        assert icons_dir.exists()
        assert icons_dir.name == "icons"
        assert icons_dir.parent.name == "local"


class TestLoadSaveStateFile:
    def test_load_missing_file_returns_default(self, temp_state_dir):
        result = _load_state_file()
        assert result == {"processes": {}, "websites": {}, "last_scan": None}

    def test_save_and_load_roundtrip(self, temp_state_dir):
        data = {"processes": {"test": {"name": "test"}}, "websites": {}, "last_scan": "2025-01-24T12:00:00"}
        _save_state_file(data)
        result = _load_state_file()
        assert result == data

    def test_load_adds_missing_keys(self, temp_state_dir):
        state_path = get_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w") as f:
            json.dump({"other": "data"}, f)
        result = _load_state_file()
        assert "processes" in result
        assert "last_scan" in result


class TestLoadSaveState:
    def test_load_state_returns_dict(self, temp_state_dir):
        result = load_state()
        assert isinstance(result, dict)
        assert "processes" in result

    def test_save_state_persists(self, temp_state_dir):
        state = {"processes": {"foo": {"name": "foo"}}, "websites": {}, "last_scan": None}
        save_state(state)
        result = load_state()
        assert result == state


class TestGetProcess:
    def test_returns_none_for_missing(self, temp_state_dir):
        result = get_process("nonexistent")
        assert result is None

    def test_returns_process_when_exists(self, temp_state_dir):
        state = {"processes": {"myapp": {"name": "myapp", "port": 8080}}, "last_scan": None}
        save_state(state)
        result = get_process("myapp")
        assert result == {"name": "myapp", "port": 8080}


class TestUpdateProcess:
    def test_creates_new_process(self, temp_state_dir):
        result = update_process("newapp", port=3000)
        assert result["name"] == "newapp"
        assert result["port"] == 3000
        assert result["visible"] is True
        assert result["icon_status"] == "pending"
        assert result["last_seen"] is not None

    def test_updates_existing_process(self, temp_state_dir):
        update_process("myapp", port=8080)
        result = update_process("myapp", is_html=True, icon_status="ready")
        assert result["port"] == 8080
        assert result["is_html"] is True
        assert result["icon_status"] == "ready"

    def test_preserves_unset_fields(self, temp_state_dir):
        update_process("myapp", port=8080, workdir="/path/to/app")
        result = update_process("myapp", is_html=True)
        assert result["port"] == 8080
        assert result["workdir"] == "/path/to/app"


class TestMarkProcessInvisible:
    def test_marks_invisible(self, temp_state_dir):
        update_process("myapp", port=8080, visible=True)
        mark_process_invisible("myapp")
        result = get_process("myapp")
        assert result["visible"] is False

    def test_no_error_for_missing(self, temp_state_dir):
        mark_process_invisible("nonexistent")


class TestGetVisibleHtmlProcesses:
    def test_returns_empty_list_when_none(self, temp_state_dir):
        result = get_visible_html_processes()
        assert result == []

    def test_filters_by_visible_and_is_html(self, temp_state_dir):
        update_process("html-app", port=8080, is_html=True, visible=True)
        update_process("api-app", port=9000, is_html=False, visible=True)
        update_process("hidden-html", port=7000, is_html=True, visible=False)
        result = get_visible_html_processes()
        assert len(result) == 1
        assert result[0]["name"] == "html-app"


class TestLastScan:
    def test_get_last_scan_returns_none_initially(self, temp_state_dir):
        result = get_last_scan()
        assert result is None

    def test_update_last_scan_sets_timestamp(self, temp_state_dir):
        update_last_scan()
        result = get_last_scan()
        assert result is not None
        assert "T" in result


class TestWebsites:
    def test_add_website(self, temp_state_dir):
        result = add_website("my-site", "https://example.com")
        assert result["name"] == "my-site"
        assert result["url"] == "https://example.com"
        assert result["is_website"] is True
        assert result["is_html"] is True

    def test_get_website(self, temp_state_dir):
        add_website("my-site", "https://example.com")
        result = get_website("my-site")
        assert result is not None
        assert result["url"] == "https://example.com"

    def test_get_website_returns_none_for_missing(self, temp_state_dir):
        result = get_website("nonexistent")
        assert result is None

    def test_remove_website(self, temp_state_dir):
        add_website("my-site", "https://example.com")
        result = remove_website("my-site")
        assert result is True
        assert get_website("my-site") is None

    def test_remove_website_returns_false_for_missing(self, temp_state_dir):
        result = remove_website("nonexistent")
        assert result is False

    def test_list_websites(self, temp_state_dir):
        add_website("site1", "https://site1.com")
        add_website("site2", "https://site2.com")
        result = list_websites()
        assert len(result) == 2
        names = {w["name"] for w in result}
        assert names == {"site1", "site2"}

    def test_update_website(self, temp_state_dir):
        add_website("my-site", "https://example.com")
        update_website("my-site", description="Test description", icon_status="ready")
        result = get_website("my-site")
        assert result["description"] == "Test description"
        assert result["icon_status"] == "ready"

    def test_update_website_ignores_nonexistent(self, temp_state_dir):
        # Should not raise an error
        update_website("nonexistent", description="Test")
        assert get_website("nonexistent") is None


class TestGetAllVisibleItems:
    def test_returns_processes_and_websites(self, temp_state_dir):
        update_process("my-process", port=8080, is_html=True, visible=True)
        add_website("my-site", "https://example.com")
        result = get_all_visible_items()
        assert len(result) == 2
        names = {item["name"] for item in result}
        assert names == {"my-process", "my-site"}

    def test_excludes_invisible_processes(self, temp_state_dir):
        update_process("visible", port=8080, is_html=True, visible=True)
        update_process("invisible", port=9000, is_html=True, visible=False)
        result = get_all_visible_items()
        assert len(result) == 1
        assert result[0]["name"] == "visible"

    def test_excludes_non_html_processes(self, temp_state_dir):
        update_process("html", port=8080, is_html=True, visible=True)
        update_process("api", port=9000, is_html=False, visible=True)
        result = get_all_visible_items()
        assert len(result) == 1
        assert result[0]["name"] == "html"
