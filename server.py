"""Local presentation interface. Run with uvicorn server:app --host 127.0.0.1."""

import json
import re
from contextlib import closing
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from analyze import acoustic_features, analyze_text, azure_config, transcribe
from fixtures import DATA, ROOT
from media import MODEL, analyze_pose, convert_media
from monitor import (
    acknowledge, alert_details, iso, open_database, prepare_events, process_event,
    timestamp,
)


SESSIONS = ROOT / "data" / "sessions"
MAX_BYTES = 40 * 1024 * 1024
locks = {}
app = FastAPI(title="IADT Monitoramento Multimodal")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method != "GET" and origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse(
            {"detail": "Abra a interface pelo mesmo endereço do servidor."}, status_code=403,
        )
    return await call_next(request)


def session_path(session_id):
    try:
        if uuid.UUID(session_id).hex != session_id:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, "Sessão não encontrada.") from None
    path = SESSIONS / session_id
    if not (path / "session.json").is_file():
        raise HTTPException(404, "Sessão não encontrada.")
    return path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, content):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def session_alerts(path):
    with closing(open_database(path / "monitor.sqlite3")) as connection:
        ids = connection.execute("SELECT id FROM alerts ORDER BY created_at").fetchall()
        return [alert_details(connection, row["id"]) for row in ids]


class SessionInput(BaseModel):
    scenario: Literal["normal", "anomalo"] | None = None


class Confirmation(BaseModel):
    actor: str


@app.get("/api/health")
def health():
    try:
        azure_config()
        configured = True
    except ValueError:
        configured = False
    return {"azure_configured": configured, "pose_ready": MODEL.is_file()}


@app.post("/api/sessions")
def create_session(body: SessionInput):
    session_id = uuid.uuid4().hex
    path = SESSIONS / session_id
    path.mkdir(parents=True)
    metadata = {
        "id": session_id, "patient_id": f"PAC-WEB-{session_id[:8].upper()}",
        "scenario": body.scenario, "created_at": iso(datetime.now(timezone.utc)),
    }
    write_json(path / "session.json", metadata)
    return metadata


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    path = session_path(session_id)
    return {
        **read_json(path / "session.json"),
        "results": read_json(path / "results.json") if (path / "results.json").is_file() else {},
        "alerts": session_alerts(path),
    }


@app.post("/api/sessions/{session_id}/alerts/{alert_id}/ack")
def confirm(session_id: str, alert_id: str, body: Confirmation):
    path = session_path(session_id)
    if not body.actor.strip() or len(body.actor) > 100:
        raise HTTPException(400, "Informe um nome com até 100 caracteres.")
    try:
        with closing(open_database(path / "monitor.sqlite3")) as connection:
            acknowledge(connection, alert_id, body.actor)
    except ValueError as error:
        raise HTTPException(404, str(error)) from None
    return {"alerts": session_alerts(path)}


