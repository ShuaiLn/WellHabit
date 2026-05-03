(function () {
    const config = window.WELLHABIT_CARE || {};
    const ids = Object.assign({
        messages: 'care-messages',
        form: 'care-form',
        input: 'care-input',
        sendBtn: 'care-send-btn',
        endBtn: 'care-end-btn',
        status: 'care-status-line',
    }, config.elementIds || {});
    const rootEl = config.rootSelector ? document.querySelector(config.rootSelector) : document;
    const messagesEl = document.getElementById(ids.messages);
    const formEl = document.getElementById(ids.form);
    const inputEl = document.getElementById(ids.input);
    const sendBtn = document.getElementById(ids.sendBtn);
    const endBtn = document.getElementById(ids.endBtn);
    const statusEl = document.getElementById(ids.status);
    const quickBtns = Array.from((rootEl || document).querySelectorAll(config.quickButtonSelector || '.care-quick-btn'));

    if (!messagesEl || !formEl || !inputEl || !sendBtn || !endBtn || !config.sessionId) return;

    const STORAGE_PREFIX = 'wellhabitCareChat:';
    const STORAGE_KEY = `${STORAGE_PREFIX}${config.sessionId}`;
    let messages = [];
    let sending = false;
    let ending = false;
    let typingVisible = false;
    let typingRowEl = null;


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

    function timeLabel() {
        return new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    function createMessageRow(item) {
        const row = document.createElement('div');
        row.className = `care-message-row ${item.role === 'assistant' ? 'assistant' : 'user'}`;

        const bubble = document.createElement('article');
        bubble.className = `care-bubble ${item.role === 'assistant' ? 'assistant' : 'user'}`;

        const meta = document.createElement('div');
        meta.className = 'care-bubble-meta';
        const speaker = document.createElement('span');
        speaker.className = 'care-bubble-speaker';
        speaker.textContent = item.role === 'assistant' ? 'AI' : 'You';

        const metaText = document.createElement('span');
        metaText.textContent = `${item.role === 'assistant' ? 'Care AI' : 'You'} · ${item.time || timeLabel()}`;

        meta.appendChild(speaker);
        meta.appendChild(metaText);

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

        row.appendChild(bubble);
        return row;
    }

    function appendMessageRow(item) {
        const row = createMessageRow(item);
        if (typingRowEl && typingRowEl.parentNode === messagesEl) {
            messagesEl.insertBefore(row, typingRowEl);
        } else {
            messagesEl.appendChild(row);
        }
        return row;
    }

    function createTypingRow() {
        const row = document.createElement('div');
        row.className = 'care-message-row assistant care-typing-row';

        const bubble = document.createElement('article');
        bubble.className = 'care-bubble assistant care-typing-bubble';

        const meta = document.createElement('div');
        meta.className = 'care-bubble-meta';
        const speaker = document.createElement('span');
        speaker.className = 'care-bubble-speaker';
        speaker.textContent = 'AI';

        const metaText = document.createElement('span');
        metaText.textContent = 'Care AI · typing';

        meta.appendChild(speaker);
        meta.appendChild(metaText);

        const dots = document.createElement('div');
        dots.className = 'care-typing-dots';
        dots.innerHTML = '<span></span><span></span><span></span>';

        bubble.appendChild(meta);
        bubble.appendChild(dots);
        row.appendChild(bubble);
        return row;
    }

    function appendTypingRow() {
        if (typingRowEl && typingRowEl.parentNode === messagesEl) return typingRowEl;
        typingRowEl = createTypingRow();
        messagesEl.appendChild(typingRowEl);
        return typingRowEl;
    }

    function removeTypingRow() {
        if (typingRowEl && typingRowEl.parentNode) {
            typingRowEl.parentNode.removeChild(typingRowEl);
        }
        typingRowEl = null;
    }

    function scrollMessagesToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderMessages() {
        messagesEl.innerHTML = '';
        typingRowEl = null;
        messages.forEach(appendMessageRow);
        if (typingVisible) appendTypingRow();
        scrollMessagesToBottom();
    }

    function setTypingVisible(visible) {
        const nextVisible = Boolean(visible);
        if (typingVisible === nextVisible) return;
        typingVisible = nextVisible;
        if (typingVisible) appendTypingRow();
        else removeTypingRow();
        scrollMessagesToBottom();
    }

    function pushMessage(role, content, riskLevel) {
        messages.push({ role, content, risk_level: riskLevel || null, time: timeLabel() });
        saveState();
        appendMessageRow(messages[messages.length - 1]);
        scrollMessagesToBottom();
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

    function setBusy(isBusy) {
        sending = Boolean(isBusy);
        const disabled = sending || ending;

        sendBtn.disabled = disabled;
        inputEl.disabled = disabled;
        endBtn.disabled = sending || ending;
        quickBtns.forEach((btn) => {
            btn.disabled = disabled;
        });

        formEl.classList.toggle('is-busy', sending);
        sendBtn.textContent = sending ? 'Sending...' : 'Send';
    }

    async function playAssistantReply(text, riskLevel) {
        const chunks = splitAssistantReply(text);
        if (!chunks.length) {
            const fallback = 'I\'m here with you.';
            pushMessage('assistant', fallback, riskLevel || 'low');
            return;
        }
        for (let index = 0; index < chunks.length; index += 1) {
            pushMessage('assistant', chunks[index], index === chunks.length - 1 ? (riskLevel || 'low') : null);
            if (index < chunks.length - 1) {
                await wait(380);
            }
        }
    }


    function maybeDispatchPositiveChatText(content) {
        const text = String(content || '').toLowerCase();
        if (!text) return;
        const positiveWords = ['better', 'relaxed', 'calm', 'good', 'great', 'helped', 'thanks', 'thank you', 'less tired', 'less stressed', '舒服', '好多了', '放松', '开心', '不错'];
        if (!positiveWords.some((word) => text.includes(word))) return;
        document.dispatchEvent(new CustomEvent('wellhabit:positive-chat-text', {
            detail: { source: 'care_chat', text_hint: 'positive_language', at: Date.now() }
        }));
    }

    async function sendMessage(content) {
        if (!content || sending || ending) return;
        pushMessage('user', content, null);
        maybeDispatchPositiveChatText(content);
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
        if (!ending) {
            endSession({ useBeacon: true });
        }
    });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') {
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
        statusEl.textContent = '';
    }
})();
