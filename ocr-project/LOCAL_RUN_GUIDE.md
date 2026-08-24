# 로컬 실행 가이드

아래 순서대로 하면 이 프로젝트를 로컬에서 바로 실행할 수 있습니다.

## 1. Tesseract 설치

PowerShell에서 실행:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e --source winget
```

설치 후 확인:

```powershell
tesseract --version
```

출력이 나오면 정상입니다. 만약 새 PowerShell을 열어도 안 나오면 PATH를 추가하세요:

```powershell
$env:Path += ';C:\Program Files\Tesseract-OCR'
```

## 2. 가상환경 생성

```powershell
cd "C:\Real-time-OCR-based-text-extraction\ocr-project\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

PowerShell 보안 정책 때문에 실행이 막히면:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 3. 의존성 설치

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 4. 테스트 실행

프로젝트 루트에서 실행:

```powershell
cd "C:\Real-time-OCR-based-text-extraction\ocr-project"
.\backend\.venv\Scripts\python -m pytest -q OCR_tests\test_ocr_pipeline.py
```

정상적으로 통과하면 로컬 환경이 준비된 상태입니다.

## 5. OCR 샘플 확인

아래 명령으로 간단한 이미지 OCR 테스트를 실행해볼 수 있습니다:

```powershell
cd "C:\Real-time-OCR-based-text-extraction\ocr-project\backend"
.\.venv\Scripts\python -c "from PIL import Image, ImageDraw, ImageFont; import pytesseract; img = Image.new('L', (800, 200), 255); draw = ImageDraw.Draw(img); draw.text((50, 60), 'HELLO OCR', fill=0, font=ImageFont.load_default()); img.save('sample_ocr.png'); print(pytesseract.image_to_string('sample_ocr.png', config='--oem 1 --psm 6').strip())"
```

결과가 아래처럼 나오면 정상입니다:

```text
HELLO OCR
```

## 6. 참고

- Python 3.11 이상 권장
- Tesseract는 필수
- 이 프로젝트는 OCR 파이프라인 로직을 검증하는 코드 중심 구조입니다.

## 7. Docker Compose로 통합 실행

이 구조는 백엔드 API와 브라우저 프론트엔드를 Docker로 묶어서 실행할 수 있습니다.

### 7.1 실행

```powershell
cd "C:\Real-time-OCR-based-text-extraction\ocr-project"
docker compose up --build
```

### 7.2 접속 주소

- 프론트엔드: http://localhost:8080
- 백엔드 헬스체크: http://localhost:8000/health
- WebSocket: ws://localhost:8000/ws/stream

모바일 브라우저에서 직접 접속하려면 PC의 LAN IP를 사용하면 됩니다.
예:

```text
http://192.168.0.10:8080
ws://192.168.0.10:8000/ws/stream
```

### 7.3 동작 원리

- 브라우저는 웹캠 스트림을 캡처해 JPEG 프레임으로 변환합니다.
- WebSocket으로 FastAPI 백엔드에 프레임을 전송합니다.
- 백엔드는 OCR 파이프라인을 통해 텍스트와 신뢰도를 다시 브라우저로 push 합니다.
- 이 구조는 PRD의 "WebSocket 기반 실시간 OCR + 결과 피드백" 흐름과 일치합니다.

### 7.4 종료

```powershell
docker compose down
```

### 7.5 모바일 카메라 테스트 (핵심)

핸드폰에서 카메라를 쓰려면 HTTPS 또는 localhost 환경이 필요합니다. 따라서 `http://localhost:8080` 또는 `http://192.168.x.x:8080`만으로는 모바일 브라우저에서 카메라 권한이 막힐 수 있습니다.

권장 방법은 아래 중 하나입니다.

#### 방법 A. ngrok로 HTTPS 터널 열기 (권장)

```powershell
docker compose up --build
ngrok http 8080
```

이후 ngrok이 출력하는 주소를 핸드폰에서 접속하면 됩니다.
예:

```text
https://abc123.ngrok-free.app
```

이 주소는 HTTPS이므로 모바일 브라우저에서 카메라 권한이 허용됩니다.
백엔드 WebSocket도 같은 도메인으로 연결되도록 설정되어 있어, 프론트는 `/ws/stream`를 동일 오리진으로 사용합니다.

#### 방법 B. 같은 와이파이 + 로컬 IP + HTTPS

- PC와 핸드폰이 같은 Wi‑Fi에 접속
- `ipconfig`로 PC IP 확인
- `https://<PC_IP>` 로 접속
- 로컬 HTTPS 인증서를 설치해야 함

이 방법은 테스트 용도로는 가능하지만 ngrok이 더 빠르고 안정적입니다.

### 7.6 현재 이 환경에서의 검증 상태

이 도구 환경에는 Docker Desktop / Docker Engine 연결이 활성화되어 있지 않아 실제 컨테이너 실행까지는 확인하지 못했지만, Compose 파일 문법 검증과 백엔드 자체의 HTTP/WebSocket 기동 검증은 완료했습니다.

- HTTP 확인: http://127.0.0.1:8000/health → 정상 응답
- WebSocket 확인: ws://127.0.0.1:8000/ws/stream → hello 메시지 응답 정상
- 테스트 확인: OCR 테스트 3개 통과

실제 Docker Desktop 환경에서 실행하려면 위 명령으로 `docker compose up --build`를 실행하면 됩니다.
