(function () {
    const timeEl = document.getElementById('nav-local-time');

    function updateLocalTime() {
        if (!timeEl) return;
        const now = new Date();
        timeEl.textContent = now.toLocaleTimeString([], {
            hour: 'numeric',
            minute: '2-digit',
        });
    }

    updateLocalTime();
    window.setInterval(updateLocalTime, 15000);
})();
