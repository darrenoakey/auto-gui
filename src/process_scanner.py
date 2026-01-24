"""
Process scanner for auto-gui.
Parses output from 'auto -q ps' command to get running processes.
"""
import json
import subprocess
from pathlib import Path
from typing import Optional


# Path to auto's state file for workdir information
AUTO_STATE_PATH = Path.home() / "local" / "auto" / "local" / "state.json"


def parse_auto_ps_output(output: str) -> list[dict]:
    """
    Parses the output of 'auto -q ps' command.

    Expected format:
    NAME                       PID   PORT
    process-name              1234   8080
    another-process          5678      -

    Returns list of dicts with keys: name, pid, port (port is None if '-')
    """
    lines = output.strip().split("\n")
    if len(lines) < 2:
        return []

    # Skip header line
    processes = []
    for line in lines[1:]:
        if not line.strip():
            continue

        # Parse fixed-width columns
        parts = line.split()
        if len(parts) < 3:
            continue

        name = parts[0]
        try:
            pid = int(parts[1])
        except ValueError:
            continue

        port_str = parts[2]
        port = None if port_str == "-" else int(port_str)

        processes.append({
            "name": name,
            "pid": pid,
            "port": port,
        })

    return processes


def run_auto_ps() -> str:
    """Runs 'auto -q ps' and returns the output."""
    result = subprocess.run(
        ["auto", "-q", "ps"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def get_auto_state() -> dict:
    """Loads auto's state file for workdir information."""
    if not AUTO_STATE_PATH.exists():
        return {"processes": {}}
    with open(AUTO_STATE_PATH, "r") as f:
        return json.load(f)


def get_process_workdir(name: str) -> Optional[str]:
    """Gets the workdir for a process from auto's state file."""
    state = get_auto_state()
    process = state.get("processes", {}).get(name)
    if process and isinstance(process, dict):
        return process.get("workdir")
    return None


def scan_processes() -> list[dict]:
    """
    Scans for running auto processes with ports.

    Returns list of dicts with keys: name, pid, port, workdir
    Filters out processes without ports.
    """
    output = run_auto_ps()
    processes = parse_auto_ps_output(output)

    # Filter to only processes with ports and add workdir
    result = []
    for proc in processes:
        if proc["port"] is not None:
            proc["workdir"] = get_process_workdir(proc["name"])
            result.append(proc)

    return result