@app.post("/api/sessions/{session_id}/replay")
def replay_demo(session_id: str):
    path = session_path(session_id)
    scenario = read_json(path / "session.json")["scenario"]
    if not scenario:
        raise HTTPException(400, "Crie uma sessão de exemplo para reproduzir o fluxo.")
    folder = DATA / scenario
    try:
        manifest = read_json(folder / "cenario.json")
        report = read_json(folder / "analise_fase2.json")
        events = prepare_events(manifest, report)
    except (FileNotFoundError, ValueError, KeyError):
        raise HTTPException(400, "Execute fixtures.py generate e analyze.py todos antes da demonstração.") from None
    lock = locks.setdefault(session_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Esta sessão já está processando uma gravação.")
    write_json(path / "results.json", {"demo": report})

    def stream():
        connection = open_database(path / "monitor.sqlite3")
        previous = timestamp(events[0]["timestamp"])
        try:
            for event in events:
                current = timestamp(event["timestamp"])
                time.sleep(min(4, max(0, (current - previous).total_seconds())))
                previous = current
                received = time.perf_counter()
                alert_id, status = process_event(connection, event)
                if alert_id:
                    with connection:
                        connection.execute(
                            "UPDATE alerts SET displayed_at=?,latency_ms=? WHERE id=?",
                            (iso(datetime.now(timezone.utc)), (time.perf_counter() - received) * 1000, alert_id),
                        )
                payload = {"event": event, "status": status, "alerts": session_alerts(path)}
                yield json.dumps(payload, ensure_ascii=False) + "\n"
            yield json.dumps({"done": True, "report": report, "alerts": session_alerts(path)}, ensure_ascii=False) + "\n"
        finally:
            connection.close()
            lock.release()

    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@app.post("/api/sessions/{session_id}/analyze/{kind}")
def analyze_recording(
    session_id: str, kind: Literal["audio", "video"],
    file: UploadFile = File(...),
    side: Literal["left", "right"] = Form("right"),
    locale: Literal["pt-BR", "pt-PT"] = Form("pt-BR"),
):
    path = session_path(session_id)
    metadata = read_json(path / "session.json")
    if metadata["scenario"]:
        raise HTTPException(400, "Abra uma sessão de gravação para analisar novos arquivos.")
    lock = locks.setdefault(session_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Aguarde a análise atual terminar.")
    recording_id = uuid.uuid4().hex
    workdir = path / recording_id
    workdir.mkdir()
    try:
        uploaded = workdir / "source"
        size = 0
        with uploaded.open("wb") as destination:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "O limite por arquivo é 40 MB.")
                destination.write(chunk)
        if not size:
            raise ValueError("O arquivo está vazio.")
        converted = workdir / ("audio.wav" if kind == "audio" else "video.webm")
        duration = convert_media(uploaded, converted, kind)
        patient = metadata["patient_id"]
        base_event = {
            "patient_id": patient, "timestamp": iso(datetime.now(timezone.utc)),
            "origin_id": recording_id,
        }
        derived = []
        if kind == "video":
            result = analyze_pose(converted, side, workdir)
            result["preview_url"] = f"/api/media/{session_id}/{recording_id}/video.webm"
            if result["has_snapshot"]:
                result["snapshot_url"] = f"/api/media/{session_id}/{recording_id}/pose.jpg"
            for index, finding in enumerate(result["findings"]):
                derived.append({
                    **base_event, "id": f"{recording_id}:{index}",
                    "source": "video_analysis", "finding": finding,
                })
        else:
            config = azure_config()
            transcript = transcribe(converted, locale, config)
            audio = {
                "transcript": transcript, "locale": locale,
                **acoustic_features(converted, transcript),
            }
            text = analyze_text(transcript, config)
            result = {"audio": audio, "text": text, "duration_s": duration,
                      "preview_url": f"/api/media/{session_id}/{recording_id}/audio.wav"}
            derived.append({
                **base_event, "id": recording_id, "source": "audio_text_analysis",
                "audio": audio, "text": text,
            })
        with closing(open_database(path / "monitor.sqlite3")) as connection:
            for event in derived:
                received = time.perf_counter()
                alert_id, _ = process_event(connection, event)
                if alert_id:
                    with connection:
                        connection.execute(
                            "UPDATE alerts SET displayed_at=?,latency_ms=? WHERE id=?",
                            (iso(datetime.now(timezone.utc)), (time.perf_counter() - received) * 1000, alert_id),
                        )
        result["recording_id"] = recording_id
        results = read_json(path / "results.json") if (path / "results.json").is_file() else {}
        results[kind] = result
        write_json(path / "results.json", results)
        uploaded.unlink()
        return {"kind": kind, "result": result, "alerts": session_alerts(path)}
    except ValueError as error:
        shutil.rmtree(workdir)
        raise HTTPException(400, str(error)) from None
    except HTTPException:
        shutil.rmtree(workdir)
        raise
    finally:
        file.file.close()
        lock.release()


@app.get("/api/media/{session_id}/{recording_id}/{filename}")
def recorded_media(session_id: str, recording_id: str, filename: str):
    path = session_path(session_id)
    if not re.fullmatch(r"[a-f0-9]{32}", recording_id):
        raise HTTPException(404)
    allowed = {"audio.wav": "audio/wav", "video.mp4": "video/mp4", "video.webm": "video/webm", "pose.jpg": "image/jpeg"}
    target = path / recording_id / filename
    if filename not in allowed or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, media_type=allowed[filename])


@app.get("/api/examples/{scenario}/{filename}")
def example_media(scenario: Literal["normal", "anomalo"], filename: str):
    folder = DATA / scenario
    if filename == "consulta.wav" and (folder / filename).is_file():
        return FileResponse(folder / filename, media_type="audio/wav")
    source = folder / "movimento.mp4"
    if filename != "movimento.webm" or not source.is_file():
        raise HTTPException(404)
    target = folder / filename
    if not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
        temporary = folder / f"preview-{uuid.uuid4().hex}.webm"
        try:
            convert_media(source, temporary, "video")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(target, media_type="video/webm")


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")

