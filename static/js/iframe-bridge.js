/**
 * Auto-GUI Iframe Bridge
 *
 * Include this script in any app rendered inside an Auto-GUI iframe so the
 * dashboard can track the app's current URL (even cross-origin, where the
 * parent page cannot read iframe.contentWindow.location).
 *
 * Usage (from the child app's HTML):
 *   <script src="http://localhost:2000/static/js/iframe-bridge.js"></script>
 *
 * Protocol (all messages posted to parent):
 *   Parent -> child: {type: 'auto-gui:request-location'}
 *   Child  -> parent: {type: 'auto-gui:navigate', path: '/relative/path?q=1#hash'}
 *
 * The bridge also notifies proactively on every history change (pushState,
 * replaceState, popstate, hashchange) so the parent reflects navigation
 * immediately without waiting for a poll.
 */
(function () {
    'use strict';

    // Only run when actually embedded in an iframe.
    if (window.top === window.self) {
        return;
    }

    var INSTALLED_KEY = '__autoGuiBridgeInstalled';
    if (window[INSTALLED_KEY]) {
        return;
    }
    window[INSTALLED_KEY] = true;

    var parent = window.parent;

    /**
     * Post the current location (relative path + search + hash) to the parent.
     */
    function notify() {
        var loc = window.location;
        var path = loc.pathname + loc.search + loc.hash;
        parent.postMessage({type: 'auto-gui:navigate', path: path}, '*');
    }

    // Respond to explicit location requests from the parent.
    window.addEventListener('message', function (event) {
        var data = event.data;
        if (data && data.type === 'auto-gui:request-location') {
            notify();
        }
    });

    // Patch history methods so SPA navigations are visible immediately.
    var origPush = history.pushState;
    var origReplace = history.replaceState;
    history.pushState = function () {
        var ret = origPush.apply(this, arguments);
        notify();
        return ret;
    };
    history.replaceState = function () {
        var ret = origReplace.apply(this, arguments);
        notify();
        return ret;
    };

    // Catch traditional navigations.
    window.addEventListener('popstate', notify);
    window.addEventListener('hashchange', notify);

    // Notify once on load so the parent has the initial path.
    notify();
})();
