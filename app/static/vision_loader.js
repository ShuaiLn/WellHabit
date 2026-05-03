const MODULE_URL = '/static/vendor/mediapipe/vision_bundle.mjs';
const WASM_URL = '/static/vendor/mediapipe/wasm';
const POSE_MODEL_URL = '/static/break_assets/pose_landmarker_lite.task';
const FACE_MODEL_URL = '/static/break_assets/face_landmarker.task';
const LOW_POWER_CAMERA_CONSTRAINTS = {
    video: {
        width: { ideal: 480, max: 640 },
        height: { ideal: 360, max: 480 },
        frameRate: { ideal: 30, max: 30 },
        facingMode: 'user',
        resizeMode: 'crop-and-scale',
    },
    audio: false,
};
const LOW_POWER_CAMERA_FALLBACKS = [
    LOW_POWER_CAMERA_CONSTRAINTS,
    { video: { width: { ideal: 320, max: 480 }, height: { ideal: 240, max: 360 }, frameRate: { ideal: 24, max: 30 }, facingMode: 'user', resizeMode: 'crop-and-scale' }, audio: false },
    { video: { width: { ideal: 640, max: 640 }, height: { ideal: 480, max: 480 }, frameRate: { ideal: 30, max: 30 }, facingMode: 'user' }, audio: false },
    { video: { facingMode: 'user' }, audio: false },
];

const modulePromises = new Map();
const filesetPromises = new Map();

function timeoutAfter(ms, label) {
    return new Promise((_, reject) => window.setTimeout(() => reject(new Error(`${label} timed out`)), ms));
}

function absoluteUrl(url) {
    return new URL(url, window.location.origin).href;
}

async function assertFetchable(url, errorCode, expectedMimeParts = []) {
    const resolved = absoluteUrl(url);
    let response;
    try {
        response = await fetch(resolved, { method: 'HEAD', cache: 'no-store' });
    } catch (error) {
        throw new Error(`${errorCode}: ${resolved} could not be reached (${error?.message || error})`);
    }
    if (!response.ok) {
        throw new Error(`${errorCode}: ${resolved} returned HTTP ${response.status}`);
    }
    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (expectedMimeParts.length && contentType && !expectedMimeParts.some((part) => contentType.includes(part))) {
        throw new Error(`${errorCode}: ${resolved} has MIME type "${contentType}"`);
    }
}

async function importVisionModule(moduleUrl = MODULE_URL) {
    const resolvedUrl = absoluteUrl(moduleUrl);
    if (!modulePromises.has(resolvedUrl)) {
        modulePromises.set(resolvedUrl, (async () => {
            await assertFetchable(resolvedUrl, 'MEDIAPIPE_MODULE_LOAD_FAILED', ['javascript', 'ecmascript']);
            const vision = await Promise.race([
                import(resolvedUrl),
                timeoutAfter(30000, 'MediaPipe module'),
            ]);
            if (!vision?.FilesetResolver || !vision?.FaceLandmarker) {
                throw new Error('MEDIAPIPE_MODULE_LOAD_FAILED: MediaPipe module loaded but required exports are missing');
            }
            return vision;
        })());
    }
    return modulePromises.get(resolvedUrl);
}

async function loadVisionFileset(vision, wasmUrl = WASM_URL) {
    const resolvedUrl = absoluteUrl(wasmUrl).replace(/\/$/, '');
    if (!filesetPromises.has(resolvedUrl)) {
        filesetPromises.set(resolvedUrl, (async () => {
            await Promise.all([
                assertFetchable(`${resolvedUrl}/vision_wasm_internal.js`, 'MEDIAPIPE_WASM_LOAD_FAILED', ['javascript']),
                assertFetchable(`${resolvedUrl}/vision_wasm_internal.wasm`, 'MEDIAPIPE_WASM_LOAD_FAILED', ['wasm']),
            ]);
            return Promise.race([
                vision.FilesetResolver.forVisionTasks(resolvedUrl),
                timeoutAfter(30000, 'MediaPipe WASM'),
            ]);
        })());
    }
    return filesetPromises.get(resolvedUrl);
}

