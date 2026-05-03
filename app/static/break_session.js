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
    totalSuggested: Number(cfg.suggestedDurationSec || 180),
    totalTimer: null,
    pausedForVisibility: false,
    visibilityPromptOpen: false,
    awaitingPerson: false,
    monitor: null,
    stream: null,
    lastFocusedBeforeModal: null,
    currentPhaseIndex: -1,
    currentPhaseLabel: '',
    currentOrbScale: 1,
    debugOpen: false,
    confirmResolver: null,
    baselineTimer: null,
    cameraInitStarted: false,
    orbRaf: 0,
};

const els = {
    fullPicker: $('full-picker'),
    workspace: $('break-workspace'),
    video: $('break-video'),
    canvas: $('break-canvas'),
    eyeIframe: $('break-eye-iframe'),
    orb: $('breathing-orb'),
    guide: $('guide-overlay'),
    guideText: $('guide-overlay-text'),
    placeholder: $('break-placeholder'),
    scoreBadge: $('alignment-score-badge'),
    totalTimer: $('break-total-timer'),
    ringTimer: $('break-ring-timer'),
    cameraStatus: $('break-camera-status'),
    modelStatus: $('break-model-status'),
    phase: $('break-current-phase'),
    completed: $('break-completed-count'),
    title: $('current-exercise-title'),
    desc: $('current-exercise-desc'),
    type: $('current-exercise-type'),
    stageCard: $('break-stage-card'),
    startBtn: $('exercise-start-btn'),
    endBtn: $('break-end-btn'),
    skipCameraBtn: $('break-skip-camera-btn'),
    modal: $('self-report-modal'),
    seeAll: $('break-see-all-btn'),
    seeAllSide: $('break-see-all-side-btn'),
    consentCard: $('pose-consent-card'),
    consentOk: $('pose-consent-ok'),
    consentSkip: $('pose-consent-skip'),
    baselineCard: $('baseline-card'),
    baselineRing: $('baseline-ring'),
    baselineText: $('break-baseline-text'),
    baselineNumber: $('baseline-ring-number'),
    progressDots: Array.from(document.querySelectorAll('.break-progress-dot')),
    statusToggle: $('break-status-toggle'),
    statusDot: $('break-status-dot'),
    debugPanel: $('break-debug-panel'),
    completeActions: $('break-complete-actions'),
    nextBtn: $('break-next-btn'),
    pickAnotherBtn: $('break-pick-another-btn'),
    confirmModal: $('break-confirm-modal'),
    confirmEyebrow: $('break-confirm-eyebrow'),
    confirmTitle: $('break-confirm-title'),
    confirmMessage: $('break-confirm-message'),
    confirmConfirm: $('break-confirm-confirm'),
    confirmCancel: $('break-confirm-cancel'),
};

