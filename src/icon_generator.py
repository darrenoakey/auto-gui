"""
Icon generator for auto-gui.
Generates app summaries and icons using Claude Agent SDK and generate_image.

Icon generation runs in a separate background worker, completely decoupled from
the main server. Items needing icons are added to a queue, and a worker processes
them one at a time without blocking the server.
"""
import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

from state_manager import (
    get_icons_dir,
    get_process,
    get_project_root,
    get_website,
    update_process,
    update_website,
)


# Path to image generation tools
GENERATE_IMAGE = Path.home() / "bin" / "generate_image"
REMOVE_BACKGROUND = Path.home() / "bin" / "remove-background"

# Global version counter for change notifications
_change_version = 0

# Icon generation queue - items are (name, is_website) tuples
_icon_queue: asyncio.Queue = None
_worker_task: asyncio.Task = None
_queued_items: set = None  # Track items currently in queue to prevent duplicates


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
    """
    global _queued_items
    queue = get_icon_queue()
    print("[icon_worker] Started")

    while True:
        try:
            # Wait for an item from the queue
            name, is_website = await queue.get()
            print(f"[icon_worker] Processing: {name} (website={is_website})")

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
            finally:
                queue.task_done()
                # Remove from tracking set so it can be queued again if needed
                if _queued_items is not None:
                    _queued_items.discard((name, is_website))

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


def queue_icon_generation(name: str, is_website: bool = False):
    """
    Queues an item for icon generation.
    This is non-blocking and returns immediately.
    Prevents duplicates - won't queue if already in queue.
    """
    global _queued_items
    queue = get_icon_queue()

    # Check if already queued
    item = (name, is_website)
    if _queued_items is not None and item in _queued_items:
        return  # Already queued, skip silently

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


def get_icon_jpg_path(name: str) -> Path:
    """Returns path to intermediate JPG icon file."""
    return get_icons_dir() / f"{name}.jpg"


def has_summary(name: str) -> bool:
    """Checks if a summary exists."""
    return get_summary_path(name).exists()


def has_icon_prompt(name: str) -> bool:
    """Checks if an icon prompt exists."""
    return get_icon_prompt_path(name).exists()


def has_icon_jpg(name: str) -> bool:
    """Checks if an intermediate JPG icon exists."""
    return get_icon_jpg_path(name).exists()


def has_icon(name: str) -> bool:
    """Checks if a final PNG icon exists."""
    return get_icon_path(name).exists()


def is_newer_than(source: Path, target: Path) -> bool:
    """
    Returns True if source is newer than target, or if target doesn't exist.
    Used for cascading regeneration - if a dependency changes, downstream files
    need to be regenerated.
    """
    if not target.exists():
        return True
    if not source.exists():
        return False
    return source.stat().st_mtime > target.stat().st_mtime


def needs_regeneration(name: str, stage: str) -> bool:
    """
    Check if a stage needs regeneration based on its dependency being newer.
    Stages: 'prompt' (depends on summary), 'jpg' (depends on prompt), 'png' (depends on jpg)
    """
    if stage == 'prompt':
        summary_path = get_summary_path(name)
        prompt_path = get_icon_prompt_path(name)
        return is_newer_than(summary_path, prompt_path)
    elif stage == 'jpg':
        prompt_path = get_icon_prompt_path(name)
        jpg_path = get_icon_jpg_path(name)
        return is_newer_than(prompt_path, jpg_path)
    elif stage == 'png':
        jpg_path = get_icon_jpg_path(name)
        png_path = get_icon_path(name)
        return is_newer_than(jpg_path, png_path)
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
    """Saves a summary for a process."""
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


async def generate_summary_async(name: str, port: Optional[int], workdir: Optional[str]) -> str:
    """
    Generates a summary for an app using Claude Agent SDK.
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

Write ONLY the summary, nothing else. Keep it under 100 words."""

    response_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=[],
            permission_mode="bypassPermissions"
        )
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text += block.text

    return response_text.strip()


async def generate_summary_for_website_async(name: str, url: str) -> str:
    """
    Generates a summary for a website by asking Claude to visit it.
    """
    prompt = f"""Visit this website and write a brief 1-2 sentence summary describing what it is about: {url}

The website is named "{name}".

