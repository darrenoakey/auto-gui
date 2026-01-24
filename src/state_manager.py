"""
State manager for auto-gui.
Handles JSON state file management for process metadata and icon state.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def get_project_root() -> Path:
    """Returns the absolute path to the auto-gui project directory."""
    return Path(__file__).parent.parent.absolute()


def get_state_path() -> Path:
    """Returns the path to the state file."""
    return get_project_root() / "local" / "state.json"


def get_icons_dir() -> Path:
    """Returns the path to the icons directory."""
    icons_dir = get_project_root() / "local" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    return icons_dir


def _load_state_file() -> dict:
    """Reads the state file from disk or returns default structure."""
    state_path = get_state_path()
    if not state_path.exists():
        return {"processes": {}, "websites": {}, "last_scan": None}
    with open(state_path, "r") as f:
        data = json.load(f)
        if "processes" not in data:
            data["processes"] = {}
        if "websites" not in data:
            data["websites"] = {}
        if "last_scan" not in data:
            data["last_scan"] = None
        return data


def _save_state_file(state_data: dict) -> None:
    """Writes the state file to disk."""
    state_path = get_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(state_data, f, indent=2)


def load_state() -> dict:
    """Loads the complete state."""
    return _load_state_file()


def save_state(state: dict) -> None:
    """Saves the complete state."""
    _save_state_file(state)


def get_process(name: str) -> Optional[dict]:
    """Returns process state by name or None if not found."""
    state = load_state()
    return state["processes"].get(name)


def update_process(
    name: str,
    port: Optional[int] = None,
    is_html: Optional[bool] = None,
    visible: Optional[bool] = None,
    icon_path: Optional[str] = None,
    icon_status: Optional[str] = None,
    workdir: Optional[str] = None,
    description: Optional[str] = None,
    is_dead: Optional[bool] = None,
) -> dict:
    """
    Updates or creates a process entry.
    Returns the updated process dict.
    """
    state = load_state()

    if name not in state["processes"]:
        state["processes"][name] = {
            "name": name,
            "port": None,
            "is_html": False,
            "visible": True,
            "icon_path": None,
            "icon_status": "pending",
            "last_seen": None,
            "workdir": None,
            "description": None,
            "is_dead": False,
        }

    process = state["processes"][name]

    if port is not None:
        process["port"] = port
    if is_html is not None:
        process["is_html"] = is_html
    if visible is not None:
        process["visible"] = visible
    if icon_path is not None:
        process["icon_path"] = icon_path
    if icon_status is not None:
        process["icon_status"] = icon_status
    if workdir is not None:
        process["workdir"] = workdir
    if description is not None:
        process["description"] = description
    if is_dead is not None:
        process["is_dead"] = is_dead

    process["last_seen"] = datetime.now().isoformat()

    save_state(state)
    return process


def mark_process_invisible(name: str) -> None:
    """Marks a process as invisible (removed from auto entirely)."""
    state = load_state()
    if name in state["processes"]:
        state["processes"][name]["visible"] = False
        save_state(state)


def mark_process_dead(name: str) -> None:
    """Marks a process as dead (registered in auto but not running)."""
    state = load_state()
    if name in state["processes"]:
        state["processes"][name]["is_dead"] = True
        save_state(state)


def get_visible_html_processes() -> list[dict]:
    """Returns list of visible processes that serve HTML."""
    state = load_state()
    return [
        p for p in state["processes"].values()
        if p.get("visible", True) and p.get("is_html", False)
    ]


def update_last_scan() -> None:
    """Updates the last_scan timestamp."""
    state = load_state()
    state["last_scan"] = datetime.now().isoformat()
    save_state(state)


def get_last_scan() -> Optional[str]:
    """Returns the last scan timestamp or None."""
    state = load_state()
    return state.get("last_scan")


def add_website(name: str, url: str) -> dict:
    """
    Adds a manual website entry.
    Returns the website dict.
    """
    state = load_state()
    state["websites"][name] = {
        "name": name,
        "url": url,
        "is_html": True,
        "visible": True,
        "icon_path": None,
        "icon_status": "pending",
        "is_website": True,
    }
    save_state(state)
    return state["websites"][name]


def remove_website(name: str) -> bool:
    """
    Removes a manual website entry.
    Returns True if removed, False if not found.
    """
    state = load_state()
    if name in state["websites"]:
        del state["websites"][name]
        save_state(state)
        return True
    return False


def get_website(name: str) -> Optional[dict]:
    """Returns website by name or None if not found."""
    state = load_state()
    return state.get("websites", {}).get(name)


def update_website(
    name: str,
    url: Optional[str] = None,
    visible: Optional[bool] = None,
    icon_path: Optional[str] = None,
    icon_status: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """Updates a website entry with provided values."""
    state = load_state()
    if name not in state["websites"]:
        return

    website = state["websites"][name]
    if url is not None:
        website["url"] = url
    if visible is not None:
        website["visible"] = visible
    if icon_path is not None:
        website["icon_path"] = icon_path
    if icon_status is not None:
        website["icon_status"] = icon_status
    if description is not None:
        website["description"] = description

    save_state(state)


def list_websites() -> list[dict]:
    """Returns list of all websites."""
    state = load_state()
    return list(state.get("websites", {}).values())


def get_all_visible_items() -> list[dict]:
    """Returns list of all visible items (processes and websites), sorted alphabetically."""
    state = load_state()
    items = []

    # Add visible HTML processes
    for p in state["processes"].values():
        if p.get("visible", True) and p.get("is_html", False):
            items.append(p)

    # Add visible websites
    for w in state.get("websites", {}).values():
        if w.get("visible", True):
            items.append(w)

    # Sort alphabetically by name
    items.sort(key=lambda x: x.get("name", "").lower())

    return items
