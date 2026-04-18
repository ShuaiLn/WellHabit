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

    if (!overlay || !titleEl || !messageEl || !beverageEl || !amountEl || !customWrap || !customEl) return;

    let activePrompt = null;

    function toggleCustomInput() {
        const show = beverageEl.value === 'other';
        customWrap.hidden = !show;
        customEl.required = show;
        if (!show) {
            customEl.value = '';
        }
    }

    function setDefaultAmount(promptType) {
        if (amountEl.value.trim()) return;
        amountEl.value = promptType === 'morning' ? 'a glass' : 'a glass';
    }

    function openPrompt(prompt, fallbackType) {
        activePrompt = prompt || {
            id: null,
            prompt_type: fallbackType || 'morning',
            message: 'Drink a glass of water to begin the day.',
            beverage: 'water'
        };

        const isMorning = activePrompt.prompt_type === 'morning';
        eyebrowEl.textContent = isMorning ? 'Morning Boost' : 'Hydration Reminder';
        titleEl.textContent = isMorning
            ? 'Drink a glass of water to begin the day.'
            : 'Better to drink a glass of water.';
        messageEl.textContent = activePrompt.message || 'Choose what you want to drink, type an amount, then tell WellHabit if you finished it.';
        beverageEl.value = ['water', 'milk', 'coke', 'other'].includes(activePrompt.beverage) ? activePrompt.beverage : 'water';
        amountEl.value = '';
        customEl.value = '';
        toggleCustomInput();
        setDefaultAmount(activePrompt.prompt_type);
        overlay.hidden = false;
    }

    function closePrompt() {
        overlay.hidden = true;
    }

    async function sendResponse(status) {
        if (!activePrompt) return;

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
            response_status: status,
        };

        try {
            const response = await fetch('/hydration/respond', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            const body = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (body.message) {
                    alert(body.message);
                }
                return;
            }

            closePrompt();
            setTimeout(() => window.location.reload(), 200);
        } catch (error) {
            console.error('Hydration response failed', error);
            alert('Saving the hydration response failed. Please try again.');
        }
    }

    beverageEl.addEventListener('change', toggleCustomInput);
    finishedBtn.addEventListener('click', () => sendResponse('done'));
    notYetBtn.addEventListener('click', () => sendResponse('not_yet'));
    skipBtn.addEventListener('click', () => sendResponse('skipped'));

    const due = config.due;
    const upcoming = config.upcoming;
    const currentHour = new Date().getHours();
    const shouldShowMorning = !config.morningPromptExists && currentHour >= 5 && currentHour < 12;

    if (due) {
        openPrompt(due, due.prompt_type);
        return;
    }

    if (shouldShowMorning) {
        openPrompt(null, 'morning');
    }

    if (upcoming && upcoming.due_at_iso) {
        const delay = new Date(upcoming.due_at_iso).getTime() - Date.now();
        if (delay > 0 && delay < 12 * 60 * 60 * 1000) {
            setTimeout(() => openPrompt(upcoming, upcoming.prompt_type), delay);
        }
    }
})();
