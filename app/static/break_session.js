import { loadPoseLandmarker, requestCamera } from './vision_loader.js';
import { PoseMonitor } from './pose_monitor.js';

const cfg = window.WELLHABIT_BREAK || {};
const exercises = Array.isArray(cfg.exercises) ? cfg.exercises : [];
const exerciseMap = Object.fromEntries(exercises.map((item) => [item.key, item]));
const $ = (id) => document.getElementById(id);

const state = {
    phase: 'loading',
    currentExercise: cfg.defaultExerciseKey || exercises[0]?.key || 'box_breathing',
    completedExercises: [],
    sessionId: null,
    cameraReady: false,
    modelReady: false,
    detectionSkipped: false,
    exerciseElapsedMs: 0,
    exerciseLastTick: 0,
    exerciseTimer: null,
    totalRemaining: Number(cfg.suggestedDurationSec || 180),
    totalTimer: null,
    pausedForVisibility: false,
    visibilityPromptOpen: false,
    awaitingPerson: false,
    monitor: null,
    stream: null,
    lastFocusedBeforeModal: null,
};

const els = {
    fullPicker: $('full-picker'), workspace: $('break-workspace'), video: $('break-video'), canvas: $('break-canvas'), eyeIframe: $('break-eye-iframe'), orb: $('breathing-orb'), guide: $('guide-overlay'), placeholder: $('break-placeholder'), placeholderText: $('break-placeholder-text'),
    scoreBadge: $('alignment-score-badge'), statePill: $('break-state-pill'), totalTimer: $('break-total-timer'), progressFill: $('break-progress-fill'), cameraStatus: $('break-camera-status'), modelStatus: $('break-model-status'), phase: $('break-current-phase'), completed: $('break-completed-count'), title: $('current-exercise-title'), desc: $('current-exercise-desc'), type: $('current-exercise-type'), startBtn: $('exercise-start-btn'), endBtn: $('break-end-btn'), skipCameraBtn: $('break-skip-camera-btn'), modal: $('self-report-modal'), seeAll: $('break-see-all-btn'), seeAllSide: $('break-see-all-side-btn'),
    consentToast: $('pose-consent-toast'), consentOk: $('pose-consent-ok'), consentSkip: $('pose-consent-skip'),
};

