(function () {
    const bootstrap = window.WELLHABIT_BOOTSTRAP || {};
    const timerConfig = bootstrap.timer || {};
    const STORAGE_KEY = 'wellhabitPomodoroState';
    const LOCK_PREFIX = 'wellhabitPomodoroSaved:';
    const SAVE_URL = timerConfig.saveUrl || '/tasks/pomodoro/save';
    const STATE_URL = timerConfig.stateUrl || '/tasks/pomodoro/state';
    const listeners = [];

    function defaultState() {
        const focusMinutes = Math.max(1, Number(timerConfig.focusMinutes) || 25);
        const breakMinutes = Math.max(1, Number(timerConfig.breakMinutes) || 5);
        return {
            focusMinutes,
            breakMinutes,
            activityLabel: timerConfig.activityLabel || 'work',
            cycleNumber: 1,
            mode: 'focus',
            remainingSeconds: focusMinutes * 60,
            isRunning: false,
            endAtMs: null,
            sessionKey: null,
            lastMessage: 'When a focus round ends, it will be saved automatically.',
            updatedAtMs: 0,
        };
    }

    function normalizeState(rawState) {
        const base = defaultState();
        const merged = Object.assign({}, base, rawState || {});
        merged.focusMinutes = Math.max(1, Number(merged.focusMinutes) || base.focusMinutes);
        merged.breakMinutes = Math.max(1, Number(merged.breakMinutes) || base.breakMinutes);
        merged.activityLabel = (merged.activityLabel || base.activityLabel).toString().trim() || base.activityLabel;
        merged.cycleNumber = Math.max(1, Number(merged.cycleNumber) || 1);
        merged.mode = merged.mode === 'break' ? 'break' : 'focus';
        merged.remainingSeconds = Math.max(0, Math.round(Number(merged.remainingSeconds) || 0));
        merged.isRunning = Boolean(merged.isRunning && merged.endAtMs);
        merged.endAtMs = merged.isRunning ? Number(merged.endAtMs) : null;
        merged.sessionKey = merged.sessionKey || null;
        merged.lastMessage = (merged.lastMessage || base.lastMessage).toString();
        merged.updatedAtMs = Math.max(0, Number(merged.updatedAtMs) || Date.now());
        if (!merged.isRunning && merged.remainingSeconds <= 0) {
            merged.remainingSeconds = (merged.mode === 'focus' ? merged.focusMinutes : merged.breakMinutes) * 60;
        }
        return merged;
    }

    function readState() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return normalizeState(raw ? JSON.parse(raw) : null);
        } catch (error) {
            return normalizeState(null);
        }
    }

    let syncTimer = null;

    function notifyListeners(state) {
        listeners.forEach((listener) => listener(state));
        document.dispatchEvent(new CustomEvent('wellhabit:timer-state', { detail: state }));
    }

    function queueServerSync(state) {
        if (!STATE_URL) return;
        if (syncTimer) window.clearTimeout(syncTimer);
        syncTimer = window.setTimeout(async () => {
            try {
                const shouldClear = !state.isRunning && state.mode === 'focus' && state.cycleNumber === 1 && getRemainingSeconds(state) === state.focusMinutes * 60;
                await fetch(STATE_URL, {
                    method: 'POST',
                    headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify(shouldClear ? { clear: true } : state),
                    keepalive: true,
                });
            } catch (error) {}
        }, 120);
    }

    function writeState(nextState) {
        const normalized = normalizeState(Object.assign({}, nextState || {}, { updatedAtMs: Date.now() }));
        localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
        notifyListeners(normalized);
        queueServerSync(normalized);
        return normalized;
    }

    async function hydrateStateFromServer() {
        try {
            const response = await fetch(STATE_URL, { headers: { Accept: 'application/json' } });
            const body = await response.json().catch(() => ({}));
            if (!response.ok || !body.state) return readState();
            const localState = readState();
            const remoteState = normalizeState(body.state);
            if ((remoteState.updatedAtMs || 0) > (localState.updatedAtMs || 0)) {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(remoteState));
                notifyListeners(remoteState);
                return remoteState;
            }
            queueServerSync(localState);
            return localState;
        } catch (error) {
            return readState();
        }
    }

    function getRemainingSeconds(state) {
        if (state.isRunning && state.endAtMs) {
            return Math.max(0, Math.ceil((state.endAtMs - Date.now()) / 1000));
        }
        return Math.max(0, Number(state.remainingSeconds) || 0);
    }

    function formatSeconds(totalSeconds) {
        const safe = Math.max(0, Math.round(totalSeconds || 0));
        const minutes = String(Math.floor(safe / 60)).padStart(2, '0');
        const seconds = String(safe % 60).padStart(2, '0');
        return `${minutes}:${seconds}`;
    }

    async function saveFocusSession(state) {
        if (!state.sessionKey) return;
        const lockKey = LOCK_PREFIX + state.sessionKey;
        if (localStorage.getItem(lockKey)) return;
        localStorage.setItem(lockKey, '1');
        try {
            const response = await fetch(SAVE_URL, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    focus_minutes: state.focusMinutes,
                    break_minutes: state.breakMinutes,
                    cycle_number: state.cycleNumber,
                    activity_label: state.activityLabel || 'work',
                }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error('Save failed');
            if (body.avatar_emoji && window.WellHabitSetAvatarEmoji) window.WellHabitSetAvatarEmoji(body.avatar_emoji);
            if (body.wellness_feedback && window.WellHabitShowWellnessFeedback) window.WellHabitShowWellnessFeedback(body.wellness_feedback);
            if (body.eye_prompt && window.WellHabitOpenEyeExercisePrompt) window.WellHabitOpenEyeExercisePrompt(body.eye_prompt);
        } catch (error) {
            localStorage.removeItem(lockKey);
            const latest = readState();
            latest.lastMessage = 'Focus round finished, but saving failed.';
            writeState(latest);
        }
    }

    function advanceIfNeeded() {
        const state = readState();
        if (!state.isRunning || !state.endAtMs) {
            notifyListeners(state);
            return state;
        }
        if (Date.now() < state.endAtMs) {
            notifyListeners(state);
            return state;
        }
        state.remainingSeconds = 0;
        state.isRunning = false;
        state.endAtMs = null;
        if (state.mode === 'focus') {
            const finishedFocusState = Object.assign({}, state);
            saveFocusSession(finishedFocusState);
            state.mode = 'break';
            state.remainingSeconds = state.breakMinutes * 60;
            state.lastMessage = 'Focus round done. Time for a break.';
        } else {
            state.mode = 'focus';
            state.cycleNumber += 1;
            state.remainingSeconds = state.focusMinutes * 60;
            state.sessionKey = null;
            state.lastMessage = 'Break ended. Start next focus round when ready.';
        }
        return writeState(state);
    }

    function syncRunningClock() {
        const state = advanceIfNeeded();
        if (!state.isRunning) notifyListeners(state);
    }

    window.WellHabitTimer = {
        getState() {
            return normalizeState(readState());
        },
        subscribe(listener) {
            if (typeof listener !== 'function') return () => {};
            listeners.push(listener);
            listener(this.getState());
            return () => {
                const index = listeners.indexOf(listener);
                if (index >= 0) listeners.splice(index, 1);
            };
        },
        configure(config) {
            const state = readState();
            if (config.focusMinutes) state.focusMinutes = Math.max(1, Number(config.focusMinutes) || defaultState().focusMinutes);
            if (config.breakMinutes) state.breakMinutes = Math.max(1, Number(config.breakMinutes) || defaultState().breakMinutes);
            if (config.activityLabel !== undefined) state.activityLabel = (config.activityLabel || 'work').toString().trim() || 'work';
            return writeState(state);
        },
        async start(config) {
            const state = readState();
            if (config) {
                if (config.focusMinutes) state.focusMinutes = Math.max(1, Number(config.focusMinutes) || defaultState().focusMinutes);
                if (config.breakMinutes) state.breakMinutes = Math.max(1, Number(config.breakMinutes) || defaultState().breakMinutes);
                if (config.activityLabel !== undefined) state.activityLabel = (config.activityLabel || 'work').toString().trim() || 'work';
            }
            if (state.isRunning) return writeState(state);
            if (window.WellHabitEnsureEyeExerciseNotificationPermission) {
                try { await window.WellHabitEnsureEyeExerciseNotificationPermission(); } catch (error) {}
            }
            const currentSeconds = getRemainingSeconds(state);
            state.remainingSeconds = currentSeconds > 0 ? currentSeconds : (state.mode === 'focus' ? state.focusMinutes : state.breakMinutes) * 60;
            if (state.mode === 'focus' && !state.sessionKey) {
                state.sessionKey = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
            }
            state.isRunning = true;
            state.endAtMs = Date.now() + state.remainingSeconds * 1000;
            state.lastMessage = 'Timer is running.';
            return writeState(state);
        },
        pause() {
            const state = readState();
            if (!state.isRunning) return writeState(state);
            state.remainingSeconds = getRemainingSeconds(state);
            state.isRunning = false;
            state.endAtMs = null;
            state.lastMessage = 'Timer paused.';
            return writeState(state);
        },
        reset(config) {
            const state = readState();
            if (config) {
                if (config.focusMinutes) state.focusMinutes = Math.max(1, Number(config.focusMinutes) || defaultState().focusMinutes);
                if (config.breakMinutes) state.breakMinutes = Math.max(1, Number(config.breakMinutes) || defaultState().breakMinutes);
                if (config.activityLabel !== undefined) state.activityLabel = (config.activityLabel || 'work').toString().trim() || 'work';
            }
            state.mode = 'focus';
            state.cycleNumber = 1;
            state.remainingSeconds = state.focusMinutes * 60;
            state.isRunning = false;
            state.endAtMs = null;
            state.sessionKey = null;
            state.lastMessage = 'When a focus round ends, it will be saved automatically.';
            return writeState(state);
        },
        refresh() {
            return advanceIfNeeded();
        },
        formatSeconds,
        getRemainingSeconds,
    };

    window.addEventListener('beforeunload', () => {
        try { queueServerSync(readState()); } catch (error) {}
    });
    window.addEventListener('storage', (event) => {
        if (event.key !== STORAGE_KEY) return;
        notifyListeners(readState());
    });

    hydrateStateFromServer().then((state) => notifyListeners(state || readState()));
    window.setInterval(syncRunningClock, 1000);
})();
