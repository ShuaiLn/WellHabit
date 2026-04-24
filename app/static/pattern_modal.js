(function () {
    function openPatternModal(targetId) {
        const modal = targetId ? document.getElementById(targetId) : null;
        if (!modal) return;
        modal.hidden = false;
        const firstInput = modal.querySelector('input[name="rating"], button[type="submit"]');
        if (firstInput) firstInput.focus({ preventScroll: true });
    }

    function closePatternModal(modal) {
        if (modal) modal.hidden = true;
    }

    function setMessage(container, text, category) {
        const message = container ? container.querySelector('[data-pattern-message]') : null;
        if (!message) return;
        message.hidden = false;
        message.textContent = text || '';
        message.classList.remove('success', 'warning', 'danger', 'info');
        if (category) message.classList.add(category);
    }

    function markCardsAfterResponse(patternId, data) {
        document.querySelectorAll(`[data-pattern-card-open="pattern-modal-${patternId}"]`).forEach((card) => {
            card.classList.add('is-responded');
            const actionRow = card.querySelector('.pattern-action-row');
            if (actionRow) {
                actionRow.innerHTML = `<span class="pattern-response-status">${data.message || 'Saved.'}</span>`;
            }
        });
    }

    document.addEventListener('click', (event) => {
        const openButton = event.target.closest('.pattern-open-btn');
        if (openButton) {
            event.preventDefault();
            openPatternModal(openButton.getAttribute('data-pattern-modal'));
            return;
        }

        const card = event.target.closest('[data-pattern-card-open]');
        if (card && !event.target.closest('button, a, input, select, textarea, form')) {
            openPatternModal(card.getAttribute('data-pattern-card-open'));
            return;
        }

        const closeButton = event.target.closest('[data-pattern-close]');
        if (closeButton) {
            closePatternModal(closeButton.closest('.pattern-modal-overlay'));
            return;
        }

        const overlay = event.target.classList && event.target.classList.contains('pattern-modal-overlay') ? event.target : null;
        if (overlay) closePatternModal(overlay);
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        document.querySelectorAll('.pattern-modal-overlay:not([hidden])').forEach(closePatternModal);
    });

    document.addEventListener('submit', async (event) => {
        const form = event.target.closest('.pattern-response-form');
        if (!form) return;
        event.preventDefault();

        const modal = form.closest('.pattern-modal-overlay');
        const submitButton = form.querySelector('button[type="submit"]');
        const originalText = submitButton ? submitButton.textContent : '';
        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = 'Saving...';
        }
        setMessage(modal, 'Saving pattern response...', 'info');

        try {
            const response = await fetch(form.action, {
                method: 'POST',
                headers: window.WellHabitCsrfHeaders({
                    'Accept': 'application/json',
                    'X-Requested-With': 'fetch'
                }),
                body: new FormData(form),
                credentials: 'same-origin'
            });
            const data = await response.json();
            setMessage(modal, data.message || 'Saved.', data.category || (data.ok ? 'success' : 'warning'));
            if (data.ok) {
                markCardsAfterResponse(form.getAttribute('data-pattern-id'), data);
                window.setTimeout(() => closePatternModal(modal), 650);
            }
        } catch (error) {
            setMessage(modal, 'Could not save without refreshing. Please try again.', 'warning');
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = originalText;
            }
        }
    });
})();
