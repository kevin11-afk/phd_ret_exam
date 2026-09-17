/**
 * Client-side face tracking. This module only *observes* — every violation
 * it notices is still reported to the server via the same /exam/violation
 * endpoint that owns strike counting, so a tampered/patched copy of this
 * file can at worst stop reporting violations, never grant extra strikes,
 * fabricate a passing score, or touch the timer/session state.
 *
 * Detection is a lightweight heuristic (TinyFaceDetector, ~190KB model),
 * not true gaze tracking:
 *   - 0 faces for a few consecutive checks  -> "face_not_detected"
 *   - >1 faces in frame                      -> "multiple_faces"
 *   - 1 face, but its bounding box is far     -> "looking_away"
 *     off-center for a few consecutive checks   (proxy for head turn; not a
 *                                                 real head-pose estimate)
 */
const Proctor = (() => {
  const MODEL_URL = "https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights";
  const CHECK_INTERVAL_MS = 1500;
  const STREAK_THRESHOLD = 15;       // consecutive bad checks before flagging
  const OFF_CENTER_FRACTION = 0.30; // bounding-box center offset, as a fraction of frame width

  let videoEl, statusEl, onViolation;
  let stream = null;
  let intervalHandle = null;
  let modelsReady = false;
  let noFaceStreak = 0;
  let awayStreak = 0;
  let ws = null;
  let canvasEl = document.createElement("canvas");
  let ctx = canvasEl.getContext("2d");

  function setStatus(text, alert) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle("alert", !!alert);
  }

  async function init(videoElement, statusElement, violationCallback) {
    videoEl = videoElement;
    statusEl = statusElement;
    onViolation = violationCallback;

    try {
      await Promise.all([
        faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
      ]);
      modelsReady = true;
    } catch (err) {
      setStatus("Face model failed to load", true);
      modelsReady = false;
    }
    
    // Connect websocket for signaling
    const token = localStorage.getItem("access_token");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${window.location.host}/exam/ws/candidate?token=${encodeURIComponent(token)}`);
    
    ws.onmessage = async (event) => {
      // No WebRTC handling needed anymore.
    };
  }

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 }, audio: false });
      videoEl.srcObject = stream;
      setStatus(modelsReady ? "Monitoring" : "Camera on (face check unavailable)");
    } catch (err) {
      setStatus("Camera access denied", true);
      // Camera access is required by the exam conditions; surface this
      // clearly but don't invent a violation type for it — that's a
      // permissions problem, not proctoring evidence.
      return false;
    }

    if (modelsReady) {
      intervalHandle = setInterval(runDetection, CHECK_INTERVAL_MS);
    }
    return true;
  }

  async function runDetection() {
    if (!videoEl || videoEl.readyState < 2) return;
    
    // Broadcast frame to admin via WebSocket
    if (ws && ws.readyState === WebSocket.OPEN) {
      canvasEl.width = 320;
      canvasEl.height = 240;
      ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
      const frameData = canvasEl.toDataURL('image/jpeg', 0.35);
      ws.send(JSON.stringify({
        type: "proctor_frame",
        frame: frameData
      }));
    }

    let detections;
    try {
      detections = await faceapi.detectAllFaces(videoEl, new faceapi.TinyFaceDetectorOptions({ scoreThreshold: 0.3 }));
    } catch (err) {
      return; // transient decode error; skip this tick
    }

    if (detections.length === 0) {
      noFaceStreak += 1;
      awayStreak = 0;
      setStatus("No face detected", true);
      if (noFaceStreak >= STREAK_THRESHOLD) {
        noFaceStreak = 0;
        onViolation("face_not_detected");
      }
      return;
    }

    if (detections.length > 1) {
      noFaceStreak = 0;
      awayStreak = 0;
      setStatus(`${detections.length} people in frame`, true);
      onViolation("multiple_faces");
      return;
    }

    noFaceStreak = 0;
    const box = detections[0].box;
    const faceCenterX = box.x + box.width / 2;
    const frameCenterX = videoEl.videoWidth / 2 || 160;
    const offsetFraction = Math.abs(faceCenterX - frameCenterX) / (videoEl.videoWidth || 320);

    if (offsetFraction > OFF_CENTER_FRACTION) {
      awayStreak += 1;
      setStatus("Face turned away", true);
      if (awayStreak >= STREAK_THRESHOLD) {
        awayStreak = 0;
        onViolation("looking_away");
      }
    } else {
      awayStreak = 0;
      setStatus("Monitoring");
    }
  }

  function stop() {
    if (intervalHandle) clearInterval(intervalHandle);
    intervalHandle = null;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  return { init, start, stop };
})();
