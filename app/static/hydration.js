(function () {
    const config = window.WELLHABIT_PROMPTS || {};
    const overlay = document.getElementById('habit-modal-overlay');
    const titleEl = document.getElementById('habit-modal-title');
    const eyebrowEl = document.getElementById('habit-modal-eyebrow');
    const messageEl = document.getElementById('habit-modal-message');
    const beverageEl = document.getElementById('hydration-beverage');
    const amountEl = document.getElementById('hydration-amount');
    const customWrap = document.getElementById('hydration-custom-wrap');
    const customEl = document.getElementById('hydration-custom');
    const finishedBtn = document.getElementById('hydration-finished-btn');
    const notYetBtn = document.getElementById('hydration-not-yet-btn');
    const skipBtn = document.getElementById('hydration-skip-btn');
    const STORAGE_KEY = 'wellhabitActiveHydrationPrompt';
    const PAUSE_KEY = 'wellhabitHydrationPauseUntil';
    const SEEN_KEY = 'wellhabitSeenHydrationPromptSignatures';
    const MISSED_SEEN_KEY = 'wellhabitSeenMissedHydrationSummaries';
    const STATUS_URL = config.statusUrl || '/hydration/status';
    const RESPOND_URL = '/hydration/respond';

    if (!overlay || !titleEl || !messageEl || !beverageEl || !amountEl || !customWrap || !customEl) return;

    let activePrompt = null;
    let upcomingTimerId = null;
    let pendingRequest = false;
    let missedBanner = null;

    function persistActivePrompt(prompt) {
        if (!prompt || !prompt.id) {
            localStorage.removeItem(STORAGE_KEY);
            return;
        }
        localStorage.setItem(STORAGE_KEY, JSON.stringify(prompt));
    }

    function restoreStoredPrompt() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || !parsed.id || !parsed.due_at_iso) {
                localStorage.removeItem(STORAGE_KEY);
                return null;
            }
            const dueAt = new Date(parsed.due_at_iso);
            const ageMs = Date.now() - dueAt.getTime();
            const graceMinutes = Number(config.hydrationDueGraceMinutes);
            if (Number.isNaN(dueAt.getTime()) || ageMs > graceMinutes * 60 * 1000 || ageMs < -12 * 60 * 60 * 1000) {
                localStorage.removeItem(STORAGE_KEY);
                return null;
            }
            return parsed;
        } catch (error) {
            localStorage.removeItem(STORAGE_KEY);
            return null;
        }
    }

    function promptSignature(prompt) {
        if (!prompt || !prompt.id) return '';
        return `${prompt.id}:${prompt.due_at_iso || ''}:${prompt.response_status || ''}`;
    }

    function summarySignature(summary) {
        if (!summary || !summary.latest_prompt_id) return '';
        return `${summary.latest_prompt_id}:${summary.count || 0}:${summary.latest_due_at_iso || ''}`;
    }

    function getSeenPromptSignatures() {
        try {
            const raw = sessionStorage.getItem(SEEN_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    }

    function hasSeenPrompt(prompt) {
        const signature = promptSignature(prompt);
        return Boolean(signature) && getSeenPromptSignatures().includes(signature);
    }

    function markPromptSeen(prompt) {
        const signature = promptSignature(prompt);
        if (!signature) return;
        const signatures = getSeenPromptSignatures();
        if (signatures.includes(signature)) return;
        signatures.push(signature);
        sessionStorage.setItem(SEEN_KEY, JSON.stringify(signatures.slice(-30)));
    }

    function getSeenMissedSummaries() {
        try {
            const raw = sessionStorage.getItem(MISSED_SEEN_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    }

    function hasSeenMissedSummary(summary) {
        const signature = summarySignature(summary);
        return Boolean(signature) && getSeenMissedSummaries().includes(signature);
    }

    function markMissedSummarySeen(summary) {
        const signature = summarySignature(summary);
        if (!signature) return;
        const seen = getSeenMissedSummaries();
        if (seen.includes(signature)) return;
        seen.push(signature);
        sessionStorage.setItem(MISSED_SEEN_KEY, JSON.stringify(seen.slice(-20)));
    }

    function ensureMissedBanner() {
        if (missedBanner) return missedBanner;
        const banner = document.createElement('div');
        banner.className = 'hydration-missed-banner';
        banner.hidden = true;
        const textWrap = document.createElement('div');
        const title = document.createElement('strong');
        title.className = 'hydration-missed-title';
        title.textContent = 'Missed water reminder';
        const text = document.createElement('p');
        text.className = 'hydration-missed-text';
        text.id = 'hydration-missed-text';
        textWrap.append(title, text);

        const actions = document.createElement('div');
        actions.className = 'hydration-missed-actions';
        const okButton = document.createElement('button');
        okButton.type = 'button';
        okButton.className = 'btn btn-secondary btn-sm';
        okButton.id = 'hydration-missed-ok-btn';
        okButton.textContent = 'Okay';
        actions.appendChild(okButton);

        banner.append(textWrap, actions);
        document.body.appendChild(banner);
        okButton.addEventListener('click', () => {
            const summary = banner._summary || null;
            if (summary) {
                markMissedSummarySeen(summary);
            }
            banner.hidden = true;
        });
        missedBanner = banner;
        return banner;
    }

    function renderMissedSummary(summary) {
        const banner = ensureMissedBanner();
        if (!summary || !summary.count) {
            banner.hidden = true;
            banner._summary = null;
            return;
        }

        if (hasSeenMissedSummary(summary)) {
            banner.hidden = true;
            banner._summary = summary;
            return;
        }

        const textEl = banner.querySelector('#hydration-missed-text');
        if (textEl) {
            textEl.textContent = summary.message || `You missed ${summary.count} water reminder${summary.count === 1 ? '' : 's'} today.`;
        }
        banner._summary = summary;
        banner.hidden = false;
    }

    function toggleCustomInput() {
        const show = beverageEl.value === 'other';
        customWrap.hidden = !show;
        customEl.required = show;
        if (!show) {
            customEl.value = '';
        }
    }

    function setDefaultAmount() {
        if (amountEl.value.trim()) return;
        amountEl.value = 'a glass';
    }

    function setButtonsDisabled(disabled) {
        pendingRequest = disabled;
        [finishedBtn, notYetBtn, skipBtn].forEach((btn) => {
            if (!btn) return;
            btn.disabled = disabled;
            btn.style.opacity = disabled ? '0.7' : '1';
        });
    }

    function getPauseUntil() {
        return Number(sessionStorage.getItem(PAUSE_KEY) || 0);
    }

    function hydrationPaused() {
        return getPauseUntil() > Date.now();
    }

    function openPrompt(prompt, fallbackType, options = {}) {
        if (!prompt || !prompt.id) {
            closePrompt();
            refreshPromptState();
            return;
        }

        if (hydrationPaused()) {
            persistActivePrompt(prompt);
            scheduleUpcoming({ due_at_iso: new Date(getPauseUntil() + 250).toISOString() });
            return;
        }

        activePrompt = prompt;

        eyebrowEl.textContent = activePrompt.slot_label || 'Hydration Reminder';
        titleEl.textContent = activePrompt.slot_label ? `${activePrompt.slot_label} reminder` : 'Scheduled water reminder';
        messageEl.textContent = activePrompt.message || 'Choose what you want to drink, type an amount, then tell WellHabit if you finished it.';
        beverageEl.value = ['water', 'milk', 'coke', 'other'].includes(activePrompt.beverage) ? activePrompt.beverage : 'water';
        amountEl.value = '';
        customEl.value = activePrompt.custom_beverage || '';
        toggleCustomInput();
        setDefaultAmount(activePrompt.prompt_type || fallbackType);
        persistActivePrompt(activePrompt);
        if (!options.skipSeenMark) {
            markPromptSeen(activePrompt);
        }
        overlay.hidden = false;
    }

    function closePrompt() {
        overlay.hidden = true;
        activePrompt = null;
        persistActivePrompt(null);
    }

    function scheduleUpcoming(prompt) {
        if (upcomingTimerId) {
            clearTimeout(upcomingTimerId);
            upcomingTimerId = null;
        }
        if (!prompt || !prompt.due_at_iso) return;
        const delay = new Date(prompt.due_at_iso).getTime() - Date.now();
        if (delay <= 0 || delay > 12 * 60 * 60 * 1000) return;
        upcomingTimerId = setTimeout(() => {
            refreshPromptState();
        }, delay + 200);
    }

    async function refreshPromptState() {
        try {
            const response = await fetch(STATUS_URL, { headers: { 'Accept': 'application/json' } });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) return;

            config.morningPromptExists = body.morning_prompt_exists;
            config.morningPrompt = body.morning_prompt;
            config.upcoming = body.upcoming_prompt;
            config.missedSummary = body.missed_summary || null;
            renderMissedSummary(config.missedSummary);

            if (body.due_prompt && body.due_prompt.id) {
                const duePrompt = body.due_prompt;
                const isActiveSamePrompt = Boolean(activePrompt && promptSignature(activePrompt) === promptSignature(duePrompt));
                if (isActiveSamePrompt) {
                    openPrompt(duePrompt, duePrompt.prompt_type, { skipSeenMark: true });
                } else if (!hasSeenPrompt(duePrompt)) {
                    openPrompt(duePrompt, duePrompt.prompt_type);
                }
            } else if (!document.hidden) {
                if (!pendingRequest) {
                    closePrompt();
                }
            }
            scheduleUpcoming(body.upcoming_prompt);
        } catch (error) {
            const stored = restoreStoredPrompt();
            if (stored && !overlay.hidden) {
                openPrompt(stored, stored.prompt_type || 'scheduled_wake');
            }
        }
    }

    async function sendResponse(status) {
        if (!activePrompt || pendingRequest) return;
        if (!activePrompt.id) {
            closePrompt();
            await refreshPromptState();
            return;
        }

        if (beverageEl.value === 'other' && !customEl.value.trim()) {
            customEl.focus();
            return;
        }

        const payload = {
            prompt_id: activePrompt.id,
            prompt_type: activePrompt.prompt_type,
            beverage: beverageEl.value,
            custom_beverage: customEl.value.trim(),
            amount_text: amountEl.value.trim(),
            action: status,
        };

        setButtonsDisabled(true);
        try {
            const response = await fetch(RESPOND_URL, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders ? window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }) : { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (body.message === 'Prompt not found.') {
                    closePrompt();
                    await refreshPromptState();
                    return;
                }
                if (body.message) {
                    alert(body.message);
                }
                return;
            }

            document.dispatchEvent(new CustomEvent('wellhabit:hydration-saved', { detail: body }));
            amountEl.value = '';
            customEl.value = '';
            closePrompt();
            renderMissedSummary(body.missed_summary || null);

            if (body.avatar_emoji && window.WellHabitSetAvatarEmoji) {
                window.WellHabitSetAvatarEmoji(body.avatar_emoji);
            }
            if (body.wellness_feedback && window.WellHabitShowWellnessFeedback) {
                window.WellHabitShowWellnessFeedback(
                    body.wellness_feedback,
                    { reloadOnClose: Boolean(window.location.pathname.includes('/dashboard')) }
                );
            } else if (window.location.pathname.includes('/dashboard')) {
                window.location.reload();
                return;
            }

            await refreshPromptState();
        } catch (error) {
            console.error('Hydration response failed', error);
            alert('Saving the hydration response failed. Please try again.');
        } finally {
            setButtonsDisabled(false);
        }
    }

    window.WellHabitHydrationStorePrompt = function (prompt) {
        persistActivePrompt(prompt);
    };

    window.WellHabitHydrationOpenPrompt = function (prompt) {
        if (prompt && prompt.id) {
            openPrompt(prompt, prompt.prompt_type || 'scheduled_wake');
        }
    };

    beverageEl.addEventListener('change', toggleCustomInput);
    finishedBtn.addEventListener('click', () => sendResponse('done'));
    notYetBtn.addEventListener('click', () => sendResponse('not_yet'));
    skipBtn.addEventListener('click', () => sendResponse('skipped'));

    renderMissedSummary(config.missedSummary || null);

    if (config.due && !hasSeenPrompt(config.due)) {
        openPrompt(config.due, config.due.prompt_type);
    }

    scheduleUpcoming(config.upcoming);
    refreshPromptState();
    window.addEventListener('focus', refreshPromptState);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refreshPromptState();
    });
})();
