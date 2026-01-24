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
 * Open a process or website in a new browser window
 */
function openInNewWindow(name, port, url, isWebsite) {
    const targetUrl = isWebsite ? url : `http://localhost:${port}/`;
    window.open(targetUrl, '_blank');
}

/**
 * Handle button click - check if popout button was clicked
 */
function handleButtonClick(event, name, port, url, isWebsite) {
    // Check if the click was on the popout button
    if (event.target.classList.contains('popout-button')) {
        event.stopPropagation();
        openInNewWindow(name, port, url, isWebsite);
        return;
    }
    // Otherwise, show the process in iframe
    showProcess(name, port, url, isWebsite);
}

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

    // Sort processes alphabetically
    processes.sort((a, b) => a.name.localeCompare(b.name));

    // Remove iframes for processes that no longer exist
    document.querySelectorAll('.process-button').forEach(button => {
        const name = button.dataset.name;
        if (!currentProcessNames.has(name)) {
            const container = loadedIframes.get(name);
            if (container) {
                container.remove();
                loadedIframes.delete(name);
            }
        }
    });

    // Clear and rebuild list to maintain sort order
    list.innerHTML = '';

    processes.forEach(process => {
        const isWebsite = process.is_website || false;
        const port = process.port || '';
        const url = process.url || '';
        const description = process.description || '';
        const isDead = process.is_dead || false;

        // Always create fresh button to ensure correct structure
        const button = document.createElement('button');
        button.className = 'process-button' + (isDead ? ' dead' : '');
        button.dataset.name = process.name;
        button.dataset.port = port;
        button.dataset.url = url;
        button.dataset.isWebsite = isWebsite ? 'true' : 'false';
        button.dataset.isDead = isDead ? 'true' : 'false';
        button.onclick = (e) => handleButtonClick(e, process.name, port, url, isWebsite);
        button.title = description;

        button.innerHTML = `
            ${isDead ? '<span class="dead-indicator" title="Process not running">✕</span>' : ''}
            <img
                src="${process.icon_status === 'ready' ? `/icons/${process.name}.png?v=${Date.now()}` : '/static/img/placeholder.png'}"
                alt="${process.name}"
                class="process-icon"
                onerror="this.src='/static/img/placeholder.png'"
            >
            <span class="process-name">${process.name}</span>
            <span class="process-port">${isWebsite ? 'web' : ':' + port}</span>
            <span class="popout-button" title="Open in new window">↗</span>
        `;

        list.appendChild(button);

        if (process.name === currentProcess) {
            button.classList.add('active');
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
