(function () {
    const config = window.WELLHABIT_CARE || {};
    const ids = Object.assign({
        messages: 'care-messages',
        form: 'care-form',
        input: 'care-input',
        sendBtn: 'care-send-btn',
        endBtn: 'care-end-btn',
        micBtn: 'care-mic-btn',
        status: 'care-status-line',
    }, config.elementIds || {});
    const rootEl = config.rootSelector ? document.querySelector(config.rootSelector) : document;
    const messagesEl = document.getElementById(ids.messages);
    const formEl = document.getElementById(ids.form);
    const inputEl = document.getElementById(ids.input);
    const sendBtn = document.getElementById(ids.sendBtn);
    const endBtn = document.getElementById(ids.endBtn);
    const micBtn = document.getElementById(ids.micBtn);
    const statusEl = document.getElementById(ids.status);
    const quickBtns = Array.from((rootEl || document).querySelectorAll(config.quickButtonSelector || '.care-quick-btn'));

    if (!messagesEl || !formEl || !inputEl || !sendBtn || !endBtn || !config.sessionId) return;

    const STORAGE_PREFIX = 'wellhabitCareChat:';
    const STORAGE_KEY = `${STORAGE_PREFIX}${config.sessionId}`;
    let messages = [];
    let sending = false;
    let ending = false;
    let typingVisible = false;
    let recognition = null;
    let voiceInputActive = false;
    let voiceFinalText = '';
    let silenceTimer = null;
    let maxVoiceTimer = null;
    let voiceWarningTimer = null;
    let lastVoiceActivityAt = 0;
    let voiceHeard = false;
    let voiceStopReason = 'manual';


    function browserSupportContext() {
        let timezone = '';
        try {
            timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        } catch (error) {
            timezone = '';
        }
        return {
            browser_locale: navigator.language || '',
            browser_languages: Array.isArray(navigator.languages) ? navigator.languages.slice(0, 6) : [],
            browser_timezone: timezone,
        };
    }

    function saveState() {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
        } catch (error) {
            // ignore storage errors
        }
    }

    function loadState() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : null;
        } catch (error) {
            return null;
        }
    }

    function clearState() {
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (error) {
            // ignore storage errors
        }
    }

    function pruneOldStateKeys() {
        try {
            const staleKeys = [];
            for (let index = 0; index < sessionStorage.length; index += 1) {
                const key = sessionStorage.key(index);
                if (key && key.startsWith(STORAGE_PREFIX) && key !== STORAGE_KEY) {
                    staleKeys.push(key);
                }
            }
            staleKeys.forEach((key) => sessionStorage.removeItem(key));
        } catch (error) {
            // ignore storage errors
        }
    }

    function clearAllCareState() {
        try {
            const keys = [];
            for (let index = 0; index < sessionStorage.length; index += 1) {
                const key = sessionStorage.key(index);
                if (key && key.startsWith(STORAGE_PREFIX)) {
                    keys.push(key);
                }
            }
            keys.forEach((key) => sessionStorage.removeItem(key));
        } catch (error) {
            // ignore storage errors
        }
    }

    function speechRecognitionClass() {
        return window.SpeechRecognition || window.webkitSpeechRecognition || null;
    }

    function isSecureEnoughForMicrophone() {
        return window.isSecureContext || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    }

    function dispatchVoiceInputState(active, reason) {
        document.dispatchEvent(new CustomEvent('wellhabit:voice-input-state', {
            detail: { active: Boolean(active), reason: reason || 'manual' }
        }));
    }

    function setMicActive(active) {
        if (!micBtn) return;
        micBtn.classList.toggle('is-recording', Boolean(active));
        micBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
        micBtn.textContent = active ? '■' : '🎙️';
        micBtn.title = active ? 'Stop voice input' : 'Voice input';
    }

    function clearVoiceTimers() {
        if (silenceTimer) window.clearInterval(silenceTimer);
        if (maxVoiceTimer) window.clearTimeout(maxVoiceTimer);
        if (voiceWarningTimer) window.clearTimeout(voiceWarningTimer);
        silenceTimer = null;
        maxVoiceTimer = null;
        voiceWarningTimer = null;
    }

    function cleanupVoiceInput(reason) {
        clearVoiceTimers();
        recognition = null;
        if (voiceInputActive) {
            voiceInputActive = false;
            dispatchVoiceInputState(false, reason || voiceStopReason);
        }
        setMicActive(false);
    }

    function finishVoiceInput(reason) {
        const finalReason = reason || voiceStopReason || 'manual';
        cleanupVoiceInput(finalReason);
        if (finalReason === 'max_duration') {
            statusEl.textContent = 'Voice input reached 60 seconds and was finalized in the input box. Please review before sending.';
        } else if (finalReason === 'silence') {
            statusEl.textContent = 'Voice input stopped after silence. You can edit before sending.';
        } else if (finalReason === 'visibility') {
            statusEl.textContent = 'Voice input stopped because the page was hidden.';
        } else if (finalReason === 'error') {
            statusEl.textContent = 'Voice input stopped. You can type or try the microphone again.';
        } else {
            statusEl.textContent = 'Voice input stopped. You can edit before sending.';
        }
    }

    function stopVoiceInput(reason) {
        voiceStopReason = reason || 'manual';
        if (!recognition) {
            finishVoiceInput(voiceStopReason);
            return;
        }
        try {
            const stoppingRecognition = recognition;
            recognition.stop();
            window.setTimeout(() => {
                if (recognition === stoppingRecognition) finishVoiceInput(voiceStopReason);
            }, 800);
        } catch (error) {
            try { recognition.abort(); } catch (abortError) {}
            finishVoiceInput(voiceStopReason);
        }
    }

    function startVoiceInput() {
        const Recognition = speechRecognitionClass();
        if (!micBtn) return;
        if (voiceInputActive) {
            stopVoiceInput('manual');
            return;
        }
        if (!Recognition) {
            statusEl.textContent = 'Voice input is not supported in this browser. Chrome or Edge usually works best.';
            return;
        }
        if (!isSecureEnoughForMicrophone()) {
            statusEl.textContent = 'Microphone permission requires HTTPS or localhost.';
            return;
        }
        if (sending || ending) return;

        window.WellHabitAudio?.cancelSpeech?.();
        voiceStopReason = 'manual';
        voiceFinalText = inputEl.value.trim();
        lastVoiceActivityAt = Date.now();
        voiceHeard = false;

        try {
            recognition = new Recognition();
            recognition.lang = 'en-US';
            recognition.interimResults = true;
            recognition.continuous = true;
            recognition.maxAlternatives = 1;

            recognition.onstart = () => {
                voiceInputActive = true;
                dispatchVoiceInputState(true, 'start');
                setMicActive(true);
                statusEl.textContent = 'Listening... speak now. It will stop after 2 seconds of silence or 60 seconds max.';
            };

            recognition.onresult = (event) => {
                let interimText = '';
                for (let index = event.resultIndex; index < event.results.length; index += 1) {
                    const result = event.results[index];
                    const transcript = (result?.[0]?.transcript || '').trim();
                    if (!transcript) continue;
                    lastVoiceActivityAt = Date.now();
                    voiceHeard = true;
                    if (result.isFinal) {
                        voiceFinalText = `${voiceFinalText} ${transcript}`.trim();
                    } else {
                        interimText = `${interimText} ${transcript}`.trim();
                    }
                }
                inputEl.value = `${voiceFinalText}${interimText ? ` ${interimText}` : ''}`.trim();
                inputEl.dispatchEvent(new Event('input', { bubbles: true }));
            };

            recognition.onerror = (event) => {
                const error = event?.error || 'error';
                if (error === 'not-allowed' || error === 'service-not-allowed') {
                    statusEl.textContent = 'Microphone permission was blocked or denied.';
                } else if (error === 'no-speech') {
                    statusEl.textContent = 'No speech was detected. You can try again.';
                } else {
                    statusEl.textContent = `Voice input error: ${error}.`;
                }
                voiceStopReason = 'error';
                try { recognition.abort(); } catch (abortError) {}
                finishVoiceInput('error');
            };

            recognition.onend = () => {
                finishVoiceInput(voiceStopReason);
            };

            recognition.start();

            voiceWarningTimer = window.setTimeout(() => {
                if (voiceInputActive) statusEl.textContent = 'Voice input will stop in 10 seconds. Keep speaking, then review before sending.';
            }, 50000);
            maxVoiceTimer = window.setTimeout(() => stopVoiceInput('max_duration'), 60000);
            silenceTimer = window.setInterval(() => {
                if (voiceInputActive && voiceHeard && Date.now() - lastVoiceActivityAt >= 2000) stopVoiceInput('silence');
            }, 300);
        } catch (error) {
            statusEl.textContent = 'Voice input could not start.';
            finishVoiceInput('error');
        }
    }

    function setBusy(isBusy) {
        sending = isBusy;
        sendBtn.disabled = isBusy;
        endBtn.disabled = isBusy;
        if (micBtn) micBtn.disabled = isBusy || ending;
        sendBtn.style.opacity = isBusy ? '0.75' : '1';
        endBtn.style.opacity = isBusy ? '0.75' : '1';
    }

    function timeLabel() {
        return new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    function appendMessageRow(item) {
        const row = document.createElement('div');
        row.className = `care-message-row ${item.role === 'assistant' ? 'assistant' : 'user'}`;

        const avatar = document.createElement('div');
        avatar.className = `care-message-avatar ${item.role === 'assistant' ? 'assistant' : 'user'}`;
        avatar.textContent = item.role === 'assistant' ? 'AI' : 'You';

        const bubble = document.createElement('article');
        bubble.className = `care-bubble ${item.role === 'assistant' ? 'assistant' : 'user'}`;

        const meta = document.createElement('div');
        meta.className = 'care-bubble-meta';
        meta.textContent = `${item.role === 'assistant' ? 'Care AI' : 'You'} · ${item.time || timeLabel()}`;

        const body = document.createElement('p');
        body.textContent = item.content;

        bubble.appendChild(meta);
        bubble.appendChild(body);

        if (item.risk_level && item.role === 'assistant') {
            const badge = document.createElement('span');
            badge.className = `care-risk-pill ${item.risk_level}`;
            badge.textContent = item.risk_level === 'high'
                ? 'Extra support suggested'
                : (item.risk_level === 'medium' ? 'Grounding support' : 'Gentle support');
            bubble.appendChild(badge);
        }

        row.appendChild(avatar);
        row.appendChild(bubble);
        messagesEl.appendChild(row);
    }

    function appendTypingRow() {
        const row = document.createElement('div');
        row.className = 'care-message-row assistant care-typing-row';

        const avatar = document.createElement('div');
        avatar.className = 'care-message-avatar assistant';
        avatar.textContent = 'AI';

        const bubble = document.createElement('article');
        bubble.className = 'care-bubble assistant care-typing-bubble';

        const meta = document.createElement('div');
        meta.className = 'care-bubble-meta';
        meta.textContent = 'Care AI · typing';

        const dots = document.createElement('div');
        dots.className = 'care-typing-dots';
        dots.innerHTML = '<span></span><span></span><span></span>';

        bubble.appendChild(meta);
        bubble.appendChild(dots);
        row.appendChild(avatar);
        row.appendChild(bubble);
        messagesEl.appendChild(row);
    }

    function renderMessages() {
        messagesEl.innerHTML = '';
        messages.forEach(appendMessageRow);
        if (typingVisible) {
            appendTypingRow();
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function setTypingVisible(visible) {
        typingVisible = visible;
        renderMessages();
    }

    function pushMessage(role, content, riskLevel) {
        messages.push({ role, content, risk_level: riskLevel || null, time: timeLabel() });
        saveState();
        renderMessages();
    }

    function splitAssistantReply(text) {
        const normalized = (text || '').replace(/\r/g, '').trim();
        if (!normalized) return [];
        const linePieces = normalized
            .split(/\n+/)
            .map((part) => part.trim())
            .filter(Boolean);
        const pieces = [];
        linePieces.forEach((line) => {
            const sentenceParts = line
                .split(/(?<=[.!?。！？])\s+/)
                .map((part) => part.trim())
                .filter(Boolean);
            if (sentenceParts.length) {
                pieces.push(...sentenceParts);
            } else {
                pieces.push(line);
            }
        });
        const merged = [];
        pieces.forEach((piece) => {
            if (!merged.length) {
                merged.push(piece);
                return;
            }
            if (piece.length < 22) {
                merged[merged.length - 1] = `${merged[merged.length - 1]} ${piece}`.trim();
            } else {
                merged.push(piece);
            }
        });
        return merged.slice(0, 6);
    }

    function wait(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function playAssistantReply(text, riskLevel) {
        const chunks = splitAssistantReply(text);
        if (!chunks.length) {
            pushMessage('assistant', 'I\'m here with you.', riskLevel || 'low');
            return;
        }
        for (let index = 0; index < chunks.length; index += 1) {
            pushMessage('assistant', chunks[index], index === chunks.length - 1 ? (riskLevel || 'low') : null);
            if (index < chunks.length - 1) {
                await wait(380);
            }
        }
        window.WellHabitAudio?.speak?.(text);
    }

    async function sendMessage(content) {
        if (!content || sending || ending) return;
        if (voiceInputActive) stopVoiceInput('send');
        pushMessage('user', content, null);
        setBusy(true);
        setTypingVisible(true);
        statusEl.textContent = 'Care AI is typing...';

        try {
            const response = await fetch(config.messageUrl, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders ? window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }) : { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: config.sessionId, messages, ...browserSupportContext() })
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(body.message || 'Reply failed.');
            }
            setTypingVisible(false);
            await playAssistantReply(body.assistant_message || 'I\'m here with you.', body.risk_level || 'low');

            if (body.quick_action && body.quick_action.prompt) {
                if (body.quick_action.type === 'eye_exercise' && window.WellHabitOpenEyeExercisePrompt) {
                    window.WellHabitOpenEyeExercisePrompt(body.quick_action.prompt, { forceOverlay: true });
                    statusEl.textContent = 'Opened the eye exercise prompt for you.';
                } else if (body.quick_action.type === 'hydration' && window.WellHabitHydrationOpenPrompt) {
                    window.WellHabitHydrationOpenPrompt(body.quick_action.prompt);
                    statusEl.textContent = 'Opened the water reminder for you.';
                } else {
                    statusEl.textContent = body.risk_level === 'high'
                        ? 'The reply suggested extra real-world support.'
                        : 'The chat will update your scores after it ends.';
                }
            } else {
                statusEl.textContent = body.risk_level === 'high'
                    ? 'The reply suggested extra real-world support.'
                    : 'The chat will update your scores after it ends.';
            }
        } catch (error) {
            setTypingVisible(false);
            pushMessage('assistant', 'I hit a problem replying just now, but I\'m still here. Try sending that again in a moment.', 'low');
            statusEl.textContent = 'Reply failed once. You can try again.';
        } finally {
            setBusy(false);
        }
    }

    async function endSession(options) {
        const useBeacon = Boolean(options && options.useBeacon);
        const redirectAfter = Boolean(options && options.redirectAfter);
        if (ending) return;
        ending = true;
        if (voiceInputActive) stopVoiceInput('end_session');
        window.WellHabitAudio?.cancelSpeech?.();

        const payload = JSON.stringify({
            session_id: config.sessionId,
            messages,
            ...browserSupportContext()
        });

        try {
            const response = await fetch(config.endUrl, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders ? window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }) : { 'Content-Type': 'application/json' },
                body: payload,
                keepalive: true
            });
            await response.json().catch(() => ({}));
        } catch (error) {
            // ignore ending failures here
        } finally {
            clearAllCareState();
            if (redirectAfter) {
                window.location.href = config.historyUrl;
            }
        }
    }

    micBtn?.addEventListener('click', startVoiceInput);

    formEl.addEventListener('submit', async (event) => {
        event.preventDefault();
        const content = inputEl.value.trim();
        if (!content) return;
        inputEl.value = '';
        await sendMessage(content);
        inputEl.focus();
    });

    inputEl.addEventListener('keydown', async (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            const content = inputEl.value.trim();
            if (!content) return;
            inputEl.value = '';
            await sendMessage(content);
            inputEl.focus();
        }
    });

    endBtn.addEventListener('click', async () => {
        statusEl.textContent = 'Ending chat and saving a short summary...';
        await endSession({ redirectAfter: true });
    });

    quickBtns.forEach((btn) => {
        btn.addEventListener('click', () => {
            inputEl.value = btn.dataset.prompt || '';
            inputEl.focus();
        });
    });

    window.addEventListener('pagehide', () => {
        if (voiceInputActive) stopVoiceInput('pagehide');
        window.WellHabitAudio?.cancelSpeech?.();
        if (!ending) {
            endSession({ useBeacon: true });
        }
    });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') {
            if (voiceInputActive) stopVoiceInput('visibility');
            window.WellHabitAudio?.cancelSpeech?.();
        }
    });

    const stored = loadState();
    window.setTimeout(pruneOldStateKeys, 0);
    const serverHistory = Array.isArray(config.historyMessages)
        ? config.historyMessages.filter((item) => item && item.role && item.content)
        : [];
    if (stored && stored.length) {
        messages = stored;
        renderMessages();
    } else if (serverHistory.length) {
        messages = serverHistory.map((item) => ({
            role: item.role === 'user' ? 'user' : 'assistant',
            content: item.content,
            risk_level: item.risk_level || null,
            time: item.time || timeLabel(),
        }));
        saveState();
        renderMessages();
        statusEl.textContent = 'Continuing your Care AI chat on Home.';
    } else {
        messages = [];
        pushMessage('assistant', config.introMessage || 'I\'m here with you.', 'low');
        statusEl.textContent = 'Start anywhere. You can talk about tiredness, anxiety, happiness, or stress.';
    }
})();
