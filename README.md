![](banner.jpg)

# Auto-GUI

A web dashboard for viewing and managing auto-managed processes. Displays running processes with their status, ports, and auto-generated icons in a clean dual-theme interface.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Start the Dashboard

```bash
./run serve
```

The dashboard will be available at `http://localhost:2000`.

You can specify a different host or port:

```bash
./run serve --host 127.0.0.1 --port 8080
```

### Manage Websites

Add a website to the dashboard manually:

```bash
./run add "My App" https://myapp.example.com
```

Remove a website:

```bash
./run remove "My App"
```

List all manually-added websites:

```bash
./run list
```

### Run Tests

```bash
./run test
```

Run specific tests by pattern:

```bash
./run test -k "test_html_checker"
```

### Run Quality Checks

```bash
./run check
```

## Examples

Start the server and open the dashboard:

```bash
./run serve
# Open http://localhost:2000 in your browser
```

Add multiple websites to track:

```bash
./run add "Production API" https://api.example.com
./run add "Staging Site" https://staging.example.com
./run add "Documentation" https://docs.example.com
```

View what's configured:

```bash
./run list
# Output:
# Manual websites:
#   Production API: https://api.example.com
#   Staging Site: https://staging.example.com
#   Documentation: https://docs.example.com
```