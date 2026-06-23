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
 * Build the base URL for a process or manual website.
 */
function buildBaseUrl(port, url, isWebsite, protocol) {
    if (isWebsite) {
        return url;
    }
    return `${protocol || 'http'}://localhost:${port}/`;
}

/**
 * Return a URL path that is safe to use in the Auto-GUI address bar.
 */
function buildDashboardUrl(name, relativeUrl) {
    const parsed = splitRelativeUrl(relativeUrl || '');
    const processSegment = encodeURIComponent(name);
    const pathSegment = parsed.path ? `/${parsed.path}` : '';
    return `/${processSegment}${pathSegment}${parsed.search}${parsed.hash}`;
}

/**
 * Split a relative URL into path, search, and hash parts.
 */
function splitRelativeUrl(relativeUrl) {
    const parsed = new URL(relativeUrl || '', 'http://auto-gui.local/');
    return {
        path: parsed.pathname.replace(/^\/+/, ''),
        search: parsed.search,
        hash: parsed.hash,
    };
}

/**
 * Combine an iframe base URL with a relative URL from the Auto-GUI route.
 */
function buildIframeUrl(baseUrl, relativeUrl) {
    const parsed = splitRelativeUrl(relativeUrl || '');
    const url = new URL(baseUrl);
    if (parsed.path) {
        // Only when there's a relative sub-path do we treat the base URL as a
        // directory root and append beneath it (adding a trailing slash as needed).
        const basePath = url.pathname.endsWith('/') ? url.pathname : `${url.pathname}/`;
        url.pathname = `${basePath}${parsed.path}`.replace(/\/{2,}/g, '/');
    }
    // With no sub-path, preserve the base URL's pathname EXACTLY. Forcing a
    // trailing slash here breaks path-style static sites (e.g. S3 hosting), where
    // `/daily-digest` is a real object but `/daily-digest/` 404s on a missing
    // `daily-digest/index.html` key.
    url.search = parsed.search;
    url.hash = parsed.hash;
    return url.toString();
}

/**
 * Convert an iframe URL back to a relative URL under that iframe's base URL.
 */
function relativeUrlFromIframeUrl(iframeUrl, baseUrl) {
    const current = new URL(iframeUrl);
    const base = new URL(baseUrl);
    if (current.origin !== base.origin) {
        return null;
    }

    const basePath = base.pathname.endsWith('/') ? base.pathname : `${base.pathname}/`;
    let path = current.pathname;
    if (path.startsWith(basePath)) {
        path = path.slice(basePath.length);
    } else {
        path = path.replace(/^\/+/, '');
    }
    return `${path}${current.search}${current.hash}`;
}

/**
 * Push or replace the dashboard URL for the selected iframe location.
 */
function updateDashboardLocation(name, relativeUrl, replace) {
    const url = buildDashboardUrl(name, relativeUrl);
    const state = {process: name, relativeUrl: relativeUrl || ''};
    if (replace) {
        history.replaceState(state, '', url);
    } else if (window.location.pathname + window.location.search + window.location.hash !== url) {
        history.pushState(state, '', url);
    }
}

/**
 * Build the initial iframe-relative URL from the server route and browser fragment.
 */
function initialRelativeUrl() {
    const path = window.SELECTED_IFRAME_PATH || '';
    return `${path}${window.location.search || ''}${window.location.hash || ''}`;
}

/**
 * Read the current iframe URL when browser same-origin rules allow it.
 */
function readIframeRelativeUrl(container) {
    const iframe = container.querySelector('iframe');
    if (!iframe) {
        return null;
    }
    try {
        return relativeUrlFromIframeUrl(iframe.contentWindow.location.href, container.dataset.baseUrl);
    } catch (_error) {
        return null;
    }
}

/**
 * Reflect an iframe location change into the Auto-GUI address bar.
 */
function syncIframeLocation(container, replace) {
    const relativeUrl = readIframeRelativeUrl(container);
    if (relativeUrl === null) {
        return;
    }
    container.dataset.relativeUrl = relativeUrl;
    updateDashboardLocation(container.dataset.name, relativeUrl, replace);
}

/**
 * Patch same-origin SPA history calls so pushState/replaceState are visible to Auto-GUI.
 */
function installSameOriginHistoryBridge(container) {
    const iframe = container.querySelector('iframe');
    if (!iframe) {
        return;
    }
    try {
        const frameWindow = iframe.contentWindow;
        if (!frameWindow || frameWindow.__autoGuiHistoryBridgeInstalled) {
            return;
        }

        const notify = () => setTimeout(() => syncIframeLocation(container, false), 0);
        const originalPushState = frameWindow.history.pushState.bind(frameWindow.history);
        const originalReplaceState = frameWindow.history.replaceState.bind(frameWindow.history);

        frameWindow.history.pushState = function (...args) {
            const result = originalPushState(...args);
            notify();
            return result;
        };
        frameWindow.history.replaceState = function (...args) {
            const result = originalReplaceState(...args);
            setTimeout(() => syncIframeLocation(container, true), 0);
            return result;
        };
        frameWindow.addEventListener('popstate', notify);
        frameWindow.addEventListener('hashchange', notify);
        frameWindow.__autoGuiHistoryBridgeInstalled = true;
    } catch (_error) {
        // Cross-origin frames cannot be inspected. They can send auto-gui:navigate via postMessage.
    }
}

/**
 * Open a process or website in a new browser window
 */
function openInNewWindow(name, port, url, isWebsite, protocol) {
    const targetUrl = buildBaseUrl(port, url, isWebsite, protocol);
    window.open(targetUrl, '_blank');
}

