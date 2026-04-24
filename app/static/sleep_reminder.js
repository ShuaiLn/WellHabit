(function () {
    const prompts = Object.assign({}, (window.WELLHABIT_BOOTSTRAP || {}).prompts || {}, window.WELLHABIT_PROMPTS || {});
    const overlay = document.getElementById('sleep-reminder-overlay');
    const messageEl = document.getElementById('sleep-reminder-message');
    const okBtn = document.getElementById('sleep-reminder-ok-btn');
    const laterBtn = document.getElementById('sleep-reminder-later-btn');
    const SNOOZE_PREFIX = 'wellhabitSleepReminderSnooze:';
    const ACK_PREFIX = 'wellhabitSleepReminderAck:';
    const statusUrl = prompts.sleepStatusUrl || '/sleep/status';
    let pendingReminder = null;

    if (!overlay || !messageEl || !okBtn) return;

    function isAcked(dateKey) {
        return Boolean(dateKey && localStorage.getItem(`${ACK_PREFIX}${dateKey}`) === '1');
    }

    function snoozedUntil(dateKey) {
        const raw = dateKey ? localStorage.getItem(`${SNOOZE_PREFIX}${dateKey}`) : '';
        const ts = raw ? Number(raw) : 0;
        return Number.isFinite(ts) ? ts : 0;
    }

    function isSnoozed(dateKey) {
        const until = snoozedUntil(dateKey);
        return Boolean(until && Date.now() < until);
    }

    function markAcked(dateKey) {
        if (dateKey) localStorage.setItem(`${ACK_PREFIX}${dateKey}`, '1');
    }

    function timerStillRunning() {
        const timer = window.WellHabitTimer?.getState?.();
        return Boolean(timer && timer.isRunning);
    }

    function openReminder(reminder) {
        if (!reminder || isAcked(reminder.date_key) || isSnoozed(reminder.date_key)) return;
        pendingReminder = reminder;
        if (timerStillRunning()) return;
        messageEl.textContent = reminder.message || 'It is time to sleep.';
        overlay.hidden = false;
    }

    function closeReminder() {
        overlay.hidden = true;
    }

    async function refreshSleepReminder() {
        try {
            const response = await fetch(statusUrl, { headers: { Accept: 'application/json' } });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) return;
            const reminder = body.due_sleep_reminder;
            if (!reminder) {
                pendingReminder = null;
                closeReminder();
                return;
            }
            if (isAcked(reminder.date_key) || isSnoozed(reminder.date_key)) {
                closeReminder();
                return;
            }
            openReminder(reminder);
        } catch (error) {}
    }

    okBtn.addEventListener('click', () => {
        if (pendingReminder?.date_key) markAcked(pendingReminder.date_key);
        closeReminder();
    });

    laterBtn?.addEventListener('click', () => {
        if (pendingReminder?.date_key) {
            localStorage.setItem(`${SNOOZE_PREFIX}${pendingReminder.date_key}`, String(Date.now() + (30 * 60 * 1000)));
        }
        closeReminder();
    });

    window.WellHabitTimer?.subscribe?.((state) => {
        if (pendingReminder && !state.isRunning) openReminder(pendingReminder);
    });

    refreshSleepReminder();
    window.setInterval(refreshSleepReminder, 60000);
    window.addEventListener('focus', refreshSleepReminder);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refreshSleepReminder();
    });
})();
