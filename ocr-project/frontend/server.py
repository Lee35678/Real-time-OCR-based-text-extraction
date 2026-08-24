"""
모바일 카메라 → PC 수신 확인용 PoC 서버

  pip install fastapi uvicorn
  python server.py

  폰   : https://xxxx.ngrok-free.app/         (촬영)
  PC   : http://localhost:8000/monitor        (수신 확인)
"""
import asyncio
import json
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from preprocess import Preprocessor, encode_jpeg
from ocr_pipeline import LatestOCRPipeline, OCRJob
from session_words import SessionWords

app = FastAPI()
HERE = Path(__file__).parent

# 서버 기동 시 1회 생성. 프로세스가 살아있는 동안만 유효하다.
SESSION_TOKEN = secrets.token_urlsafe(16)

MAX_FRAME_BYTES = 2 * 1024 * 1024      # 2MB 초과 프레임은 연결 종료

WS_CLOSE_TOO_BIG = 1009
WS_CLOSE_POLICY = 1008

monitors: set[WebSocket] = set()   # PC 모니터 소켓들
stats = {"frames": 0, "bytes": 0, "started": None}
session_words = SessionWords()


def token_ok(given: str | None) -> bool:
    """타이밍 공격을 피하기 위해 compare_digest 로 비교한다."""
    if not given:
        return False
    return secrets.compare_digest(given, SESSION_TOKEN)


def is_local_host_header(request: Request) -> bool:
    """
    ngrok 에이전트도 127.0.0.1 에서 접속해오므로 client.host 로는 구분이 안 된다.
    Host 헤더로 판별해야 터널 경유 요청에 토큰이 새지 않는다.
    """
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "[::1]", "::1")


@app.get("/")
def capture_page():
    """폰에서 여는 촬영 페이지"""
    return FileResponse(HERE / "capture.html")


@app.get("/monitor")
def monitor_page(request: Request):
    """PC에서 여는 수신 확인 페이지. 토큰을 페이지에 심어 전달한다."""
    if not is_local_host_header(request):
        return PlainTextResponse(
            "모니터는 이 서버가 도는 PC에서 http://localhost:8000/monitor 로만 열 수 있습니다.",
            status_code=403,
        )
    html = (HERE / "monitor.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__SESSION_TOKEN__", SESSION_TOKEN))


@app.get("/qr")
def qr(u: str):
    """접속 주소를 QR PNG로 변환 — pip install qrcode[pil]"""
    import io

    import qrcode
    from fastapi.responses import Response

    img = qrcode.make(u)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


def require_http_token(request: Request) -> None:
    from fastapi import HTTPException
    if not token_ok(request.query_params.get("t")):
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/api/session/words")
def list_session_words(request: Request):
    require_http_token(request)
    return {"items": session_words.list()}


@app.delete("/api/session/words/{item_id}")
def delete_session_word(item_id: str, request: Request):
    require_http_token(request)
    if not session_words.delete(item_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="word not found")
    return Response(status_code=204)


