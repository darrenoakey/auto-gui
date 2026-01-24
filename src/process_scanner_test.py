"""Tests for process_scanner module."""
from unittest.mock import patch

from process_scanner import (
    get_auto_state,
    get_process_workdir,
    get_registered_process_names,
    parse_auto_ps_output,
    run_auto_ps,
    scan_processes,
)


class TestParseAutoPsOutput:
    def test_parses_standard_output(self):
        output = """NAME                       PID   PORT
claude_server             2842  40123
daz-cad-2                19444   8765
web-summary               2867   8889"""
        result = parse_auto_ps_output(output)
        assert len(result) == 3
        assert result[0] == {"name": "claude_server", "pid": 2842, "port": 40123}
        assert result[1] == {"name": "daz-cad-2", "pid": 19444, "port": 8765}
        assert result[2] == {"name": "web-summary", "pid": 2867, "port": 8889}

    def test_handles_dash_port(self):
        output = """NAME                       PID   PORT
calendar-display          2900      -
discord_events           94853      -"""
        result = parse_auto_ps_output(output)
        assert len(result) == 2
        assert result[0]["port"] is None
        assert result[1]["port"] is None

    def test_handles_empty_output(self):
        result = parse_auto_ps_output("")
        assert result == []

    def test_handles_header_only(self):
        output = "NAME                       PID   PORT"
        result = parse_auto_ps_output(output)
        assert result == []

    def test_handles_mixed_ports(self):
        output = """NAME                       PID   PORT
with-port                 1234   8080
no-port                   5678      -
another-port              9012   3000"""
        result = parse_auto_ps_output(output)
        assert len(result) == 3
        assert result[0]["port"] == 8080
        assert result[1]["port"] is None
        assert result[2]["port"] == 3000


class TestRunAutoPs:
    def test_calls_auto_command(self):
        with patch("process_scanner.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "NAME PID PORT\ntest 123 8080"
            result = run_auto_ps()
            mock_run.assert_called_once_with(
                ["auto", "-q", "ps"],
                capture_output=True,
                text=True,
            )
            assert result == "NAME PID PORT\ntest 123 8080"


class TestGetAutoState:
    def test_returns_empty_when_file_missing(self, tmp_path):
        with patch("process_scanner.AUTO_STATE_PATH", tmp_path / "nonexistent.json"):
            result = get_auto_state()
            assert result == {"processes": {}}

    def test_loads_state_file(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {"test": {"workdir": "/path"}}}')
        with patch("process_scanner.AUTO_STATE_PATH", state_file):
            result = get_auto_state()
            assert result == {"processes": {"test": {"workdir": "/path"}}}


class TestGetProcessWorkdir:
    def test_returns_workdir_when_present(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {"myapp": {"workdir": "/home/user/app"}}}')
        with patch("process_scanner.AUTO_STATE_PATH", state_file):
            result = get_process_workdir("myapp")
            assert result == "/home/user/app"

    def test_returns_none_when_no_workdir(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {"myapp": {"pid": 123}}}')
        with patch("process_scanner.AUTO_STATE_PATH", state_file):
            result = get_process_workdir("myapp")
            assert result is None

    def test_returns_none_when_process_missing(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {}}')
        with patch("process_scanner.AUTO_STATE_PATH", state_file):
            result = get_process_workdir("nonexistent")
            assert result is None


class TestGetRegisteredProcessNames:
    def test_returns_empty_set_when_no_processes(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {}}')
        with patch("process_scanner.AUTO_STATE_PATH", state_file):
            result = get_registered_process_names()
            assert result == set()

    def test_returns_all_registered_names(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {"app1": {}, "app2": {}, "app3": {}}}')
        with patch("process_scanner.AUTO_STATE_PATH", state_file):
            result = get_registered_process_names()
            assert result == {"app1", "app2", "app3"}


class TestScanProcesses:
    def test_filters_and_enriches_processes(self, tmp_path):
        auto_output = """NAME                       PID   PORT
app-with-port             1234   8080
app-no-port               5678      -
another-app               9012   3000"""

        state_file = tmp_path / "state.json"
        state_file.write_text('{"processes": {"app-with-port": {"workdir": "/path/to/app"}}}')

        with (
            patch("process_scanner.run_auto_ps", return_value=auto_output),
            patch("process_scanner.AUTO_STATE_PATH", state_file),
        ):
            result = scan_processes()

            # Should only have processes with ports
            assert len(result) == 2
            assert result[0]["name"] == "app-with-port"
            assert result[0]["port"] == 8080
            assert result[0]["workdir"] == "/path/to/app"
            assert result[1]["name"] == "another-app"
            assert result[1]["port"] == 3000
            assert result[1]["workdir"] is None
