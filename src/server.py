"""
FastAPI server for auto-gui.
Web dashboard for auto-managed processes.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from html_checker import check_port_returns_html
from icon_generator import (
    get_change_version,
    has_icon,
    queue_icon_generation,
    start_icon_worker,
    stop_icon_worker,
)
from process_scanner import get_registered_process_names, scan_processes
from state_manager import (
    get_all_visible_items,
    get_icons_dir,
    get_last_scan,
    get_process,
    get_project_root,
    get_visible_html_processes,
    list_websites,
    mark_process_dead,
    mark_process_invisible,
    update_last_scan,
    update_process,
)


# Scan interval in seconds
SCAN_INTERVAL = 600  # 10 minutes

# Process name to exclude (self)
SELF_NAME = "auto-gui"


async def scan_and_update_processes(trigger_icons: bool = True):
    """Scans for processes and updates state.

    Args:
        trigger_icons: If True, trigger icon generation for items without icons.
                      Set to False during startup to avoid blocking.
    """
    processes = scan_processes()
    current_names = set()

    # Get all processes registered in auto (whether running or not)
    registered_names = get_registered_process_names()

    for proc in processes:
        name = proc["name"]
        port = proc["port"]

        # Skip self
        if name == SELF_NAME:
            continue

        current_names.add(name)

        # Get existing state
        existing = get_process(name)

        # Once a process is identified as HTML, it stays that way forever.
        # Only check HTML for processes not already known to be GUI apps.
        if existing and existing.get("is_html"):
            is_html = True
        else:
            is_html = await check_port_returns_html(port)

        # Update state - process is running so is_dead=False
        update_process(
            name=name,
            port=port,
            is_html=is_html,
            visible=True,
            is_dead=False,
            workdir=proc.get("workdir"),
        )

        # Queue icon generation if HTML and icon doesn't exist
        # (no timestamp checks - only generate if files are missing)
        if trigger_icons and is_html and not has_icon(name):
            queue_icon_generation(name, is_website=False)

    # Handle processes that are visible but not currently running
    for proc in get_visible_html_processes():
        if proc["name"] not in current_names:
            # Check if still registered in auto's state
            if proc["name"] in registered_names:
                # Still registered, just dead - mark as dead but keep visible
                mark_process_dead(proc["name"])
            else:
                # Completely removed from auto - hide it
                mark_process_invisible(proc["name"])

    # Queue website icons (only if missing - no timestamp checks)
    if trigger_icons:
        for website in list_websites():
            wname = website["name"]
            if not has_icon(wname):
                queue_icon_generation(wname, is_website=True)

    update_last_scan()


# Icon generation is imported from icon_generator module


async def background_scanner():
    """Background task that scans for processes periodically."""
    while True:
        try:
            await scan_and_update_processes()
        except Exception as e:
            print(f"Error scanning processes: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifecycle manager for the FastAPI app."""
    # Startup: run initial scan WITHOUT triggering icon generation
    # This ensures the server starts quickly and is responsive
    await scan_and_update_processes(trigger_icons=False)

    # Start the icon generation worker (processes queue in background)
    start_icon_worker()

    # Start background scanner - it will queue icon generation on first run
    scanner_task = asyncio.create_task(background_scanner())

    print("Server ready - icon generation will run in background")
    yield

    # Shutdown: stop icon worker and cancel background scanner
    stop_icon_worker()
    scanner_task.cancel()
    try:
        await scanner_task
    except asyncio.CancelledError:
        pass


# Create FastAPI app
app = FastAPI(
    title="Auto-GUI",
    description="Web dashboard for auto-managed processes",
    lifespan=lifespan,
)

# Get paths
project_root = get_project_root()
static_dir = project_root / "static"
templates_dir = project_root / "templates"
icons_dir = get_icons_dir()

# Mount static files
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/icons", StaticFiles(directory=str(icons_dir)), name="icons")

# Setup templates
templates = Jinja2Templates(directory=str(templates_dir))


import os
SERVER_PID = os.getpid()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main dashboard page."""
    items = get_all_visible_items()
    last_scan = get_last_scan()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "processes": items,
            "last_scan": last_scan,
            "server_pid": SERVER_PID,
        },
    )


@app.get("/api/processes")
async def api_processes():
    """Return current processes and websites as JSON for polling."""
    items = get_all_visible_items()
    last_scan = get_last_scan()
    return {
        "processes": items,
        "last_scan": last_scan,
        "server_pid": SERVER_PID,
        "change_version": get_change_version(),
    }


@app.post("/api/scan")
async def api_scan():
    """Trigger a manual process scan."""
    await scan_and_update_processes()
    return {"status": "ok", "last_scan": get_last_scan()}
