"""
Icon generator for auto-gui.
Generates app summaries and icons using daz-agent-sdk.

Icon generation runs in a separate background worker, completely decoupled from
the main server. Items needing icons are added to a queue, and a worker processes
them one at a time without blocking the server.

Design principles:
- NO timestamp checking - only check if files exist
- If any step runs, all downstream steps run (force_downstream boolean)
- Atomic swap for image files - old icon stays visible until new one is ready
- Generate to temp file, then swap atomically
"""
import asyncio
from pathlib import Path
from typing import Optional

from daz_agent_sdk import agent, Tier

from state_manager import (
    get_icons_dir,
    get_process,
    get_project_root,
    get_website,
    update_process,
    update_website,
)


# Global version counter for change notifications
_change_version = 0

# Retry behavior for transient Claude SDK rate limit stream events
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_INITIAL_BACKOFF_SECONDS = 1.0
RATE_LIMIT_MAX_BACKOFF_SECONDS = 30.0
ICON_IMAGE_PROVIDER = "spark"

# Icon generation queue - items are (name, is_website) tuples
_icon_queue: asyncio.Queue = None
_worker_task: asyncio.Task = None
_queued_items: set = None  # Track items currently in queue to prevent duplicates

# Track consecutive failures per item — prevents infinite 30s retry loops
# Maps (name, is_website) -> failure count
_failure_counts: dict = {}
MAX_CONSECUTIVE_FAILURES = 3
FAILURE_COOLDOWN_SCANS = 20  # Skip this many scan cycles after max failures (~10 min)


def get_change_version() -> int:
    """Returns current change version for polling."""
    return _change_version


def increment_change_version():
    """Increments change version to signal updates."""
    global _change_version
    _change_version += 1


def get_icon_queue() -> asyncio.Queue:
    """Returns the icon generation queue, creating it if needed."""
    global _icon_queue, _queued_items
    if _icon_queue is None:
        _icon_queue = asyncio.Queue()
    if _queued_items is None:
        _queued_items = set()
    return _icon_queue


async def icon_worker():
    """
    Background worker that processes icon generation requests from the queue.
    Runs completely independently from the main server loop.
    Processes one item at a time to avoid overwhelming the system.

    Transparent background deps are loaded lazily by the SDK on first use,
    not eagerly at startup. This saves ~1GB of RAM when no icons need generating.
    """
    global _queued_items
    queue = get_icon_queue()
    print("[icon_worker] Started")

    while True:
        try:
            # Wait for an item from the queue
            name, is_website = await queue.get()
            print(f"[icon_worker] Processing: {name} (website={is_website})")

            item_key = (name, is_website)
            try:
                if is_website:
                    await process_website_async(name, get_website(name).get("url", ""))
                else:
                    process = get_process(name)
                    if process:
                        await process_app_async(
                            name,
                            process.get("port"),
                            process.get("workdir")
                        )
            except Exception as e:
                print(f"[icon_worker] Error processing {name}: {e}")
                # Reset status so it doesn't stay stuck at "generating"
                try:
                    if is_website:
                        update_website(name, icon_status="failed")
                    else:
                        update_process(name, icon_status="failed")
                except Exception:
                    pass
            finally:
                queue.task_done()
                # Remove from tracking set so it can be queued again if needed
                if _queued_items is not None:
                    _queued_items.discard((name, is_website))

            # Track success/failure by checking if icon was actually created
            if has_icon(name):
                _failure_counts.pop(item_key, None)
            else:
                count = _failure_counts.get(item_key, 0) + 1
                _failure_counts[item_key] = count
                if count >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"[icon_worker] {name} failed {count} times, "
                        f"backing off for ~{FAILURE_COOLDOWN_SCANS * 30}s"
                    )

        except asyncio.CancelledError:
            print("[icon_worker] Shutting down")
            break
        except Exception as e:
            print(f"[icon_worker] Unexpected error: {e}")
            await asyncio.sleep(1)  # Prevent tight loop on errors


