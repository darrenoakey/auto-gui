/**
 * Auto-GUI Frontend JavaScript
 * Handles iframe switching, process polling, and auto-refresh
 */

// Store for loaded iframes
const loadedIframes = new Map();
let currentProcess = null;

// Poll interval (10 seconds for faster updates during icon generation)
const POLL_INTERVAL = 10000;

// Server state tracking
let serverAvailable = true;
let needsRefresh = false;
let consecutiveSuccesses = 0;
const REQUIRED_SUCCESSES = 2;  // Require 2 successful polls before refreshing

/**
 * Show a process or website iframe, creating it if necessary
 */
function showProcess(name, port, url, isWebsite) {
    const content = document.getElementById('content');
    const welcome = document.getElementById('welcome');

    // Hide welcome message
    if (welcome) {
        welcome.style.display = 'none';
    }

    // Hide all iframes
    document.querySelectorAll('.iframe-container').forEach(container => {
        container.classList.remove('active');
    });

    // Update button states
    document.querySelectorAll('.process-button').forEach(button => {
        button.classList.remove('active');
    });
    const activeButton = document.querySelector(`[data-name="${name}"]`);
    if (activeButton) {
        activeButton.classList.add('active');
    }

    // Check if iframe already exists
    let container = loadedIframes.get(name);

    if (!container) {
        // Create new iframe container
        container = document.createElement('div');
        container.className = 'iframe-container loading';
        container.dataset.name = name;

        const iframe = document.createElement('iframe');
        // Use URL for websites, localhost:port for processes
        iframe.src = isWebsite ? url : `http://localhost:${port}/`;
        iframe.title = name;
        iframe.onload = () => {
            container.classList.remove('loading');
        };

        container.appendChild(iframe);
        content.appendChild(container);
        loadedIframes.set(name, container);
    }

    // Show the container
    container.classList.add('active');
    currentProcess = name;
}

/**
 * Poll for process updates and check for server restart
 */
async function pollProcesses() {
    try {
        const response = await fetch('/api/processes');

        // Check for non-OK response
        if (!response.ok) {
            handleServerUnavailable();
            return;
        }

        const data = await response.json();

        // Server is responding - mark as available
        if (!serverAvailable) {
            console.log('Server is back online');
            serverAvailable = true;
        }

        // Check if server has restarted (different PID)
        if (data.server_pid !== window.SERVER_PID) {
            console.log('Server PID changed, marking for refresh...');
            needsRefresh = true;
        }

        // If we need a refresh, wait for consecutive successes before reloading
        if (needsRefresh) {
            consecutiveSuccesses++;
            console.log(`Server stable check ${consecutiveSuccesses}/${REQUIRED_SUCCESSES}`);
            if (consecutiveSuccesses >= REQUIRED_SUCCESSES) {
                console.log('Server confirmed stable, refreshing page...');
                location.reload(true);
                return;
            }
            // Don't update UI while waiting for refresh
            return;
        }

        // Reset success counter on normal operation
        consecutiveSuccesses = 0;

        // Check for content changes (icons, summaries)
        if (data.change_version !== window.CHANGE_VERSION) {
            console.log('Content changed, updating...');
            window.CHANGE_VERSION = data.change_version;
        }

        updateProcessList(data.processes);
        updateLastScan(data.last_scan);
    } catch (error) {
        handleServerUnavailable();
    }
}

/**
 * Handle server being unavailable
 */
function handleServerUnavailable() {
    if (serverAvailable) {
        console.log('Server unavailable, waiting for it to come back...');
        serverAvailable = false;
    }
    // Reset consecutive successes - need fresh count when server returns
    consecutiveSuccesses = 0;
}

/**
 * Update the process list in the sidebar
 */
function updateProcessList(processes) {
    const list = document.getElementById('process-list');
    const currentProcessNames = new Set(processes.map(p => p.name));

    // Remove buttons for processes that no longer exist
    document.querySelectorAll('.process-button').forEach(button => {
        if (!currentProcessNames.has(button.dataset.name)) {
            button.remove();
            // Also remove the iframe
            const container = loadedIframes.get(button.dataset.name);
            if (container) {
                container.remove();
                loadedIframes.delete(button.dataset.name);
            }
        }
    });

    // Add or update buttons for current processes
    const existingButtons = new Map();
    document.querySelectorAll('.process-button').forEach(button => {
        existingButtons.set(button.dataset.name, button);
    });

    processes.forEach(process => {
        let button = existingButtons.get(process.name);
        const isWebsite = process.is_website || false;
        const port = process.port || '';
        const url = process.url || '';
        const description = process.description || '';

        if (!button) {
            // Create new button
            button = document.createElement('button');
            button.className = 'process-button';
            button.dataset.name = process.name;
            button.dataset.port = port;
            button.dataset.url = url;
            button.dataset.isWebsite = isWebsite ? 'true' : 'false';
            button.onclick = () => showProcess(process.name, port, url, isWebsite);
            button.title = description;

            button.innerHTML = `
                <img
                    src="${process.icon_status === 'ready' ? `/icons/${process.name}.png?v=${Date.now()}` : '/static/img/placeholder.png'}"
                    alt="${process.name}"
                    class="process-icon"
                    onerror="this.src='/static/img/placeholder.png'"
                >
                <span class="process-name">${process.name}</span>
                <span class="process-port">${isWebsite ? 'web' : ':' + port}</span>
            `;

            list.appendChild(button);

            if (process.name === currentProcess) {
                button.classList.add('active');
            }
        } else {
            // Update port/url if changed
            button.dataset.port = port;
            button.dataset.url = url;
            button.title = description;
            const portEl = button.querySelector('.process-port');
            portEl.textContent = isWebsite ? 'web' : `:${port}`;

            // Update icon if status changed to ready
            const icon = button.querySelector('.process-icon');
            if (process.icon_status === 'ready') {
                const newSrc = `/icons/${process.name}.png?v=${Date.now()}`;
                if (!icon.src.includes(`/icons/${process.name}.png`)) {
                    icon.src = newSrc;
                }
            }
        }
    });
}

/**
 * Update the last scan timestamp display
 */
function updateLastScan(timestamp) {
    const el = document.getElementById('last-scan');
    if (el && timestamp) {
        el.textContent = `Last: ${timestamp.substring(0, 16)}`;
    }
}

/**
 * Start polling for updates
 */
function startPolling() {
    setInterval(pollProcesses, POLL_INTERVAL);
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    startPolling();
});
