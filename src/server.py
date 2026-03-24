"""
FastAPI server for auto-gui.
Web dashboard for auto-managed processes.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
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
from process_scanner import scan_processes
from state_manager import (
    StateError,
    get_all_visible_items,
    get_icons_dir,
    get_last_scan,
    get_process,
    get_project_root,
    get_visible_html_processes,
    list_websites,
    mark_process_invisible,
    update_last_scan,
    update_process,
    update_website,
)


# Scan interval in seconds
SCAN_INTERVAL = 30  # 30 seconds

# Process name to exclude (self)
SELF_NAME = "auto-gui"


async def scan_and_update_processes(trigger_icons: bool = True, force_icons: bool = False):
    """Scans for processes and updates state.

    Args:
        trigger_icons: If True, trigger icon generation for items without icons.
                      Set to False during startup to avoid blocking.
        force_icons: If True, ignore failure cooldown and retry failed items.
    """
    # Run the blocking subprocess call in a thread to avoid freezing the event loop
    loop = asyncio.get_event_loop()
    processes = await loop.run_in_executor(None, scan_processes)
    current_names = set()

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
        # But always recheck protocol - a service can switch HTTP <-> HTTPS.
        if existing and existing.get("is_html"):
            is_html = True
            _, detected_protocol = await check_port_returns_html(port)
            protocol = detected_protocol or existing.get("protocol", "http")
        else:
            is_html, protocol = await check_port_returns_html(port)
            if protocol is None:
                protocol = "http"  # Default fallback

        # Update state - process is running so is_dead=False
        update_process(
            name=name,
            port=port,
            is_html=is_html,
            visible=True,
            is_dead=False,
            workdir=proc.get("workdir"),
            protocol=protocol,
        )

        # Icon queuing for running processes is handled below in the
        # get_visible_html_processes() loop, which covers ALL visible HTML
        # processes (running + dead) in one place.

    # Handle processes that are visible but not currently running
    for proc in get_visible_html_processes():
        name = proc["name"]
        if name not in current_names:
            # Not running - hide it from the dashboard
            mark_process_invisible(name)

        # Queue icon generation for ANY visible HTML process without an icon,
        # whether running or dead. Dead/stopped processes still need icons.
        if trigger_icons and not has_icon(name):
            queue_icon_generation(name, is_website=False, force=force_icons)
        elif has_icon(name) and proc.get("icon_status") != "ready":
            update_process(name, icon_status="ready")

    # Queue website icons (only if missing - no timestamp checks)
    if trigger_icons:
        for website in list_websites():
            wname = website["name"]
            if not has_icon(wname):
                queue_icon_generation(wname, is_website=True, force=force_icons)
            elif website.get("icon_status") != "ready":
                update_website(wname, icon_status="ready")

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


@app.exception_handler(StateError)
async def state_error_handler(_request: Request, exc: StateError):
    """Handle state file errors with a clear error message."""
    return PlainTextResponse(
        f"State File Error\n\n{exc}\n\nTry: auto -q restart auto-gui",
        status_code=503,
    )


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
            "selected_process": None,
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
    """Trigger a manual process scan. Retries previously-failed icon generation."""
    await scan_and_update_processes(force_icons=True)
    return {"status": "ok", "last_scan": get_last_scan()}


@app.get("/{name}", response_class=HTMLResponse)
async def process_page(request: Request, name: str):
    """Render the dashboard with a specific process selected via URL."""
    items = get_all_visible_items()
    last_scan = get_last_scan()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "processes": items,
            "last_scan": last_scan,
            "server_pid": SERVER_PID,
            "selected_process": name,
        },
    )
