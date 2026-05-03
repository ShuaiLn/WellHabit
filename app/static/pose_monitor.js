const POSE = {
    nose: 0,
    leftShoulder: 11,
    rightShoulder: 12,
    leftElbow: 13,
    rightElbow: 14,
    leftWrist: 15,
    rightWrist: 16,
    leftHip: 23,
    rightHip: 24,
};

function dist(a, b) {
    if (!a || !b) return 0;
    return Math.hypot((a.x || 0) - (b.x || 0), (a.y || 0) - (b.y || 0));
}
function midpoint(a, b) {
    if (!a || !b) return null;
    return { x: ((a.x || 0) + (b.x || 0)) / 2, y: ((a.y || 0) + (b.y || 0)) / 2 };
}
function clamp(v, min, max) { return Math.max(min, Math.min(max, Number(v) || 0)); }
function visiblePoint(point, threshold = 0.5) { return point && (point.visibility === undefined || point.visibility >= threshold); }
function nowMs() { return performance.now(); }
function lerp(a, b, t) { return Number(a || 0) + (Number(b || 0) - Number(a || 0)) * t; }
function cloneLandmarks(landmarks) {
    return landmarks ? landmarks.map((p) => Object.assign({}, p)) : null;
}
function lerpLandmarks(from, to, t) {
    if (!from || !to || from.length !== to.length) return cloneLandmarks(to || from);
    return to.map((point, index) => {
        const prev = from[index] || point || {};
        const next = point || prev || {};
        return {
            x: lerp(prev.x, next.x, t),
            y: lerp(prev.y, next.y, t),
            z: lerp(prev.z, next.z, t),
            visibility: next.visibility ?? prev.visibility,
        };
    });
}
const POSE_DEFAULT_INTERVAL_MS = 1000;
const POSE_BOOST_INTERVAL_MS = 200;
const POSE_BASELINE_INTERVAL_MS = 500;
const POSE_INTERPOLATION_MS = 450;
const FACE_DEFER_GRACE_MS = 45;
const FACE_DEFER_RETRY_MS = 80;
const MOTION_CHECK_INTERVAL_MS = 250;
const MOTION_BOOST_DURATION_MS = 3500;
const MOTION_THRESHOLD = 18;
function hasGoodBaselineLandmarks(landmarks) {
    if (!landmarks) return false;
    const ls = landmarks[POSE.leftShoulder], rs = landmarks[POSE.rightShoulder], lh = landmarks[POSE.leftHip], rh = landmarks[POSE.rightHip], nose = landmarks[POSE.nose];
    if (![ls, rs, lh, rh, nose].every((p) => visiblePoint(p, 0.55))) return false;
    const shoulderWidth = dist(ls, rs);
    const hipWidth = dist(lh, rh);
    if (shoulderWidth < 0.08 || shoulderWidth > 0.75 || hipWidth < 0.05) return false;
    const shoulderMid = midpoint(ls, rs);
    const hipMid = midpoint(lh, rh);
    if (!shoulderMid || !hipMid) return false;
    return Math.abs(shoulderMid.x - hipMid.x) < 0.35 && hipMid.y > shoulderMid.y;
}

