(function () {
    const DEFAULT_MODULE_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';
    const DEFAULT_WASM_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm';
    const DEFAULT_MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task';

    const SAMPLE_INTERVAL_MS = 150;
    const EYE_BASELINE_MS = 10000;
    const HEAD_BASELINE_MS = 30000;
    const PERCLOS_WINDOW_MS = 60000;
    const YAWN_WINDOW_MS = 10 * 60 * 1000;
    const HEAVY_SUSTAIN_MS = 5000;
    const LIGHT_ALERT_COOLDOWN_MS = 3 * 60 * 1000;
    const REPORT_COOLDOWNS_MS = {
        microsleep: 30 * 1000,
        mild_signal: 5 * 60 * 1000,
        heavy_signal: 3 * 60 * 1000,
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

    class FatigueMonitor {
        constructor() {
            this.video = document.getElementById('focus-camera-video');
            this.placeholder = document.getElementById('focus-camera-placeholder');
            this.startBtn = document.getElementById('camera-start-btn');
            this.stopBtn = document.getElementById('camera-stop-btn');
            this.statusLine = document.getElementById('camera-status-line');
            this.scoreText = document.getElementById('fatigue-score-text');
            this.scoreBar = document.getElementById('fatigue-score-bar');
            this.metricEls = {
                perclos: document.getElementById('fatigue-metric-perclos'),
                blink: document.getElementById('fatigue-metric-blink'),
                yawn: document.getElementById('fatigue-metric-yawn'),
                head: document.getElementById('fatigue-metric-head'),
                gaze: document.getElementById('fatigue-metric-gaze'),
            };
            this.toast = document.getElementById('fatigue-toast');
            this.modal = document.getElementById('fatigue-break-overlay');
            this.restBtn = document.getElementById('fatigue-rest-btn');
            this.keepBtn = document.getElementById('fatigue-keep-btn');
            this.reportUrl = document.getElementById('fatigue-monitor-panel')?.dataset.reportUrl || '/api/pomodoro/fatigue';
            this.moduleUrl = document.getElementById('fatigue-monitor-panel')?.dataset.moduleUrl || DEFAULT_MODULE_URL;
            this.wasmUrl = document.getElementById('fatigue-monitor-panel')?.dataset.wasmUrl || DEFAULT_WASM_URL;
            this.modelUrl = document.getElementById('fatigue-monitor-panel')?.dataset.modelUrl || DEFAULT_MODEL_URL;
            this.stream = null;
            this.faceLandmarker = null;
            this.loadingModel = null;
            this.rafId = null;
            this.lastSampleAt = 0;
            this.startedAt = 0;
            this.samples = [];
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
            this.pausedForVisibility = false;
            this.emaScore = 0;
            this.yawnCandidate = null;
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
            this.startBtn?.addEventListener('click', () => this.startFromUserGesture());
            this.stopBtn?.addEventListener('click', () => this.stop('Camera preview is off.'));
            this.restBtn?.addEventListener('click', () => {
                this.hideBreakModal();
                this.reportEvent('break_confirmed', Object.assign({}, this.lastPayload || {}, { user_confirmed_break: true }), true);
                if (window.WellHabitTimer?.skipToBreak) {
                    window.WellHabitTimer.skipToBreak('Break started early because of a possible fatigue signal.');
                }
                this.stop('Break started. Camera released.');
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
            if (!state || state.mode !== 'focus' || !state.isRunning) {
                if (this.stream) this.stop(state?.mode === 'break' ? 'Break mode. Camera released.' : 'Timer paused. Camera released.');
            }
        }

        setCameraRunning(isRunning) {
            if (this.video) this.video.hidden = !isRunning;
            if (this.placeholder) this.placeholder.hidden = isRunning;
            if (this.startBtn) this.startBtn.hidden = isRunning;
            if (this.stopBtn) this.stopBtn.hidden = !isRunning;
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
        }

        renderIdle() {
            this.updateScoreUi(0);
            this.updateMetric('perclos', 'PERCLOS: --');
            this.updateMetric('blink', 'Blink: --');
            this.updateMetric('yawn', 'Yawns: --');
            this.updateMetric('head', 'Head: --');
            this.updateMetric('gaze', 'Gaze: --');
        }

        updateScoreUi(score) {
            const clean = clamp(score, 0, 1);
            if (this.scoreText) this.scoreText.textContent = `${Math.round(clean * 100)}%`;
            if (this.scoreBar) this.scoreBar.style.width = `${Math.round(clean * 100)}%`;
        }

        updateMetric(key, text) {
            if (this.metricEls[key]) this.metricEls[key].textContent = text;
        }

        async startFromUserGesture() {
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
                return 'Camera started, but the MediaPipe JavaScript module could not load. This is usually a CDN/network block. Timer still works normally.';
            }
            if (message.includes('MEDIAPIPE_WASM_LOAD_FAILED')) {
                return 'Camera started, but the MediaPipe WASM files could not load. Check CDN access or host the files locally under static/vendor/.';
            }
            if (message.includes('MEDIAPIPE_MODEL_LOAD_FAILED')) {
                return 'Camera started, but the Face Landmarker model could not load. Check network access to the model file or host it locally.';
            }
            return 'Camera started, but the face model failed to initialize. Timer still works normally.';
        }

        async start() {
            if (!this.video) return;
            if (this.stream) return;
            if (!isSecureEnoughForCamera()) {
                this.setStatus('Camera requires HTTPS or localhost. Timer can still run without camera signals.');
                return;
            }
            if (!navigator.mediaDevices?.getUserMedia) {
                this.setStatus('This browser does not support camera access. Timer can still run without camera signals.');
                return;
            }

            this.setStatus('Requesting camera permission...');
            try {
                this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
            } catch (error) {
                this.stop(this.cameraErrorMessage(error));
                return;
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
                this.stop(this.modelErrorMessage(error));
                return;
            }

            this.setStatus('Calibrating your normal eye/head baseline. Keep working normally for a few seconds.');
            this.loop();
        }

        stop(message) {
            if (this.rafId) window.cancelAnimationFrame(this.rafId);
            this.rafId = null;
            if (this.stream) {
                this.stream.getTracks().forEach((track) => track.stop());
                this.stream = null;
            }
            this.pausedForVisibility = false;
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
            this.samples = [];
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
            this.yawnCandidate = null;
            this.lastPayload = null;
            if (resetScore) this.emaScore = 0;
        }

        async loadModel() {
            if (this.faceLandmarker) return this.faceLandmarker;
            if (this.loadingModel) return this.loadingModel;
            this.loadingModel = (async () => {
                let vision;
                try {
                    vision = await import(this.moduleUrl);
                } catch (error) {
                    throw new Error(`MEDIAPIPE_MODULE_LOAD_FAILED: ${error?.message || error}`);
                }

                let fileset;
                try {
                    fileset = await vision.FilesetResolver.forVisionTasks(this.wasmUrl);
                } catch (error) {
                    throw new Error(`MEDIAPIPE_WASM_LOAD_FAILED: ${error?.message || error}`);
                }

                try {
                    return await vision.FaceLandmarker.createFromOptions(fileset, {
                        baseOptions: {
                            modelAssetPath: this.modelUrl,
                            delegate: 'GPU',
                        },
                        runningMode: 'VIDEO',
                        numFaces: 1,
                        outputFaceBlendshapes: true,
                        outputFacialTransformationMatrixes: true,
                    });
                } catch (gpuError) {
                    try {
                        return await vision.FaceLandmarker.createFromOptions(fileset, {
                            baseOptions: { modelAssetPath: this.modelUrl, delegate: 'CPU' },
                            runningMode: 'VIDEO',
                            numFaces: 1,
                            outputFaceBlendshapes: true,
                            outputFacialTransformationMatrixes: true,
                        });
                    } catch (cpuError) {
                        throw new Error(`MEDIAPIPE_MODEL_LOAD_FAILED: ${cpuError?.message || gpuError?.message || cpuError}`);
                    }
                }
            })().then((landmarker) => {
                this.faceLandmarker = landmarker;
                return landmarker;
            }).finally(() => {
                this.loadingModel = null;
            });
            return this.loadingModel;
        }

        loop() {
            this.rafId = window.requestAnimationFrame((now) => {
                if (!this.stream || !this.faceLandmarker) return;
                if (this.pausedForVisibility) {
                    this.loop();
                    return;
                }
                if (now - this.lastSampleAt >= SAMPLE_INTERVAL_MS) {
                    this.lastSampleAt = now;
                    this.sample(now);
                }
                this.loop();
            });
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
            const light = this.brightness();
            if (light < 35) {
                this.setStatus('Light is too dark, so fatigue detection is paused instead of guessing.');
                return;
            }
            let result;
            try {
                result = this.faceLandmarker.detectForVideo(this.video, now);
            } catch (error) {
                this.setStatus('Face detection paused for this frame.');
                return;
            }
            const landmarks = result?.faceLandmarks?.[0];
            if (!landmarks) {
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
            const gazeDown = average([blend.eyeLookDownLeft, blend.eyeLookDownRight], 0);
            const head = matrixToEulerDegrees(result?.facialTransformationMatrixes?.[0]);
            const elapsed = now - this.startedAt;

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
            const blinkThreshold = clamp(median(this.eyeBaseline, 0.18) + 0.35, 0.35, 0.85);
            const earThreshold = clamp(median(this.earBaseline, 0.25) * 0.62, 0.08, 0.23);
            const marThreshold = clamp(Math.max(0.58, median(this.marBaseline, 0.35) * 1.65), 0.52, 0.82);
            const closedByBlend = blinkBlend >= blinkThreshold;
            const closedByEar = ear > 0 && ear <= earThreshold;
            const closed = eyeCalibrated ? (closedByBlend && closedByEar) : (blinkBlend > 0.55 && ear > 0 && ear < 0.16);

            if (closed && !this.closedSince) this.closedSince = now;
            if (!closed && this.closedSince) this.closedSince = null;
            if (!closed && this.lastClosed) this.blinkEvents.push(now);
            this.lastClosed = closed;
            this.blinkEvents = this.blinkEvents.filter((ts) => now - ts <= 60000);
            const microSleep = Boolean(this.closedSince && now - this.closedSince >= 400);

            if (gazeDown > 0.40) {
                if (!this.gazeDownSince) this.gazeDownSince = now;
            } else {
                this.gazeDownSince = null;
            }
            const sustainedGazeDown = Boolean(this.gazeDownSince && now - this.gazeDownSince >= 3000);

            const sample = { ts: now, closed, blinkBlend, ear, mar, jawOpen, gazeDown, pitch: head.pitch, yaw: head.yaw, roll: head.roll, light };
            this.samples.push(sample);
            this.samples = this.samples.filter((item) => now - item.ts <= PERCLOS_WINDOW_MS);
            this.pitchHistory.push({ ts: now, pitch: head.pitch, yaw: head.yaw });
            this.pitchHistory = this.pitchHistory.filter((item) => now - item.ts <= 30000);
            this.marHistory.push({ ts: now, mar });
            this.marHistory = this.marHistory.filter((item) => now - item.ts <= 2500);
            this.detectYawn(now, mar, jawOpen, closed, marThreshold);
            this.yawnEvents = this.yawnEvents.filter((ts) => now - ts <= YAWN_WINDOW_MS);

            const perclos = this.samples.length ? this.samples.filter((item) => item.closed).length / this.samples.length : 0;
            const baselinePitch = median(this.pitchBaseline, head.pitch);
            const recentPitch10 = this.pitchHistory.filter((item) => now - item.ts <= 10000).map((item) => item.pitch);
            const recentYaw10 = this.pitchHistory.filter((item) => now - item.ts <= 10000).map((item) => item.yaw);
            const lowHead = headCalibrated && Math.abs(head.pitch - baselinePitch) > 15 && sustainedGazeDown;
            const nodding = this.detectNodding(now, baselinePitch);
            const poseVariance = headCalibrated && this.pitchHistory.length > 10 && (
                stddev(recentPitch10) > Math.max(8, stddev(this.pitchBaseline) * 2.0) ||
                stddev(recentYaw10) > Math.max(8, stddev(this.yawBaseline) * 2.0)
            );
            const yawnScore = clamp(this.yawnEvents.length / 3, 0, 1);
            const perclosScore = clamp(perclos / 0.15, 0, 1);
            const headScore = (lowHead || nodding) ? 1 : (poseVariance ? 0.45 : 0);
            const gazeScore = sustainedGazeDown ? 1 : (gazeDown > 0.35 ? 0.35 : 0);
            let rawScore = 0.45 * perclosScore + 0.25 * headScore + 0.20 * yawnScore + 0.10 * gazeScore;
            if (microSleep) rawScore = Math.max(rawScore, 0.95);
            this.emaScore = this.emaScore ? (this.emaScore * 0.90 + rawScore * 0.10) : rawScore;
            const score = clamp(this.emaScore, 0, 1);

            const payload = {
                fatigue_score: Number(score.toFixed(3)),
                raw_score: Number(rawScore.toFixed(3)),
                perclos: Number(perclos.toFixed(3)),
                blink_rate_per_min: this.blinkEvents.length,
                eye_closed: closed,
                microsleep: microSleep,
                yawn_count_10m: this.yawnEvents.length,
                jaw_open: Number(jawOpen.toFixed(3)),
                mar: Number(mar.toFixed(3)),
                pitch_delta: Number((head.pitch - baselinePitch).toFixed(2)),
                nodding,
                gaze_down: Number(gazeDown.toFixed(3)),
                sustained_gaze_down: sustainedGazeDown,
                brightness: Math.round(light),
                calibrated_eye: eyeCalibrated,
                calibrated_head: headCalibrated,
            };
            this.lastPayload = payload;
            this.renderMetrics(payload, score, elapsed, eyeCalibrated, headCalibrated);
            this.maybeAlert(now, score, microSleep, payload);
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
                if (duration >= 1500 && duration <= 6500 && steadyEnough && (this.yawnCandidate.hadEyeEvidence || this.yawnCandidate.maxMar > 0.70)) {
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
            const slowDrop = first.pitch - minItem.pitch > 8 && Math.abs(minItem.pitch - baselinePitch) > 10;
            const quickRecovery = last.pitch - minItem.pitch > 8 && now - minItem.ts < 1800;
            return Boolean(slowDrop && quickRecovery);
        }

        renderMetrics(payload, score, elapsed, eyeCalibrated, headCalibrated) {
            this.updateScoreUi(score);
            this.updateMetric('perclos', `PERCLOS: ${Math.round(payload.perclos * 100)}%`);
            this.updateMetric('blink', `Blink: ${payload.blink_rate_per_min}/min${payload.microsleep ? ' · long close' : ''}`);
            this.updateMetric('yawn', `Yawns: ${payload.yawn_count_10m}/10min`);
            this.updateMetric('head', `Head: ${payload.nodding ? 'nodding signal' : `${payload.pitch_delta}°`}`);
            this.updateMetric('gaze', `Gaze: ${payload.sustained_gaze_down ? 'down sustained' : 'normal/weak'}`);
            if (!eyeCalibrated || !headCalibrated) {
                const seconds = Math.max(0, Math.ceil((HEAD_BASELINE_MS - elapsed) / 1000));
                this.setStatus(`Calibrating personal baseline (${seconds}s). This is habit support, not medical advice.`);
            } else if (score >= 0.7) {
                this.setStatus('Possible strong fatigue signal. Confirm if you want to enter break early.');
            } else if (score >= 0.5) {
                this.setStatus('Possible mild fatigue signal. Try one slow breath and relax your shoulders.');
            } else {
                this.setStatus('Camera fatigue monitor is active. Frames are analyzed locally and not stored.');
            }
        }

        maybeAlert(now, score, microSleep, payload) {
            const band = microSleep ? 'microsleep' : (score >= 0.7 ? 'heavy' : (score >= 0.5 ? 'mild' : 'normal'));
            if (microSleep) {
                this.reportEvent('microsleep', payload, true);
            } else if (band === 'heavy') {
                const enteredHeavy = this.lastFatigueBand !== 'heavy';
                if (enteredHeavy || this.canReportEvent('heavy_signal', now)) {
                    this.reportEvent('heavy_signal', payload, true);
                }
            } else if (band === 'mild') {
                const enteredMild = this.lastFatigueBand === 'normal';
                if (enteredMild || this.canReportEvent('mild_signal', now)) {
                    this.reportEvent('mild_signal', payload, false);
                }
            }
            this.lastFatigueBand = band === 'microsleep' ? (score >= 0.7 ? 'heavy' : (score >= 0.5 ? 'mild' : 'normal')) : band;

            if (score >= 0.5 && score < 0.7 && now - this.lastLightAlertAt > LIGHT_ALERT_COOLDOWN_MS) {
                this.lastLightAlertAt = now;
                this.showToast('Possible fatigue signal: try one slow breath and relax your shoulders.');
            }
            if (microSleep || score >= 0.7) {
                if (!this.heavySince) this.heavySince = now;
                if (microSleep || now - this.heavySince >= HEAVY_SUSTAIN_MS) this.showBreakModal();
            } else {
                this.heavySince = null;
            }
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
