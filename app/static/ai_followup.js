(function () {
    const bootstrap = window.WELLHABIT_BOOTSTRAP || {};

    window.WellHabitOpenAiSuggestionFollowup = (function () {
        const overlay = document.getElementById('ai-suggestion-followup-overlay');
        const taskEl = document.getElementById('ai-suggestion-followup-task');
        const questionEl = document.getElementById('ai-suggestion-followup-question');
        const ratingGrid = document.getElementById('ai-suggestion-rating-grid');
        const laterBtn = document.getElementById('ai-suggestion-followup-later-btn');
        let activePrompt = null;

        function hide() {
            if (overlay) overlay.hidden = true;
            activePrompt = null;
        }

        async function saveRating(rating) {
            if (!activePrompt || !activePrompt.task_id) return;
            const response = await fetch(`/tasks/${activePrompt.task_id}/ai-followup`, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ rating }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.message || 'Saving the AI suggestion follow-up failed.');
            hide();
            if (body.wellness_feedback && window.WellHabitShowWellnessFeedback) {
                window.WellHabitShowWellnessFeedback(body.wellness_feedback);
            }
        }

        function renderButtons() {
            if (!ratingGrid) return;
            ratingGrid.innerHTML = '';
            for (let value = 1; value <= 10; value += 1) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-secondary ai-rating-btn';
                button.textContent = String(value);
                button.addEventListener('click', () => {
                    saveRating(value).catch((error) => window.alert(error.message || 'Saving the AI suggestion follow-up failed.'));
                });
                ratingGrid.appendChild(button);
            }
        }

        function show(prompt) {
            if (!overlay || !prompt) return;
            activePrompt = prompt;
            if (taskEl) taskEl.textContent = `Completed task: ${prompt.task_title || 'AI suggestion'}`;
            if (questionEl) questionEl.textContent = prompt.question || 'After doing this AI suggestion, how much better do you feel out of 10 regarding the negativity detected earlier?';
            renderButtons();
            overlay.hidden = false;
        }

        laterBtn?.addEventListener('click', hide);
        overlay?.addEventListener('click', (event) => {
            if (event.target === overlay) hide();
        });

        if (bootstrap.pendingAiSuggestionFollowup) show(bootstrap.pendingAiSuggestionFollowup);
        return show;
    })();

    window.WellHabitOpenAiSuggestionAdded = (function () {
        const overlay = document.getElementById('ai-suggestion-added-overlay');
        const titleEl = document.getElementById('ai-suggestion-added-title');
        const detailEl = document.getElementById('ai-suggestion-added-detail');
        const okBtn = document.getElementById('ai-suggestion-added-ok-btn');

        function hide() {
            if (overlay) overlay.hidden = true;
        }

        function show(prompt) {
            if (!overlay || !prompt) return;
            if (titleEl) titleEl.textContent = prompt.message || `AI suggestion added: ${prompt.task_title || 'New task'}`;
            if (detailEl) detailEl.textContent = prompt.detail || `Added to today's todo list · source: ${prompt.source_label || 'ai'}`;
            overlay.hidden = false;
        }

        function showWhenReady(prompt) {
            const feedbackOverlay = document.getElementById('wellness-feedback-overlay');
            if (feedbackOverlay && !feedbackOverlay.hidden) {
                const handler = () => {
                    document.removeEventListener('wellhabit:wellness-feedback-hidden', handler);
                    show(prompt);
                };
                document.addEventListener('wellhabit:wellness-feedback-hidden', handler);
                return;
            }
            show(prompt);
        }

        okBtn?.addEventListener('click', hide);
        overlay?.addEventListener('click', (event) => {
            if (event.target === overlay) hide();
        });

        if (bootstrap.pendingAiSuggestionAdded) showWhenReady(bootstrap.pendingAiSuggestionAdded);

        return function (payload) {
            const result = showWhenReady(payload);
            window.setTimeout(() => {
                const currentOverlay = document.getElementById('ai-suggestion-added-overlay');
                if (currentOverlay) currentOverlay.hidden = true;
            }, 3000);
            return result;
        };
    })();
})();
