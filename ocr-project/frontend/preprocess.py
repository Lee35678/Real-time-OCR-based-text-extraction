"""
프레임 전처리 파이프라인 (P0-2)

응답 경로에 들어가므로 프레임당 20ms 예산을 지켜야 한다.
OCR 이전에 쓸모없는 프레임을 걸러내는 것이 이 모듈의 핵심 역할이다.

  변화 없음 판정 → 흐림 판정 → CLAHE → Adaptive Threshold → Deskew

앞의 두 판정을 먼저 두는 이유는 싸기 때문이다. 축소본 차분과
Laplacian 분산은 각각 1ms 미만인데, 여기서 걸러지면 뒤의 무거운
연산을 통째로 건너뛴다.
"""
import time

import cv2
import numpy as np

# ── 튜닝 파라미터 ───────────────────────────────────────────
# 측정/판정은 전부 축소본에서 한다. 1280x720 원본으로 Laplacian과
# minAreaRect를 돌리면 둘이서만 13ms를 먹어 20ms 예산이 깨진다.
# 흐림·기울기·변화량은 모두 저주파 특성이라 축소본으로도 충분하다.
ANALYZE_W = 640        # 선명도/기울기 판정용 축소 폭
SHARPNESS_MIN = 25.0   # Laplacian 분산(축소본 기준). 미만이면 흐린 프레임
DIFF_MIN = 2.0         # 직전 프레임 대비 평균 절대차. 미만이면 정지 화면
DIFF_W = 160           # 차분 비교용 축소 폭 (작을수록 싸고 둔감)
MEDIAN_K = 3           # 모아레 완화용 median blur 커널 (0이면 비활성)
CLAHE_CLIP = 2.0
CLAHE_GRID = (8, 8)
BLOCK_SIZE = 31        # adaptive threshold 창 크기 (홀수)
C_CONST = 15           # adaptive threshold 상수. 클수록 배경을 더 지운다
DESKEW_MAX_DEG = 15.0  # 이 각도를 넘으면 오검출로 보고 회전하지 않는다


def _deskew_angle(binary: np.ndarray) -> float:
    """
    글자 픽셀 전체를 감싸는 최소 사각형의 기울기로 각도를 추정한다.
    Hough 변환보다 훨씬 싸고, 문서처럼 텍스트가 화면을 채우는
    경우에는 정확도도 충분하다.

    minAreaRect 비용은 점 개수에 비례한다. 원본 해상도로 넣으면
    5ms가 넘어가므로 축소본에서 각도만 뽑는다. 각도는 스케일에
    영향받지 않으니 손해가 없다.
    """
    h, w = binary.shape
    if w > ANALYZE_W:
        binary = cv2.resize(binary, (ANALYZE_W, max(1, int(h * ANALYZE_W / w))),
                            interpolation=cv2.INTER_NEAREST)

    # binary는 흰 배경 / 검은 글자 → 글자를 흰색으로 뒤집어야 좌표를 딴다
    coords = cv2.findNonZero(255 - binary)
    if coords is None or len(coords) < 50:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]

    # minAreaRect의 각도 규약은 OpenCV 버전마다 다르다.
    # 4.x는 (0, 90], 5.0은 [-90, 0) 으로 준다.
    # 사각형의 방향은 애초에 90도 주기이므로, mod 90 으로 접고
    # (-45, 45] 로 옮기면 어느 규약이든 같은 답이 나온다.
    angle %= 90
    if angle > 45:
        angle -= 90
    return angle


def _rotate(img: np.ndarray, angle: float, border: int) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        img, m, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border,
    )


