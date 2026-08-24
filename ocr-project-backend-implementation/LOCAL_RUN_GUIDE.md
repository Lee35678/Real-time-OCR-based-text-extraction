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
cd "C:\Real-time-OCR-based-text-extraction\ocr-project-backend-implementation\backend"
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
cd "C:\Real-time-OCR-based-text-extraction\ocr-project-backend-implementation"
.\backend\.venv\Scripts\python -m pytest -q tests\test_ocr_pipeline.py
```

정상적으로 통과하면 로컬 환경이 준비된 상태입니다.

## 5. OCR 샘플 확인

아래 명령으로 간단한 이미지 OCR 테스트를 실행해볼 수 있습니다:

```powershell
cd "C:\Real-time-OCR-based-text-extraction\ocr-project-backend-implementation\backend"
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

필요하면 다음으로 FastAPI 서버 실행 또는 웹캠 기반 실시간 OCR 실행 방법도 이어서 정리해드릴게요.
