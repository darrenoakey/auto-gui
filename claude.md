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

### Cascading Idempotent Icon Generation
The icon generator uses a 4-step **cascading** idempotent pipeline with timestamp-based regeneration:
1. Summary file → created if missing (uses Claude Agent SDK)
2. Icon prompt file → created if missing OR if summary is newer
3. JPG file → created if missing OR if prompt is newer
4. PNG file → created if missing OR if JPG is newer

**To regenerate an icon**: Just delete its `*_icon_prompt.txt` file. The old images stay visible until new ones are ready (no deletion needed).

**Critical**: No deletion code in auto-gui. Cascading regeneration handles updates automatically.

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