class PoseRenderer {
    constructor(canvas, video) {
        this.canvas = canvas;
        this.video = video;
        this.ctx = canvas?.getContext('2d');
    }
    setupCanvas() {
        if (!this.canvas || !this.video || !this.ctx) return;
        const width = this.video.videoWidth || 640;
        const height = this.video.videoHeight || 480;
        const dpr = window.devicePixelRatio || 1;
        if (this.canvas.width !== Math.round(width * dpr) || this.canvas.height !== Math.round(height * dpr)) {
            this.canvas.width = Math.round(width * dpr);
            this.canvas.height = Math.round(height * dpr);
            this.canvas.style.width = '100%';
            this.canvas.style.height = '100%';
            this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }
    }
    clear() {
        if (!this.ctx) return;
        const w = this.video?.videoWidth || 640;
        const h = this.video?.videoHeight || 480;
        this.ctx.clearRect(0, 0, w, h);
    }
    xy(point) {
        const w = this.video?.videoWidth || 640;
        const h = this.video?.videoHeight || 480;
        // The CSS mirrors both video and canvas. Do not mirror coordinates here, or the overlay flips twice.
        return { x: (point?.x || 0) * w, y: (point?.y || 0) * h };
    }
    dot(point, radius = 6) {
        if (!this.ctx || !point) return;
        const p = this.xy(point);
        this.ctx.beginPath();
        this.ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        this.ctx.fill();
    }
    line(a, b, width = 4) {
        if (!this.ctx || !a || !b) return;
        const pa = this.xy(a), pb = this.xy(b);
        this.ctx.beginPath();
        this.ctx.moveTo(pa.x, pa.y);
        this.ctx.lineTo(pb.x, pb.y);
        this.ctx.lineWidth = width;
        this.ctx.stroke();
    }
    drawGuideCircle(targetAngle, nose, shoulderWidth, following) {
        if (!this.ctx || !nose) return;
        const w = this.video?.videoWidth || 640;
        const h = this.video?.videoHeight || 480;
        const center = this.xy(nose);
        const radiusFromShoulders = (Number(shoulderWidth) || 0.22) * w * 1.2;
        const r = clamp(radiusFromShoulders, 48, Math.min(w, h) * 0.35);
        this.ctx.lineWidth = 4;
        this.ctx.strokeStyle = 'rgba(255,255,255,0.85)';
        this.ctx.beginPath();
        this.ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
        this.ctx.stroke();

        this.ctx.fillStyle = '#d95c5c';
        this.ctx.beginPath();
        this.ctx.arc(center.x + Math.cos(targetAngle) * r, center.y + Math.sin(targetAngle) * r, 9, 0, Math.PI * 2);
        this.ctx.fill();

        this.ctx.fillStyle = following ? '#5f9f68' : 'rgba(255,255,255,0.92)';
        this.dot(nose, 9);
    }
    drawCatCowCue(landmarks, evaluation) {
        if (!this.ctx || !landmarks) return;
        this.ctx.strokeStyle = 'rgba(255,255,255,0.72)';
        this.ctx.fillStyle = evaluation.score > 70 ? '#5f9f68' : 'rgba(255,255,255,0.92)';
        this.line(landmarks[POSE.leftShoulder], landmarks[POSE.rightShoulder], 5);
        this.dot(landmarks[POSE.nose], 7);
        this.dot(landmarks[POSE.leftShoulder], 7);
        this.dot(landmarks[POSE.rightShoulder], 7);
    }
    drawSkeleton(landmarks, evaluation) {
        if (!this.ctx || !landmarks) return;
        this.ctx.strokeStyle = 'rgba(255,255,255,0.85)';
        this.ctx.fillStyle = 'rgba(255,255,255,0.95)';
        const points = [POSE.nose, POSE.leftShoulder, POSE.rightShoulder, POSE.leftElbow, POSE.rightElbow, POSE.leftWrist, POSE.rightWrist, POSE.leftHip, POSE.rightHip];
        [[POSE.leftShoulder, POSE.rightShoulder], [POSE.leftShoulder, POSE.leftElbow], [POSE.rightShoulder, POSE.rightElbow], [POSE.leftElbow, POSE.leftWrist], [POSE.rightElbow, POSE.rightWrist], [POSE.leftShoulder, POSE.leftHip], [POSE.rightShoulder, POSE.rightHip], [POSE.leftHip, POSE.rightHip]].forEach(([a, b]) => this.line(landmarks[a], landmarks[b], 5));
        points.forEach((i) => this.dot(landmarks[i], 5));
        const w = this.video?.videoWidth || 640;
        const h = this.video?.videoHeight || 480;
        this.ctx.strokeStyle = 'rgba(217,92,92,0.75)';
        this.ctx.lineWidth = 4;
        this.ctx.strokeRect(w * 0.28, h * 0.20, w * 0.44, h * 0.58);
    }
    render(exerciseKey, landmarks, baseline, evaluation) {
        this.setupCanvas();
        this.clear();
        if (!landmarks || !this.ctx || !evaluation) return;
        this.ctx.lineCap = 'round';
        if (exerciseKey === 'box_breathing') {
            this.ctx.fillStyle = evaluation.metrics?.tension === 'high' ? '#d99b4d' : '#5f9f68';
            this.dot(landmarks[POSE.leftShoulder], 8); this.dot(landmarks[POSE.rightShoulder], 8);
        } else if (exerciseKey === 'neck_rolls') {
            const currentShoulderWidth = dist(landmarks[POSE.leftShoulder], landmarks[POSE.rightShoulder]) || baseline?.shoulder_width || 0.28;
            this.drawGuideCircle(evaluation.metrics?.targetAngle || 0, landmarks[POSE.nose], currentShoulderWidth, evaluation.score > 70);
        } else if (exerciseKey === 'seated_cat_cow') {
            this.drawCatCowCue(landmarks, evaluation);
        } else if (exerciseKey === 'shoulder_opener') {
            this.drawSkeleton(landmarks, evaluation);
        }
    }
}

