(function () {
    const widget = document.getElementById('global-timer-widget');
    const widgetMode = document.getElementById('global-timer-mode');
    const widgetTime = document.getElementById('global-timer-time');
    const widgetActivity = document.getElementById('global-timer-activity');
    const widgetStatus = document.getElementById('global-timer-status');
    const widgetMiniToggle = document.getElementById('global-timer-mini-toggle');
    const widgetMiniFace = document.getElementById('global-timer-mini-face');
    const widgetMiniTime = document.getElementById('global-timer-mini-time');
    const widgetMiniAvatar = document.getElementById('global-timer-mini-avatar');
    const widgetAvatar = document.getElementById('global-timer-avatar');
    const widgetFull = document.getElementById('global-timer-full');
    const widgetDragZone = document.getElementById('global-timer-drag-zone');
    const WIDGET_UI_KEY = 'wellhabitPomodoroWidgetUI';

    if (!widget || !window.WellHabitTimer) return;

    function readWidgetUi() {
        try {
            const raw = localStorage.getItem(WIDGET_UI_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            return {
                minimized: Boolean(parsed && parsed.minimized),
                left: Number.isFinite(Number(parsed && parsed.left)) ? Number(parsed.left) : null,
                top: Number.isFinite(Number(parsed && parsed.top)) ? Number(parsed.top) : null,
            };
        } catch (error) {
            return { minimized: false, left: null, top: null };
        }
    }

    function writeWidgetUi(nextUi) {
        const current = readWidgetUi();
        const merged = Object.assign({}, current, nextUi || {});
        localStorage.setItem(WIDGET_UI_KEY, JSON.stringify(merged));
        return merged;
    }

    function applyWidgetUi() {
        const ui = readWidgetUi();
        widget.classList.toggle('is-minimized', ui.minimized);
        if (widgetMiniToggle) {
            widgetMiniToggle.setAttribute('aria-expanded', ui.minimized ? 'false' : 'true');
            widgetMiniToggle.title = ui.minimized ? 'Expand timer' : 'Minimize timer';
        }
        if (widgetMiniFace) widgetMiniFace.hidden = !ui.minimized;
        if (widgetFull) widgetFull.hidden = ui.minimized;
        if (ui.left !== null && ui.top !== null) {
            widget.style.left = `${ui.left}px`;
            widget.style.top = `${ui.top}px`;
            widget.style.right = 'auto';
            widget.style.bottom = 'auto';
        }
    }

    function clampWidgetPosition(left, top) {
        const width = widget ? widget.offsetWidth : 280;
        const height = widget ? widget.offsetHeight : 100;
        const maxLeft = Math.max(8, window.innerWidth - width - 8);
        const maxTop = Math.max(8, window.innerHeight - height - 8);
        return {
            left: Math.min(Math.max(8, left), maxLeft),
            top: Math.min(Math.max(8, top), maxTop),
        };
    }

    function shouldShowWidget(state) {
        const baseline = state.focusMinutes * 60;
        return state.isRunning || state.mode !== 'focus' || state.cycleNumber !== 1 || window.WellHabitTimer.getRemainingSeconds(state) !== baseline;
    }

    function renderWidget(state) {
        const remaining = window.WellHabitTimer.getRemainingSeconds(state);
        widget.hidden = !shouldShowWidget(state);
        widget.classList.toggle('is-running', state.isRunning);
        if (widgetMode) widgetMode.textContent = state.mode === 'focus' ? `Focus · Cycle ${state.cycleNumber}` : `Break · Cycle ${state.cycleNumber}`;
        if (widgetTime) widgetTime.textContent = window.WellHabitTimer.formatSeconds(remaining);
        if (widgetMiniTime) widgetMiniTime.textContent = window.WellHabitTimer.formatSeconds(remaining);
        if (widgetAvatar) widgetAvatar.textContent = window.currentAvatarEmoji || '🙂';
        if (widgetMiniAvatar) widgetMiniAvatar.textContent = window.currentAvatarEmoji || '🙂';
        if (widgetActivity) widgetActivity.textContent = state.activityLabel || 'work';
        if (widgetStatus) widgetStatus.textContent = state.isRunning ? 'Running' : 'Paused';
        applyWidgetUi();
    }

    let dragState = null;
    function beginDrag(event) {
        const point = event.touches ? event.touches[0] : event;
        if (!point) return;
        const rect = widget.getBoundingClientRect();
        dragState = { offsetX: point.clientX - rect.left, offsetY: point.clientY - rect.top };
        widget.classList.add('is-dragging');
    }
    function moveDrag(event) {
        if (!dragState) return;
        const point = event.touches ? event.touches[0] : event;
        if (!point) return;
        const position = clampWidgetPosition(point.clientX - dragState.offsetX, point.clientY - dragState.offsetY);
        widget.style.left = `${position.left}px`;
        widget.style.top = `${position.top}px`;
        widget.style.right = 'auto';
        widget.style.bottom = 'auto';
        if (!event.touches) event.preventDefault();
    }
    function endDrag() {
        if (!dragState) return;
        dragState = null;
        widget.classList.remove('is-dragging');
        const rect = widget.getBoundingClientRect();
        const position = clampWidgetPosition(rect.left, rect.top);
        writeWidgetUi(position);
        applyWidgetUi();
    }

    widgetMiniToggle?.addEventListener('click', () => {
        const current = readWidgetUi();
        writeWidgetUi({ minimized: !current.minimized });
        applyWidgetUi();
    });
    widgetDragZone?.addEventListener('mousedown', beginDrag);
    widgetMiniFace?.addEventListener('mousedown', beginDrag);
    window.addEventListener('mousemove', moveDrag);
    window.addEventListener('mouseup', endDrag);
    widgetDragZone?.addEventListener('touchstart', beginDrag, { passive: true });
    widgetMiniFace?.addEventListener('touchstart', beginDrag, { passive: true });
    window.addEventListener('touchmove', moveDrag, { passive: false });
    window.addEventListener('touchend', endDrag);
    window.addEventListener('resize', applyWidgetUi);

    applyWidgetUi();
    window.WellHabitTimer.subscribe(renderWidget);
    renderWidget(window.WellHabitTimer.getState());
})();