function formatSeconds(sec) {
    const safe = Math.max(0, Math.round(sec || 0));
    return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`;
}
function setText(el, text) { if (el) el.textContent = text; }
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

function repsForExercise(ex, elapsedSec) {
    const elapsed = Math.max(0, Number(elapsedSec || 0));
    if (ex.key === 'box_breathing') return Math.floor(elapsed / 16);
    if (ex.key === 'neck_rolls') return Math.floor(elapsed / 8);
    if (ex.key === 'seated_cat_cow') return Math.floor(elapsed / 6);
    if (ex.key === 'shoulder_opener') return Math.floor(Math.min(elapsed, 15) / 5);
    return 0;
}

function updateRepsBadge(ex, elapsedSec = 0, explicitReps = null) {
    if (!els.scoreBadge) return;
    const shouldShow = ex.camera_required !== 'none';
    els.scoreBadge.hidden = !shouldShow;
    if (!shouldShow) return;
    const reps = explicitReps ?? repsForExercise(ex, elapsedSec);
    els.scoreBadge.textContent = `Reps ${Math.max(0, Number(reps) || 0)}`;
}

function showPicker(show) {
    if (els.fullPicker) els.fullPicker.hidden = !show;
    if (els.workspace) els.workspace.hidden = show;
}

function setPhase(phase, message = '') {
    state.phase = phase;
    if (message) setGuideText(message, true);
    updateDebugPanel();
}

function setGuideText(text, animate = false) {
    if (!els.guideText || !els.guide) return;
    if (!text) {
        els.guide.hidden = true;
        return;
    }
    if (!animate) {
        els.guide.hidden = false;
        els.guideText.textContent = text;
        return;
    }
    els.guide.hidden = false;
    els.guide.classList.remove('is-swapping');
    void els.guide.offsetWidth;
    els.guide.classList.add('is-swapping');
    window.setTimeout(() => {
        els.guideText.textContent = text;
        els.guide.classList.remove('is-swapping');
    }, 120);
}

function setStageCardVisible(show) {
    if (els.placeholder) els.placeholder.hidden = !show;
}

function showCompleteActions(show, nextExercise = null) {
    if (!els.completeActions) return;
    els.completeActions.hidden = !show;
    if (!show || !els.nextBtn) return;
    els.nextBtn.dataset.nextExercise = nextExercise?.key || '';
    els.nextBtn.textContent = nextExercise ? `Next: ${nextExercise.title}` : 'Finish break';
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
}

function updateStatusDot() {
    if (!els.statusDot) return;
    let mode = 'soft';
    if (state.detectionSkipped) mode = 'muted';
    else if (state.cameraReady && state.modelReady) mode = 'ready';
    els.statusDot.dataset.state = mode;
}

function updateDebugPanel() {
    setText(els.cameraStatus, `Camera: ${state.cameraReady ? 'ready' : (state.detectionSkipped ? 'off' : 'unavailable')}`);
    setText(els.modelStatus, `Pose model: ${state.modelReady ? 'ready' : (state.detectionSkipped ? 'skipped' : 'loading')}`);
    setText(els.phase, `Phase: ${state.currentPhaseLabel || '—'}`);
    setText(els.completed, `Done: ${state.completedExercises.length}`);
    updateStatusDot();
}

function updateProgressDots() {
    const dots = els.progressDots || [];
    if (!dots.length) return;
    const filled = Math.min(dots.length, state.completedExercises.length);
    dots.forEach((dot, index) => {
        dot.dataset.active = index < filled ? 'true' : 'false';
    });
}

function updateRingTimer() {
    setText(els.totalTimer, formatSeconds(state.totalRemaining));
    if (!els.ringTimer) return;
    const total = Math.max(1, state.totalSuggested || 1);
    const pct = Math.max(0, Math.min(1, state.totalRemaining / total));
    const angle = Math.round(pct * 360);
    els.ringTimer.style.setProperty('--ring-fill', `${angle}deg`);
}

function nextExerciseAfterCurrent() {
    if (!exercises.length) return null;
    const currentIndex = exercises.findIndex((item) => item.key === state.currentExercise);
    for (let offset = 1; offset <= exercises.length; offset += 1) {
        const item = exercises[(Math.max(0, currentIndex) + offset) % exercises.length];
        if (!state.completedExercises.includes(item.key)) return item;
    }
    return null;
}

function updateExerciseUi() {
    const ex = activeExercise();
    document.querySelectorAll('[data-exercise-key]').forEach((node) => {
        const active = node.dataset.exerciseKey === ex.key;
        node.dataset.active = active ? 'true' : 'false';
        node.dataset.completed = state.completedExercises.includes(node.dataset.exerciseKey) ? 'true' : 'false';
        if (!node.dataset.label) node.dataset.label = node.title || node.querySelector('strong')?.textContent || '';
        const needsCamera = node.dataset.cameraRequired === 'required' && state.detectionSkipped;
        node.disabled = Boolean(needsCamera);
        node.title = needsCamera ? 'Needs camera' : (node.dataset.label || '');
    });
    setText(els.title, ex.title || 'Break');
    setText(els.desc, ex.long_desc || ex.short_desc || 'Follow the guide.');
    setText(els.type, `${ex.type || 'exercise'} · ${ex.duration_sec || 0}s`);
    if (els.startBtn) {
        const unavailable = isCameraExercise(ex) && !canUseCamera();
        els.startBtn.disabled = unavailable;
        els.startBtn.textContent = state.phase === 'exercising' ? 'Restart exercise' : 'Start';
    }
    if (els.skipCameraBtn) {
        els.skipCameraBtn.hidden = !(ex.camera_required === 'optional' && !state.detectionSkipped);
    }
    updateProgressDots();
    updateDebugPanel();
}

async function postJson(url, payload) {
    const res = await fetch(url, {
        method: 'POST',
        headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload || {}),
    });
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
    } catch (error) {
        snapshot = {};
    }
    const body = await postJson(cfg.startUrl, { trigger: cfg.reason || 'manual', fatigue_signal_snapshot: snapshot });
    state.sessionId = body.session_id;
}

async function askConfirm({ eyebrow = 'Break', title = 'Continue?', message = '', confirmText = 'Continue', cancelText = 'Cancel' }) {
    if (!els.confirmModal || !els.confirmConfirm || !els.confirmCancel) return window.confirm(message || title);
    setText(els.confirmEyebrow, eyebrow);
    setText(els.confirmTitle, title);
    setText(els.confirmMessage, message);
    els.confirmConfirm.textContent = confirmText;
    els.confirmCancel.textContent = cancelText;
    els.confirmModal.hidden = false;
    document.body.classList.add('modal-open');
    return new Promise((resolve) => {
        const close = (answer) => {
            els.confirmModal.hidden = true;
            if (els.modal?.hidden !== false) document.body.classList.remove('modal-open');
            els.confirmConfirm.onclick = null;
            els.confirmCancel.onclick = null;
            resolve(answer);
        };
        els.confirmConfirm.onclick = () => close(true);
        els.confirmCancel.onclick = () => close(false);
        els.confirmConfirm.focus();
    });
}

function startTotalTimer() {
    if (state.totalTimer) return;
    updateRingTimer();
    state.totalTimer = window.setInterval(async () => {
        if (state.pausedForVisibility || document.visibilityState !== 'visible') return;
        state.totalRemaining -= 1;
        updateRingTimer();
        if (state.totalRemaining <= 0) {
            clearInterval(state.totalTimer);
            state.totalTimer = null;
            const shouldContinue = await askConfirm({
                eyebrow: 'Suggested break complete',
                title: 'Would you like more time?',
                message: 'You reached the suggested end of this break. You can keep going for one more minute or finish now.',
                confirmText: 'Continue 1 min',
                cancelText: 'Finish break',
            });
            if (shouldContinue) {
                state.totalRemaining = 60;
                state.totalSuggested = Math.max(state.totalSuggested, 60);
                updateRingTimer();
                startTotalTimer();
            } else {
                showSelfReport();
            }
        }
    }, 1000);
}

function phaseForExercise(ex, elapsedSec) {
    if (!ex.phases || !ex.phases.length) return { label: ex.short_desc || 'Rest', cycleRatio: 0.5, seconds: Number(ex.duration_sec || 1), index: 0, startSec: 0, elapsedInPhase: elapsedSec };
    const total = ex.phases.reduce((sum, item) => sum + Number(item.seconds || 0), 0) || 1;
    let t = elapsedSec % total;
    let start = 0;
    for (let index = 0; index < ex.phases.length; index += 1) {
        const ph = ex.phases[index];
        const seconds = Number(ph.seconds || 0);
        if (t < seconds) {
            return { label: ph.label, cycleRatio: seconds ? t / seconds : 0, seconds, index, startSec: start, elapsedInPhase: t };
        }
        t -= seconds;
        start += seconds;
    }
    const first = ex.phases[0];
    return { label: first.label, cycleRatio: 0, seconds: Number(first.seconds || 1), index: 0, startSec: 0, elapsedInPhase: 0 };
}

function targetScaleForPhase(ex, phase) {
    const label = String(phase.label || '').toLowerCase();
    const previous = ex.phases?.[Math.max(0, phase.index - 1)]?.label?.toLowerCase() || '';
    if (label.includes('inhale') || label.includes('open chest')) return 1.55;
    if (label.includes('exhale') || label.includes('round back')) return 1;
    if (label.includes('hold')) {
        return previous.includes('inhale') ? 1.55 : 1;
    }
    if (label.includes('rest') || label.includes('relax')) return 1.12;
    return 1.25;
}

function startingScaleForPhase(ex, phase) {
    const label = String(phase.label || '').toLowerCase();
    if (label.includes('inhale') || label.includes('open chest')) return 1;
    if (label.includes('exhale') || label.includes('round back')) return 1.55;
    return targetScaleForPhase(ex, phase);
}

function updateOrbForPhase(ex, phase, options = {}) {
    if (!els.orb) return;
    const shouldShow = ex.key === 'box_breathing' || ex.type === 'breathing' || ex.type === 'mixed';
    els.orb.hidden = !shouldShow;
    if (!shouldShow) return;
    const duration = Math.max(0.6, Number(phase.seconds || 1));
    const targetScale = targetScaleForPhase(ex, phase);
    const startScale = options.fromStart ? startingScaleForPhase(ex, phase) : state.currentOrbScale;

    if (state.orbRaf) window.cancelAnimationFrame(state.orbRaf);
    els.orb.style.transitionDuration = '0ms';
    els.orb.style.transitionTimingFunction = 'ease-in-out';
    els.orb.style.transform = `translate(-50%, -50%) scale(${startScale})`;
    void els.orb.offsetWidth;
    state.orbRaf = window.requestAnimationFrame(() => {
        els.orb.style.transitionDuration = `${Math.round(duration * 1000)}ms`;
        els.orb.style.transitionTimingFunction = 'cubic-bezier(0.37, 0, 0.22, 1)';
        els.orb.style.transform = `translate(-50%, -50%) scale(${targetScale})`;
        state.orbRaf = 0;
    });
    state.currentOrbScale = targetScale;
}

function openStageReadyCard(message = '') {
    const ex = activeExercise();
    showEyeExerciseSurface(false);
    if (!canUseCamera() || ex.camera_required === 'none') showCameraSurface(false);
    if (els.orb) els.orb.hidden = ex.type !== 'breathing';
    setStageCardVisible(true);
    showCompleteActions(false);
    if (els.scoreBadge) els.scoreBadge.hidden = true;
    if (state.phase !== 'baseline') {
        setGuideText(message || 'Press Start when you are ready.', true);
    }
    if (els.desc && message) {
        const base = ex.long_desc || ex.short_desc || 'Follow the guide.';
        els.desc.textContent = `${base} ${message}`.trim();
    }
}

function completeCurrentExercise() {
    const ex = activeExercise();
    if (!state.completedExercises.includes(ex.key)) state.completedExercises.push(ex.key);
    clearInterval(state.exerciseTimer);
    state.exerciseTimer = null;
    state.awaitingPerson = false;
    state.currentPhaseIndex = -1;
    state.currentPhaseLabel = '';
    state.monitor?.pause();
    showEyeExerciseSurface(false);
    if (els.scoreBadge) els.scoreBadge.hidden = true;
    if (els.orb) els.orb.hidden = true;
    const nextExercise = nextExerciseAfterCurrent();
    setPhase('completed', `${ex.title} complete.`);
    setStageCardVisible(true);
    setText(els.type, 'Complete');
    setText(els.title, `${ex.title} complete`);
    setText(els.desc, nextExercise ? 'Take a breath, then move into the next exercise whenever you want.' : 'Nice work. You can pick another exercise or finish the break.');
    showCompleteActions(true, nextExercise);
    updateExerciseUi();
}

function startExercise() {
    const ex = activeExercise();
    clearInterval(state.exerciseTimer);
    state.awaitingPerson = false;
    state.currentPhaseIndex = -1;
    state.currentPhaseLabel = '';
    showCompleteActions(false);

    if (isCameraExercise(ex) && !canUseCamera()) {
        setPhase('ready', 'Camera feedback is unavailable. Breathing can still guide this break.');
        openStageReadyCard('Camera feedback is off for this one.');
        return;
    }
    showPicker(false);
    setStageCardVisible(false);

    if (ex.key === 'eye_reset') {
        state.monitor?.pause();
        showCameraSurface(false);
        showEyeExerciseSurface(true);
        if (els.orb) els.orb.hidden = true;
        if (els.guide) els.guide.hidden = true;
    } else if (canUseCamera() && ex.camera_required !== 'none') {
        showEyeExerciseSurface(false);
        showCameraSurface(true);
        state.monitor.setExercise(ex.key, ex);
        state.monitor.resume();
    } else {
        state.monitor?.pause();
        showEyeExerciseSurface(false);
        showCameraSurface(false);
    }

    state.exerciseElapsedMs = 0;
    state.exerciseLastTick = performance.now();
    setPhase('exercising', ex.key === 'eye_reset' ? '' : `Start ${ex.title}. Move gently.`);
    updateRepsBadge(ex, 0);

    const firstPhase = phaseForExercise(ex, 0);
    state.currentPhaseIndex = firstPhase.index;
    state.currentPhaseLabel = firstPhase.label;
    updateOrbForPhase(ex, firstPhase, { fromStart: true });
    if (ex.key !== 'eye_reset') setGuideText(firstPhase.label || `Start ${ex.title}`, true);
    updateDebugPanel();

    state.exerciseTimer = window.setInterval(() => {
        const now = performance.now();
        if (exerciseClockPaused()) {
            state.exerciseLastTick = now;
            return;
        }
        state.exerciseElapsedMs += now - state.exerciseLastTick;
        state.exerciseLastTick = now;
        const elapsed = state.exerciseElapsedMs / 1000;
        updateRepsBadge(ex, elapsed);
        const phase = phaseForExercise(ex, elapsed);
        if (phase.index !== state.currentPhaseIndex || phase.label !== state.currentPhaseLabel) {
            state.currentPhaseIndex = phase.index;
            state.currentPhaseLabel = phase.label;
            updateOrbForPhase(ex, phase);
            if (ex.key !== 'eye_reset') setGuideText(phase.label || 'Rest quietly', true);
            updateDebugPanel();
        }
        if (elapsed >= Number(ex.duration_sec || 30)) completeCurrentExercise();
    }, 120);
}

function focusableInModal() {
    const target = els.modal?.hidden === false ? els.modal : (els.confirmModal?.hidden === false ? els.confirmModal : null);
    if (!target) return [];
    return Array.from(target.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter((node) => !node.disabled && node.offsetParent !== null);
}

function showSelfReport() {
    clearInterval(state.exerciseTimer);
    state.exerciseTimer = null;
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
        try {
            sessionStorage.setItem('wellhabitBreakCompletionSignal', JSON.stringify({
                break_completion: true,
                self_report: report,
                exercises_done: state.completedExercises,
                at: Date.now(),
            }));
        } catch (error) {}
        window.location.href = `${cfg.dashboardUrl || '/dashboard'}?from=break&felt=${encodeURIComponent(report)}`;
    } catch (error) {
        setGuideText(error.message || 'Could not save break. Please try again.', true);
    }
}

function showBaselineProgress(show, progress = 0) {
    if (!els.baselineCard) return;
    els.baselineCard.hidden = !show;
    if (!show) return;
    const pct = Math.max(0, Math.min(1, progress));
    const angle = Math.round(pct * 360);
    if (els.baselineRing) els.baselineRing.style.setProperty('--baseline-fill', `${angle}deg`);
    const remaining = Math.max(0, 5 - Math.ceil(pct * 5));
    setText(els.baselineNumber, String(Math.max(0, remaining)));
}

async function ensurePoseConsent() {
    let saved = null;
    try { saved = localStorage.getItem('poseDetectionConsent'); } catch (error) { saved = null; }
    if (saved === 'ok') return true;
    if (saved === 'skip') return false;
    if (!els.consentCard) return true;
    els.consentCard.hidden = false;
    setStageCardVisible(false);
    return new Promise((resolve) => {
        const done = (allowed) => {
            try { localStorage.setItem('poseDetectionConsent', allowed ? 'ok' : 'skip'); } catch (error) {}
            els.consentCard.hidden = true;
            resolve(allowed);
        };
        els.consentOk?.addEventListener('click', () => done(true), { once: true });
        els.consentSkip?.addEventListener('click', () => done(false), { once: true });
        els.consentOk?.focus();
    });
}


async function maybeInitCameraAndModel() {
    if (state.cameraInitStarted || activeExercise().key === 'eye_reset') return;
    state.cameraInitStarted = true;
    await initCameraAndModel();
}

async function initCameraAndModel() {
    const consent = await ensurePoseConsent();
    if (!consent) {
        state.detectionSkipped = true;
        updateExerciseUi();
        openStageReadyCard('Camera skipped. Box Breathing and Eye Reset are ready.');
        return;
    }
    try {
        state.stream = await requestCamera();
        if (!state.stream) throw new Error('No stream');
        els.video.srcObject = state.stream;
        await els.video.play().catch(() => {});
        state.cameraReady = true;
        updateExerciseUi();
    } catch (error) {
        state.detectionSkipped = true;
        updateExerciseUi();
        openStageReadyCard('Camera unavailable. Breathing can still lead this break.');
        return;
    }
    try {
        const landmarker = await loadPoseLandmarker();
        state.modelReady = true;
        state.monitor = new PoseMonitor();
        state.monitor.init(els.video, els.canvas, landmarker);
        setPhase('baseline', 'Getting ready. Stay still for a few seconds.');
        setStageCardVisible(false);
        showCameraSurface(true);
        setText(els.baselineText, 'Stay still for 5 seconds while we get your gentle baseline.');
        showBaselineProgress(true, 0);
        const startedAt = performance.now();
        clearInterval(state.baselineTimer);
        state.baselineTimer = window.setInterval(() => {
            showBaselineProgress(true, (performance.now() - startedAt) / 5000);
        }, 100);
        const baseline = await state.monitor.captureBaseline(5000);
        clearInterval(state.baselineTimer);
        state.baselineTimer = null;
        showBaselineProgress(false, 1);
        if (baseline && baseline.quality === 'fallback') {
            openStageReadyCard('Breathing will still work smoothly. Posture feedback may stay quieter this round.');
        }
        state.monitor.onEvaluation((ev) => {
            if (state.phase !== 'exercising') return;
            if (ev?.paused) {
                state.awaitingPerson = true;
                setText(els.phase, `Phase: ${ev.phase || 'Paused'}`);
                setGuideText(ev.hints?.[0] || 'Come back into the frame when you are ready.', true);
                return;
            }
            if (state.awaitingPerson) {
                state.awaitingPerson = false;
                state.exerciseLastTick = performance.now();
            }
            setText(els.phase, `Phase: ${ev.phase || '—'}`);
            if (ev?.hints?.length && !activeExercise().phases?.length) setGuideText(ev.hints[0], true);
            updateRepsBadge(activeExercise(), ev.elapsed, ev.metrics?.reps);
        });
    } catch (error) {
        state.modelReady = false;
        state.detectionSkipped = true;
        showCameraSurface(false);
        updateExerciseUi();
        openStageReadyCard('Pose feedback is resting right now. Breathing is still available.');
    }
}

async function handleVisibilityChange() {
    if (document.visibilityState !== 'visible') {
        state.pausedForVisibility = true;
        state.exerciseLastTick = performance.now();
        state.monitor?.pause();
        setGuideText('Paused while you were away.', true);
        return;
    }
    if (!state.pausedForVisibility || state.visibilityPromptOpen) return;
    state.visibilityPromptOpen = true;
    const shouldContinue = await askConfirm({
        eyebrow: 'Welcome back',
        title: 'Continue this break?',
        message: 'Your break paused while the tab was hidden. Continue from where you left off?',
        confirmText: 'Continue',
        cancelText: 'End break',
    });
    state.visibilityPromptOpen = false;
    if (document.visibilityState !== 'visible') return;
    if (shouldContinue) {
        state.pausedForVisibility = false;
        state.exerciseLastTick = performance.now();
        if (canUseCamera() && activeExercise().camera_required !== 'none') state.monitor?.resume();
        setGuideText('Welcome back. Continue gently.', true);
    } else {
        state.pausedForVisibility = false;
        showSelfReport();
    }
}

async function handleExerciseSelection(key, options = {}) {
    const next = exerciseMap[key];
    if (!next) return;
    state.currentExercise = next.key;
    showEyeExerciseSurface(false);
    clearInterval(state.exerciseTimer);
    state.exerciseTimer = null;
    state.currentPhaseIndex = -1;
    state.currentPhaseLabel = '';
    if (els.orb) els.orb.hidden = next.type !== 'breathing';
    showPicker(false);
    if (next.key !== 'eye_reset') await maybeInitCameraAndModel();
    updateExerciseUi();
    setPhase('ready', options.message || 'Press Start when you are ready.');
    openStageReadyCard(options.message || '');
    if (options.autoStart) startExercise();
}

function bindEvents() {
    document.querySelectorAll('[data-exercise-key]').forEach((node) => {
        node.addEventListener('click', () => {
            if (node.disabled) return;
void handleExerciseSelection(node.dataset.exerciseKey);
        });
        node.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                node.click();
            }
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
        updateExerciseUi();
        openStageReadyCard('Camera turned off. Breathing and eye reset stay available.');
    });
    els.seeAll?.addEventListener('click', () => showPicker(true));
    els.seeAllSide?.addEventListener('click', () => showPicker(true));
    els.pickAnotherBtn?.addEventListener('click', () => showPicker(true));
    els.nextBtn?.addEventListener('click', () => {
        const nextKey = els.nextBtn.dataset.nextExercise;
        if (!nextKey) {
            showSelfReport();
            return;
        }
void handleExerciseSelection(nextKey, { autoStart: true, message: 'Starting the next exercise.' });
    });
    els.statusToggle?.addEventListener('click', () => {
        state.debugOpen = !state.debugOpen;
        if (els.debugPanel) els.debugPanel.hidden = !state.debugOpen;
    });
    document.querySelectorAll('[data-self-report]').forEach((btn) => btn.addEventListener('click', () => finishBreak(btn.dataset.selfReport)));
    document.addEventListener('visibilitychange', handleVisibilityChange);
    document.addEventListener('keydown', (event) => {
        if ((els.modal?.hidden !== false && els.confirmModal?.hidden !== false) || event.key !== 'Tab') return;
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
    updateExerciseUi();
    updateRingTimer();
    const manualMode = (cfg.reason || 'manual') === 'manual';
    if (manualMode) showPicker(true); else showPicker(false);
    setPhase('loading', 'Loading break session...');
    startTotalTimer();
    try {
        await startBackendSession();
    } catch (error) {
        setGuideText('Break page loaded, but session saving is unavailable.', true);
    }
    if (activeExercise().key === 'eye_reset') {
        state.detectionSkipped = true;
        updateExerciseUi();
        showCameraSurface(false);
    } else if (!manualMode) {
        await maybeInitCameraAndModel();
    }
    updateExerciseUi();
    if (state.phase !== 'baseline') {
        setPhase('ready', state.detectionSkipped ? 'Camera off. Box Breathing and quiet rest are available.' : 'Ready. Press Start.');
        openStageReadyCard(state.detectionSkipped ? 'Camera is optional here.' : '');
    }
    if (shouldAutoStartEyeReset()) startExercise();
}

init();
