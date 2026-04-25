const MODULE_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';
const WASM_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm';
const LOCAL_MODEL_URL = '/static/break_assets/pose_landmarker_lite.task';

function timeoutAfter(ms, label) {
    return new Promise((_, reject) => window.setTimeout(() => reject(new Error(`${label} timed out`)), ms));
}

export async function loadPoseLandmarker(onProgress) {
    const progress = typeof onProgress === 'function' ? onProgress : () => {};
    progress(5);
    const vision = await Promise.race([import(MODULE_URL), timeoutAfter(30000, 'MediaPipe module')]);
    progress(35);
    const fileset = await Promise.race([vision.FilesetResolver.forVisionTasks(WASM_URL), timeoutAfter(30000, 'MediaPipe WASM')]);
    progress(65);
    const landmarker = await Promise.race([
        vision.PoseLandmarker.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: LOCAL_MODEL_URL, delegate: 'CPU' },
            runningMode: 'VIDEO',
            numPoses: 1,
        }),
        timeoutAfter(30000, 'Pose model'),
    ]);
    progress(100);
    return landmarker;
}

export async function requestCamera() {
    if (!navigator.mediaDevices?.getUserMedia) return null;
    try { sessionStorage.removeItem('cameraHandoff'); } catch (error) {}
    return navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        audio: false,
    });
}
