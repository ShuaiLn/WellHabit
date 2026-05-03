(function () {
    const modal = document.getElementById('affect-confirm-overlay');
    const yesBtn = document.getElementById('affect-confirm-yes');
    const noBtn = document.getElementById('affect-confirm-no');
    const unsureBtn = document.getElementById('affect-confirm-unsure');
    const statusEl = document.getElementById('affect-confirm-status');
    const cfg = window.WellHabitAffectConfirm || {};
    const CONFIRM_URL = cfg.confirmUrl || '/api/affect/confirm';
    const STORAGE_KEY = 'wellhabitAffectSignals';
    const LAST_PROMPT_KEY = 'wellhabitLastAffectConfirmPromptAt';
    const SIGNAL_TTL_MS = 15 * 60 * 1000;
    const PROMPT_COOLDOWN_MS = 30 * 60 * 1000;

    let activeEvidence = null;
    let submitting = false;

    function now() { return Date.now(); }

    function readSignals() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) || {} : {};
        } catch (error) {
            return {};
        }
    }

    function writeSignals(signals) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(signals || {})); } catch (error) {}
    }

    function clearSignals() {
        try { localStorage.removeItem(STORAGE_KEY); } catch (error) {}
    }

    function setSignal(key, detail) {
        const signals = readSignals();
        signals[key] = { at: now(), detail: detail || {} };
        writeSignals(signals);
        maybeShowPrompt();
    }

    function fresh(entry) {
        return Boolean(entry && Number.isFinite(Number(entry.at)) && now() - Number(entry.at) <= SIGNAL_TTL_MS);
    }

    function compactFlags(signals) {
        const flags = {
            camera_positive_affect_signal: fresh(signals.camera_positive_affect_signal),
            focus_completed: fresh(signals.focus_completed),
            break_completion: fresh(signals.break_completion),
            hydration_logged: fresh(signals.hydration_logged),
            positive_chat_text: fresh(signals.positive_chat_text),
            self_report: fresh(signals.self_report),
        };
        const supportCount = [flags.focus_completed, flags.break_completion, flags.hydration_logged, flags.positive_chat_text, flags.self_report].filter(Boolean).length;
        return { flags, supportCount };
    }

    function lastPromptTooRecent() {
        try {
            const ts = Number(localStorage.getItem(LAST_PROMPT_KEY) || 0);
            return Number.isFinite(ts) && now() - ts < PROMPT_COOLDOWN_MS;
        } catch (error) {
            return false;
        }
    }

    function rememberPromptTime() {
        try { localStorage.setItem(LAST_PROMPT_KEY, String(now())); } catch (error) {}
    }

    function importBreakSignal() {
        try {
            const raw = sessionStorage.getItem('wellhabitBreakCompletionSignal');
            if (!raw) return;
            sessionStorage.removeItem('wellhabitBreakCompletionSignal');
            const detail = JSON.parse(raw || '{}') || {};
            setSignal('break_completion', detail);
            if (detail.self_report && detail.self_report !== 'still_tired') {
                setSignal('self_report', detail);
            }
        } catch (error) {}
    }

    function maybeShowPrompt() {
        if (!modal || submitting || modal.hidden === false || lastPromptTooRecent()) return;
        const signals = readSignals();
        const { flags, supportCount } = compactFlags(signals);
        if (!flags.camera_positive_affect_signal || supportCount < 1) return;
        activeEvidence = {
            flags,
            camera_metrics: signals.camera_positive_affect_signal?.detail?.metrics || signals.camera_positive_affect_signal?.detail || {},
            context: {
                focus_completed: signals.focus_completed?.detail || null,
                break_completion: signals.break_completion?.detail || null,
                hydration_logged: signals.hydration_logged?.detail || null,
                positive_chat_text: signals.positive_chat_text?.detail || null,
                self_report: signals.self_report?.detail || null,
            },
        };
        rememberPromptTime();
        if (statusEl) statusEl.textContent = '';
        modal.hidden = false;
        document.body.classList.add('modal-open');
        yesBtn?.focus();
    }

    function closePrompt() {
        if (modal) modal.hidden = true;
        document.body.classList.remove('modal-open');
        activeEvidence = null;
    }

    async function submit(answer) {
        if (submitting) return;
        submitting = true;
        if (statusEl) statusEl.textContent = 'Saving your confirmation...';
        try {
            const response = await fetch(CONFIRM_URL, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders ? window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }) : { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    answer,
                    evidence: activeEvidence || {},
                }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok || body.ok === false) throw new Error(body.message || 'Save failed');
            clearSignals();
            closePrompt();
        } catch (error) {
            if (statusEl) statusEl.textContent = 'Could not save that answer. You can try again.';
        } finally {
            submitting = false;
        }
    }

    yesBtn?.addEventListener('click', () => submit('yes'));
    noBtn?.addEventListener('click', () => submit('no'));
    unsureBtn?.addEventListener('click', () => submit('not_sure'));

    document.addEventListener('wellhabit:possible-relaxed-affect', (event) => {
        setSignal('camera_positive_affect_signal', event.detail || {});
    });
    document.addEventListener('wellhabit:focus-session-saved', (event) => {
        setSignal('focus_completed', event.detail || {});
    });
    document.addEventListener('wellhabit:hydration-saved', (event) => {
        setSignal('hydration_logged', event.detail || {});
    });
    document.addEventListener('wellhabit:positive-chat-text', (event) => {
        setSignal('positive_chat_text', event.detail || {});
    });

    importBreakSignal();
    maybeShowPrompt();
})();
