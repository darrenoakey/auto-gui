/**
 * Auto-GUI Frontend JavaScript
 * Handles iframe switching, process polling, and auto-refresh
 */

// Store for loaded iframes
const loadedIframes = new Map();
let currentProcess = null;
let processListSignature = '';

// Ask once a minute whether the sidebar moved. The answer is almost always no.
const POLL_INTERVAL = 60000;
let changeRevision = 0;

// Interval for checking whether the active iframe's URL has changed (1.5 seconds).
// This catches full-page navigations inside same-origin iframes that the
// pushState/popstate bridge misses, and also sends a request-location message
// to cross-origin iframes that include the auto-gui iframe-bridge script.
const LOCATION_POLL_INTERVAL = 1500;

// Server state tracking
let serverAvailable = true;

/**
 * Build the proxy base URL for a process or manual website.
 *
 * Iframe traffic goes through the reverse proxy on the other loopback host
 * (localhost <-> 127.0.0.1), same port. Browsers allow only six HTTP/1.1
 * connections per host. Long-lived proxied streams (SSE, hot-reload) must not
 * share that pool with the dashboard, or the sidebar and clicks queue forever
 * while /healthz still answers.
 *
 * URL tracking still works: the injected shim posts auto-gui:navigate. The
 * same-origin history bridge remains as a fallback when the hosts match.
 */
function proxyFrameOrigin() {
    const host = window.location.hostname;
    let frameHost = host;
    if (host === 'localhost' || host === '::1' || host === '[::1]') {
        frameHost = '127.0.0.1';
    } else if (host === '127.0.0.1') {
        frameHost = 'localhost';
    }
    const port = window.location.port ? `:${window.location.port}` : '';
    return `${window.location.protocol}//${frameHost}${port}`;
}

