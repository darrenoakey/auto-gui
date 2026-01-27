# Auto-GUI Project

FastAPI web dashboard for auto-managed processes with dual themes (space/dark, nature/light).

## Project Structure

```
src/                    # Python source files (all have co-located tests)
  server.py             # FastAPI app with lifespan management
  state_manager.py      # JSON state persistence in local/state.json
  process_scanner.py    # Parses `auto -q ps` output
  html_checker.py       # Checks if ports serve HTML (requires 200 + HTML structure)
  icon_generator.py     # Idempotent icon pipeline using Claude Agent SDK
static/                 # CSS, JS, images
templates/              # Jinja2 templates
local/                  # Runtime artifacts (gitignored)
  state.json            # Process/website state
  icons/                # Generated icons (JPG intermediate, PNG final)
  {name}_summary.txt    # App summaries
  {name}_icon_prompt.txt # Icon generation prompts
```

## Key Design Decisions

### Cascading Icon Generation (No Timestamps)
The icon generator uses a 4-step **cascading** pipeline. NO timestamp checking - only checks if files exist:
1. Summary file → created if missing (uses Claude Agent SDK)
2. Icon prompt file → created if missing (or if step 1 ran)
3. JPG file → created if missing (or if step 2 ran)
4. PNG file → created if missing (or if step 3 ran)

**Key design principle**: If any step runs, ALL downstream steps run (`force_downstream` boolean passed through the chain).

**To regenerate an icon**: Delete the `*_icon_prompt.txt` file. Summary stays, but prompt/jpg/png regenerate.

**Atomic swap**: Image files are generated to `.tmp` files, then atomically swapped in. The old icon stays visible until the new one is completely ready. This ensures there's never a moment without a valid icon.

### Background Icon Worker
Icon generation runs in a **separate background worker**, completely decoupled from the main server:
- Queue-based processing with duplicate prevention
- Server startup doesn't trigger icon generation (deferred until server is fully running)
- Blocking subprocess calls (`generate_image`, `remove-background`) use `run_in_executor` to avoid blocking the event loop
- One item processed at a time to avoid overwhelming the system

### Claude Agent SDK (NOT raw Anthropic API)
Always use `claude_agent_sdk` for programmatic AI - it uses ambient authentication from Claude Code, no API keys needed. See `~/.claude/skills/ai/skill.md`.

### Server Trigger Logic
The server triggers icon generation based on file existence (`has_icon(name)`), NOT state.json `icon_status`. This ensures proper idempotent behavior.

### HTML Detection
`html_checker.py` requires both HTTP 200 status AND actual HTML structure in body (`<!doctype html` or `<html`). This filters out API servers and error pages.

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

## Gotchas

- Filter out "auto-gui" from its own process list (SELF_NAME constant)
- CSS `.process-icon` should NOT have a background color (interferes with transparency)
- CSS backdrop-filter (frosting) should only be on sidebar, not the main content area
- The `remove-background` tool can be too aggressive on icons - JPGs often look better than the processed PNGs
- Icon prompts need explicit requirements: flat solid background (no gradients), bold simple shapes for tiny display, high contrast
- Frontend polling is tolerant of server restarts - waits for consecutive successful polls before refreshing
- **Use `auto -q restart auto-gui`** to restart the server, never `./run serve` directly
- Process list is sorted alphabetically - sorting happens both server-side (`get_all_visible_items`) and client-side (JS rebuilds list on each poll)
- Dead vs removed: processes still in auto's state.json but not running are "dead" (shown with ✕), processes completely removed from auto are hidden
- Popout button uses event.target check in `handleButtonClick()` to distinguish clicks on the ↗ from clicks on the main button
- `generate_image` tool doesn't overwrite - it creates numbered files. We use `.tmp` files and atomic swap to avoid this issue.
- `generate_image` enforces `.jpg`/`.jpeg` extensions - if output path doesn't end in these, it ADDS `.jpg`. So temp files must be named `name.tmp.jpg` (not `name.jpg.tmp`) or the file gets created at an unexpected path and existence checks fail.