/**
 * Handle button click - check if popout button was clicked
 */
function handleButtonClick(event, name, port, url, isWebsite, protocol) {
    // Check if the click was on the popout button
    if (event.target.classList.contains('popout-button')) {
        event.stopPropagation();
        openInNewWindow(name, port, url, isWebsite, protocol);
        return;
    }
    // Otherwise, show the process in iframe
    showProcess(name, port, url, isWebsite, protocol);
}

/**
 * Show the welcome screen and clear current selection
 */
function showWelcome() {
    const welcome = document.getElementById('welcome');
    if (welcome) {
        welcome.style.display = '';
    }

    // Hide all iframes
    document.querySelectorAll('.iframe-container').forEach(container => {
        container.classList.remove('active');
    });

    // Clear button states
    document.querySelectorAll('.process-button').forEach(button => {
        button.classList.remove('active');
    });

    currentProcess = null;
}

/**
 * Show a process or website iframe, creating it if necessary
 */
function showProcess(name, port, url, isWebsite, protocol, options) {
    const settings = options || {};
    const relativeUrl = settings.relativeUrl || '';
    const skipPush = settings.skipPush || false;
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
        container.dataset.baseUrl = buildBaseUrl(port, url, isWebsite, protocol);
        container.dataset.relativeUrl = relativeUrl;
        container.dataset.replaceOnNextLoad = 'true';

        const iframe = document.createElement('iframe');
        iframe.src = buildIframeUrl(container.dataset.baseUrl, relativeUrl);
        iframe.title = name;
        iframe.onload = () => {
            container.classList.remove('loading');
            const replace = container.dataset.replaceOnNextLoad === 'true';
            container.dataset.replaceOnNextLoad = 'false';
            syncIframeLocation(container, replace);
            installSameOriginHistoryBridge(container);
        };

        container.appendChild(iframe);
        content.appendChild(container);
        loadedIframes.set(name, container);
    } else if (relativeUrl) {
        const iframe = container.querySelector('iframe');
        const nextUrl = buildIframeUrl(container.dataset.baseUrl, relativeUrl);
        if (iframe && iframe.src !== nextUrl) {
            container.classList.add('loading');
            container.dataset.relativeUrl = relativeUrl;
            container.dataset.replaceOnNextLoad = 'true';
            iframe.src = nextUrl;
        }
    }

    // Show the container
    container.classList.add('active');
    currentProcess = name;

    // Update URL unless we're restoring from popstate/initial load
    if (!skipPush) {
        updateDashboardLocation(name, relativeUrl, false);
    }
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

    // If selected process disappeared from the list, go back to welcome
    if (currentProcess && !currentProcessNames.has(currentProcess)) {
        showWelcome();
        history.pushState({}, '', '/');
    }

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
        const protocol = process.protocol || 'http';

        // Always create fresh button to ensure correct structure
        const button = document.createElement('button');
        button.className = 'process-button' + (isDead ? ' dead' : '');
        button.dataset.name = process.name;
        button.dataset.port = port;
        button.dataset.url = url;
        button.dataset.isWebsite = isWebsite ? 'true' : 'false';
        button.dataset.isDead = isDead ? 'true' : 'false';
        button.dataset.protocol = protocol;
        button.onclick = (e) => handleButtonClick(e, process.name, port, url, isWebsite, protocol);
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
    // Set initial history state for the current URL
    if (window.SELECTED_PROCESS) {
        const relativeUrl = initialRelativeUrl();
        updateDashboardLocation(window.SELECTED_PROCESS, relativeUrl, true);
        // Find the matching button and activate that process
        const button = document.querySelector(`[data-name="${window.SELECTED_PROCESS}"]`);
        if (button) {
            const port = button.dataset.port;
            const url = button.dataset.url;
            const isWebsite = button.dataset.isWebsite === 'true';
            const protocol = button.dataset.protocol || 'http';
            showProcess(window.SELECTED_PROCESS, port, url, isWebsite, protocol, {
                skipPush: true,
                relativeUrl,
            });
        }
    } else {
        history.replaceState({}, '', '/');
    }

    startPolling();
});

// Handle browser back/forward navigation
window.addEventListener('popstate', (event) => {
    if (event.state && event.state.process) {
        const name = event.state.process;
        const button = document.querySelector(`[data-name="${name}"]`);
        if (button) {
            const port = button.dataset.port;
            const url = button.dataset.url;
            const isWebsite = button.dataset.isWebsite === 'true';
            const protocol = button.dataset.protocol || 'http';
            showProcess(name, port, url, isWebsite, protocol, {
                skipPush: true,
                relativeUrl: event.state.relativeUrl || '',
            });
        }
    } else {
        showWelcome();
    }
});

// Cross-origin frames cannot be inspected by the parent page. Apps can opt in
// by posting {type: 'auto-gui:navigate', path: '/current/path?x=1#section'}.
window.addEventListener('message', (event) => {
    if (!currentProcess) {
        return;
    }
    const container = loadedIframes.get(currentProcess);
    if (!container) {
        return;
    }
    const iframe = container.querySelector('iframe');
    if (!iframe || event.source !== iframe.contentWindow) {
        return;
    }
    const data = event.data;
    if (!data || data.type !== 'auto-gui:navigate' || typeof data.path !== 'string') {
        return;
    }
    const relativeUrl = data.path.replace(/^\/+/, '');
    container.dataset.relativeUrl = relativeUrl;
    updateDashboardLocation(currentProcess, relativeUrl, false);
});