Write ONLY the summary, nothing else. Keep it under 100 words."""

    response_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=["WebFetch"],
            permission_mode="bypassPermissions"
        )
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text += block.text

    return response_text.strip()


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

    response_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=[],
            permission_mode="bypassPermissions"
        )
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text += block.text

    # Add mandatory suffix with strict background and rendering requirements
    ai_description = response_text.strip()
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


def generate_image_sync(prompt: str, output_path: Path) -> bool:
    """
    Calls generate_image to create an icon.
    Synchronous.
    """
    if not GENERATE_IMAGE.exists():
        print(f"generate_image not found at {GENERATE_IMAGE}")
        return False

    cmd = [
        str(GENERATE_IMAGE),
        "--prompt", prompt,
        "--width", "128",
        "--height", "128",
        "--output", str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Image generation failed: {e}")
        return False


def remove_background_sync(input_path: Path, output_path: Path) -> bool:
    """
    Removes background from an image for transparency.
    Synchronous.
    """
    if not REMOVE_BACKGROUND.exists():
        print(f"remove-background not found at {REMOVE_BACKGROUND}")
        return False

    cmd = [
        str(REMOVE_BACKGROUND),
        str(input_path),
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Background removal failed: {e}")
        return False


async def process_app_async(name: str, port: Optional[int], workdir: Optional[str]) -> None:
    """
    Processes an app through cascading idempotent steps.
    If an earlier artifact is newer than a later one, the later one is regenerated.
    Steps: summary → icon_prompt → jpg → png

    To regenerate an icon: just delete the prompt file. The jpg and png will
    be regenerated because the new prompt will be newer than them.
    """
    # Step 1: Generate summary if needed
    if not has_summary(name):
        print(f"[{name}] Generating summary...")
        try:
            summary = await generate_summary_async(name, port, workdir)
            save_summary(name, summary)
            update_process(name, description=summary)
            increment_change_version()
        except Exception as e:
            print(f"[{name}] Failed to generate summary: {e}")
            return
    else:
        summary = load_summary(name)
        process = get_process(name)
        if process and not process.get("description"):
            update_process(name, description=summary)

    # Step 2: Generate icon prompt if needed OR if summary is newer
    if needs_regeneration(name, 'prompt'):
        summary = load_summary(name)
        if not summary:
            return
        print(f"[{name}] Generating icon prompt...")
        try:
            icon_prompt = await generate_icon_description_async(name, summary)
            save_icon_prompt(name, icon_prompt)
        except Exception as e:
            print(f"[{name}] Failed to generate icon prompt: {e}")
            return

    # Step 3: Generate JPG if needed OR if prompt is newer (run in thread pool)
    jpg_path = get_icon_jpg_path(name)
    if needs_regeneration(name, 'jpg'):
        icon_prompt = load_icon_prompt(name)
        if not icon_prompt:
            return
        print(f"[{name}] Generating JPG...")
        update_process(name, icon_status="generating")
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, generate_image_sync, icon_prompt, jpg_path)
        if not success:
            print(f"[{name}] Failed to generate JPG")
            update_process(name, icon_status="failed")
            return

    # Step 4: Generate PNG if needed OR if jpg is newer (run in thread pool)
    png_path = get_icon_path(name)
    if needs_regeneration(name, 'png'):
        if not has_icon_jpg(name):
            return
        print(f"[{name}] Removing background...")
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, remove_background_sync, jpg_path, png_path)
        if success:
            update_process(name, icon_path=str(png_path), icon_status="ready")
            increment_change_version()
        else:
            print(f"[{name}] Failed to remove background")
            update_process(name, icon_status="failed")
    else:
        process = get_process(name)
        if process and process.get("icon_status") != "ready":
            update_process(name, icon_status="ready", icon_path=str(png_path))


async def process_website_async(name: str, url: str) -> None:
    """
    Processes a website through cascading idempotent steps.
    If an earlier artifact is newer than a later one, the later one is regenerated.
    Steps: summary → icon_prompt → jpg → png

    To regenerate an icon: just delete the prompt file. The jpg and png will
    be regenerated because the new prompt will be newer than them.
    """
    # Step 1: Generate summary if needed
    if not has_summary(name):
        print(f"[{name}] Generating summary...")
        try:
            summary = await generate_summary_for_website_async(name, url)
            save_summary(name, summary)
            update_website(name, description=summary)
            increment_change_version()
        except Exception as e:
            print(f"[{name}] Failed to generate summary: {e}")
            return
    else:
        summary = load_summary(name)
        website = get_website(name)
        if website and not website.get("description"):
            update_website(name, description=summary)

    # Step 2: Generate icon prompt if needed OR if summary is newer
    if needs_regeneration(name, 'prompt'):
        summary = load_summary(name)
        if not summary:
            return
        print(f"[{name}] Generating icon prompt...")
        try:
            icon_prompt = await generate_icon_description_async(name, summary)
            save_icon_prompt(name, icon_prompt)
        except Exception as e:
            print(f"[{name}] Failed to generate icon prompt: {e}")
            return

    # Step 3: Generate JPG if needed OR if prompt is newer (run in thread pool)
    jpg_path = get_icon_jpg_path(name)
    if needs_regeneration(name, 'jpg'):
        icon_prompt = load_icon_prompt(name)
        if not icon_prompt:
            return
        print(f"[{name}] Generating JPG...")
        update_website(name, icon_status="generating")
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, generate_image_sync, icon_prompt, jpg_path)
        if not success:
            print(f"[{name}] Failed to generate JPG")
            update_website(name, icon_status="failed")
            return

    # Step 4: Generate PNG if needed OR if jpg is newer (run in thread pool)
    png_path = get_icon_path(name)
    if needs_regeneration(name, 'png'):
        if not has_icon_jpg(name):
            return
        print(f"[{name}] Removing background...")
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, remove_background_sync, jpg_path, png_path)
        if success:
            update_website(name, icon_path=str(png_path), icon_status="ready")
            increment_change_version()
        else:
            print(f"[{name}] Failed to remove background")
            update_website(name, icon_status="failed")
    else:
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
