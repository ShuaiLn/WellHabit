(function () {
    const CURRENT_SCRIPT_URL = document.currentScript?.src || new URL('/static/fatigue_monitor.js', window.location.origin).href;
    const DEFAULT_LOADER_URL = new URL('vision_loader.js', CURRENT_SCRIPT_URL).href;
    const DEFAULT_MODULE_URL = '/static/vendor/mediapipe/vision_bundle.mjs';
    const DEFAULT_WASM_URL = '/static/vendor/mediapipe/wasm';
    const DEFAULT_MODEL_URL = '/static/break_assets/face_landmarker.task';

    // Keep the camera preview smooth, but keep face inference high enough for fast fatigue signals.
    // Blink/PERCLOS/yawn/gaze need about 10-15 analysis samples per second; body pose can run slower elsewhere.
    const TARGET_INFERENCE_INTERVAL_MS = 85;
    const MIN_INFERENCE_INTERVAL_MS = 67;
    const MAX_INFERENCE_INTERVAL_MS = 150;
    const DETECT_SLOW_MS = 120;
    const DETECT_FAST_MS = 60;
    const EYE_BASELINE_MS = 10000;
    const HEAD_BASELINE_MS = 30000;
    const PERCLOS_WINDOW_MS = 60000;
    const YAWN_WINDOW_MS = 10 * 60 * 1000;
    const HEAVY_SUSTAIN_MS = 5000;
    const LIGHT_ALERT_COOLDOWN_MS = 3 * 60 * 1000;
    const ALERT_EXIT_COOLDOWN_MS = 3 * 60 * 1000;
    const CAMERA_CONSENT_KEY = 'wellhabitFatigueCameraConsent';
    const RELAXED_AFFECT_SUSTAIN_MS = 20 * 1000;
    const RELAXED_AFFECT_COOLDOWN_MS = 8 * 60 * 1000;
    // Low analysis FPS should not pause the camera. Only show a best-effort performance note.
    const LOW_FPS_BEST_EFFORT_THRESHOLD = 2;
    const LOW_FPS_SEVERE_THRESHOLD = 1;
    const LOW_POWER_CAMERA_CONSTRAINTS = {
        video: {
            facingMode: 'user',
            width: { ideal: 480, max: 640 },
            height: { ideal: 360, max: 480 },
            frameRate: { ideal: 30, max: 30 },
            resizeMode: 'crop-and-scale',
        },
        audio: false,
    };
    const CAMERA_CONSTRAINT_FALLBACKS = [
        LOW_POWER_CAMERA_CONSTRAINTS,
        { video: { facingMode: 'user', width: { ideal: 320, max: 480 }, height: { ideal: 240, max: 360 }, frameRate: { ideal: 24, max: 30 }, resizeMode: 'crop-and-scale' }, audio: false },
        { video: { facingMode: 'user', width: { ideal: 640, max: 640 }, height: { ideal: 480, max: 480 }, frameRate: { ideal: 30, max: 30 } }, audio: false },
        { video: { facingMode: 'user' }, audio: false },
    ];
    const REPORT_COOLDOWNS_MS = {
        microsleep: 30 * 1000,
        mild_signal: 5 * 60 * 1000,
        heavy_signal: 3 * 60 * 1000,
        possible_relaxed_affect: RELAXED_AFFECT_COOLDOWN_MS,
        break_confirmed: Infinity,
        break_declined: Infinity,
    };

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, Number(value) || 0));
    }

    function average(values, fallback) {
        const clean = values.filter((value) => Number.isFinite(value));
        if (!clean.length) return fallback;
        return clean.reduce((sum, value) => sum + value, 0) / clean.length;
    }

    function median(values, fallback) {
        const clean = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
        if (!clean.length) return fallback;
        const middle = Math.floor(clean.length / 2);
        return clean.length % 2 ? clean[middle] : (clean[middle - 1] + clean[middle]) / 2;
    }

    function stddev(values) {
        const clean = values.filter((value) => Number.isFinite(value));
        if (clean.length < 2) return 0;
        const mean = average(clean, 0);
        const variance = average(clean.map((value) => Math.pow(value - mean, 2)), 0);
        return Math.sqrt(variance);
    }

    function distance(a, b) {
        if (!a || !b) return 0;
        const dx = Number(a.x || 0) - Number(b.x || 0);
        const dy = Number(a.y || 0) - Number(b.y || 0);
        return Math.sqrt(dx * dx + dy * dy);
    }

    function categoryMap(result) {
        const map = Object.create(null);
        const categories = result?.faceBlendshapes?.[0]?.categories || [];
        categories.forEach((category) => {
            const name = category.categoryName || category.displayName;
            if (name) map[name] = Number(category.score || 0);
        });
        return map;
    }

    function eyeAspectRatio(landmarks, indices) {
        const [p1, p2, p3, p4, p5, p6] = indices.map((index) => landmarks?.[index]);
        const horizontal = distance(p1, p4);
        if (!horizontal) return 0;
        return (distance(p2, p6) + distance(p3, p5)) / (2 * horizontal);
    }

    function mouthAspectRatio(landmarks) {
        const vertical = distance(landmarks?.[13], landmarks?.[14]);
        const horizontal = distance(landmarks?.[78], landmarks?.[308]);
        return horizontal ? vertical / horizontal : 0;
    }

    function matrixToEulerDegrees(matrixLike) {
        const data = matrixLike?.data || matrixLike?.matrix || matrixLike;
        if (!data || data.length < 16) return { pitch: 0, yaw: 0, roll: 0 };
        const m00 = Number(data[0] || 0);
        const m01 = Number(data[1] || 0);
        const m02 = Number(data[2] || 0);
        const m10 = Number(data[4] || 0);
        const m11 = Number(data[5] || 0);
        const m12 = Number(data[6] || 0);
        const m20 = Number(data[8] || 0);
        const m21 = Number(data[9] || 0);
        const m22 = Number(data[10] || 0);
        const sy = Math.sqrt(m00 * m00 + m10 * m10);
        const singular = sy < 1e-6;
        let pitch;
        let yaw;
        let roll;
        if (!singular) {
            pitch = Math.atan2(m21, m22);
            yaw = Math.atan2(-m20, sy);
            roll = Math.atan2(m10, m00);
        } else {
            pitch = Math.atan2(-m12, m11);
            yaw = Math.atan2(-m20, sy);
            roll = 0;
        }
        const toDegrees = 180 / Math.PI;
        return { pitch: pitch * toDegrees, yaw: yaw * toDegrees, roll: roll * toDegrees };
    }

    function isSecureEnoughForCamera() {
        return window.isSecureContext || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    }


    async function requestLowPowerCameraStream() {
        let lastError = null;
        for (const constraints of CAMERA_CONSTRAINT_FALLBACKS) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia(constraints);
                await tightenVideoTrack(stream);
                return stream;
            } catch (error) {
                lastError = error;
                const name = error?.name || '';
                if (name !== 'OverconstrainedError' && name !== 'ConstraintNotSatisfiedError' && name !== 'TypeError') throw error;
            }
        }
        throw lastError || new Error('Camera constraints failed');
    }

    async function tightenVideoTrack(stream) {
        const track = stream?.getVideoTracks?.()[0];
        if (!track?.applyConstraints) return;
        try {
            await track.applyConstraints({
                width: { ideal: 480, max: 640 },
                height: { ideal: 360, max: 480 },
                frameRate: { ideal: 30, max: 30 },
                resizeMode: 'crop-and-scale',
            });
        } catch (error) {
            // Some browsers reject resizeMode/max constraints even though the stream is usable.
            // Keep the preview running and rely on inference throttling instead of failing startup.
        }
    }

    function getVideoTrackSettings(stream) {
        try {
            return stream?.getVideoTracks?.()[0]?.getSettings?.() || {};
        } catch (error) {
            return {};
        }
    }

    class FatigueMonitor {
        constructor() {
            this.video = document.getElementById('focus-camera-video');
            this.placeholder = document.getElementById('focus-camera-placeholder');
            this.startBtn = document.getElementById('camera-start-btn');
            this.stopBtn = document.getElementById('camera-stop-btn');
            this.statusLine = document.getElementById('camera-status-line');
            this.statusRow = document.getElementById('fatigue-status-row');
            this.statusLabel = document.getElementById('fatigue-status-label');
            this.statusCopy = document.getElementById('fatigue-status-copy');
            this.consentOverlay = document.getElementById('camera-consent-overlay');
            this.consentAllowBtn = document.getElementById('camera-consent-allow');
            this.consentCancelBtn = document.getElementById('camera-consent-cancel');
            this.scoreText = document.getElementById('fatigue-score-text');
            this.scoreBar = document.getElementById('fatigue-score-bar');
            this.metricEls = {
                perclos: document.getElementById('fatigue-metric-perclos'),
                blink: document.getElementById('fatigue-metric-blink'),
                yawn: document.getElementById('fatigue-metric-yawn'),
                head: document.getElementById('fatigue-metric-head'),
                gaze: document.getElementById('fatigue-metric-gaze'),
                fps: document.getElementById('fatigue-metric-fps'),
            };
            this.toast = document.getElementById('fatigue-toast');
            this.modal = document.getElementById('fatigue-break-overlay');
            this.restBtn = document.getElementById('fatigue-rest-btn');
            this.keepBtn = document.getElementById('fatigue-keep-btn');
            const panel = document.getElementById('fatigue-monitor-panel');
            this.reportUrl = panel?.dataset.reportUrl || '/api/pomodoro/fatigue';
            this.loaderUrl = panel?.dataset.loaderUrl || DEFAULT_LOADER_URL;
            this.moduleUrl = panel?.dataset.moduleUrl || DEFAULT_MODULE_URL;
            this.wasmUrl = document.getElementById('fatigue-monitor-panel')?.dataset.wasmUrl || DEFAULT_WASM_URL;
            this.modelUrl = document.getElementById('fatigue-monitor-panel')?.dataset.modelUrl || DEFAULT_MODEL_URL;
            this.stream = null;
            this.faceLandmarker = null;
            this.loadingModel = null;
            this.rafId = null;
            this.inferenceTimerId = null;
            this.inferenceRunning = false;
            this.lastSampleAt = 0;
            this.nextInferenceAt = 0;
            this.startedAt = 0;
            this.adaptiveSampleInterval = TARGET_INFERENCE_INTERVAL_MS;
            this.detectDurationMs = 0;
            this.sampleDurationMs = 0;
            this.cameraSettings = {};
            window.WellHabitVisionTiming = window.WellHabitVisionTiming || {};
            this.samples = [];
            this.sampleTimestamps = [];
            this.pitchHistory = [];
            this.marHistory = [];
            this.yawnEvents = [];
            this.eyeBaseline = [];
            this.earBaseline = [];
            this.marBaseline = [];
            this.pitchBaseline = [];
            this.yawBaseline = [];
            this.rollBaseline = [];
            this.closedSince = null;
            this.lastClosed = false;
            this.blinkEvents = [];
            this.gazeDownSince = null;
            this.heavySince = null;
            this.lastLightAlertAt = 0;
            this.lastReportByType = Object.create(null);
            this.lastFatigueBand = 'normal';
            this.inAlertState = false;
            this.alertCooldownUntil = 0;
            this.pausedForVisibility = false;
            this.pausedForTimer = false;
            this.emaScore = 0;
            this.yawnCandidate = null;
            this.relaxedAffectSince = null;
            this.lastRelaxedAffectEventAt = 0;
            this.userOptedIn = false;
            this.lastPayload = null;
            this.lightCanvas = document.createElement('canvas');
            this.lightCanvas.width = 32;
            this.lightCanvas.height = 18;
            this.lightCtx = this.lightCanvas.getContext('2d', { willReadFrequently: true });
            this.bindEvents();
            this.setCameraRunning(false);
            this.renderIdle();
        }

        bindEvents() {
            this.startBtn?.addEventListener('click', () => this.enableForNextSession());
            this.stopBtn?.addEventListener('click', () => this.stop('Camera preview is off.'));
            this.consentCancelBtn?.addEventListener('click', () => this.resolveConsent(false));
            this.consentAllowBtn?.addEventListener('click', () => this.resolveConsent(true));
            this.restBtn?.addEventListener('click', async () => {
                this.hideBreakModal();
                const snapshot = Object.assign({}, this.lastPayload || {}, { user_confirmed_break: true });
                this.reportEvent('break_confirmed', snapshot, true);
                try { sessionStorage.setItem('fatigueSignalSnapshot', JSON.stringify(snapshot)); } catch (error) {}
                if (window.WellHabitTimer?.stopAndSave) {
                    try { await window.WellHabitTimer.stopAndSave('fatigue_break'); } catch (error) {}
                }
                this.pauseForHandoff();
                window.location.href = '/break?reason=fatigue';
            });
            this.keepBtn?.addEventListener('click', () => {
                this.hideBreakModal();
                this.reportEvent('break_declined', Object.assign({}, this.lastPayload || {}, { user_confirmed_break: false }), true);
                this.heavySince = null;
                this.setStatus('Okay. I will keep watching for possible fatigue signals during this focus round.');
            });
            window.addEventListener('pagehide', () => this.stop('Camera released.'));
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState !== 'visible') {
                    this.hideBreakModal();
                    this.setVideoTracksEnabled(false);
                    return;
                }
                this.setVideoTracksEnabled(true);
            });
            document.addEventListener('wellhabit:timer-state', (event) => this.handleTimerState(event.detail));
        }

        handleTimerState(state) {
            if (!this.stream) return;
            if (!state || state.mode !== 'focus' || !state.isRunning) {
                this.setStatus('Camera preview is still on. Use Turn off to release it.');
            }
        }

        setCameraRunning(isRunning) {
            const running = Boolean(isRunning);
            if (this.video) this.video.hidden = !running;
            if (this.placeholder) this.placeholder.hidden = running;
            if (this.startBtn) this.startBtn.hidden = running;
            if (this.stopBtn) this.stopBtn.hidden = !running;
            document.dispatchEvent(new CustomEvent('wellhabit:fatigue-camera-state', { detail: { active: running } }));
        }

        isCameraRunning() {
            return Boolean(this.stream);
        }

        hasStoredConsent() {
            try { return localStorage.getItem(CAMERA_CONSENT_KEY) === '1'; } catch (error) { return false; }
        }

        rememberConsent() {
            try { localStorage.setItem(CAMERA_CONSENT_KEY, '1'); } catch (error) {}
        }

        async shouldAutoStartCamera() {
            if (this.hasStoredConsent()) return true;
            if (!navigator.permissions?.query) return false;
            try {
                const permission = await navigator.permissions.query({ name: 'camera' });
                return permission?.state === 'granted';
            } catch (error) {
                return false;
            }
        }

        requestConsent() {
            if (!this.consentOverlay) return Promise.resolve(true);
            this.consentOverlay.hidden = false;
            return new Promise((resolve) => { this.pendingConsentResolve = resolve; });
        }

        resolveConsent(allowed) {
            if (this.consentOverlay) this.consentOverlay.hidden = true;
            const resolve = this.pendingConsentResolve;
            this.pendingConsentResolve = null;
            if (allowed) this.rememberConsent();
            if (resolve) resolve(Boolean(allowed));
        }

        async ensureCameraConsent({ silent = false } = {}) {
            if (this.hasStoredConsent()) return true;
            if (silent) return false;
            return this.requestConsent();
        }

        async enableForNextSession() {
            const allowed = await this.ensureCameraConsent({ silent: false });
            if (!allowed) {
                this.setStatus('Camera support is off. Timer still works normally.');
                return false;
            }
            if (!isSecureEnoughForCamera() || !navigator.mediaDevices?.getUserMedia) {
                this.setStatus('Camera requires HTTPS/localhost and browser camera support.');
                return false;
            }
            try {
                const stream = await requestLowPowerCameraStream();
                stream.getTracks().forEach((track) => track.stop());
                this.setStatus('Camera is enabled for focus sessions. Start the timer when you are ready.');
                return true;
            } catch (error) {
                this.setStatus(this.cameraErrorMessage(error));
                return false;
            }
        }

        setVideoTracksEnabled(enabled) {
            if (!this.stream) return;
            this.stream.getVideoTracks().forEach((track) => {
                track.enabled = Boolean(enabled);
            });
            this.pausedForVisibility = !enabled;
            if (!enabled) {
                this.setStatus('Tab hidden. Camera capture is temporarily paused to save power.');
            } else {
                this.lastSampleAt = 0;
                this.setStatus('Tab visible again. Camera fatigue monitor resumed.');
            }
        }

        setStatus(text) {
            if (this.statusLine) this.statusLine.textContent = text;
            const timerMessage = document.getElementById('timer-message');
            if (!this.stream && timerMessage && text) timerMessage.textContent = text;
        }

        renderIdle() {
            this.updateScoreUi(0);
            this.updateFatigueStatus('idle', 'Camera off', 'Enable camera only if you want focus support.');
            this.updateMetric('perclos', '--', 'neutral');
            this.updateMetric('blink', '--', 'neutral');
            this.updateMetric('yawn', '--', 'neutral');
            this.updateMetric('head', '--', 'neutral');
            this.updateMetric('gaze', '--', 'neutral');
            this.updateMetric('fps', '--', 'neutral');
        }

        updateScoreUi(score) {
            const clean = clamp(score, 0, 1);
            if (this.scoreText) this.scoreText.textContent = `${Math.round(clean * 100)}%`;
            if (this.scoreBar) this.scoreBar.style.width = `${Math.round(clean * 100)}%`;
        }

        updateMetric(key, text, state = 'normal', title) {
            const el = this.metricEls[key];
            if (!el) return;
            const valueEl = el.querySelector('em') || el;
            valueEl.textContent = text;
            el.dataset.state = state;
            if (title) el.title = title;
        }

        updateFatigueStatus(band, label, copy) {
            if (this.statusRow) this.statusRow.dataset.band = band || 'idle';
            if (this.statusLabel) this.statusLabel.textContent = label || '';
            if (this.statusCopy) this.statusCopy.textContent = copy || '';
        }

        async startFromUserGesture(options = {}) {
            const allowed = await this.ensureCameraConsent({ silent: Boolean(options.auto) });
            if (!allowed) {
                this.setStatus('Camera support is off. Timer still works normally.');
                return false;
            }
            this.userOptedIn = true;
            return this.start();
        }

        cameraErrorMessage(error) {
            const name = error?.name || '';
            if (name === 'NotAllowedError' || name === 'SecurityError') {
                return 'Camera permission was blocked or denied. Timer still works normally without camera fatigue signals.';
            }
            if (name === 'NotFoundError' || name === 'OverconstrainedError') {
                return 'No usable camera was found. Timer still works normally without camera fatigue signals.';
            }
            if (name === 'NotReadableError' || name === 'AbortError') {
                return 'The camera is busy or unavailable. Close other camera apps, then try again.';
            }
            return 'Camera could not start. Timer still works normally without camera fatigue signals.';
        }

        modelErrorMessage(error) {
            const message = String(error?.message || '');
            if (message.includes('MEDIAPIPE_MODULE_LOAD_FAILED')) {
                return 'Camera started, but the local MediaPipe JavaScript module could not load. Check CSP, .mjs MIME type, or /static/vendor/mediapipe/vision_bundle.mjs. Timer still works normally.';
            }
            if (message.includes('MEDIAPIPE_WASM_LOAD_FAILED')) {
                return 'Camera started, but the local MediaPipe WASM files could not load. Check /static/vendor/mediapipe/wasm and the .wasm MIME type. Timer still works normally.';
            }
            if (message.includes('MEDIAPIPE_MODEL_LOAD_FAILED')) {
                return 'Camera started, but the local Face Landmarker model could not load. Check /static/break_assets/face_landmarker.task. Timer still works normally.';
            }
            return 'Camera started, but the local face model failed to initialize. Timer still works normally.';
        }

        async start() {
            if (!this.video) return false;
            if (this.stream) {
                this.pausedForTimer = false;
                this.lastSampleAt = 0;
                if (this.video.srcObject !== this.stream) this.video.srcObject = this.stream;
                this.setCameraRunning(true);
                if (!this.inferenceTimerId && this.faceLandmarker) this.loop();
                this.setStatus(this.faceLandmarker ? 'Camera fatigue monitor resumed.' : 'Camera preview is ready; local fatigue model is unavailable.');
                return true;
            }
            if (!isSecureEnoughForCamera()) {
                this.setStatus('Camera requires HTTPS or localhost. Timer can still run without camera signals.');
                return false;
            }
            if (!navigator.mediaDevices?.getUserMedia) {
                this.setStatus('This browser does not support camera access. Timer can still run without camera signals.');
                return false;
            }

            this.setStatus('Requesting camera permission...');
            try {
                this.stream = await requestLowPowerCameraStream();
                this.cameraSettings = getVideoTrackSettings(this.stream);
            } catch (error) {
                this.stop(this.cameraErrorMessage(error));
                return false;
            }

            this.video.srcObject = this.stream;
            await this.video.play().catch(() => {});
            this.setCameraRunning(true);
            this.resetRuntimeState();

            this.setStatus('Loading face model for on-device fatigue signals...');
            try {
                await this.loadModel();
            } catch (error) {
                console.warn('Fatigue model load failed:', error);
                this.faceLandmarker = null;
                this.setStatus(`${this.modelErrorMessage(error)} Camera preview stays on, but local AI fatigue analysis is unavailable.`);
                this.updateFatigueStatus('idle', 'Camera preview only', 'Local face model unavailable; fatigue scoring is paused.');
                return true;
            }

            this.setStatus('Calibrating your normal eye/head baseline. Keep working normally for a few seconds.');
            this.updateFatigueStatus('calibrating', 'Calibrating', 'Keep working normally while WellHabit learns your baseline.');
            this.loop();
            return true;
        }


        pause(message) {
            this.stopInferenceLoop();
            this.pausedForTimer = true;
            this.hideBreakModal();
            this.updateFatigueStatus('idle', 'Paused', 'Camera stays ready. Fatigue analysis resumes when the timer resumes.');
            this.setStatus(message || 'Timer paused. Camera stays ready.');
        }

        pauseForHandoff() {
            this.stopInferenceLoop();
            this.pausedForVisibility = true;
            try { sessionStorage.setItem('cameraHandoff', '1'); } catch (error) {}
            window.WellHabitCameraHandoffStream = this.stream || null;
            this.setStatus('Camera handoff prepared for guided break.');
        }

        stop(message) {
            this.stopInferenceLoop();
            if (this.stream) {
                this.stream.getTracks().forEach((track) => track.stop());
                this.stream = null;
            }
            this.pausedForVisibility = false;
            this.pausedForTimer = false;
            if (this.video) this.video.srcObject = null;
            this.setCameraRunning(false);
            this.hideBreakModal();
            this.resetRuntimeState(false);
            this.renderIdle();
            this.setStatus(message || 'Camera preview is off.');
        }

        resetRuntimeState(resetScore = true) {
            this.startedAt = performance.now();
            this.lastSampleAt = 0;
            this.nextInferenceAt = 0;
            this.adaptiveSampleInterval = TARGET_INFERENCE_INTERVAL_MS;
            this.detectDurationMs = 0;
            this.sampleDurationMs = 0;
            this.samples = [];
            this.sampleTimestamps = [];
            this.pitchHistory = [];
            this.marHistory = [];
            this.yawnEvents = [];
            this.eyeBaseline = [];
            this.earBaseline = [];
            this.marBaseline = [];
            this.pitchBaseline = [];
            this.yawBaseline = [];
            this.rollBaseline = [];
            this.closedSince = null;
            this.lastClosed = false;
            this.blinkEvents = [];
            this.gazeDownSince = null;
            this.heavySince = null;
            this.lastLightAlertAt = 0;
            this.lastReportByType = Object.create(null);
            this.lastFatigueBand = 'normal';
            this.inAlertState = false;
            this.alertCooldownUntil = 0;
            this.yawnCandidate = null;
            this.relaxedAffectSince = null;
            this.lastRelaxedAffectEventAt = 0;
            this.lastPayload = null;
            if (resetScore) this.emaScore = 0;
        }

        async loadModel() {
            if (this.faceLandmarker) return this.faceLandmarker;
            if (this.loadingModel) return this.loadingModel;
            this.loadingModel = (async () => {
                let loader;
                try {
                    loader = await import(this.loaderUrl);
                } catch (error) {
                    throw new Error(`MEDIAPIPE_MODULE_LOAD_FAILED: ${error?.message || error}`);
                }

                try {
                    return await loader.loadFaceLandmarker({
                        moduleUrl: this.moduleUrl,
                        wasmUrl: this.wasmUrl,
                        modelUrl: this.modelUrl,
                    });
                } catch (error) {
                    const message = String(error?.message || error);
                    if (message.includes('MEDIAPIPE_MODEL_LOAD_FAILED')) throw error;
                    if (message.toLowerCase().includes('module')) {
                        throw new Error(`MEDIAPIPE_MODULE_LOAD_FAILED: ${message}`);
                    }
                    if (message.toLowerCase().includes('wasm')) {
                        throw new Error(`MEDIAPIPE_WASM_LOAD_FAILED: ${message}`);
                    }
                    throw new Error(`MEDIAPIPE_MODEL_LOAD_FAILED: ${message}`);
                }
            })().then((landmarker) => {
                this.faceLandmarker = landmarker;
                return landmarker;
            }).finally(() => {
                this.loadingModel = null;
            });
            return this.loadingModel;
        }

        stopInferenceLoop() {
            if (this.rafId) window.cancelAnimationFrame(this.rafId);
            this.rafId = null;
            if (this.inferenceTimerId) window.clearTimeout(this.inferenceTimerId);
            this.inferenceTimerId = null;
            this.inferenceRunning = false;
        }

        loop() {
            if (this.inferenceTimerId || !this.stream || !this.faceLandmarker) return;
            this.nextInferenceAt = performance.now();
            const scheduleNext = () => {
                if (!this.stream || !this.faceLandmarker) {
                    this.inferenceTimerId = null;
                    return;
                }
                const delay = Math.max(0, this.nextInferenceAt - performance.now());
                this.inferenceTimerId = window.setTimeout(runOnce, delay);
            };
            const runOnce = () => {
                this.inferenceTimerId = null;
                if (!this.stream || !this.faceLandmarker) return;
                if (this.pausedForVisibility || this.pausedForTimer || this.inferenceRunning) {
                    this.nextInferenceAt = performance.now() + this.adaptiveSampleInterval;
                    scheduleNext();
                    return;
                }
                const now = performance.now();
                this.lastSampleAt = now;
                this.nextInferenceAt = Math.max(this.nextInferenceAt + this.adaptiveSampleInterval, now);
                this.inferenceRunning = true;
                window.WellHabitVisionTiming.faceRunning = true;
                window.WellHabitVisionTiming.lastFaceStartAt = now;
                try {
                    this.sample(now);
                } finally {
                    this.inferenceRunning = false;
                    window.WellHabitVisionTiming.faceRunning = false;
                    window.WellHabitVisionTiming.lastFaceEndAt = performance.now();
                }
                scheduleNext();
            };
            scheduleNext();
        }

        recordDetectCost(detectMs, sampleMs) {
            if (Number.isFinite(detectMs)) {
                this.detectDurationMs = this.detectDurationMs ? (this.detectDurationMs * 0.82 + detectMs * 0.18) : detectMs;
                if (this.detectDurationMs > DETECT_SLOW_MS) {
                    // Keep face signals high-frequency. Slow machines may stretch slightly,
                    // but blink/PERCLOS/yawn/gaze should not collapse to 2–3 FPS.
                    this.adaptiveSampleInterval = Math.min(MAX_INFERENCE_INTERVAL_MS, this.adaptiveSampleInterval + 10);
                } else if (this.detectDurationMs < DETECT_FAST_MS) {
                    this.adaptiveSampleInterval = Math.max(MIN_INFERENCE_INTERVAL_MS, this.adaptiveSampleInterval - 8);
                }
            }
            if (Number.isFinite(sampleMs)) {
                this.sampleDurationMs = this.sampleDurationMs ? (this.sampleDurationMs * 0.82 + sampleMs * 0.18) : sampleMs;
            }
        }

        currentPerfSnapshot(estimatedFps) {
            const settings = this.cameraSettings || getVideoTrackSettings(this.stream);
            const cameraFps = Number(settings.frameRate || 0);
            const width = Number(settings.width || this.video?.videoWidth || 0);
            const height = Number(settings.height || this.video?.videoHeight || 0);
            return {
                analysis_fps: Number((Number(estimatedFps) || 0).toFixed(1)),
                camera_frame_rate: cameraFps ? Number(cameraFps.toFixed(1)) : null,
                camera_width: width || null,
                camera_height: height || null,
                detect_ms: Number((this.detectDurationMs || 0).toFixed(1)),
                sample_ms: Number((this.sampleDurationMs || 0).toFixed(1)),
                target_interval_ms: Math.round(this.adaptiveSampleInterval),
            };
        }

        brightness() {
            if (!this.video || !this.lightCtx || !this.video.videoWidth) return 255;
            try {
                this.lightCtx.drawImage(this.video, 0, 0, this.lightCanvas.width, this.lightCanvas.height);
                const data = this.lightCtx.getImageData(0, 0, this.lightCanvas.width, this.lightCanvas.height).data;
                let total = 0;
                for (let i = 0; i < data.length; i += 4) {
                    total += (data[i] + data[i + 1] + data[i + 2]) / 3;
                }
                return total / (data.length / 4);
            } catch (error) {
                return 255;
            }
        }

        sample(now) {
            const sampleStart = performance.now();
            const elapsed = now - this.startedAt;
            const light = this.brightness();
            if (light < 35) {
                this.updateFatigueStatus('idle', 'Paused', 'Light is too dark, so WellHabit is not guessing.');
                this.setStatus('Light is too dark, so fatigue detection is paused instead of guessing.');
                return;
            }

            let result;
            let detectMs = 0;
            try {
                const detectStart = performance.now();
                result = this.faceLandmarker.detectForVideo(this.video, now);
                detectMs = performance.now() - detectStart;
                this.recordDetectCost(detectMs, performance.now() - sampleStart);
            } catch (error) {
                this.recordDetectCost(detectMs, performance.now() - sampleStart);
                this.setStatus('Face detection skipped this frame; camera preview stays on.');
                return;
            }
            this.sampleTimestamps.push(now);
            this.sampleTimestamps = this.sampleTimestamps.filter((ts) => now - ts <= 3000);
            const estimatedFps = this.sampleTimestamps.length / 3;
            const lowFpsBestEffort = elapsed > 3500 && estimatedFps < LOW_FPS_BEST_EFFORT_THRESHOLD;
            const perf = this.currentPerfSnapshot(estimatedFps);
            const perfTitle = `Face pipeline: target 10–15/s for blink, PERCLOS, yawn, gaze, and head posture. Camera request: 480×360 preferred. Actual capture: ${perf.camera_width || '?'}×${perf.camera_height || '?'} at ${perf.camera_frame_rate || '?'} fps. Face inference: ${perf.analysis_fps}/s, detect ${perf.detect_ms} ms, interval ${perf.target_interval_ms} ms.`;
            const perfState = perf.analysis_fps < LOW_FPS_SEVERE_THRESHOLD ? 'warning' : (lowFpsBestEffort ? 'neutral' : 'normal');
            this.updateMetric('fps', `Face ${perf.analysis_fps}/s · ${perf.detect_ms}ms`, perfState, perfTitle);

            const landmarks = result?.faceLandmarks?.[0];
            if (!landmarks) {
                this.updateFatigueStatus('idle', 'Away from camera', 'No face is detected. This is treated as away, not fatigue.');
                this.setStatus('No face detected. This is treated as away from camera, not fatigue.');
                return;
            }

            const blend = categoryMap(result);
            const blinkBlend = average([blend.eyeBlinkLeft, blend.eyeBlinkRight], 0);
            const leftEar = eyeAspectRatio(landmarks, [33, 160, 158, 133, 153, 144]);
            const rightEar = eyeAspectRatio(landmarks, [362, 385, 387, 263, 373, 380]);
            const ear = average([leftEar, rightEar], 0);
            const mar = mouthAspectRatio(landmarks);
            const jawOpen = Number(blend.jawOpen || 0);
            const smileBlend = average([blend.mouthSmileLeft, blend.mouthSmileRight], 0);
            const browInnerUp = Number(blend.browInnerUp || 0);
            const gazeDown = average([blend.eyeLookDownLeft, blend.eyeLookDownRight], 0);
            const head = matrixToEulerDegrees(result?.facialTransformationMatrixes?.[0]);

            if (elapsed <= EYE_BASELINE_MS) {
                this.eyeBaseline.push(blinkBlend);
                if (ear > 0) this.earBaseline.push(ear);
                if (mar > 0) this.marBaseline.push(mar);
            }
            if (elapsed <= HEAD_BASELINE_MS) {
                this.pitchBaseline.push(head.pitch);
                this.yawBaseline.push(head.yaw);
                this.rollBaseline.push(head.roll);
            }

            const eyeCalibrated = this.eyeBaseline.length >= 12 && elapsed > Math.min(EYE_BASELINE_MS, 3500);
            const headCalibrated = this.pitchBaseline.length >= 12 && elapsed > Math.min(HEAD_BASELINE_MS, 5000);
            const baselinePitch = median(this.pitchBaseline, head.pitch);
            const baselineYaw = median(this.yawBaseline, head.yaw);
            const pitchDelta = head.pitch - baselinePitch;
            const yawDelta = head.yaw - baselineYaw;
            if (headCalibrated && (Math.abs(pitchDelta) > 25 || Math.abs(yawDelta) > 25)) {
                this.updateFatigueStatus('idle', 'Paused', 'Your face angle is too far from baseline for reliable eye/mouth geometry.');
                this.setStatus('Face angle is outside the reliable range, so this frame is ignored.');
                return;
            }

            const blinkThreshold = clamp(median(this.eyeBaseline, 0.18) + 0.35, 0.35, 0.85);
            const earMean = median(this.earBaseline, 0.25);
            const earSigma = stddev(this.earBaseline) || 0.025;
            const earClosedThreshold = clamp(earMean - 2 * earSigma, 0.08, 0.23);
            const earBlinkThreshold = clamp(earMean - 1.5 * earSigma, 0.10, 0.25);
            const marThreshold = clamp(Math.max(0.58, median(this.marBaseline, 0.35) * 1.65), 0.52, 0.82);
            const closedByBlend = blinkBlend >= blinkThreshold;
            const closedByEar = ear > 0 && ear <= earClosedThreshold;
            const blinkDip = ear > 0 && ear <= earBlinkThreshold;
            const closed = eyeCalibrated ? (closedByBlend && closedByEar) : (blinkBlend > 0.55 && ear > 0 && ear < 0.16);

            if (closed && !this.closedSince) this.closedSince = now;
            const closeDuration = this.closedSince ? now - this.closedSince : 0;
            if (!closed && this.closedSince) {
                if (closeDuration >= 80 && closeDuration <= 400 && blinkDip) this.blinkEvents.push(now);
                this.closedSince = null;
            }
            this.lastClosed = closed;
            this.blinkEvents = this.blinkEvents.filter((ts) => now - ts <= 60000);
            const microSleep = Boolean(this.closedSince && now - this.closedSince >= 400);

            if (gazeDown > 0.40) {
                if (!this.gazeDownSince) this.gazeDownSince = now;
            } else {
                this.gazeDownSince = null;
            }
            const sustainedGazeDown = Boolean(this.gazeDownSince && now - this.gazeDownSince >= 3000);

            const sample = { ts: now, closed, blinkBlend, ear, mar, jawOpen, smileBlend, browInnerUp, gazeDown, pitch: head.pitch, yaw: head.yaw, roll: head.roll, light };
            this.samples.push(sample);
            this.samples = this.samples.filter((item) => now - item.ts <= PERCLOS_WINDOW_MS);
            this.pitchHistory.push({ ts: now, pitch: head.pitch, yaw: head.yaw });
            this.pitchHistory = this.pitchHistory.filter((item) => now - item.ts <= 30000);
            this.marHistory.push({ ts: now, mar });
            this.marHistory = this.marHistory.filter((item) => now - item.ts <= 2500);
            this.detectYawn(now, mar, jawOpen, closed, marThreshold);
            this.yawnEvents = this.yawnEvents.filter((ts) => now - ts <= YAWN_WINDOW_MS);

            const perclos = this.samples.length ? this.samples.filter((item) => item.closed).length / this.samples.length : 0;
            const recentPitch10 = this.pitchHistory.filter((item) => now - item.ts <= 10000).map((item) => item.pitch);
            const recentYaw10 = this.pitchHistory.filter((item) => now - item.ts <= 10000).map((item) => item.yaw);
            const lowHead = headCalibrated && Math.abs(pitchDelta) > 15 && sustainedGazeDown;
            const nodding = this.detectNodding(now, baselinePitch);
            const poseVariance = headCalibrated && this.pitchHistory.length > 10 && (
                stddev(recentPitch10) > Math.max(8, stddev(this.pitchBaseline) * 2.0) ||
                stddev(recentYaw10) > Math.max(8, stddev(this.yawBaseline) * 2.0)
            );
            const onScreenSamples = this.samples.filter((item) => now - item.ts <= 30000);
            const onScreenRatio = onScreenSamples.length ? onScreenSamples.filter((item) => item.gazeDown <= 0.35).length / onScreenSamples.length : 1;

            const blinkRate = this.blinkEvents.length;
            const perclosScore = clamp(perclos / 0.30, 0, 1);
            const microsleepScore = microSleep ? 1 : 0;
            const blinkAnomalyScore = clamp(Math.max(0, 8 - blinkRate, blinkRate - 28) / 18, 0, 1);
            const yawnScore = clamp(this.yawnEvents.length / 3, 0, 1);
            const headScore = (lowHead || nodding) ? 1 : (poseVariance ? 0.45 : 0);
            const gazeScore = sustainedGazeDown ? 1 : (onScreenRatio < 0.60 ? 0.65 : 0);
            const evidenceFlags = [
                perclosScore >= 0.45,
                microsleepScore >= 1,
                blinkAnomalyScore >= 0.70,
                yawnScore >= 0.70,
                headScore >= 0.70,
                gazeScore >= 0.70,
            ];
            const evidenceHighCount = evidenceFlags.filter(Boolean).length;
            const supportingSignalHigh = blinkAnomalyScore >= 0.70 || yawnScore >= 0.70 || headScore >= 0.70 || gazeScore >= 0.70;
            let rawScore = 0.40 * perclosScore
                + 0.25 * microsleepScore
                + 0.15 * blinkAnomalyScore
                + 0.10 * yawnScore
                + 0.05 * headScore
                + 0.05 * gazeScore;
            if (microSleep) rawScore = Math.max(rawScore, 0.95);
            if (evidenceHighCount >= 3) rawScore = Math.max(rawScore, 0.70);
            if (!microSleep && evidenceHighCount < 3 && !(perclosScore >= 0.45 && supportingSignalHigh)) {
                rawScore = Math.min(rawScore, 0.29);
            }
            this.emaScore = this.emaScore ? (this.emaScore * 0.86 + rawScore * 0.14) : rawScore;
            const score = clamp(this.emaScore, 0, 1);
            const relaxedCandidate = eyeCalibrated && perclos < 0.08 && score < 0.40 && !microSleep && smileBlend > 0.15 && browInnerUp < 0.35;
            if (relaxedCandidate) {
                if (!this.relaxedAffectSince) this.relaxedAffectSince = now;
            } else {
                this.relaxedAffectSince = null;
            }
            const sustainedRelaxedAffect = Boolean(this.relaxedAffectSince && now - this.relaxedAffectSince >= RELAXED_AFFECT_SUSTAIN_MS);

            const payload = {
                fatigue_score: Number(score.toFixed(3)),
                raw_score: Number(rawScore.toFixed(3)),
                perclos: Number(perclos.toFixed(3)),
                perclos_score: Number(perclosScore.toFixed(3)),
                blink_rate_per_min: blinkRate,
                blink_anomaly_score: Number(blinkAnomalyScore.toFixed(3)),
                eye_closed: closed,
                microsleep: microSleep,
                microsleep_score: microsleepScore,
                yawn_count_10m: this.yawnEvents.length,
                yawn_score: Number(yawnScore.toFixed(3)),
                jaw_open: Number(jawOpen.toFixed(3)),
                mar: Number(mar.toFixed(3)),
                smile_blendshape: Number(smileBlend.toFixed(3)),
                brow_inner_up: Number(browInnerUp.toFixed(3)),
                possible_positive_affect_signal: sustainedRelaxedAffect,
                pitch_delta: Number(pitchDelta.toFixed(2)),
                yaw_delta: Number(yawDelta.toFixed(2)),
                nodding,
                head_score: Number(headScore.toFixed(3)),
                gaze_down: Number(gazeDown.toFixed(3)),
                sustained_gaze_down: sustainedGazeDown,
                gaze_on_screen_ratio: Number(onScreenRatio.toFixed(3)),
                gaze_score: Number(gazeScore.toFixed(3)),
                evidence_high_count: evidenceHighCount,
                brightness: Math.round(light),
                estimated_fps: perf.analysis_fps,
                analysis_fps: perf.analysis_fps,
                camera_frame_rate: perf.camera_frame_rate,
                camera_width: perf.camera_width,
                camera_height: perf.camera_height,
                detect_ms: perf.detect_ms,
                sample_ms: perf.sample_ms,
                target_interval_ms: perf.target_interval_ms,
                low_fps_best_effort: lowFpsBestEffort,
                calibrated_eye: eyeCalibrated,
                calibrated_head: headCalibrated,
            };
            this.lastPayload = payload;
            this.renderMetrics(payload, score, elapsed, eyeCalibrated, headCalibrated);
            this.maybeReportPositiveAffect(now, payload, sustainedRelaxedAffect);
            this.maybeAlert(now, score, microSleep, payload);
        }

        maybeReportPositiveAffect(now, payload, sustainedRelaxedAffect) {
            if (!sustainedRelaxedAffect) return;
            if (now - this.lastRelaxedAffectEventAt < RELAXED_AFFECT_COOLDOWN_MS) return;
            this.lastRelaxedAffectEventAt = now;
            const snapshot = Object.assign({}, payload, {
                possible_positive_affect_signal: true,
                inference_label: 'possible_relaxed_affect',
            });
            document.dispatchEvent(new CustomEvent('wellhabit:possible-relaxed-affect', {
                detail: {
                    source: 'pomodoro_camera',
                    event_type: 'possible_relaxed_affect',
                    metrics: snapshot,
                },
            }));
            this.reportEvent('possible_relaxed_affect', snapshot, false);
        }

        detectYawn(now, mar, jawOpen, closed, marThreshold) {
            const mouthHigh = jawOpen > 0.60 && mar > marThreshold;
            if (mouthHigh && !this.yawnCandidate) this.yawnCandidate = { start: now, maxMar: mar, hadEyeEvidence: closed };
            if (mouthHigh && this.yawnCandidate) {
                this.yawnCandidate.maxMar = Math.max(this.yawnCandidate.maxMar, mar);
                this.yawnCandidate.hadEyeEvidence = this.yawnCandidate.hadEyeEvidence || closed;
            }
            if (!mouthHigh && this.yawnCandidate) {
                const duration = now - this.yawnCandidate.start;
                const windowMars = this.marHistory.map((item) => item.mar);
                const steadyEnough = stddev(windowMars) < 0.18;
                if (duration >= 3000 && duration <= 6500 && steadyEnough && (this.yawnCandidate.hadEyeEvidence || this.yawnCandidate.maxMar > 0.70)) {
                    const lastYawn = this.yawnEvents[this.yawnEvents.length - 1] || 0;
                    if (now - lastYawn > 8000) this.yawnEvents.push(now);
                }
                this.yawnCandidate = null;
            }
        }

        detectNodding(now, baselinePitch) {
            const recent = this.pitchHistory.filter((item) => now - item.ts <= 5000);
            if (recent.length < 8) return false;
            let minItem = recent[0];
            for (const item of recent) {
                if (item.pitch < minItem.pitch) minItem = item;
            }
            const first = recent[0];
            const last = recent[recent.length - 1];
            const slowDrop = first.pitch - minItem.pitch > 15 && Math.abs(minItem.pitch - baselinePitch) > 15;
            const quickRecovery = last.pitch - minItem.pitch > 10 && now - minItem.ts < 2200;
            return Boolean(slowDrop && quickRecovery);
        }

        metricState(score) {
            if (score >= 0.70) return 'danger';
            if (score >= 0.40) return 'warning';
            return 'normal';
        }

        renderMetrics(payload, score, elapsed, eyeCalibrated, headCalibrated) {
            this.updateScoreUi(score);
            this.updateMetric('perclos', `${Math.round(payload.perclos * 100)}% · ${payload.perclos_score >= 0.45 ? 'elevated' : 'normal'}`, this.metricState(payload.perclos_score));
            this.updateMetric('blink', `${payload.blink_rate_per_min}/min · ${payload.blink_anomaly_score >= 0.70 ? 'unusual' : 'normal'}`, this.metricState(payload.blink_anomaly_score));
            this.updateMetric('yawn', `${payload.yawn_count_10m} in last 10 min`, this.metricState(payload.yawn_score));
            this.updateMetric('head', payload.nodding ? 'nodding signal' : (payload.head_score >= 0.70 ? 'tilted' : 'stable'), this.metricState(payload.head_score));
            this.updateMetric('gaze', `on screen ${Math.round(payload.gaze_on_screen_ratio * 100)}%`, this.metricState(payload.gaze_score));
            const fpsState = payload.estimated_fps < LOW_FPS_SEVERE_THRESHOLD ? 'warning' : (payload.low_fps_best_effort ? 'neutral' : 'normal');
            const payloadPerfTitle = `Face pipeline: target 10–15/s for blink, PERCLOS, yawn, gaze, and head posture. Camera request: 480×360 preferred. Actual capture: ${payload.camera_width || '?'}×${payload.camera_height || '?'} at ${payload.camera_frame_rate || '?'} fps. Face inference: ${payload.analysis_fps}/s, detect ${payload.detect_ms} ms, interval ${payload.target_interval_ms} ms.`;
            this.updateMetric('fps', `Face ${payload.analysis_fps}/s · ${payload.detect_ms}ms`, fpsState, payloadPerfTitle);

            if (!eyeCalibrated || !headCalibrated) {
                const seconds = Math.max(0, Math.ceil((HEAD_BASELINE_MS - elapsed) / 1000));
                this.updateFatigueStatus('calibrating', 'Calibrating', `Learning your baseline (${seconds}s).`);
                this.setStatus(`Calibrating personal baseline (${seconds}s).`);
            } else if (score >= 0.60 || payload.microsleep) {
                this.updateFatigueStatus('danger', 'Take a break?', 'Several signals line up. A short break is the safer default.');
                this.setStatus('Possible strong fatigue signal. Confirm if you want to enter break early.');
            } else if (score >= 0.30) {
                this.updateFatigueStatus('warning', 'Getting tired', 'Try one slow breath, relax your shoulders, and keep the task simple.');
                this.setStatus('Possible mild fatigue signal. Try one slow breath and relax your shoulders.');
            } else if (payload.low_fps_best_effort) {
                this.updateFatigueStatus('normal', 'Best effort', 'Camera stays visible. Face analysis is still prioritized for fast fatigue signals.');
                this.setStatus('Best effort mode: camera preview stays on; face analysis keeps the fastest safe rate.');
            } else {
                this.updateFatigueStatus('normal', 'Alert', '');
                this.setStatus('Camera fatigue monitor is active. Frames are analyzed locally and not stored.');
            }
        }

        isInBreakCooldown() {
            const until = Number(window.WellHabitFatigueCooldownUntil || 0);
            return Number.isFinite(until) && Date.now() < until;
        }

        maybeAlert(now, score, microSleep, payload) {
            const externalCooldown = this.isInBreakCooldown();
            const localCooldown = now < this.alertCooldownUntil;
            const shouldAlert = Boolean(microSleep || score >= 0.60 || (payload?.evidence_high_count || 0) >= 3);
            const shouldNudge = !shouldAlert && score >= 0.30;

            if (!shouldAlert && this.inAlertState) {
                this.inAlertState = false;
                this.alertCooldownUntil = now + ALERT_EXIT_COOLDOWN_MS;
            }

            if (externalCooldown || localCooldown) {
                this.heavySince = null;
                return;
            }

            if (microSleep) {
                this.reportEvent('microsleep', payload, true);
            } else if (shouldAlert && this.lastFatigueBand !== 'alert') {
                this.reportEvent('heavy_signal', payload, true);
            } else if (shouldNudge && this.lastFatigueBand === 'normal') {
                this.reportEvent('mild_signal', payload, false);
            }

            if (shouldNudge && now - this.lastLightAlertAt > LIGHT_ALERT_COOLDOWN_MS) {
                this.lastLightAlertAt = now;
                this.showToast('Getting tired: try one slow breath and relax your shoulders.');
            }

            if (shouldAlert) {
                this.inAlertState = true;
                if (!this.heavySince) this.heavySince = now;
                if (microSleep || now - this.heavySince >= HEAVY_SUSTAIN_MS) this.showBreakModal();
                this.lastFatigueBand = 'alert';
                return;
            }

            this.heavySince = null;
            this.lastFatigueBand = shouldNudge ? 'drowsy' : 'normal';
        }

        showToast(message) {
            if (!this.toast) return;
            this.toast.textContent = message;
            this.toast.hidden = false;
            window.clearTimeout(this.toastTimer);
            this.toastTimer = window.setTimeout(() => {
                if (this.toast) this.toast.hidden = true;
            }, 6500);
        }

        showBreakModal() {
            if (this.modal) this.modal.hidden = false;
        }

        hideBreakModal() {
            if (this.modal) this.modal.hidden = true;
        }

        canReportEvent(eventType, now) {
            const last = this.lastReportByType[eventType] || 0;
            if (!last) return true;
            const cooldown = REPORT_COOLDOWNS_MS[eventType] ?? (5 * 60 * 1000);
            if (cooldown === Infinity) return false;
            return now - last >= cooldown;
        }

        async reportEvent(eventType, payload, important) {
            if (!this.reportUrl) return;
            const now = performance.now();
            if (!this.canReportEvent(eventType, now)) return;
            this.lastReportByType[eventType] = now;
            const timerState = window.WellHabitTimer?.getState ? window.WellHabitTimer.getState() : null;
            try {
                await fetch(this.reportUrl, {
                    method: 'POST',
                    headers: window.WellHabitCsrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        event_type: eventType,
                        source: 'pomodoro_camera',
                        metrics: payload || {},
                        timer: timerState ? {
                            mode: timerState.mode,
                            cycle_number: timerState.cycleNumber,
                            activity_label: timerState.activityLabel,
                            remaining_seconds: window.WellHabitTimer?.getRemainingSeconds ? window.WellHabitTimer.getRemainingSeconds(timerState) : timerState.remainingSeconds,
                        } : {},
                    }),
                    keepalive: Boolean(important),
                });
            } catch (error) {
                // Camera support must never interrupt Pomodoro.
            }
        }
    }

    function registerMediaPipeCacheWorker() {
        if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
        navigator.serviceWorker.register('/wellhabit-sw.js').catch(() => {});
    }

    function init() {
        if (!document.getElementById('fatigue-monitor-panel')) return;
        registerMediaPipeCacheWorker();
        window.WellHabitFatigueMonitor = new FatigueMonitor();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
