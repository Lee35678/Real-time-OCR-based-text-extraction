# StreamOCR 백엔드 연동 패키지

이 폴더는 **서버가 프론트엔드로 보낼 데이터와 API를 먼저 완성한 상태**입니다.
OCR 엔진 자체는 포함하지 않았으며, `ocr_connector.py`의 `recognize()`만 교체하면
나머지 WebSocket 전송, latest-first 큐, 단어 중복 제거, 목록/삭제/CSV가 연결됩니다.

## 전달 파일

| 파일 | 역할 | 수정 필요 여부 |
|---|---|---|
| `server.py` | 프레임 수신, 결과 전송, 세션 단어 API | 보통 수정 불필요 |
| `preprocess.py` | OpenCV 전처리 | 수정 불필요 |
| `ocr_pipeline.py` | ProcessPool + latest-first 1칸 큐 | 수정 불필요 |
| `ocr_connector.py` | 실제 OCR 엔진 연결 지점 | **`recognize()`만 구현** |
| `session_words.py` | 메모리 단어 목록/중복 제거/CSV | DB 연결 시 교체 가능 |

## 전체 연결 흐름

```text
모바일 TEXT 헤더 + JPEG BINARY
        ↓  /ws/send?t={token}
OpenCV 전처리
        ├─ 즉시 ACK 전송 (응답 경로)
        └─ latest-first OCR 큐 (대기 1장, 오래된 작업 폐기)
                    ↓ ProcessPool
             ocr_connector.recognize()
                    ↓
             ocr_result JSON 전송
                    ├─ bbox 오버레이
                    ├─ 0건 5초 촬영 가이드
                    └─ 세션 단어 목록 누적
```

임베딩과 Supabase 저장은 이 흐름에 동기로 추가하면 안 됩니다. OCR 결과가 나온
뒤 별도 백그라운드 큐에 넣어야 합니다.

## 1. 업로드 규약 (프론트 → 서버)

연결 주소:

```text
WSS /ws/send?t={session_token}
```

프레임마다 아래 두 메시지를 순서대로 보냅니다.

```json
{"seq": 41, "w": 1280, "h": 720, "captured_at_ms": 1787547600123}
```

그 다음 메시지는 JPEG 바이너리입니다. `captured_at_ms`는 선택값입니다.

## 2. 서버 전송 규약 (서버 → 프론트)

같은 WebSocket에서 세 종류의 JSON 텍스트 메시지를 보냅니다. 프론트는 반드시
`type`으로 분기해야 합니다.

### ACK

프레임을 서버가 수신하고 전처리한 직후 전송합니다.

```json
{"type":"ack","seq":41,"size":84210}
```

### OCR 결과

```json
{
  "type": "ocr_result",
  "version": 1,
  "seq": 41,
  "source": {"w": 1280, "h": 720},
  "tokens": [
    {
      "text": "hello",
      "confidence": 94.2,
      "bbox": {"x": 120, "y": 86, "w": 210, "h": 64}
    }
  ],
  "new_words": [
    {
      "id": "서버생성ID",
      "text": "hello",
      "confidence": 94.2,
      "bbox": {"x": 120, "y": 86, "w": 210, "h": 64},
      "seq": 41,
      "created_at_ms": 1787547600200
    }
  ],
  "guide": {"visible": false, "code": null, "message": null},
  "timing": {"ocr_ms": 73.4, "queue_dropped": 0},
  "captured_at_ms": 1787547600123
}
```

- `tokens`: 현재 프레임에서 인식된 전체 토큰입니다. 바운딩 박스 오버레이에 사용합니다.
- `new_words`: 이번 프레임에서 세션 목록에 새로 추가된 단어만 들어갑니다.
- `bbox`: `source.w × source.h` 기준의 절대 픽셀 좌표입니다.
- `guide.visible`: 유효 토큰이 5초 이상 없으면 `true`입니다.
- OCR 연결 전에는 `tokens`와 `new_words`가 빈 배열로 오며, 약 5초 뒤 가이드가 켜집니다.

### 촬영 가이드 상태 변경

프레임이 전처리에서 계속 폐기되는 경우에도 정확히 동작하도록 별도 타이머가
가이드 상태가 바뀔 때만 전송합니다.

```json
{
  "type": "guide",
  "version": 1,
  "visible": true,
  "code": "NO_TEXT_5S",
  "message": "텍스트가 화면에 크게 보이도록 가까이 이동하세요."
}
```

유효 토큰이 다시 검출되면 가장 가까운 `ocr_result.guide.visible`이 `false`로
오므로 즉시 숨깁니다.

## 3. OCR 연결 방법

`ocr_connector.py`의 `recognize(image)`가 유일한 연결 지점입니다. 입력 `image`는
OpenCV 전처리가 끝난 `numpy.ndarray` 흑백 이미지입니다. 아래 모양으로 반환합니다.

```python
return [
    {
        "text": "hello",
        "confidence": 94.2,
        "bbox": {"x": 120, "y": 86, "w": 210, "h": 64},
    }
]
```

권장 Tesseract 설정은 `lang="eng"`, `--oem 1 --psm 6`, `image_to_data`입니다.
confidence 60 미만 제거, 사전 대조 등의 후처리도 이 함수 안이나 별도 모듈에서
끝낸 뒤 위 규격만 반환하세요. `recognize()`는 ProcessPool에서 실행되므로
`async` 함수로 바꾸지 말고, 소켓이나 DB 연결 객체를 함수 안에 전달하지 마세요.

## 4. 세션 단어 API

모든 요청에 현재 세션 토큰을 `?t=`로 붙입니다.

```text
GET    /api/session/words?t={token}
DELETE /api/session/words/{id}?t={token}
GET    /api/session/words.csv?t={token}
```

목록 응답:

```json
{"items":[{"id":"...","text":"hello","confidence":94.2,"bbox":{},"seq":41,"created_at_ms":1787547600200}]}
```

삭제 성공은 `204`, 없는 ID는 `404`, 잘못된 토큰은 `401`입니다. CSV는 UTF-8
BOM을 포함하므로 Excel에서 한글이 깨지지 않습니다.

현재 저장소는 PoC용 메모리 방식이며 서버 재시작 시 초기화됩니다. Supabase로
바꿀 때도 API 응답 모양과 `id`를 유지하면 프론트 수정이 필요 없습니다.

## 5. 프론트 연결 체크리스트

- WebSocket JSON을 `type === "ack"`, `type === "ocr_result"`, `type === "guide"`로 분기
- `seq`가 오래된 결과면 최신 오버레이를 덮어쓰지 않도록 무시
- `source` 좌표를 실제 프리뷰 크기와 `object-fit: cover` 크롭에 맞춰 변환
- `guide.visible`로 촬영 가이드 표시/숨김
- `new_words` 실시간 추가 후, 재접속 시 목록 API로 전체 동기화
- 삭제는 성공 응답을 받은 뒤 화면에서 제거
- CSV 버튼은 CSV API 주소로 다운로드

## 6. 실행 및 빠른 확인

```powershell
python -m pip install fastapi "uvicorn[standard]" opencv-python numpy
python server.py
```

`http://localhost:8000/monitor`를 열고 기존 방식으로 모바일을 연결합니다.
OCR 미연결 상태에서도 ACK는 기존처럼 오고, 전처리 통과 프레임마다 빈
`ocr_result`가 옵니다. 실제 OCR 연결 뒤에는 `tokens`만 채워지는지 확인하면 됩니다.
