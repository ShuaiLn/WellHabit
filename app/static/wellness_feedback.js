(function () {
    const bootstrap = window.WELLHABIT_BOOTSTRAP || {};
    const overlay = document.getElementById('wellness-feedback-overlay');
    const titleEl = document.getElementById('wellness-feedback-title');
    const eyebrowEl = document.getElementById('wellness-feedback-eyebrow');
    const messageEl = document.getElementById('wellness-feedback-message');
    const metricsEl = document.getElementById('wellness-feedback-metrics');
    const moodWrap = document.getElementById('wellness-feedback-mood-wrap');
    const moodPill = document.getElementById('wellness-feedback-mood-pill');
    const summaryWrap = document.getElementById('wellness-feedback-summary-wrap');
    const summaryText = document.getElementById('wellness-feedback-summary-text');
    const boundaryWrap = document.getElementById('wellness-feedback-boundary-wrap');
    const boundaryList = document.getElementById('wellness-feedback-boundary-list');
    const supportWrap = document.getElementById('wellness-feedback-support-wrap');
    const supportTitle = document.getElementById('wellness-feedback-support-title');
    const supportContact = document.getElementById('wellness-feedback-support-contact');
    const supportChat = document.getElementById('wellness-feedback-support-chat');
    const supportUrgent = document.getElementById('wellness-feedback-support-urgent');
    const okBtn = document.getElementById('wellness-feedback-ok-btn');

    let reloadOnClose = false;

    function setAvatarEmoji(nextEmoji) {
        if (nextEmoji) window.currentAvatarEmoji = nextEmoji;
        const fullAvatar = document.getElementById('global-timer-avatar');
        const miniAvatar = document.getElementById('global-timer-mini-avatar');
        if (fullAvatar && nextEmoji) fullAvatar.textContent = nextEmoji;
        if (miniAvatar && nextEmoji) miniAvatar.textContent = nextEmoji;
    }

    window.WellHabitSetAvatarEmoji = setAvatarEmoji;

    function hide() {
        if (overlay) overlay.hidden = true;
        document.body.classList.remove('modal-open');
        document.dispatchEvent(new CustomEvent('wellhabit:wellness-feedback-hidden'));
        if (reloadOnClose) {
            reloadOnClose = false;
            window.location.reload();
        }
    }

    function renderMetrics(metrics) {
        if (!metricsEl) return;
        metricsEl.innerHTML = '';
        const visibleMetrics = (metrics || []).filter((item) => Number(item.delta || 0) !== 0).slice(0, 4);
        visibleMetrics.forEach((item) => {
            const chip = document.createElement('span');
            const toneClass = item.tone_class || (item.delta > 0 ? 'plus' : (item.delta < 0 ? 'minus' : 'zero'));
            chip.className = `wellness-feedback-chip ${toneClass} metric-${item.key || 'overall'}`;
            chip.textContent = `${item.label} ${item.signed || '+0'}`;
            metricsEl.appendChild(chip);
        });
        metricsEl.hidden = visibleMetrics.length === 0;
    }

    function show(feedback, options) {
        if (!overlay || !feedback) return;
        reloadOnClose = Boolean(options && options.reloadOnClose);
        if (feedback.avatar_emoji) setAvatarEmoji(feedback.avatar_emoji);
        try {
            sessionStorage.setItem('wellhabitHydrationPauseUntil', String(Date.now() + 15000));
        } catch (error) {}
        const tone = feedback.tone || 'steady';
        overlay.dataset.tone = tone;
        if (eyebrowEl) {
            eyebrowEl.textContent = tone === 'positive' ? 'AI Encouragement' : (tone === 'negative' ? 'AI Suggestion' : 'AI Wellness Update');
        }
        if (titleEl) titleEl.textContent = feedback.title || 'Scores updated';
        if (messageEl) messageEl.textContent = feedback.message || 'Your wellness scores were refreshed.';
        if (feedback.detected_mood) {
            if (moodPill) moodPill.textContent = `AI analyzed mood: ${feedback.detected_mood}`;
            if (moodWrap) moodWrap.hidden = false;
        } else if (moodWrap) {
            moodWrap.hidden = true;
        }
        if (feedback.care_summary) {
            if (summaryText) summaryText.textContent = feedback.care_summary;
            if (summaryWrap) summaryWrap.hidden = false;
        } else if (summaryWrap) {
            summaryWrap.hidden = true;
        }
        if (Array.isArray(feedback.boundary_lines) && feedback.boundary_lines.length) {
            if (boundaryList) {
                boundaryList.innerHTML = '';
                feedback.boundary_lines.forEach((line) => {
                    const li = document.createElement('li');
                    li.textContent = line;
                    boundaryList.appendChild(li);
                });
            }
            if (boundaryWrap) {
                boundaryWrap.hidden = false;
                boundaryWrap.open = false;
            }
        } else if (boundaryWrap) {
            boundaryWrap.hidden = true;
            boundaryWrap.open = false;
        }
        if (feedback.crisis_support && feedback.crisis_support.show_now) {
            if (supportTitle) {
                supportTitle.textContent = `${feedback.crisis_support.service_name || 'Real-person support'}${feedback.crisis_support.region_label ? ` · ${feedback.crisis_support.region_label}` : ''}`;
            }
            if (supportContact) supportContact.textContent = feedback.crisis_support.contact_line || 'Use a local crisis line or emergency number.';
            if (supportChat) supportChat.textContent = feedback.crisis_support.chat_line || '';
            if (supportUrgent) supportUrgent.textContent = feedback.crisis_support.urgent_line || '';
            if (supportWrap) supportWrap.hidden = false;
        } else if (supportWrap) {
            supportWrap.hidden = true;
        }
        renderMetrics(feedback.metrics || []);
        document.body.classList.add('modal-open');
        overlay.hidden = false;
    }

    okBtn?.addEventListener('click', hide);
    overlay?.addEventListener('click', (event) => {
        if (event.target === overlay) hide();
    });

    const initialFeedback = bootstrap.pendingWellnessFeedback || null;
    if (initialFeedback) show(initialFeedback);

    window.WellHabitShowWellnessFeedback = function (payload, options) {
        const result = show(payload, options);
        window.setTimeout(() => {
            const currentOverlay = document.getElementById('wellness-feedback-overlay');
            if (currentOverlay) currentOverlay.hidden = true;
            document.body.classList.remove('modal-open');
            document.dispatchEvent(new CustomEvent('wellhabit:wellness-feedback-hidden'));
        }, 3000);
        return result;
    };
})();
