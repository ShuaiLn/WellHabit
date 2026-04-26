(function () {
    if (window.WELLHABIT_DISABLE_EYE_EXERCISE_OVERLAY) {
        window.WellHabitOpenEyeExercisePrompt = function () {};
        window.WellHabitEnsureEyeExerciseNotificationPermission = async function () { return 'disabled'; };
        return;
    }
    const bootstrap = window.WELLHABIT_BOOTSTRAP || {};
    const prompts = Object.assign({}, bootstrap.prompts || {}, window.WELLHABIT_PROMPTS || {});
    const overlay = document.getElementById('eye-exercise-overlay');
    const messageEl = document.getElementById('eye-exercise-message');
    const sourceEl = document.getElementById('eye-exercise-source');
    const iframe = document.getElementById('eye-exercise-iframe');
    const videoWrap = document.getElementById('eye-exercise-video-wrap');
    const actionsWrap = document.getElementById('eye-exercise-actions');
    const finishedWrap = document.getElementById('eye-exercise-finished-wrap');
    const yesBtn = document.getElementById('eye-exercise-yes-btn');
    const notYetBtn = document.getElementById('eye-exercise-not-yet-btn');
    const noThanksBtn = document.getElementById('eye-exercise-no-thanks-btn');
    const finishedBtn = document.getElementById('eye-exercise-finished-btn');
    const respondUrl = prompts.eyeExerciseRespondUrl || '/eye-exercise/respond';
    const statusUrl = prompts.eyeExerciseStatusUrl || '/eye-exercise/status';
    const startUrl = prompts.eyeExerciseStartUrl || '/eye-exercise/start';
    let activePrompt = null;
    let activeBrowserNotification = null;
    let lastNotifiedPromptId = null;
    let manualOpenInProgress = false;

    if (!overlay || !messageEl || !yesBtn || !notYetBtn || !noThanksBtn || !finishedBtn) {
        window.WellHabitOpenEyeExercisePrompt = function () {};
        return;
    }

    function resetVideo() {
        if (iframe) iframe.src = '';
        if (videoWrap) videoWrap.hidden = true;
        if (finishedWrap) finishedWrap.hidden = true;
        if (actionsWrap) actionsWrap.hidden = false;
    }

    function closeBrowserNotification() {
        if (activeBrowserNotification) {
            activeBrowserNotification.close();
            activeBrowserNotification = null;
        }
    }

    function isPageActiveForPrompt() {
        return document.visibilityState === 'visible' && document.hasFocus();
    }

    function maybeNotify(prompt) {
        if (!prompt || isPageActiveForPrompt()) {
            closeBrowserNotification();
            return false;
        }
        if (!('Notification' in window) || Notification.permission !== 'granted') return false;
        if (lastNotifiedPromptId === prompt.id) return true;
        closeBrowserNotification();
        const bodyText = prompt.message || 'Eye exercise reminder. Click to return to WellHabit.';
        const notification = new Notification('WellHabit eye exercise reminder', {
            body: bodyText,
            tag: `wellhabit-eye-exercise-${prompt.id}`,
            renotify: true,
        });
        activeBrowserNotification = notification;
        lastNotifiedPromptId = prompt.id;
        notification.onclick = () => {
            window.focus();
            closeBrowserNotification();
            showPrompt(prompt, { forceOverlay: true });
        };
        notification.onclose = () => {
            if (activeBrowserNotification === notification) activeBrowserNotification = null;
        };
        return true;
    }

    function closePrompt() {
        overlay.hidden = true;
        resetVideo();
        closeBrowserNotification();
    }

    function showPrompt(prompt, options = {}) {
        if (!prompt) return;
        activePrompt = prompt;
        if (!options.forceOverlay && maybeNotify(prompt)) {
            overlay.hidden = true;
            resetVideo();
            return;
        }
        closeBrowserNotification();
        messageEl.textContent = prompt.message || `You've focused for ${prompts.eyeExerciseThresholdMinutes || 20} minutes. Do you want to do an eye exercise now?`;
        if (sourceEl) sourceEl.textContent = prompt.source_text || 'Source: YouTube · lenstark.com';
        resetVideo();
        overlay.hidden = false;
        if (prompt.response_status === 'watching') {
            if (iframe) iframe.src = prompt.embed_url || 'https://www.youtube.com/embed/iVb4vUp70zY';
            if (videoWrap) videoWrap.hidden = false;
            if (actionsWrap) actionsWrap.hidden = true;
            if (finishedWrap) finishedWrap.hidden = false;
        }
    }

    function clearEyePanelQuery() {
        try {
            const url = new URL(window.location.href);
            if (url.searchParams.get('panel') !== 'eye') return;
            url.searchParams.delete('panel');
            const cleanUrl = `${url.pathname}${url.search}${url.hash}`;
            window.history.replaceState({}, '', cleanUrl);
        } catch (error) {
            // ignore URL cleanup errors
        }
    }

    async function openManualPrompt(options = {}) {
        if (manualOpenInProgress) return null;
        manualOpenInProgress = true;
        try {
            const response = await fetch(startUrl, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders ? window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }) : { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: 'nav' }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.message || 'Could not open eye exercise.');
            if (body.avatar_emoji && window.WellHabitSetAvatarEmoji) window.WellHabitSetAvatarEmoji(body.avatar_emoji);
            if (body.eye_prompt) showPrompt(body.eye_prompt, { forceOverlay: true });
            if (options.cleanUrl !== false) clearEyePanelQuery();
            return body;
        } finally {
            manualOpenInProgress = false;
        }
    }

    async function sendAction(action) {
        if (!activePrompt) return null;
        const response = await fetch(respondUrl, {
            method: 'POST',
            headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ prompt_id: activePrompt.id, action }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.message || 'Eye exercise action failed.');
        if (body.avatar_emoji && window.WellHabitSetAvatarEmoji) window.WellHabitSetAvatarEmoji(body.avatar_emoji);
        if (body.wellness_feedback && window.WellHabitShowWellnessFeedback) {
            window.WellHabitShowWellnessFeedback(body.wellness_feedback, { reloadOnClose: Boolean(body.refresh_dashboard && window.location.pathname.includes('/dashboard')) });
        }
        if (body.eye_prompt) {
            activePrompt = body.eye_prompt;
            lastNotifiedPromptId = body.eye_prompt.id || null;
        } else {
            lastNotifiedPromptId = null;
        }
        if (body.show_video) {
            if (iframe) iframe.src = (body.eye_prompt && body.eye_prompt.embed_url) || activePrompt.embed_url || 'https://www.youtube.com/embed/iVb4vUp70zY';
            if (videoWrap) videoWrap.hidden = false;
            if (actionsWrap) actionsWrap.hidden = true;
            if (finishedWrap) finishedWrap.hidden = false;
        } else {
            closePrompt();
        }
        if (body.refresh_dashboard && window.location.pathname.includes('/dashboard') && !body.wellness_feedback) {
            window.location.reload();
        }
        return body;
    }

    yesBtn.addEventListener('click', () => { sendAction('yes').catch((error) => window.alert(error.message || 'Eye exercise action failed.')); });
    notYetBtn.addEventListener('click', () => { sendAction('not_yet').catch((error) => window.alert(error.message || 'Eye exercise action failed.')); });
    noThanksBtn.addEventListener('click', () => { sendAction('no_thanks').catch((error) => window.alert(error.message || 'Eye exercise action failed.')); });
    finishedBtn.addEventListener('click', () => { sendAction('finished').catch((error) => window.alert(error.message || 'Eye exercise action failed.')); });
    overlay.addEventListener('click', (event) => {
        if (event.target === overlay) closePrompt();
    });

    async function refreshStatus() {
        try {
            const response = await fetch(statusUrl, { headers: { Accept: 'application/json' } });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) return;
            if (body.avatar_emoji && window.WellHabitSetAvatarEmoji) window.WellHabitSetAvatarEmoji(body.avatar_emoji);
            if (!body.eye_prompt) {
                closeBrowserNotification();
                return;
            }
            showPrompt(body.eye_prompt);
        } catch (error) {}
    }

    function openFromUrlIfRequested() {
        try {
            const params = new URLSearchParams(window.location.search);
            if (params.get('panel') !== 'eye') return;
            openManualPrompt({ cleanUrl: true }).catch((error) => window.alert(error.message || 'Could not open eye exercise.'));
        } catch (error) {
            // ignore malformed URL state
        }
    }

    document.addEventListener('click', (event) => {
        const link = event.target.closest ? event.target.closest('[data-nav-eye]') : null;
        if (!link) return;
        let targetUrl;
        try {
            targetUrl = new URL(link.href, window.location.href);
        } catch (error) {
            return;
        }
        if (targetUrl.origin !== window.location.origin || targetUrl.pathname !== window.location.pathname) return;
        event.preventDefault();
        try {
            const currentUrl = new URL(window.location.href);
            currentUrl.searchParams.set('panel', 'eye');
            window.history.replaceState({}, '', `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`);
        } catch (error) {
            // ignore URL update errors
        }
        openManualPrompt({ cleanUrl: true }).catch((error) => window.alert(error.message || 'Could not open eye exercise.'));
    });

    window.WellHabitEnsureEyeExerciseNotificationPermission = async function () {
        if (!(window.Notification && Notification.requestPermission)) return 'unsupported';
        if (Notification.permission === 'granted' || Notification.permission === 'denied') return Notification.permission;
        try {
            return await Notification.requestPermission();
        } catch (error) {
            return 'default';
        }
    };

    window.addEventListener('focus', refreshStatus);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            closeBrowserNotification();
            refreshStatus();
        }
    });
    window.addEventListener('beforeunload', closeBrowserNotification);
    window.setInterval(refreshStatus, 60000);
    refreshStatus();
    window.WellHabitOpenEyeExercisePrompt = showPrompt;
    window.WellHabitOpenManualEyeExercisePrompt = openManualPrompt;
    openFromUrlIfRequested();
})();