def start_icon_worker() -> asyncio.Task:
    """Starts the icon worker background task."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(icon_worker())
    return _worker_task


def stop_icon_worker():
    """Stops the icon worker background task."""
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()


def queue_icon_generation(name: str, is_website: bool = False, force: bool = False):
    """
    Queues an item for icon generation.
    This is non-blocking and returns immediately.
    Prevents duplicates - won't queue if already in queue.
    Backs off after repeated failures to avoid infinite retry loops.

    Args:
        force: If True, ignore failure cooldown (e.g. for manual scans).
    """
    global _queued_items
    queue = get_icon_queue()

    item = (name, is_website)

    # Check if already queued
    if _queued_items is not None and item in _queued_items:
        return  # Already queued, skip silently

    # Back off after repeated failures (unless forced)
    if not force and item in _failure_counts:
        count = _failure_counts[item]
        if count >= MAX_CONSECUTIVE_FAILURES:
            # Only retry every FAILURE_COOLDOWN_SCANS cycles
            # Increment count as a scan counter; reset when it hits the threshold
            _failure_counts[item] = count + 1
            if count < MAX_CONSECUTIVE_FAILURES + FAILURE_COOLDOWN_SCANS:
                return  # Still in cooldown
            # Cooldown expired — reset and allow retry
            _failure_counts[item] = 0
            print(f"[icon_queue] Cooldown expired, retrying: {name}")

    try:
        queue.put_nowait(item)
        if _queued_items is not None:
            _queued_items.add(item)
        print(f"[icon_queue] Queued: {name} (website={is_website})")
    except asyncio.QueueFull:
        print(f"[icon_queue] Queue full, skipping: {name}")


def get_local_dir() -> Path:
    """Returns the local directory for summaries and icons."""
    local_dir = get_project_root() / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    return local_dir


def get_summary_path(name: str) -> Path:
    """Returns path to summary file for a process."""
    return get_local_dir() / f"{name}_summary.txt"


def get_icon_path(name: str) -> Path:
    """Returns path to final PNG icon file."""
    return get_icons_dir() / f"{name}.png"


def get_icon_prompt_path(name: str) -> Path:
    """Returns path to icon prompt file."""
    return get_local_dir() / f"{name}_icon_prompt.txt"


def has_summary(name: str) -> bool:
    """Checks if a summary exists."""
    return get_summary_path(name).exists()


def has_icon_prompt(name: str) -> bool:
    """Checks if an icon prompt exists."""
    return get_icon_prompt_path(name).exists()


def has_icon(name: str) -> bool:
    """Checks if a final PNG icon exists."""
    return get_icon_path(name).exists()


def atomic_swap(temp_path: Path, final_path: Path) -> bool:
    """
    Atomically swaps a temp file into place.
    - Renames final_path to final_path.old (if exists)
    - Renames temp_path to final_path
    - Deletes final_path.old

    Returns True on success, False on failure.
    The old file stays in place until the new one is ready.
    """
    old_path = final_path.with_suffix(final_path.suffix + ".old")

    try:
        # Move existing file to .old (if exists)
        if final_path.exists():
            final_path.rename(old_path)

        # Move temp file to final location
        temp_path.rename(final_path)

        # Delete old file
        if old_path.exists():
            old_path.unlink()

        return True
    except Exception as e:
        print(f"Atomic swap failed: {e}")
        # Try to restore old file if something went wrong
        if old_path.exists() and not final_path.exists():
            try:
                old_path.rename(final_path)
            except Exception:
                pass
        return False


def load_icon_prompt(name: str) -> Optional[str]:
    """Loads the icon prompt for a process."""
    path = get_icon_prompt_path(name)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def load_summary(name: str) -> Optional[str]:
    """Loads the summary for a process."""
    path = get_summary_path(name)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def save_summary(name: str, summary: str) -> None:
    """Saves a summary for a process. Raises ValueError if summary is empty."""
    if not summary or not summary.strip():
        raise ValueError(f"[{name}] Refusing to save empty summary")
    path = get_summary_path(name)
    path.write_text(summary, encoding="utf-8")


def fetch_app_homepage(port: int) -> Optional[str]:
    """Fetches the homepage HTML of an app to understand what it does."""
    import httpx
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(f"http://localhost:{port}/")
            if response.status_code == 200:
                return response.text[:5000]  # First 5000 chars
    except Exception:
        pass
    return None


def find_readme(workdir: Optional[str]) -> Optional[str]:
    """Finds and reads README.md from a process workdir."""
    if not workdir:
        return None

    for readme_name in ["README.md", "readme.md", "README.txt", "readme.txt"]:
        readme_path = Path(workdir) / readme_name
        if readme_path.exists():
            try:
                content = readme_path.read_text(encoding="utf-8")
                return content[:3000]  # First 3000 chars
            except Exception:
                pass
    return None


def is_rate_limit_event_error(error: Exception) -> bool:
    """Returns True if an exception indicates a transient rate-limit stream event."""
    return "rate_limit_event" in str(error)


async def query_text_with_backoff(prompt: str) -> str:
    """
    Runs an agent query and returns the response text.
    Retries with exponential backoff for transient rate_limit_event errors.
    """
    attempt = 0
    backoff_seconds = RATE_LIMIT_INITIAL_BACKOFF_SECONDS

    while True:
        try:
            response = await agent.ask(prompt, tier=Tier.HIGH, cwd=get_project_root())
            print(f"[agent] response.text={response.text!r:.200} model={response.model_used}")
            text = response.text.strip()
            if not text:
                raise ValueError("AI returned empty response")
            return text
        except Exception as error:
            if not is_rate_limit_event_error(error) or attempt >= RATE_LIMIT_MAX_RETRIES:
                raise

            attempt += 1
            sleep_seconds = min(backoff_seconds, RATE_LIMIT_MAX_BACKOFF_SECONDS)
            print(
                f"[agent] rate_limit_event; retrying in {sleep_seconds:.1f}s "
                f"(attempt {attempt}/{RATE_LIMIT_MAX_RETRIES})"
            )
            await asyncio.sleep(sleep_seconds)
            backoff_seconds = min(backoff_seconds * 2, RATE_LIMIT_MAX_BACKOFF_SECONDS)


async def generate_summary_async(name: str, port: Optional[int], workdir: Optional[str]) -> str:
    """
    Generates a summary for an app using daz-agent-sdk.
    """
    # Gather context
    context_parts = [f"Process name: {name}"]

    if port:
        homepage = fetch_app_homepage(port)
        if homepage:
            context_parts.append(f"Homepage HTML (excerpt):\n{homepage[:2000]}")

    readme = find_readme(workdir)
    if readme:
        context_parts.append(f"README content:\n{readme}")

    context = "\n\n".join(context_parts)

    prompt = f"""Based on the following information about an application, write a brief 1-2 sentence summary describing what this app does. Be specific and practical.

