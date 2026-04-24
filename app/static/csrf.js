(function () {
    window.WellHabitGetCsrfToken = function () {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    };

    window.WellHabitCsrfHeaders = function (headers) {
        const merged = Object.assign({}, headers || {});
        const token = window.WellHabitGetCsrfToken();
        if (token) merged['X-CSRFToken'] = token;
        return merged;
    };

    document.querySelectorAll('form').forEach((form) => {
        if ((form.getAttribute('method') || '').toLowerCase() !== 'post') return;
        if (form.querySelector('input[name="csrf_token"]')) return;
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'csrf_token';
        hidden.value = window.WellHabitGetCsrfToken();
        form.prepend(hidden);
    });
})();