function formatSeconds(sec) {
    const safe = Math.max(0, Math.round(sec || 0));
    return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`;
}
function setText(el, text) { if (el) el.textContent = text; }
function setPhase(phase, message) {
    state.phase = phase;
    setText(els.statePill, phase.replace('_', ' '));
    if (message) setText(els.guide, message);
}
function activeExercise() { return exerciseMap[state.currentExercise] || exercises[0] || {}; }
function isCameraExercise(exercise = activeExercise()) { return exercise.camera_required === 'required'; }
function canUseCamera() { return state.cameraReady && state.modelReady && state.monitor && !state.detectionSkipped; }
function exerciseClockPaused() { return state.pausedForVisibility || state.awaitingPerson || document.visibilityState !== 'visible'; }
function urlExerciseParam() {
    try { return new URLSearchParams(window.location.search).get('exercise'); }
    catch (error) { return null; }
}
function shouldAutoStartEyeReset() {
    return activeExercise().key === 'eye_reset' && urlExerciseParam() === 'eye_reset';
}

function updateExerciseUi() {
    const ex = activeExercise();
    document.querySelectorAll('[data-exercise-key]').forEach((node) => {
        const active = node.dataset.exerciseKey === ex.key;
        node.dataset.active = active ? 'true' : 'false';
        node.dataset.completed = state.completedExercises.includes(node.dataset.exerciseKey) ? 'true' : 'false';
        const needsCamera = node.dataset.cameraRequired === 'required' && state.detectionSkipped;
        node.disabled = Boolean(needsCamera);
        node.title = needsCamera ? 'Needs camera' : '';
    });
    setText(els.title, ex.title || 'Break');
    setText(els.desc, ex.long_desc || ex.short_desc || 'Follow the guide.');
    setText(els.type, `${ex.type || 'exercise'} · intensity ${ex.intensity ?? '—'}`);
    if (els.startBtn) els.startBtn.textContent = state.phase === 'exercising' ? 'Restart exercise' : 'Start exercise';
    setText(els.completed, `Done: ${state.completedExercises.length}`);
    const pct = exercises.length ? (state.completedExercises.length / exercises.length) * 100 : 0;
    if (els.progressFill) els.progressFill.style.width = `${Math.min(100, pct)}%`;
}
function showPicker(show) {
    if (els.fullPicker) els.fullPicker.hidden = !show;
    if (els.workspace) els.workspace.hidden = show;
}
function showEyeExerciseSurface(show) {
    if (!els.eyeIframe) return;
    els.eyeIframe.hidden = !show;
    if (show) {
        els.eyeIframe.src = cfg.eyeExerciseEmbedUrl || 'https://www.youtube.com/embed/iVb4vUp70zY';
    } else {
        els.eyeIframe.src = '';
    }
}
function showCameraSurface(showVideo) {
    if (showVideo) showEyeExerciseSurface(false);
    if (els.video) els.video.hidden = !showVideo;
    if (els.canvas) els.canvas.hidden = !showVideo;
    if (els.placeholder) els.placeholder.hidden = showVideo;
}
function updateStatuses() {
    setText(els.cameraStatus, `Camera: ${state.cameraReady ? 'ready' : (state.detectionSkipped ? 'off' : 'unavailable')}`);
    setText(els.modelStatus, `Pose model: ${state.modelReady ? 'ready' : (state.detectionSkipped ? 'skipped' : 'loading')}`);
}
async function postJson(url, payload) {
    const res = await fetch(url, { method: 'POST', headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(payload || {}) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.message || 'Request failed');
    return body;
}
async function startBackendSession() {
    if (state.sessionId) return;
    let snapshot = {};
    try {
        const raw = sessionStorage.getItem('fatigueSignalSnapshot');
        sessionStorage.removeItem('fatigueSignalSnapshot');
        if ((cfg.reason || 'manual') === 'fatigue' && raw) snapshot = JSON.parse(raw || '{}');
    } catch (error) { snapshot = {}; }
    const body = await postJson(cfg.startUrl, { trigger: cfg.reason || 'manual', fatigue_signal_snapshot: snapshot });
    state.sessionId = body.session_id;
}
function startTotalTimer() {
    if (state.totalTimer) return;
    state.totalTimer = setInterval(() => {
        if (state.pausedForVisibility || document.visibilityState !== 'visible') return;
        state.totalRemaining -= 1;
        setText(els.totalTimer, formatSeconds(state.totalRemaining));
        if (state.totalRemaining <= 0) {
            clearInterval(state.totalTimer); state.totalTimer = null;
            if (confirm('Your suggested break is done. Continue anyway?')) {
                state.totalRemaining = 60;
                startTotalTimer();
            } else {
                showSelfReport();
            }
        }
    }, 1000);
}
function phaseForExercise(ex, elapsedSec) {
    if (!ex.phases || !ex.phases.length) return { label: 'Rest', cycleRatio: 0.5 };
    const total = ex.phases.reduce((sum, item) => sum + Number(item.seconds || 0), 0) || 1;
    let t = elapsedSec % total;
    for (const ph of ex.phases) {
        const seconds = Number(ph.seconds || 0);
        if (t < seconds) return { label: ph.label, cycleRatio: seconds ? t / seconds : 0 };
        t -= seconds;
    }
    return { label: ex.phases[0].label, cycleRatio: 0 };
}
function updateOrb(ex, elapsedSec) {
    if (!els.orb) return;
    const shouldShow = ex.key === 'box_breathing' || ex.key === 'quiet_timer' || ex.type === 'breathing';
    els.orb.hidden = !shouldShow;
    if (!shouldShow) return;
    const phase = phaseForExercise(ex, elapsedSec);
    let scale = 1;
    if (/inhale/i.test(phase.label)) scale = 1 + 0.55 * phase.cycleRatio;
    else if (/exhale/i.test(phase.label)) scale = 1.55 - 0.55 * phase.cycleRatio;
    else scale = /hold/i.test(phase.label) && elapsedSec % 16 < 8 ? 1.55 : 1;
    els.orb.style.transform = `translate(-50%, -50%) scale(${scale})`;
}
function completeCurrentExercise() {
    const ex = activeExercise();
    if (!state.completedExercises.includes(ex.key)) state.completedExercises.push(ex.key);
    clearInterval(state.exerciseTimer); state.exerciseTimer = null;
    state.awaitingPerson = false;
    if (els.orb) els.orb.hidden = true;
    showEyeExerciseSurface(false);
    if (els.scoreBadge) els.scoreBadge.hidden = true;
    setPhase('completed', `${ex.title} complete. Choose another exercise or end the break.`);
    updateExerciseUi();
}
function startExercise() {
    const ex = activeExercise();
    clearInterval(state.exerciseTimer);
    state.awaitingPerson = false;
    if (isCameraExercise(ex) && !canUseCamera()) {
        setPhase('ready', 'Camera detection is unavailable. Choose Box Breathing or Just Close Your Eyes, or enable camera later.');
        return;
    }
    showPicker(false);
    if (ex.key === 'eye_reset') {
        state.monitor?.pause();
        showCameraSurface(false);
        showEyeExerciseSurface(true);
        if (els.placeholder) els.placeholder.hidden = true;
    } else if (canUseCamera() && ex.camera_required !== 'none') {
        showEyeExerciseSurface(false);
        showCameraSurface(true);
        state.monitor.setExercise(ex.key, ex);
        state.monitor.resume();
    } else {
        state.monitor?.pause();
        showEyeExerciseSurface(false);
        showCameraSurface(false);
        setText(els.placeholderText, 'Camera off · visual guide only');
    }
    state.exerciseElapsedMs = 0;
    state.exerciseLastTick = performance.now();
    setPhase('exercising', `Start ${ex.title}. Move gently.`);
    if (els.scoreBadge) els.scoreBadge.hidden = ex.key !== 'shoulder_opener';
    state.exerciseTimer = setInterval(() => {
        const now = performance.now();
        if (exerciseClockPaused()) {
            state.exerciseLastTick = now;
            return;
        }
        state.exerciseElapsedMs += now - state.exerciseLastTick;
        state.exerciseLastTick = now;
        const elapsed = state.exerciseElapsedMs / 1000;
        updateOrb(ex, elapsed);
        const phase = phaseForExercise(ex, elapsed);
        setText(els.phase, `Phase: ${phase.label}`);
        if (ex.key === 'eye_reset') setText(els.guide, phase.label || 'Rest your eyes');
        else if (!canUseCamera() || ex.camera_required === 'none') setText(els.guide, phase.label || 'Rest quietly');
        if (elapsed >= Number(ex.duration_sec || 30)) completeCurrentExercise();
    }, 250);
}
function focusableInModal() {
    if (!els.modal || els.modal.hidden) return [];
    return Array.from(els.modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter((node) => !node.disabled && node.offsetParent !== null);
}
function showSelfReport() {
    clearInterval(state.exerciseTimer); state.exerciseTimer = null;
    state.monitor?.pause();
    showEyeExerciseSurface(false);
    setPhase('self_report', 'Choose how you feel now.');
    state.lastFocusedBeforeModal = document.activeElement;
    if (els.modal) els.modal.hidden = false;
    document.body.classList.add('modal-open');
    focusableInModal()[0]?.focus();
}
async function finishBreak(report) {
    try {
        const body = await postJson(cfg.finishUrl, { session_id: state.sessionId, exercises_done: state.completedExercises, self_report: report });
        try { sessionStorage.setItem('fatigueCooldownUntil', body.cooldown_until || ''); } catch (error) {}
        window.location.href = `${cfg.dashboardUrl || '/dashboard'}?from=break&felt=${encodeURIComponent(report)}`;
    } catch (error) {
        setText(els.guide, error.message || 'Could not save break. Please try again.');
    }
}
function ensurePoseConsent() {
    let saved = null;
    try { saved = localStorage.getItem('poseDetectionConsent'); } catch (error) { saved = null; }
    if (saved === 'ok') return Promise.resolve(true);
    if (saved === 'skip') return Promise.resolve(false);
    if (!els.consentToast) return Promise.resolve(true);
    els.consentToast.hidden = false;
    els.consentOk?.focus();
    return new Promise((resolve) => {
        const done = (allowed) => {
            try { localStorage.setItem('poseDetectionConsent', allowed ? 'ok' : 'skip'); } catch (error) {}
            els.consentToast.hidden = true;
            resolve(allowed);
        };
        els.consentOk?.addEventListener('click', () => done(true), { once: true });
        els.consentSkip?.addEventListener('click', () => done(false), { once: true });
    });
}
async function initCameraAndModel() {
    const consent = await ensurePoseConsent();
    if (!consent) {
        state.detectionSkipped = true;
        updateStatuses(); updateExerciseUi(); showCameraSurface(false);
        setText(els.placeholderText, 'Camera off · visual guide only');
        return;
    }
    try {
        state.stream = await requestCamera();
        if (!state.stream) throw new Error('No stream');
        els.video.srcObject = state.stream;
        await els.video.play().catch(() => {});
        state.cameraReady = true; updateStatuses();
    } catch (error) {
        state.detectionSkipped = true; updateStatuses();
        setText(els.placeholderText, 'Camera off · visual guide only');
        return;
    }
    try {
        const landmarker = await loadPoseLandmarker((p) => setText(els.modelStatus, `Pose model: ${p}%`));
        state.modelReady = true; updateStatuses();
        state.monitor = new PoseMonitor();
        state.monitor.init(els.video, els.canvas, landmarker);
        setPhase('baseline', 'Getting ready, stay still for 5 seconds.');
        showCameraSurface(true);
        const baseline = await state.monitor.captureBaseline(5000);
        if (baseline && baseline.quality === 'fallback') {
            setText(els.guide, 'Could not get a clean baseline. You can still use breathing, or stand/sit centered and restart a posture exercise.');
        }
        state.monitor.onEvaluation((ev) => {
            if (state.phase !== 'exercising') return;
            if (ev?.paused) {
                state.awaitingPerson = true;
                setText(els.phase, `Phase: ${ev.phase || 'Paused'}`);
                setText(els.guide, ev.hints?.[0] || 'Come back to the camera');
                return;
            }
            if (state.awaitingPerson) {
                state.awaitingPerson = false;
                state.exerciseLastTick = performance.now();
            }
            setText(els.phase, `Phase: ${ev.phase || '—'}`);
            setText(els.guide, (ev.hints && ev.hints.length) ? ev.hints[0] : (ev.phase || 'Good. Keep it gentle.'));
            if (els.scoreBadge && activeExercise().key === 'shoulder_opener') {
                els.scoreBadge.hidden = false;
                els.scoreBadge.textContent = `Score ${Math.round(ev.score || 0)}`;
            }
        });
    } catch (error) {
        state.modelReady = false; state.detectionSkipped = true; updateStatuses(); showCameraSurface(false);
        setText(els.placeholderText, 'Pose detection unavailable. Breathing exercises still work.');
    }
}
function handleVisibilityChange() {
    if (document.visibilityState !== 'visible') {
        state.pausedForVisibility = true;
        state.exerciseLastTick = performance.now();
        state.monitor?.pause();
        setText(els.guide, 'Paused while you were away.');
        return;
    }
    if (!state.pausedForVisibility || state.visibilityPromptOpen) return;
    state.visibilityPromptOpen = true;
    const shouldContinue = confirm('Paused while you were away. Continue?');
    state.visibilityPromptOpen = false;
    if (document.visibilityState !== 'visible') return;
    if (shouldContinue) {
        state.pausedForVisibility = false;
        state.exerciseLastTick = performance.now();
        if (canUseCamera() && activeExercise().camera_required !== 'none') state.monitor?.resume();
        setText(els.guide, 'Welcome back. Continue gently.');
    } else {
        state.pausedForVisibility = false;
        showSelfReport();
    }
}
function bindEvents() {
    document.querySelectorAll('[data-exercise-key]').forEach((node) => {
        node.addEventListener('click', () => { if (node.disabled) return; state.currentExercise = node.dataset.exerciseKey; showEyeExerciseSurface(false); updateExerciseUi(); showPicker(false); setPhase('ready', 'Press Start exercise when ready.'); });
        node.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); node.click(); }
        });
    });
    els.startBtn?.addEventListener('click', startExercise);
    els.endBtn?.addEventListener('click', showSelfReport);
    els.skipCameraBtn?.addEventListener('click', () => {
        state.detectionSkipped = true;
        state.awaitingPerson = false;
        state.monitor?.pause();
        if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
        state.stream = null;
        showCameraSurface(false);
        updateStatuses(); updateExerciseUi();
        setText(els.placeholderText, 'Camera off · visual guide only');
    });
    els.seeAll?.addEventListener('click', () => showPicker(true));
    els.seeAllSide?.addEventListener('click', () => showPicker(true));
    document.querySelectorAll('[data-self-report]').forEach((btn) => btn.addEventListener('click', () => finishBreak(btn.dataset.selfReport)));
    document.addEventListener('visibilitychange', handleVisibilityChange);
    document.addEventListener('keydown', (event) => {
        if (els.modal?.hidden || event.key !== 'Tab') return;
        const focusable = focusableInModal();
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    window.addEventListener('pagehide', () => { if (state.stream) state.stream.getTracks().forEach((t) => t.stop()); });
}
async function init() {
    bindEvents();
    updateExerciseUi(); updateStatuses();
    if ((cfg.reason || 'manual') === 'manual') showPicker(true); else showPicker(false);
    setPhase('loading', 'Loading break session...');
    startTotalTimer();
    try { await startBackendSession(); } catch (error) { setText(els.guide, 'Break page loaded, but session saving is unavailable.'); }
    if (activeExercise().key === 'eye_reset') {
        state.detectionSkipped = true;
        updateStatuses();
        showCameraSurface(false);
    } else {
        await initCameraAndModel();
    }
    updateExerciseUi();
    setPhase('ready', state.detectionSkipped ? 'Camera off. Box Breathing and quiet rest are available.' : 'Ready. Press Start exercise.');
    if (shouldAutoStartEyeReset()) startExercise();
}

init();
