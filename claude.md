# Auto-GUI Project

FastAPI web dashboard for auto-managed processes with dual themes (space/dark, nature/light).

## Project Structure

```
src/                    # Python source files (all have co-located tests)
  server.py             # FastAPI app with lifespan management
  state_manager.py      # JSON state persistence in local/state.json
  process_scanner.py    # Parses `auto -q ps` output
  html_checker.py       # Checks if ports serve HTML (requires 200 + HTML structure)
  icon_generator.py     # Idempotent icon pipeline using daz-agent-sdk
static/                 # CSS, JS, images
templates/              # Jinja2 templates
local/                  # Runtime artifacts (gitignored)
  state.json            # Process/website state
  icons/                # Generated icons (PNG with transparency)
  {name}_summary.txt    # App summaries
  {name}_icon_prompt.txt # Icon generation prompts
```

## Key Design Decisions

### Cascading Icon Generation (No Timestamps)
The icon generator uses a 3-step **cascading** pipeline. NO timestamp checking - only checks if files exist:
1. Summary file → created if missing (uses daz-agent-sdk)
2. Icon prompt file → created if missing (or if step 1 ran)
3. Transparent PNG → created if missing (or if step 2 ran), using `agent.image()` with `transparent=True`

**Key design principle**: If any step runs, ALL downstream steps run (`force_downstream` boolean passed through the chain).

**To regenerate an icon**: Delete the `*_icon_prompt.txt` file. Summary stays, but prompt/png regenerate.

**Atomic swap**: Image files are generated to `.tmp` files, then atomically swapped in. The old icon stays visible until the new one is completely ready. This ensures there's never a moment without a valid icon.

**Empty response guard**: `save_summary()` raises `ValueError` on empty summaries, and `query_text_with_backoff()` rejects empty AI responses. This prevents 0-byte summary files from silently blocking the pipeline.

### Background Icon Worker
Icon generation runs in a **separate background worker**, completely decoupled from the main server:
- Queue-based processing with duplicate prevention
- Server startup doesn't trigger icon generation (deferred until server is fully running)
- Uses `agent.image()` (async, non-blocking) for image generation with built-in background removal
- One item processed at a time to avoid overwhelming the system

### daz-agent-sdk (NOT raw Anthropic API)
Always use `daz_agent_sdk` for programmatic AI — provider-agnostic with tier-based routing. See `~/.claude/skills/ai/skill.md`.

**CRITICAL**: Always specify `cwd=get_project_root()` in `agent.ask()` calls when running in a daemon process. The SDK may spawn subprocesses that inherit the working directory. If the process was started by `auto` from a directory that becomes unavailable (external drive, temp dir, etc.), the SDK fails. Using an explicit, code-relative cwd avoids this.

### Server Trigger Logic
The server triggers icon generation based on file existence (`has_icon(name)`), NOT state.json `icon_status`. This ensures proper idempotent behavior.

### HTML Detection
`html_checker.py` tries both HTTP and HTTPS, requiring HTTP 200 status AND actual HTML structure in body (`<!doctype html` or `<html`). This filters out API servers and error pages. Special case: if HTTPS requires a client certificate (`CERTIFICATE_REQUIRED`), we assume it's a GUI and return True (most client-cert services are web dashboards).

### URL Routing
`GET /{name}` serves the same `index.html` with `selected_process` set, enabling direct URL navigation and refresh persistence. The single-segment `{name}` doesn't conflict with `/api/processes` (multi-segment) or `/static`/`/icons` (mounted StaticFiles take priority). Frontend uses `pushState`/`popState` for browser history integration.

### is_html Persistence
Once a process is identified as `is_html: true`, it **stays that way forever** - never rechecked or downgraded. This prevents GUI apps from disappearing if they're temporarily unavailable during a scan. Non-HTML processes continue to be checked (they might become GUI apps). A GUI app only disappears when completely removed from auto.

## Commands

```bash
./run serve              # Start server on port 2000
./run test               # Run pytest
./run add "name" url     # Add manual website
./run remove name        # Remove website
./run list               # List websites
```

## Testing

All tests are in `src/*_test.py` files. Run with `pytest src/`.

**E2E smoke tests** (`TestSmokeE2E` in `server_test.py`) use Playwright against the live server at localhost:2000. They auto-skip if the server isn't running. Each test saves a screenshot to `local/smoke_*.png` for visual verification.

## Gotchas

- Filter out "auto-gui" from its own process list (SELF_NAME constant)
- CSS `.process-icon` should NOT have a background color (interferes with transparency)
- CSS backdrop-filter (frosting) should only be on sidebar, not the main content area
- Icon prompts need explicit requirements: flat solid background (no gradients), bold simple shapes for tiny display, high contrast
- Frontend polling is tolerant of server restarts - waits for consecutive successful polls before refreshing
- **Use `auto -q restart auto-gui`** to restart the server, never `./run serve` directly
- Process list is sorted alphabetically - sorting happens both server-side (`get_all_visible_items`) and client-side (JS rebuilds list on each poll)
- Dead vs removed: processes still in auto's state.json but not running are "dead" (shown with ✕), processes completely removed from auto are hidden
- Popout button uses event.target check in `handleButtonClick()` to distinguish clicks on the ↗ from clicks on the main button
- `agent.image()` with `transparent=True` generates PNG directly — no separate background removal step needed
- `SCAN_INTERVAL` is 30 seconds (not 10 minutes) — dead/alive detection should be responsive
- **State file permission errors**: macOS sandbox can cause transient `PermissionError` on launchd-spawned processes accessing files on external drives. The `StateError` exception provides clear recovery hints (`auto -q restart auto-gui`). Smoke tests in `state_manager_test.py` verify accessibility.