{context}

Write ONLY the summary, nothing else. Keep it under 100 words. You MUST produce a summary — if information is limited, summarize based on the process name alone."""

    return await query_text_with_backoff(prompt)


async def generate_summary_for_website_async(name: str, url: str) -> str:
    """
    Generates a summary for a website by asking Claude to visit it.
    """
    prompt = f"""Write a brief 1-2 sentence summary of a website named "{name}" at URL {url}.

Do NOT visit or fetch the URL. Just infer what the site does from its name and URL.

Write ONLY the summary text, nothing else. Keep it under 100 words."""

    return await query_text_with_backoff(prompt)


async def generate_icon_description_async(name: str, summary: str) -> str:
    """
    Generates an icon description based on app summary.
    Returns the AI-generated description plus mandatory suffix requirements.
    """
    prompt = f"""I need to create an app icon for "{name}".

App summary: {summary}

Describe a 3D ISOMETRIC illustration of a SUBSTANTIAL PHYSICAL OBJECT that represents this app.

Requirements:
- Must be a REAL, SOLID, 3D OBJECT - something you could pick up and hold
- Isometric 3D perspective with clear depth, shading, and volume
- ONE main object only (a machine, device, container, tool, furniture, etc.)
- The object must look SUBSTANTIAL and SOLID - not flat, not abstract, not a stream of shapes