export async function getVision(options = {}) {
    const moduleUrl = options.moduleUrl || MODULE_URL;
    const wasmUrl = options.wasmUrl || WASM_URL;
    const progress = typeof options.onProgress === 'function' ? options.onProgress : () => {};
    progress(5);
    const vision = await importVisionModule(moduleUrl);
    progress(35);
    const fileset = await loadVisionFileset(vision, wasmUrl);
    progress(65);
    return { vision, fileset };
}

export async function loadPoseLandmarker(onProgress, options = {}) {
    const progress = typeof onProgress === 'function' ? onProgress : () => {};
    const { vision, fileset } = await getVision({
        moduleUrl: options.moduleUrl || MODULE_URL,
        wasmUrl: options.wasmUrl || WASM_URL,
        onProgress: progress,
    });
    const modelUrl = absoluteUrl(options.modelUrl || POSE_MODEL_URL);
    const poseOptions = {
        baseOptions: { modelAssetPath: modelUrl, delegate: 'GPU' },
        runningMode: 'VIDEO',
        numPoses: 1,
    };
    let landmarker;
    try {
        landmarker = await Promise.race([
            vision.PoseLandmarker.createFromOptions(fileset, poseOptions),
            timeoutAfter(30000, 'Pose model'),
        ]);
    } catch (gpuError) {
        landmarker = await Promise.race([
            vision.PoseLandmarker.createFromOptions(fileset, Object.assign({}, poseOptions, {
                baseOptions: { modelAssetPath: modelUrl, delegate: 'CPU' },
            })),
            timeoutAfter(30000, 'Pose model CPU'),
        ]);
    }
    progress(100);
    return landmarker;
}

export async function loadFaceLandmarker(options = {}) {
    const { vision, fileset } = await getVision({
        moduleUrl: options.moduleUrl || MODULE_URL,
        wasmUrl: options.wasmUrl || WASM_URL,
        onProgress: options.onProgress,
    });
    const modelUrl = absoluteUrl(options.modelUrl || FACE_MODEL_URL);
    await assertFetchable(modelUrl, 'MEDIAPIPE_MODEL_LOAD_FAILED');
    const baseOptions = { modelAssetPath: modelUrl, delegate: 'GPU' };
    const faceOptions = {
        baseOptions,
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: true,
        outputFacialTransformationMatrixes: true,
    };
    try {
        return await Promise.race([
            vision.FaceLandmarker.createFromOptions(fileset, faceOptions),
            timeoutAfter(30000, 'Face model'),
        ]);
    } catch (gpuError) {
        try {
            return await Promise.race([
                vision.FaceLandmarker.createFromOptions(fileset, Object.assign({}, faceOptions, {
                    baseOptions: { modelAssetPath: modelUrl, delegate: 'CPU' },
                })),
                timeoutAfter(30000, 'Face model CPU'),
            ]);
        } catch (cpuError) {
            throw new Error(`MEDIAPIPE_MODEL_LOAD_FAILED: ${cpuError?.message || gpuError?.message || cpuError}`);
        }
    }
}

async function tightenCameraStream(stream) {
    const track = stream?.getVideoTracks?.()[0];
    if (!track?.applyConstraints) return stream;
    try {
        await track.applyConstraints({
            width: { ideal: 480, max: 640 },
            height: { ideal: 360, max: 480 },
            frameRate: { ideal: 30, max: 30 },
            resizeMode: 'crop-and-scale',
        });
    } catch (error) {
        // Keep the already-open camera stream. Some browsers reject max/resizeMode constraints.
    }
    return stream;
}

export async function requestCamera() {
    if (!navigator.mediaDevices?.getUserMedia) return null;
    try { sessionStorage.removeItem('cameraHandoff'); } catch (error) {}
    let lastError = null;
    for (const constraints of LOW_POWER_CAMERA_FALLBACKS) {
        try {
            const stream = await navigator.mediaDevices.getUserMedia(constraints);
            return await tightenCameraStream(stream);
        } catch (error) {
            lastError = error;
            const name = error?.name || '';
            if (name !== 'OverconstrainedError' && name !== 'ConstraintNotSatisfiedError' && name !== 'TypeError') throw error;
        }
    }
    throw lastError || new Error('Camera constraints failed');
}