class Preprocessor:
    """
    연결 1개당 인스턴스 1개. 직전 프레임을 들고 있어야 차분 판정이
    되므로 상태를 가진다. 폰이 재접속하면 새로 만들어야 한다.
    """

    def __init__(self) -> None:
        self._clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=CLAHE_GRID)
        self._prev_small: np.ndarray | None = None

    def reset(self) -> None:
        self._prev_small = None

    def process(self, jpeg: bytes) -> dict:
        """
        반환 dict:
          ok        : 전처리를 끝까지 수행했는가 (False면 OCR 대상 아님)
          reason    : 폐기 사유 ('static' | 'blurry' | 'decode_fail' | None)
          sharpness : Laplacian 분산
          diff      : 직전 프레임 대비 평균 절대차
          angle     : 보정한 기울기 (도)
          ms        : 총 소요 시간
          image     : 전처리 결과 (ok일 때만, uint8 2D)
        """
        t0 = time.perf_counter()
        out = {
            "ok": False, "reason": None, "sharpness": 0.0,
            "diff": 0.0, "angle": 0.0, "ms": 0.0, "image": None,
        }

        def done(reason: str | None = None):
            out["reason"] = reason
            out["ms"] = (time.perf_counter() - t0) * 1000
            return out

        # ── 1. 디코드 ────────────────────────────────────────
        # 컬러로 받아 cvtColor 하면 4.6ms, 바로 흑백으로 디코드하면
        # 1.8ms다. 어차피 색은 한 번도 쓰지 않는다.
        buf = np.frombuffer(jpeg, np.uint8)
        gray = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return done("decode_fail")

        h, w = gray.shape
        analyze = cv2.resize(gray, (ANALYZE_W, max(1, int(h * ANALYZE_W / w))),
                             interpolation=cv2.INTER_AREA) if w > ANALYZE_W else gray

        # ── 2. 변화 없는 프레임 스킵 (가장 쌈) ────────────────
        ah, aw = analyze.shape
        small = cv2.resize(analyze, (DIFF_W, max(1, int(ah * DIFF_W / aw))),
                           interpolation=cv2.INTER_AREA)
        if self._prev_small is not None:
            diff = float(np.mean(cv2.absdiff(small, self._prev_small)))
            out["diff"] = diff
            if diff < DIFF_MIN:
                self._prev_small = small
                return done("static")
        self._prev_small = small

        # ── 3. 선명도 판정 (OCR 이전에 폐기) ──────────────────
        # 블러를 먹이기 전에, 축소본에서 잰다. CV_64F 원본이면 8.5ms지만
        # CV_16S 축소본이면 1.1ms다. 초점이 나간 프레임은 저해상도에서도
        # 똑같이 뭉개지므로 판정력은 유지된다.
        sharp = float(cv2.Laplacian(analyze, cv2.CV_16S).var())
        out["sharpness"] = sharp
        if sharp < SHARPNESS_MIN:
            return done("blurry")

        # ── 4. 모아레 완화 ───────────────────────────────────
        # 화면 촬영 시 픽셀 격자 간섭이 고주파 점무늬로 나타난다.
        # median은 획을 살리면서 이 점무늬만 지우는 데 gaussian보다 낫다.
        if MEDIAN_K >= 3:
            gray = cv2.medianBlur(gray, MEDIAN_K)

        # ── 5. 대비 보정 ─────────────────────────────────────
        # 전역 히스토그램 평활화는 조명이 한쪽만 밝을 때 무너진다.
        # CLAHE는 타일 단위라 그늘진 구석의 글자를 살려낸다.
        gray = self._clahe.apply(gray)

        # ── 6. 이진화 ────────────────────────────────────────
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            BLOCK_SIZE, C_CONST,
        )

        # ── 7. 기울기 보정 ───────────────────────────────────
        angle = _deskew_angle(binary)
        if abs(angle) > 0.3 and abs(angle) <= DESKEW_MAX_DEG:
            # INTER_LINEAR 회전은 획 가장자리에 중간 회색을 만든다.
            # OCR 입력은 2값이어야 깔끔하므로 다시 이진화한다.
            # (INTER_NEAREST로 돌리면 회색은 안 생기지만 가는 획이 끊긴다)
            binary = _rotate(binary, angle, border=255)
            _, binary = cv2.threshold(binary, 127, 255, cv2.THRESH_BINARY)
            out["angle"] = angle

        out["image"] = binary
        out["ok"] = True
        return done()


PREVIEW_W = 640


def encode_jpeg(img: np.ndarray, quality: int = 60, max_w: int = PREVIEW_W) -> bytes | None:
    """
    모니터 전송용 미리보기. 실패하면 None.

    이진 이미지는 경계가 날카로워서 JPEG 압축률이 나쁘다. 원본보다
    큰 90KB가 나오는데, 눈으로 확인하는 용도에 원본 해상도가 필요하지
    않으므로 640폭으로 줄여 보낸다. OCR에 넘길 이미지는 이걸 거치지
    않는다 — 여기서 줄인 건 미리보기뿐이다.
    """
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, max(1, int(h * max_w / w))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else None
