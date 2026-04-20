(function () {
    const config = window.WELLHABIT_CARE || {};
    const messagesEl = document.getElementById('care-messages');
    const formEl = document.getElementById('care-form');
    const inputEl = document.getElementById('care-input');
    const sendBtn = document.getElementById('care-send-btn');
    const endBtn = document.getElementById('care-end-btn');
    const statusEl = document.getElementById('care-status-line');
    const quickBtns = Array.from(document.querySelectorAll('.care-quick-btn'));

    if (!messagesEl || !formEl || !inputEl || !sendBtn || !endBtn || !config.sessionId) return;

    const STORAGE_KEY = `wellhabitCareChat:${config.sessionId}`;
    let messages = [];
    let sending = false;
    let ending = false;
    let typingVisible = false;

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

    function setBusy(isBusy) {
        sending = isBusy;
        sendBtn.disabled = isBusy;
        endBtn.disabled = isBusy;
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
    }

    async function sendMessage(content) {
        if (!content || sending || ending) return;
        pushMessage('user', content, null);
        setBusy(true);
        setTypingVisible(true);
        statusEl.textContent = 'Care AI is typing...';

        try {
            const response = await fetch(config.messageUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: config.sessionId, messages })
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(body.message || 'Reply failed.');
            }
            setTypingVisible(false);
            await playAssistantReply(body.assistant_message || 'I\'m here with you.', body.risk_level || 'low');
            statusEl.textContent = body.risk_level === 'high'
                ? 'The reply suggested extra real-world support.'
                : 'The chat will update your scores after it ends.';
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
            messages
        });

        if (useBeacon && navigator.sendBeacon) {
            const blob = new Blob([payload], { type: 'application/json' });
            navigator.sendBeacon(config.endUrl, blob);
            clearState();
            return;
        }

        try {
            const response = await fetch(config.endUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
                keepalive: true
            });
            await response.json().catch(() => ({}));
        } catch (error) {
            // ignore ending failures here
        } finally {
            clearState();
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

    const stored = loadState();
    if (stored && stored.length) {
        messages = stored;
        renderMessages();
    } else {
        messages = [];
        pushMessage('assistant', config.introMessage || 'I\'m here with you.', 'low');
        statusEl.textContent = 'Start anywhere. You can talk about tiredness, anxiety, happiness, or stress.';
    }
})();