Think of chunky physical objects: a 3D printer, a toolbox, a safe, a vending machine, a jukebox, a telescope, a robot, a treasure chest, a globe on a stand, a vintage radio, a filing cabinet, etc.

Examples of GOOD descriptions:
- "A chunky 3D isometric vintage radio with knobs and speaker grille"
- "A solid 3D isometric wooden treasure chest with metal bands and lock"
- "A substantial 3D isometric robot with boxy body and articulated arms"
- "A hefty 3D isometric telescope on a wooden tripod"

BAD examples (DO NOT do these):
- "A letter D" (text is not an object)
- "Flowing beads or particles" (not a solid object)
- "Abstract shapes" (not a physical object)
- "Flat 2D icon" (must be clearly 3D with depth)

Respond with ONLY the object description (1 sentence describing the 3D object), nothing else. Do NOT mention the background."""

    ai_description = await query_text_with_backoff(prompt)

    # Add mandatory suffix with strict background and rendering requirements
    full_prompt = f"""{ai_description}

MANDATORY REQUIREMENTS:
- SIZE: This will display as a TINY 32x32 pixel icon. Use BOLD, SIMPLE shapes only. NO fine details, NO small text, NO intricate patterns. Think chunky and iconic.
- Background: COMPLETELY FLAT solid color (bright teal, coral, orange, or purple). NO gradients, NO lighting effects, NO shadows on background, NO variation whatsoever. The background must be a single uniform color designed to be easily removed.
- Object: Rendered in 3D isometric style with clear depth and shading ON THE OBJECT ONLY.
- The background color must be VERY DIFFERENT from any color in the object (high contrast).
- Fill the frame - object as large as possible."""

    return full_prompt


def save_icon_prompt(name: str, prompt: str) -> None:
    """Saves the icon prompt."""
    path = get_icon_prompt_path(name)
    path.write_text(prompt, encoding="utf-8")


async def generate_icon_async(prompt: str, output_path: Path) -> bool:
    """
    Calls agent.image to create an icon with transparent background.
    Writes directly to output_path as a transparent PNG.
    """
    try:
        await agent.image(
            prompt,
            width=128,
            height=128,
            output=str(output_path),
            transparent=True,
            provider=ICON_IMAGE_PROVIDER,
        )
        return output_path.exists()
    except Exception as e:
        print(f"Icon generation failed: {e}")
        return False


async def process_app_async(name: str, port: Optional[int], workdir: Optional[str]) -> None:
    """
    Processes an app through cascading steps.
    Steps: summary → icon_prompt → png (transparent, generated in one step)

    Design: NO timestamp checking. Only check if files exist.
    If any step runs, all downstream steps run (force_downstream).
    Images use atomic swap - old icon stays visible until new one is ready.

    To regenerate: delete the prompt file. Summary stays, downstream regenerates.
    """
    force_downstream = False

    # Step 1: Generate summary if missing
    if not has_summary(name):
        print(f"[{name}] Generating summary...")
        try:
            summary = await generate_summary_async(name, port, workdir)
            save_summary(name, summary)
            update_process(name, description=summary)
            increment_change_version()
            force_downstream = True
        except Exception as e:
            print(f"[{name}] Failed to generate summary: {e}")
            return
    else:
        summary = load_summary(name)
        process = get_process(name)
        if process and not process.get("description"):
            update_process(name, description=summary)

    # Step 2: Generate icon prompt if missing OR forced
    if force_downstream or not has_icon_prompt(name):
        summary = load_summary(name)
        if not summary:
            return
        print(f"[{name}] Generating icon prompt...")
        try:
            icon_prompt = await generate_icon_description_async(name, summary)
            save_icon_prompt(name, icon_prompt)
            force_downstream = True
        except Exception as e:
            print(f"[{name}] Failed to generate icon prompt: {e}")
            return

    # Step 3: Generate transparent PNG if missing OR forced
    png_path = get_icon_path(name)
    if force_downstream or not has_icon(name):
        icon_prompt = load_icon_prompt(name)
        if not icon_prompt:
            return
        print(f"[{name}] Generating icon...")
        update_process(name, icon_status="generating")

        # Generate to temp file, then atomic swap
        temp_png = png_path.with_name(f"{png_path.stem}.tmp.png")
        success = await generate_icon_async(icon_prompt, temp_png)

        if not success or not temp_png.exists():
            print(f"[{name}] Failed to generate icon")
            update_process(name, icon_status="failed")
            if temp_png.exists():
                temp_png.unlink()
            return

        # Atomic swap - old png stays until new one is ready
        if not atomic_swap(temp_png, png_path):
            print(f"[{name}] Failed to swap PNG")
            update_process(name, icon_status="failed")
            return

        update_process(name, icon_path=str(png_path), icon_status="ready")
        increment_change_version()
    else:
        # Ensure status is ready if PNG exists
        process = get_process(name)
        if process and process.get("icon_status") != "ready":
            update_process(name, icon_status="ready", icon_path=str(png_path))


async def process_website_async(name: str, url: str) -> None:
    """
    Processes a website through cascading steps.
    Steps: summary → icon_prompt → png (transparent, generated in one step)

    Design: NO timestamp checking. Only check if files exist.
    If any step runs, all downstream steps run (force_downstream).
    Images use atomic swap - old icon stays visible until new one is ready.

    To regenerate: delete the prompt file. Summary stays, downstream regenerates.
    """
    force_downstream = False

    # Step 1: Generate summary if missing
    if not has_summary(name):
        print(f"[{name}] Generating summary...")
        try:
            summary = await generate_summary_for_website_async(name, url)
            save_summary(name, summary)
            update_website(name, description=summary)
            increment_change_version()
            force_downstream = True
        except Exception as e:
            print(f"[{name}] Failed to generate summary: {e}")
            return
    else:
        summary = load_summary(name)
        website = get_website(name)
        if website and not website.get("description"):
            update_website(name, description=summary)

    # Step 2: Generate icon prompt if missing OR forced
    if force_downstream or not has_icon_prompt(name):
        summary = load_summary(name)
        if not summary:
            return
        print(f"[{name}] Generating icon prompt...")
        try:
            icon_prompt = await generate_icon_description_async(name, summary)
            save_icon_prompt(name, icon_prompt)
            force_downstream = True
        except Exception as e:
            print(f"[{name}] Failed to generate icon prompt: {e}")
            return

    # Step 3: Generate transparent PNG if missing OR forced
    png_path = get_icon_path(name)
    if force_downstream or not has_icon(name):
        icon_prompt = load_icon_prompt(name)
        if not icon_prompt:
            return
        print(f"[{name}] Generating icon...")
        update_website(name, icon_status="generating")

        # Generate to temp file, then atomic swap
        temp_png = png_path.with_name(f"{png_path.stem}.tmp.png")
        success = await generate_icon_async(icon_prompt, temp_png)

        if not success or not temp_png.exists():
            print(f"[{name}] Failed to generate icon")
            update_website(name, icon_status="failed")
            if temp_png.exists():
                temp_png.unlink()
            return

        # Atomic swap - old png stays until new one is ready
        if not atomic_swap(temp_png, png_path):
            print(f"[{name}] Failed to swap PNG")
            update_website(name, icon_status="failed")
            return

        update_website(name, icon_path=str(png_path), icon_status="ready")
        increment_change_version()
    else:
        # Ensure status is ready if PNG exists
        website = get_website(name)
        if website and website.get("icon_status") != "ready":
            update_website(name, icon_status="ready", icon_path=str(png_path))


async def generate_icon_for_process(name: str) -> bool:
    """
    Generates summary and icon for a process.
    Returns True on success.
    """
    process = get_process(name)
    if not process:
        print(f"Process {name} not found in state")
        return False

    port = process.get("port")
    workdir = process.get("workdir")

    await process_app_async(name, port, workdir)
    return True


async def generate_icon_for_website(name: str) -> bool:
    """
    Generates summary and icon for a website.
    Returns True on success.
    """
    website = get_website(name)
    if not website:
        print(f"Website {name} not found in state")
        return False

    url = website.get("url")
    if not url:
        print(f"Website {name} has no URL")
        return False

    await process_website_async(name, url)
    return True