function buildBaseUrl(port, url, isWebsite, protocol, name) {
    const base = `${proxyFrameOrigin()}/proxy/${encodeURIComponent(name)}`;
    return isWebsite ? base : `${base}/`;
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
 * Return the configured sub-path of a website URL as a relative URL string.
 *
 * Because resolve_backend() now returns only the origin for websites, the
 * path component of the configured URL must be used as the default landing
 * relative URL so that clicking a path-style website in the sidebar navigates
 * to the correct page (e.g. 'daily-digest') rather than the origin root.
 *
 * Returns '' for port-based processes or URLs with no meaningful path.
 */
function defaultRelativeUrl(url, isWebsite) {
    if (!isWebsite || !url) return '';
    try {
        const parsed = new URL(url);
        return parsed.pathname.replace(/^\/+/, '') + parsed.search + parsed.hash;
    } catch (_e) {
        return '';
    }
}

/**
 * Combine an iframe base URL with a relative URL from the Auto-GUI route.
 */
function buildIframeUrl(baseUrl, relativeUrl) {
    const parsed = splitRelativeUrl(relativeUrl || '');
    const url = new URL(baseUrl, window.location.origin);
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
 * Convert a proxy iframe URL back to a relative URL under that iframe's base URL.
 *
 * Because all iframes now go through the /proxy/{name} route, they are
 * same-origin with the dashboard, so this always works.
 *
 * Returns null when the URL is not under the proxy base (can't determine
 * app-relative path).
 */
function relativeUrlFromIframeUrl(iframeUrl, baseUrl) {
    const current = new URL(iframeUrl);
    const base = new URL(baseUrl, window.location.origin);
    if (current.origin !== base.origin) {
        return null;
    }

    const basePath = base.pathname.endsWith('/') ? base.pathname : `${base.pathname}/`;
    let path = current.pathname;
    if (path.startsWith(basePath)) {
        path = path.slice(basePath.length);
    } else if (path === base.pathname) {
        // iframe is at the proxy root (no trailing slash variant) — treat as empty path
        path = '';
    } else {
        // Path is not under the proxy base; can't determine app-relative URL
        return null;
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
    const targetUrl = buildBaseUrl(port, url, isWebsite, protocol, name);
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
 * Drop the network activity of an iframe that is not on screen.
 *
 * Hidden iframes used to keep SSE and hot-reload sockets open. Those sockets
 * count against the browser's six-connection cap and freeze every Auto-GUI tab.
 */
function parkIframe(container) {
    const iframe = container.querySelector('iframe');
    if (!iframe) {
        return;
    }
    const src = iframe.getAttribute('src') || '';
    if (!src || src === 'about:blank') {
        return;
    }
    container.dataset.parkedSrc = iframe.src;
    iframe.src = 'about:blank';
}

function releaseInactiveIframes(activeName) {
    loadedIframes.forEach((container, name) => {
        if (name !== activeName) {
            parkIframe(container);
        }
    });
}

function resumeIframe(container) {
    const parked = container.dataset.parkedSrc;
    if (!parked) {
        return;
    }
    const iframe = container.querySelector('iframe');
    delete container.dataset.parkedSrc;
    if (iframe && iframe.src !== parked) {
        container.classList.add('loading');
        container.dataset.replaceOnNextLoad = 'true';
        iframe.src = parked;
    }
}

function showWelcome() {
    const welcome = document.getElementById('welcome');
    if (welcome) {
        welcome.style.display = '';
    }

    releaseInactiveIframes(null);

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
    // For websites the proxy backend is origin-only; the configured URL path
    // is the landing page. Fall back to it when no explicit relativeUrl is set.
    const relativeUrl = settings.relativeUrl || defaultRelativeUrl(url, isWebsite);
    const skipPush = settings.skipPush || false;
    const content = document.getElementById('content');
    const welcome = document.getElementById('welcome');

    // Hide welcome message
    if (welcome) {
        welcome.style.display = 'none';
    }

    releaseInactiveIframes(name);

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
        container.dataset.baseUrl = buildBaseUrl(port, url, isWebsite, protocol, name);
        container.dataset.relativeUrl = relativeUrl;
        container.dataset.replaceOnNextLoad = 'true';

        const iframe = document.createElement('iframe');
        iframe.src = buildIframeUrl(container.dataset.baseUrl, relativeUrl);
        iframe.title = name;
        iframe.allow = 'microphone; autoplay';
        iframe.onload = () => {
            if (container.dataset.parkedSrc || iframe.src === 'about:blank') {
                return;
            }
            container.classList.remove('loading');
            const replace = container.dataset.replaceOnNextLoad === 'true';
            container.dataset.replaceOnNextLoad = 'false';
            syncIframeLocation(container, replace);
            installSameOriginHistoryBridge(container);
        };

        container.appendChild(iframe);
        content.appendChild(container);
        loadedIframes.set(name, container);
    } else {
        resumeIframe(container);
        if (relativeUrl) {
            const iframe = container.querySelector('iframe');
            const nextUrl = buildIframeUrl(container.dataset.baseUrl, relativeUrl);
            if (iframe && iframe.src !== nextUrl) {
                container.classList.add('loading');
                container.dataset.relativeUrl = relativeUrl;
                container.dataset.replaceOnNextLoad = 'true';
                iframe.src = nextUrl;
            }
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
 * One cheap poll. A no does no further work. A yes names what moved.
 */
async function pollChanges() {
    try {
        const response = await fetch(`/api/changes?since=${changeRevision}`, {
            cache: 'no-store',
        });
        if (!response.ok) {
            handleServerUnavailable();
            return;
        }
        const data = await response.json();
        serverAvailable = true;

        if (data.server_pid !== window.SERVER_PID) {
            location.reload();
            return;
        }
        if (!data.changed) {
            return;
        }

        changeRevision = data.revision;
        const types = new Set((data.changes || []).map(change => change.type));
        if (types.size > 0) {
            await refreshProcessList();
        }
    } catch (_error) {
        handleServerUnavailable();
    }
}

/**
 * Load the sidebar only after the change feed says it moved.
 */
async function refreshProcessList() {
    const response = await fetch('/api/processes', {cache: 'no-store'});
    if (!response.ok) {
        handleServerUnavailable();
        return;
    }
    const data = await response.json();
    if (data.change_version !== window.CHANGE_VERSION) {
        window.CHANGE_VERSION = data.change_version;
    }
    updateProcessList(data.processes);
    updateLastScan(data.last_scan);
}

/**
 * Handle server being unavailable
 */
function handleServerUnavailable() {
    serverAvailable = false;
}

/**
 * Update the process list in the sidebar
 */
function processListSignatureOf(processes) {
    const rows = processes.map(process => [
        process.name,
        process.port || '',
        process.url || '',
        process.is_dead ? '1' : '0',
        process.icon_status || '',
        process.protocol || '',
        process.is_website ? '1' : '0',
        process.description || '',
    ].join('\u001f'));
    return `${rows.join('\u001e')}|${window.CHANGE_VERSION}`;
}

function updateProcessList(processes) {
    const list = document.getElementById('process-list');
    const currentProcessNames = new Set(processes.map(p => p.name));

    // Sort processes alphabetically
    processes.sort((a, b) => a.name.localeCompare(b.name));

    const signature = processListSignatureOf(processes);
    if (signature === processListSignature && list.querySelector('.process-button')) {
        list.querySelectorAll('.process-button').forEach(button => {
            button.classList.toggle('active', button.dataset.name === currentProcess);
        });
        return;
    }
    processListSignature = signature;

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
                src="${process.icon_status === 'ready' ? `/icons/${encodeURIComponent(process.name)}.png?v=${window.CHANGE_VERSION}` : '/static/img/placeholder.png'}"
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
 * Check the active iframe for location changes and sync them into the dashboard URL.
 *
 * Same-origin iframes: read contentWindow.location directly (always works).
 * Cross-origin iframes: post a request-location message; if the child page
 * includes the iframe-bridge script it will respond with an auto-gui:navigate
 * message that the existing message listener already handles.
 */
function checkActiveIframeLocation() {
    if (!currentProcess) {
        return;
    }
    const container = loadedIframes.get(currentProcess);
    if (!container) {
        return;
    }
    const iframe = container.querySelector('iframe');
    if (!iframe) {
        return;
    }

    // Try same-origin read first.
    const relativeUrl = readIframeRelativeUrl(container);
    if (relativeUrl !== null) {
        if (relativeUrl !== container.dataset.relativeUrl) {
            container.dataset.relativeUrl = relativeUrl;
            updateDashboardLocation(container.dataset.name, relativeUrl, false);
        }
        return;
    }

    // Cross-origin: ask the iframe to report its location via postMessage.
    // The iframe-bridge.js script (if installed) will respond. This is the only
    // way to track navigation inside cross-origin frames.
    try {
        iframe.contentWindow.postMessage({type: 'auto-gui:request-location'}, '*');
    } catch (_error) {
        // Content window is inaccessible — nothing we can do.
    }
}

/**
 * Start polling for updates
 */
function startPolling() {
    changeRevision = window.CHANGE_REVISION || 0;
    setInterval(pollChanges, POLL_INTERVAL);
    setInterval(checkActiveIframeLocation, LOCATION_POLL_INTERVAL);
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    // Set initial history state for the current URL
    if (window.SELECTED_PROCESS) {
        // Find the matching button first so we can read its url/isWebsite.
        const button = document.querySelector(`[data-name="${window.SELECTED_PROCESS}"]`);
        const initRelUrl = initialRelativeUrl();
        if (button) {
            const port = button.dataset.port;
            const url = button.dataset.url;
            const isWebsite = button.dataset.isWebsite === 'true';
            const protocol = button.dataset.protocol || 'http';
            // If the URL has no iframe sub-path (e.g. loading /Daily Digest directly),
            // use the configured URL's path as the landing relative URL so path-style
            // websites navigate to their configured sub-path rather than the origin root.
            const effectiveRelUrl = initRelUrl || defaultRelativeUrl(url, isWebsite);
            updateDashboardLocation(window.SELECTED_PROCESS, effectiveRelUrl, true);
            showProcess(window.SELECTED_PROCESS, port, url, isWebsite, protocol, {
                skipPush: true,
                relativeUrl: effectiveRelUrl,
            });
        } else {
            updateDashboardLocation(window.SELECTED_PROCESS, initRelUrl, true);
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
// by including iframe-bridge.js (which posts proactively AND responds to
// request-location polls) or by manually posting:
//   {type: 'auto-gui:navigate', path: '/current/path?x=1#section'}
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
    // Normalize: supports both app-relative ('/page') and proxy-prefixed
    // ('/proxy/name/page') paths. The proxy shim sends app-relative, but
    // third-party bridges may send full proxy paths.
    let relativeUrl;
    try {
        const fullUrl = new URL(data.path, window.location.origin).href;
        const normalized = relativeUrlFromIframeUrl(fullUrl, container.dataset.baseUrl);
        relativeUrl = normalized !== null ? normalized : data.path.replace(/^\/+/, '');
    } catch (_e) {
        relativeUrl = data.path.replace(/^\/+/, '');
    }
    // Skip if nothing changed — prevents duplicate history entries when the
    // bridge reports the same location on every request-location poll.
    if (relativeUrl === container.dataset.relativeUrl) {
        return;
    }
    container.dataset.relativeUrl = relativeUrl;
    updateDashboardLocation(currentProcess, relativeUrl, false);
});
