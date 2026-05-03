(function () {
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    window.WellHabitGetCsrfToken = getCsrfToken;

    window.WellHabitCsrfHeaders = function (headers) {
        const merged = Object.assign({}, headers || {});
        const token = getCsrfToken();
        if (token && !merged['X-CSRFToken'] && !merged['X-CSRF-Token']) merged['X-CSRFToken'] = token;
        return merged;
    };

    function isUnsafeMethod(method) {
        const upper = String(method || 'GET').toUpperCase();
        return upper !== 'GET' && upper !== 'HEAD' && upper !== 'OPTIONS' && upper !== 'TRACE';
    }

    function isSameOriginUrl(input) {
        try {
            const url = input instanceof Request ? input.url : input;
            return new URL(url, window.location.href).origin === window.location.origin;
        } catch (error) {
            return true;
        }
    }

    function csrfHeadersForFetch(headers) {
        const out = new Headers(headers || {});
        const token = getCsrfToken();
        if (token && !out.has('X-CSRFToken') && !out.has('X-CSRF-Token')) {
            out.set('X-CSRFToken', token);
        }
        return out;
    }

    // Safety net: all same-origin POST/PUT/PATCH/DELETE fetch() calls get CSRF,
    // even if a future module forgets to call WellHabitCsrfHeaders().
    if (!window.__wellHabitFetchCsrfPatched && typeof window.fetch === 'function') {
        const nativeFetch = window.fetch.bind(window);
        window.fetch = function (input, init) {
            const requestMethod = init?.method || (input instanceof Request ? input.method : 'GET');
            if (isUnsafeMethod(requestMethod) && isSameOriginUrl(input)) {
                const nextInit = Object.assign({}, init || {});
                if (input instanceof Request && !init) {
                    nextInit.headers = csrfHeadersForFetch(input.headers);
                    return nativeFetch(input, nextInit);
                }
                nextInit.headers = csrfHeadersForFetch(nextInit.headers || (input instanceof Request ? input.headers : undefined));
                return nativeFetch(input, nextInit);
            }
            return nativeFetch(input, init);
        };
        window.__wellHabitFetchCsrfPatched = true;
    }

    function ensureCsrfInput(form) {
        if (!form || form.nodeType !== 1) return;
        if ((form.getAttribute('method') || '').toLowerCase() !== 'post') return;
        if (form.querySelector('input[name="csrf_token"]')) return;
        const token = getCsrfToken();
        if (!token) return;
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'csrf_token';
        hidden.value = token;
        form.prepend(hidden);
    }

    function scanForForms(root) {
        if (!root || root.nodeType !== 1) return;
        if (root.matches?.('form')) ensureCsrfInput(root);
        root.querySelectorAll?.('form').forEach(ensureCsrfInput);
    }

    scanForForms(document);

    // Handles forms created late, and forms whose method changes to POST later.
    document.addEventListener('submit', function (event) {
        ensureCsrfInput(event.target);
    }, true);

    if ('MutationObserver' in window) {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach(scanForForms);
                if (mutation.type === 'attributes') scanForForms(mutation.target);
            });
        });
        observer.observe(document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['method']
        });
    }
})();