@app.get("/api/session/words.csv")
def export_session_words(request: Request):
    require_http_token(request)
    return Response(
        session_words.csv_bytes(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="session-words.csv"'},
    )


@app.websocket("/ws/send")
async def ws_send(ws: WebSocket):
    """폰 → 서버. 헤더(JSON) 다음에 프레임(binary)이 한 쌍으로 들어온다."""
    if not token_ok(ws.query_params.get("t")):
        # accept 전에는 close 코드가 전달되지 않으므로 accept 후 닫는다
        await ws.accept()
        await ws.close(code=WS_CLOSE_POLICY, reason="invalid token")
        print("[!] 토큰 없는/잘못된 폰 연결 거부")
        return

    await ws.accept()
    print("\n[+] 폰 연결됨")
    # 세션마다 초기화 — 누적 평균이 아니라 이번 세션 값을 본다
    stats["frames"] = 0
    stats["bytes"] = 0
    stats["started"] = time.time()
    recent: list[float] = []      # 최근 프레임 도착 시각
    last_size = None
    frozen = 0

    # 연결마다 새로 만든다 — 직전 프레임 상태를 물려받으면 안 된다
    pre = Preprocessor()
    send_lock = asyncio.Lock()
    last_detected_at = time.monotonic()
    guide_visible = False

    async def send_ocr_result(job, tokens, ocr_ms):
        nonlocal last_detected_at, guide_visible
        added = session_words.add_tokens(tokens, job.seq)
        now = time.monotonic()
        if tokens:
            last_detected_at = now
        show_guide = now - last_detected_at >= 5.0
        guide_visible = show_guide
        payload = {
            "type": "ocr_result", "version": 1, "seq": job.seq,
            "source": {"w": job.source_w, "h": job.source_h},
            "tokens": tokens, "new_words": added,
            "guide": {
                "visible": show_guide,
                "code": "NO_TEXT_5S" if show_guide else None,
                "message": "텍스트가 화면에 크게 보이도록 가까이 이동하세요." if show_guide else None,
            },
            "timing": {"ocr_ms": round(ocr_ms, 1), "queue_dropped": ocr.dropped},
            "captured_at_ms": job.captured_at_ms,
        }
        try:
            async with send_lock:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def guide_timer():
        nonlocal guide_visible
        while True:
            await asyncio.sleep(1)
            should_show = time.monotonic() - last_detected_at >= 5.0
            if should_show == guide_visible:
                continue
            guide_visible = should_show
            payload = {
                "type": "guide", "version": 1,
                "visible": should_show,
                "code": "NO_TEXT_5S" if should_show else None,
                "message": "텍스트가 화면에 크게 보이도록 가까이 이동하세요." if should_show else None,
            }
            try:
                async with send_lock:
                    await ws.send_text(json.dumps(payload, ensure_ascii=False))
            except (WebSocketDisconnect, RuntimeError):
                return

    ocr = LatestOCRPipeline(send_ocr_result)
    ocr.start()
    guide_task = asyncio.create_task(guide_timer())
    kept = 0
    dropped = {"static": 0, "blurry": 0, "decode_fail": 0}
    proc_ms: list[float] = []

    header = None
    try:
        while True:
            msg = await ws.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("text") is not None:
                header = json.loads(msg["text"])
                continue

            data = msg.get("bytes")
            if data is None:
                continue
            if len(data) > MAX_FRAME_BYTES:
                print(f"  [!] 프레임 {len(data)/1024/1024:.1f}MB, 상한 초과 - 연결 종료", flush=True)
                await ws.close(code=WS_CLOSE_TOO_BIG, reason="frame too large")
                return
            if header is None:
                print("  [!] 헤더 없이 바이너리 수신 - 건너뜀", flush=True)
                continue

            stats["frames"] += 1
            stats["bytes"] += len(data)
            seq = header.get("seq", 0)

            # ── 전처리 (P0-2). 응답 경로이므로 20ms 예산 ──────
            r = pre.process(data)
            proc_ms.append(r["ms"])
            if len(proc_ms) > 20:
                proc_ms.pop(0)
            if r["ok"]:
                kept += 1
                ocr.submit(OCRJob(
                    seq=seq, image=r["image"],
                    source_w=int(header.get("w") or r["image"].shape[1]),
                    source_h=int(header.get("h") or r["image"].shape[0]),
                    captured_at_ms=int(header.get("captured_at_ms") or time.time() * 1000),
                ))
            else:
                dropped[r["reason"]] = dropped.get(r["reason"], 0) + 1

            proc_jpeg = encode_jpeg(r["image"]) if r["ok"] else None

            # 최근 20프레임 기준 실시간 fps
            now = time.time()
            recent.append(now)
            if len(recent) > 20:
                recent.pop(0)
            fps = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0.0

            # 같은 크기가 반복되면 화면이 멈춘 것 (백그라운드 전환 등)
            if last_size == len(data):
                frozen += 1
            else:
                frozen = 0
            last_size = len(data)
            flag = "  ← 정지 화면?" if frozen >= 5 else ""

            verdict = "OK  " if r["ok"] else f"DROP {r['reason']}"
            print(
                f"  #{seq:<5} {len(data)/1024:6.1f}KB  {fps:4.1f}fps  "
                f"| {verdict:<12} sharp {r['sharpness']:7.1f}  "
                f"diff {r['diff']:5.1f}  skew {r['angle']:+5.1f}deg  "
                f"{r['ms']:5.1f}ms  "
                f"| 통과 {kept}/{stats['frames']}{flag}",
                flush=True,
            )

            # 폰에게 응답 (폰이 왕복 지연을 계산할 수 있게)
            async with send_lock:
                await ws.send_text(json.dumps({"type": "ack", "seq": seq, "size": len(data)}))

            # PC 모니터들에게 중계
            # 메타(text) → 원본(binary) → [전처리본(binary)] 순서.
            # 전처리본은 통과한 프레임에만 붙으므로 has_proc 로 알린다.
            meta = json.dumps({
                "seq": seq, "size": len(data),
                "w": header.get("w"), "h": header.get("h"),
                "frames": stats["frames"], "fps": round(fps, 1),
                "total_mb": round(stats["bytes"] / 1024 / 1024, 2),
                # 전처리 결과
                "ok": r["ok"], "reason": r["reason"],
                "sharpness": round(r["sharpness"], 1),
                "diff": round(r["diff"], 2),
                "angle": round(r["angle"], 2),
                "proc_ms": round(r["ms"], 1),
                "proc_ms_avg": round(sum(proc_ms) / len(proc_ms), 1),
                "kept": kept,
                "dropped": dict(dropped),
                "has_proc": proc_jpeg is not None,
            })
            dead = []
            for m in monitors:
                try:
                    await m.send_text(meta)
                    await m.send_bytes(data)
                    if proc_jpeg is not None:
                        await m.send_bytes(proc_jpeg)
                except Exception:
                    dead.append(m)
            for m in dead:
                monitors.discard(m)

            header = None

    except WebSocketDisconnect:
        avg = sum(proc_ms) / len(proc_ms) if proc_ms else 0.0
        print(
            f"[-] 폰 연결 끊김 | 수신 {stats['frames']}장  통과 {kept}장  "
            f"폐기 {dict(dropped)}  전처리 평균 {avg:.1f}ms\n"
        )
    except Exception as e:
        import traceback
        print(f"[!] 서버 오류: {type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        guide_task.cancel()
        try:
            await guide_task
        except asyncio.CancelledError:
            pass
        await ocr.close()


@app.websocket("/ws/watch")
async def ws_watch(ws: WebSocket):
    """서버 → PC 모니터"""
    if not token_ok(ws.query_params.get("t")):
        await ws.accept()
        await ws.close(code=WS_CLOSE_POLICY, reason="invalid token")
        print("[!] 토큰 없는/잘못된 모니터 연결 거부")
        return

    await ws.accept()
    monitors.add(ws)
    print(f"[+] 모니터 연결됨 (총 {len(monitors)}개)")
    try:
        while True:
            await ws.receive_text()      # 연결 유지용
    except WebSocketDisconnect:
        monitors.discard(ws)
        print(f"[-] 모니터 연결 끊김 (남은 {len(monitors)}개)")


if __name__ == "__main__":
    import socket
    import uvicorn

    def local_ip() -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    ip = local_ip()
    has_cert = (HERE / "cert.pem").exists() and (HERE / "key.pem").exists()
    scheme = "https" if has_cert else "http"

    print("=" * 56)
    print(f"  세션 토큰 : {SESSION_TOKEN}")
    print("  모니터를 http://localhost:8000/monitor 로 열면 QR에 자동 포함됩니다.")
    print("-" * 56)
    if has_cert:
        print(f"  폰   : {scheme}://{ip}:8000")
        print(f"  PC   : {scheme}://{ip}:8000/monitor")
        print("  ※ 폰에서 '안전하지 않음' 경고 → 고급 → 계속 진행")
    else:
        print("  cert.pem / key.pem 이 없습니다.")
        print("  python make_cert.py 를 먼저 실행하세요.")
        print(f"  (지금은 http 모드 — 카메라가 열리지 않습니다)")
        print(f"  PC 확인용 : http://localhost:8000/monitor")
    print("=" * 56)

    uvicorn.run(
        app, host="0.0.0.0", port=8000, log_level="warning",
        ssl_certfile=str(HERE / "cert.pem") if has_cert else None,
        ssl_keyfile=str(HERE / "key.pem") if has_cert else None,
    )