export class PoseMonitor {
    constructor() {
        this.videoEl = null; this.canvasEl = null; this.landmarker = null; this.renderer = null;
        this.rafId = null; this.inferenceTimerId = null; this.inferenceRunning = false; this.callbacks = []; this.baseline = null; this.baselineQuality = 'unknown'; this.exerciseKey = 'box_breathing'; this.exerciseData = null;
        this.startedAt = 0; this.paused = false; this.lastMidpoint = null; this.unstableUntil = 0;
        this.lastDetectAt = 0; this.lastLandmarks = null; this.previousLandmarks = null; this.currentLandmarks = null; this.transitionStartedAt = 0; this.lastEvaluation = null;
        this.boostedUntil = 0; this.lastMotionCheckAt = 0; this.previousMotionFrame = null;
        this.motionCanvas = document.createElement('canvas'); this.motionCanvas.width = 32; this.motionCanvas.height = 18;
        this.motionCtx = this.motionCanvas.getContext('2d', { willReadFrequently: true });
    }
    init(videoEl, canvasEl, landmarker) {
        this.videoEl = videoEl; this.canvasEl = canvasEl; this.landmarker = landmarker; this.renderer = new PoseRenderer(canvasEl, videoEl);
        this.startedAt = nowMs(); this.loop();
    }
    async captureBaseline(durationMs = 5000) {
        const samples = [];
        const start = nowMs();
        const hardDeadline = start + Math.max(durationMs + 7000, 12000);
        return new Promise((resolve) => {
            const finish = () => {
                this.baseline = this.computeBaseline(samples);
                this.baseline.sample_count = samples.length;
                this.baseline.quality = samples.length >= 6 ? 'good' : 'fallback';
                this.baselineQuality = this.baseline.quality;
                resolve(this.baseline);
            };
            const collect = () => {
                const t = nowMs();
                if (!this.landmarker || !this.videoEl || t >= hardDeadline) { finish(); return; }
                if (document.visibilityState === 'visible') {
                    const landmarks = this.detect();
                    if (hasGoodBaselineLandmarks(landmarks)) samples.push(landmarks);
                }
                if (t - start >= durationMs && samples.length >= 6) { finish(); return; }
                window.setTimeout(collect, POSE_BASELINE_INTERVAL_MS);
            };
            collect();
        });
    }
    computeBaseline(samples) {
        const widths = [], shoulderYs = [], hipYs = [], headWidths = [];
        samples.forEach((lm) => {
            const ls = lm[POSE.leftShoulder], rs = lm[POSE.rightShoulder], lh = lm[POSE.leftHip], rh = lm[POSE.rightHip], nose = lm[POSE.nose];
            if (visiblePoint(ls) && visiblePoint(rs)) { widths.push(dist(ls, rs)); shoulderYs.push(midpoint(ls, rs).y); }
            if (visiblePoint(lh) && visiblePoint(rh)) hipYs.push(midpoint(lh, rh).y);
            if (visiblePoint(ls) && visiblePoint(rs) && visiblePoint(nose)) headWidths.push(Math.abs(nose.x - midpoint(ls, rs).x));
        });
        const avg = (arr, fb) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : fb;
        return { shoulder_width: avg(widths, 0.28), head_width: avg(headWidths, 0.05), shoulder_mid_y: avg(shoulderYs, 0.42), hip_mid_y: avg(hipYs, 0.72) };
    }
    setExercise(exerciseKey, exerciseData) {
        this.exerciseKey = exerciseKey;
        this.exerciseData = exerciseData || {};
        this.startedAt = nowMs();
        this.lastMidpoint = null;
        this.unstableUntil = 0;
        this.lastDetectAt = 0;
        this.lastLandmarks = null;
        this.previousLandmarks = null;
        this.currentLandmarks = null;
        this.transitionStartedAt = 0;
        this.lastEvaluation = null;
    }
    onEvaluation(callback) { if (typeof callback === 'function') this.callbacks.push(callback); }
    pause() { this.paused = true; this.stopLoops(); }
    resume() { if (!this.paused && (this.rafId || this.inferenceTimerId)) return; this.paused = false; this.loop(); }
    stop() { this.stopLoops(); }
    stopLoops() {
        if (this.rafId) cancelAnimationFrame(this.rafId);
        this.rafId = null;
        if (this.inferenceTimerId) clearTimeout(this.inferenceTimerId);
        this.inferenceTimerId = null;
        this.inferenceRunning = false;
    }
    detect() {
        if (!this.landmarker || !this.videoEl || this.videoEl.readyState < 2) return null;
        try { return this.landmarker.detectForVideo(this.videoEl, performance.now())?.landmarks?.[0] || null; } catch (error) { return null; }
    }
    rememberPoseResult(landmarks, ts) {
        if (!landmarks) {
            this.previousLandmarks = null;
            this.currentLandmarks = null;
            this.lastLandmarks = null;
            this.transitionStartedAt = ts;
            return;
        }
        this.previousLandmarks = cloneLandmarks(this.currentLandmarks || landmarks);
        this.currentLandmarks = cloneLandmarks(landmarks);
        this.lastLandmarks = this.currentLandmarks;
        this.transitionStartedAt = ts;
    }
    interpolatedLandmarks(ts = nowMs()) {
        if (!this.currentLandmarks) return null;
        if (!this.previousLandmarks) return this.currentLandmarks;
        const alpha = clamp((ts - this.transitionStartedAt) / POSE_INTERPOLATION_MS, 0, 1);
        return lerpLandmarks(this.previousLandmarks, this.currentLandmarks, alpha);
    }
    currentPoseInterval(ts = nowMs()) {
        return ts < this.boostedUntil ? POSE_BOOST_INTERVAL_MS : POSE_DEFAULT_INTERVAL_MS;
    }
    shouldDeferForFace(ts = nowMs()) {
        const timing = window.WellHabitVisionTiming || {};
        if (timing.faceRunning) return true;
        const lastFaceEndAt = Number(timing.lastFaceEndAt || 0);
        return lastFaceEndAt > 0 && ts - lastFaceEndAt < FACE_DEFER_GRACE_MS;
    }
    shouldBoostFromMotion(ts = nowMs()) {
        if (!this.videoEl || !this.motionCtx || this.videoEl.readyState < 2) return false;
        if (ts - this.lastMotionCheckAt < MOTION_CHECK_INTERVAL_MS) return false;
        this.lastMotionCheckAt = ts;
        try {
            this.motionCtx.drawImage(this.videoEl, 0, 0, this.motionCanvas.width, this.motionCanvas.height);
            const data = this.motionCtx.getImageData(0, 0, this.motionCanvas.width, this.motionCanvas.height).data;
            const current = new Uint8Array(this.motionCanvas.width * this.motionCanvas.height);
            let totalDiff = 0;
            for (let i = 0, j = 0; i < data.length; i += 4, j += 1) {
                const luma = Math.round((data[i] + data[i + 1] + data[i + 2]) / 3);
                current[j] = luma;
                if (this.previousMotionFrame) totalDiff += Math.abs(luma - this.previousMotionFrame[j]);
            }
            const avgDiff = this.previousMotionFrame ? totalDiff / current.length : 0;
            this.previousMotionFrame = current;
            return avgDiff >= MOTION_THRESHOLD;
        } catch (error) {
            return false;
        }
    }
    hasStablePerson(landmarks) {
        const now = nowMs();
        const ls = landmarks?.[POSE.leftShoulder], rs = landmarks?.[POSE.rightShoulder];
        if (!visiblePoint(ls) || !visiblePoint(rs)) {
            this.lastMidpoint = null;
            return { ok: false, hint: 'Come back to the camera', reason: 'person_missing' };
        }
        if (now < this.unstableUntil) {
            return { ok: false, hint: 'Only one person please', reason: 'person_unstable' };
        }
        const mid = midpoint(ls, rs);
        if (this.lastMidpoint && Math.hypot(mid.x - this.lastMidpoint.x, mid.y - this.lastMidpoint.y) > 0.28) {
            this.unstableUntil = now + 2500;
            this.lastMidpoint = null;
            return { ok: false, hint: 'Only one person please', reason: 'person_unstable' };
        }
        this.lastMidpoint = mid;
        return { ok: true, hint: '', reason: '' };
    }
    pausedEvaluation(hint, reason, elapsed) {
        return { score: 0, phase: 'Paused', hints: [hint], metrics: {}, elapsed, paused: true, pauseReason: reason };
    }
    evaluate(landmarks) {
        const elapsed = (nowMs() - this.startedAt) / 1000;
        let score = 50, phase = 'Guide', hints = [], metrics = {};
        const base = this.baseline || { shoulder_width: 0.28, shoulder_mid_y: 0.42, hip_mid_y: 0.72 };
        if (!landmarks) return this.pausedEvaluation('Come back to the camera', 'person_missing', elapsed);
        const stable = this.hasStablePerson(landmarks);
        if (!stable.ok) return this.pausedEvaluation(stable.hint, stable.reason, elapsed);
        const ls = landmarks[POSE.leftShoulder], rs = landmarks[POSE.rightShoulder], nose = landmarks[POSE.nose], lh = landmarks[POSE.leftHip], rh = landmarks[POSE.rightHip];
        const shoulderMid = midpoint(ls, rs);
        const hipMid = midpoint(lh, rh) || { x: shoulderMid.x, y: base.hip_mid_y };
        if (this.exerciseKey === 'box_breathing') {
            const tension = shoulderMid.y < base.shoulder_mid_y - 0.035 ? 'high' : 'low';
            score = tension === 'high' ? 55 : 90; phase = 'Breathe'; metrics.tension = tension;
            metrics.reps = Math.floor(elapsed / 16);
            if (tension === 'high') hints.push('Drop your shoulders');
        } else if (this.exerciseKey === 'neck_rolls') {
            if (!visiblePoint(nose, 0.35)) return this.pausedEvaluation('Face the camera', 'person_missing', elapsed);
            const targetAngle = (elapsed / 8) * Math.PI * 2 - Math.PI / 2;
            const scale = Math.max(0.06, base.shoulder_width * 1.05);
            const noseOffset = { x: clamp((nose.x - shoulderMid.x) / scale, -1, 1), y: clamp((nose.y - shoulderMid.y) / scale, -1, 1) };
            const target = { x: Math.cos(targetAngle), y: Math.sin(targetAngle) };
            const error = Math.hypot(noseOffset.x - target.x, noseOffset.y - target.y);
            score = clamp(100 - error * 55, 0, 100);
            phase = score > 70 ? 'Following ✓' : 'Tilt toward the red dot';
            metrics = { targetAngle, noseOffset, distance: error, shoulderWidth: dist(ls, rs) || base.shoulder_width, reps: Math.floor(elapsed / 8) };
            if (score <= 70) hints.push('Tilt your head toward the red dot slowly');
        } else if (this.exerciseKey === 'seated_cat_cow') {
            const cycle = elapsed % 6; phase = cycle < 3 ? 'Inhale · lift chest' : 'Exhale · round back';
            const bend = clamp((hipMid.x - shoulderMid.x) * 500 + (base.hip_mid_y - shoulderMid.y) * 120, -90, 90);
            const wantedPositive = cycle < 3;
            const good = wantedPositive ? bend > 8 : bend < -8;
            score = good ? 86 : (Math.abs(bend) < 8 ? 55 : 30); metrics = { bend, reps: Math.floor(elapsed / 6) };
            if (!good) hints.push(Math.abs(bend) < 8 ? 'Make the movement a little clearer' : 'Reverse the movement with the breath');
        } else if (this.exerciseKey === 'shoulder_opener') {
            phase = elapsed < 15 ? 'Shoulder rolls' : 'Chest opener';
            const widthScore = clamp((dist(ls, rs) / Math.max(0.01, base.shoulder_width)) * 75, 0, 100);
            const symmetry = 100 - clamp(Math.abs(ls.y - rs.y) * 500, 0, 100);
            const headCenter = 100 - clamp(Math.abs(nose.x - shoulderMid.x) * 600, 0, 100);
            const chest = 100 - clamp(Math.abs(shoulderMid.x - hipMid.x) * 400, 0, 100);
            score = clamp(widthScore * 0.35 + symmetry * 0.25 + headCenter * 0.2 + chest * 0.2, 0, 100);
            const alignmentMetrics = { widthScore, symmetry, headCenter, chest };
            metrics = { ...alignmentMetrics, reps: Math.floor(Math.min(elapsed, 15) / 5) };
            const weakest = Object.entries(alignmentMetrics).sort((a, b) => a[1] - b[1])[0]?.[0];
            const hintMap = { widthScore: 'Open your chest gently', symmetry: 'Level your shoulders', headCenter: 'Bring your head to center', chest: 'Stack ribs over hips' };
            if (score < 75) hints.push(hintMap[weakest] || 'Hold the open posture');
        }
        return { score, phase, hints, metrics, elapsed, paused: false, pauseReason: '' };
    }
    loop() {
        if (this.paused) return;
        this.startInferenceLoop();
        this.startRenderLoop();
    }
    startInferenceLoop() {
        if (this.inferenceTimerId || this.paused) return;
        const schedule = (delay) => {
            this.inferenceTimerId = setTimeout(runOnce, Math.max(0, delay));
        };
        const runOnce = () => {
            this.inferenceTimerId = null;
            if (this.paused || !this.landmarker || !this.videoEl) return;
            const ts = nowMs();
            if (this.shouldBoostFromMotion(ts)) this.boostedUntil = ts + MOTION_BOOST_DURATION_MS;
            if (this.shouldDeferForFace(ts)) {
                schedule(FACE_DEFER_RETRY_MS);
                return;
            }
            if (!this.inferenceRunning) {
                this.inferenceRunning = true;
                try {
                    this.lastDetectAt = nowMs();
                    const landmarks = this.detect();
                    this.rememberPoseResult(landmarks, this.lastDetectAt);
                    this.lastEvaluation = this.evaluate(this.currentLandmarks);
                    this.callbacks.forEach((cb) => cb(this.lastEvaluation));
                } finally {
                    this.inferenceRunning = false;
                }
            }
            schedule(this.currentPoseInterval(nowMs()));
        };
        schedule(250);
    }
    startRenderLoop() {
        if (this.rafId || this.paused) return;
        const tick = () => {
            this.rafId = null;
            if (this.paused) return;
            const renderLandmarks = this.interpolatedLandmarks(nowMs());
            this.renderer?.render(this.exerciseKey, renderLandmarks, this.baseline, this.lastEvaluation);
            this.rafId = requestAnimationFrame(tick);
        };
        this.rafId = requestAnimationFrame(tick);
    }
}
