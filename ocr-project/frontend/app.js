const video = document.getElementById('camera');
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const resultEl = document.getElementById('result');
const statusEl = document.getElementById('status');
const overlayEl = document.getElementById('overlay');
const canvas = document.getElementById('capture-canvas');
const ctx = canvas.getContext('2d');

const backendScheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
const backendUrl = `${backendScheme}://${window.location.host}/ws/stream`;

let stream = null;
let socket = null;
let sessionId = `browser-${Date.now()}`;
let frameId = 0;
let captureTimer = null;

function setStatus(message, tone = 'normal') {
  statusEl.textContent = message;
  statusEl.style.color = tone === 'error' ? '#fca5a5' : tone === 'success' ? '#86efac' : '#e2e8f0';
}

function updateOverlay(message) {
  overlayEl.textContent = message;
}

function openSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    return;
  }

  socket = new WebSocket(backendUrl);

  socket.onopen = () => {
    setStatus('연결됨', 'success');
    socket.send(JSON.stringify({ type: 'hello', session_id: sessionId }));
    updateOverlay('카메라가 연결되었습니다.');
  };

  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === 'ready') {
      updateOverlay('스트림 준비 완료');
      return;
    }

    if (payload.type === 'ocr_result') {
      const text = payload.text || '인식 결과 없음';
      resultEl.textContent = text;
      updateOverlay(text);
      return;
    }

    if (payload.type === 'ocr_status') {
      resultEl.textContent = '아직 인식된 텍스트가 없습니다.';
      updateOverlay('텍스트를 찾지 못했습니다. 조명/거리/각도를 조정해보세요.');
      return;
    }

    if (payload.type === 'error') {
      setStatus(payload.detail || '에러', 'error');
      updateOverlay('서버에서 오류가 발생했습니다.');
    }
  };

  socket.onerror = () => {
    setStatus('연결 오류', 'error');
    updateOverlay('백엔드 연결에 실패했습니다.');
  };

  socket.onclose = () => {
    setStatus('재연결 대기 중', 'error');
    updateOverlay('연결이 종료되었습니다.');
  };
}

async function startCamera() {
  if (!window.isSecureContext) {
    setStatus('HTTPS 환경이 필요합니다.', 'error');
    updateOverlay('핸드폰 카메라 사용은 HTTPS 또는 localhost 환경에서만 가능합니다. ngrok 또는 로컬 HTTPS를 사용해 주세요.');
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus('카메라 API를 지원하지 않습니다.', 'error');
    return;
  }

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: 'environment',
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: false,
    });

    video.srcObject = stream;
    video.play();
    openSocket();

    captureTimer = setInterval(() => {
      if (!video.videoWidth || !video.videoHeight) {
        return;
      }

      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const imageDataUrl = canvas.toDataURL('image/jpeg', 0.7);

      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(
          JSON.stringify({
            type: 'frame',
            session_id: sessionId,
            frame_id: frameId,
            timestamp: Date.now() / 1000,
            image: imageDataUrl,
          })
        );
      }

      frameId += 1;
    }, 300);

    setStatus('카메라 실행 중', 'success');
    updateOverlay('카메라가 실행 중입니다.');
  } catch (error) {
    setStatus('카메라 권한이 필요합니다.', 'error');
    updateOverlay('브라우저에서 카메라 권한을 허용해 주세요.');
    console.error(error);
  }
}

function stopCamera() {
  if (captureTimer) {
    clearInterval(captureTimer);
    captureTimer = null;
  }

  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }

  if (socket) {
    socket.close();
    socket = null;
  }

  video.srcObject = null;
  setStatus('중지됨');
  updateOverlay('카메라가 중지되었습니다.');
}

startBtn.addEventListener('click', startCamera);
stopBtn.addEventListener('click', stopCamera);

window.addEventListener('beforeunload', () => {
  stopCamera();
});
