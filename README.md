![](banner.jpg)

# Auto-GUI

A clean, beautiful web dashboard that lets you see all your background services at a glance. If you use [auto](https://github.com/darrenoakey/auto) to manage long-running processes, Auto-GUI gives them a home — complete with status indicators, clickable links, and auto-generated icons.

## Getting Started

Install the dependencies:

```bash
pip install -r requirements.txt
```

Start the dashboard:

```bash
./run serve
```

Open [http://localhost:2000](http://localhost:2000) in your browser — that's it!

## Features

### Live Process Dashboard

Auto-GUI automatically discovers every process managed by `auto` and displays them in a sortable list. Each process shows:

- **Status** — whether it's running or stopped
- **Port** — if it's listening on one
- **Quick link** — click to open any web-based service directly

The dashboard polls for changes in the background, so you never need to refresh.

### Auto-Generated Icons

Each service gets its own unique icon, generated automatically. This makes it easy to visually scan your dashboard and find what you're looking for.

### Manual Websites

Not everything runs through `auto`. You can pin any URL to your dashboard:

```bash
./run add "My App" https://myapp.example.com
./run add "Documentation" https://docs.example.com
./run add "Staging" https://staging.example.com
```

See what you've added:

```bash
./run list
```

Remove one you no longer need:

```bash
./run remove "My App"
```

### Dual Themes

The dashboard ships with two themes — a dark space theme and a light nature theme. Switch between them to suit your mood or environment.

### Direct Links

Every service has its own URL (e.g., `http://localhost:2000/my-service`). You can bookmark individual services, and the page works correctly on refresh.

## Configuration

By default, the server runs on all interfaces at port 2000. You can change this:

```bash
./run serve --host 127.0.0.1 --port 8080
```

## Tips and Tricks

- **Running as a service** — Register Auto-GUI itself with `auto` so it starts automatically on login. Use `auto -q restart auto-gui` to restart it when needed.

- **Bookmark your favorites** — Since each process has its own URL, you can bookmark `http://localhost:2000/my-api` to jump straight to a specific service.

- **Pin external services** — Use `./run add` to put frequently-visited internal tools, staging environments, or documentation sites right alongside your local services.

- **Run quality checks** — If you're contributing, `./run check` runs the full test suite in one step.