"""Generate and validate the synthetic Phase 1 scenarios."""

import argparse
import json
import math
from shutil import copyfile
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "generated"
FPS = 20
DURATION = 12
SCENARIOS = {
    "normal": "O exercício correu bem. Estou sem falta de ar.",
    "anomalo": "Estou muito cansado. Sinto falta de ar durante o exercício.",
}


def make_video(path, anomalous):
    size = (480, 360)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, size)
    if not writer.isOpened():
        raise RuntimeError(f"Não foi possível criar {path}")
    try:
        for frame_number in range(FPS * DURATION):
            seconds = frame_number / FPS
            lift = math.sin(math.pi * (seconds % 4) / 4)
            if anomalous and 4 <= seconds < 8:
                lift *= 0.25
            frame = np.full((size[1], size[0], 3), 245, dtype=np.uint8)
            shoulder = (240, 135)
            hip = (240, 225)
            hand = (int(240 + 85 * lift), int(212 - 112 * lift))
            color = (65, 55, 42)
            cv2.circle(frame, (240, 100), 23, color, 4)
            for start, end in (
                (shoulder, hip), (hip, (205, 310)), (hip, (275, 310)),
                (shoulder, (200, 208)), (shoulder, hand),
            ):
                cv2.line(frame, start, end, color, 5, cv2.LINE_AA)
            writer.write(frame)
    finally:
        writer.release()


def generate():
    start = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)

    def timestamp(seconds):
        return (start + timedelta(seconds=seconds)).isoformat()

    for scenario, transcript in SCENARIOS.items():
        anomalous = scenario == "anomalo"
        patient = f"PAC-{scenario.upper()}"
        folder = DATA / scenario
        folder.mkdir(parents=True, exist_ok=True)

        transcript_path = folder / "consulta.txt"
        transcript_path.write_text(transcript, encoding="utf-8")
        copyfile(ROOT / "data" / "reference" / f"{scenario}.wav", folder / "consulta.wav")
        make_video(folder / "movimento.mp4", anomalous)

        def event(event_id, source, seconds, **payload):
            return {
                "id": event_id, "patient_id": patient, "source": source,
                "timestamp": timestamp(seconds), **payload,
            }

        events = [
            event("video-1", "video", 0, file="movimento.mp4", duration_s=DURATION),
            event("audio-1", "audio", 0, file="consulta.wav", voice_locale="pt-PT"),
            event("texto-1", "texto", 0, file="consulta.txt", language="pt"),
            event("rx-1", "prescricao", 0, item_id="ITEM-DEMO", value=1, unit="unidade_demo"),
        ]
        for seconds in (0, 2, 4, 6, 8, 10):
            altered = anomalous and seconds in (4, 6)
            events.append(event(
                f"vital-{seconds}", "monitor", seconds,
                heart_rate={"value": 128 if altered else 76 + seconds, "unit": "bpm"},
                systolic={"value": 95 if altered else 118, "unit": "mmHg"},
                diastolic={"value": 62 if altered else 76, "unit": "mmHg"},
                spo2={"value": 89 if altered else 98, "unit": "%"},
            ))
        events.append(event(
            "rx-2", "prescricao", 6, item_id="ITEM-DEMO",
            value=3 if anomalous else 1, unit="unidade_demo",
        ))
        events.sort(key=lambda item: item["timestamp"])
        expected = [
            {"kind": "movimento", "start": timestamp(4), "end": timestamp(8), "events": ["video-1"]},
            {"kind": "sinais_vitais", "start": timestamp(4), "end": timestamp(8), "events": ["vital-4", "vital-6"]},
            {"kind": "prescricao", "start": timestamp(6), "end": timestamp(6), "events": ["rx-1", "rx-2"]},
            {"kind": "audio_texto", "start": timestamp(0), "end": timestamp(12), "events": ["audio-1", "texto-1"]},
        ] if anomalous else []
        manifest = {
            "schema_version": 1, "patient_id": patient, "origin": "sintetico",
            "events": events, "expected_findings": expected,
        }
        (folder / "cenario.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate():
    for scenario in SCENARIOS:
        folder = DATA / scenario
        manifest = json.loads((folder / "cenario.json").read_text(encoding="utf-8"))
        events = manifest["events"]
        by_id = {event["id"]: event for event in events}
        require(len(by_id) == len(events), "IDs de evento duplicados")
        require(all(e["patient_id"] == manifest["patient_id"] for e in events), "Paciente divergente")
        times = [datetime.fromisoformat(e["timestamp"]) for e in events]
        require(all(t.utcoffset() == timedelta(0) for t in times), "Horário fora de UTC")
        require(times == sorted(times), "Eventos fora de ordem")
        require({e["source"] for e in events} == {
            "video", "audio", "texto", "prescricao", "monitor",
        }, "Modalidade ausente")
        for event in events:
            if "file" in event:
                require((folder / event["file"]).is_file(), f"Mídia ausente: {event['file']}")
        with wave.open(str(folder / by_id["audio-1"]["file"])) as audio:
            require(audio.getparams()[:3] == (1, 2, 16000), "WAV fora do formato esperado")
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
            require(samples.size > 16000 and np.any(samples), "Áudio vazio ou silencioso")
        capture = cv2.VideoCapture(str(folder / by_id["video-1"]["file"]))
        try:
            frames = 0
            while capture.read()[0]:
                frames += 1
            require(frames == FPS * DURATION, f"Quantidade inesperada de quadros: {frames}")
        finally:
            capture.release()
        require(
            (folder / by_id["texto-1"]["file"]).read_text(encoding="utf-8") == SCENARIOS[scenario],
            "Transcrição de referência divergente",
        )
        findings = manifest["expected_findings"]
        kinds = {"movimento", "sinais_vitais", "prescricao", "audio_texto"} if scenario == "anomalo" else set()
        require({f["kind"] for f in findings} == kinds, "Achados anotados divergentes")
        for finding in findings:
            require(finding["start"] <= finding["end"], "Intervalo de achado invertido")
            require(all(event_id in by_id for event_id in finding["events"]), "Referência inválida")
        print(f"{scenario}: {len(events)} eventos, {frames} quadros, {len(findings)} achados anotados")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate"))
    args = parser.parse_args()
    if args.command == "generate":
        generate()
    validate()

