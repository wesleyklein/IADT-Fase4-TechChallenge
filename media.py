"""Decode browser recordings and measure a simple arm-elevation exercise."""

import math
import subprocess
import wave

import cv2
import imageio_ffmpeg
import mediapipe as mp
import numpy as np

from fixtures import ROOT


MODEL = ROOT / "models" / "pose_landmarker_lite.task"


def convert_media(source, destination, kind):
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-v", "error",
        "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "wav,ogg,mov,matroska,webm,mp3,avi,mpegts,aac,flac",
        "-i", str(source), "-t", "61",
    ]
    if kind == "audio":
        command += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
    else:
        command += [
            "-an", "-vf", "scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,fps=10",
            "-c:v", "libvpx", "-pix_fmt", "yuv420p", "-deadline", "realtime", "-cpu-used", "6", "-b:v", "900k",
        ]
    try:
        subprocess.run(command + [str(destination)], check=True, capture_output=True, timeout=90)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError("Não foi possível ler a gravação. Tente outro arquivo de áudio ou vídeo.") from None
    if kind == "audio":
        with wave.open(str(destination)) as audio:
            duration = audio.getnframes() / audio.getframerate()
    else:
        capture = cv2.VideoCapture(str(destination))
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps else 0
        finally:
            capture.release()
    if not 1 <= duration <= 60:
        raise ValueError("A gravação precisa ter entre 1 e 60 segundos.")
    return duration


def elevation(hip, shoulder, elbow, aspect):
    first = np.array([(hip.x - shoulder.x) * aspect, hip.y - shoulder.y])
    second = np.array([(elbow.x - shoulder.x) * aspect, elbow.y - shoulder.y])
    norm = np.linalg.norm(first) * np.linalg.norm(second)
    if norm < 1e-6:
        return None
    cosine = np.clip(np.dot(first, second) / norm, -1, 1)
    return round(math.degrees(math.acos(float(cosine))), 1)


def analyze_pose(path, side, workdir):
    if not MODEL.is_file():
        raise ValueError("Modelo de pose ausente. Execute setup_model.py.")
    indices = (11, 13, 23) if side == "left" else (12, 14, 24)
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_poses=1,
    )
    samples = []
    capture = cv2.VideoCapture(str(path))
    snapshot = None
    best_angle = -1
    frame_index = 0
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as detector:
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                raise ValueError("Vídeo sem quadros válidos.")
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                seconds = frame_index / fps
                frame_index += 1
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                result = detector.detect_for_video(image, round(seconds * 1000))
                angle = None
                if result.pose_landmarks:
                    landmarks = result.pose_landmarks[0]
                    selected = [landmarks[index] for index in indices]
                    if all(
                        (point.visibility or 0) >= 0.6
                        and (point.presence or 0) >= 0.6
                        and 0 <= point.x <= 1 and 0 <= point.y <= 1
                        for point in selected
                    ):
                        shoulder, elbow, hip = selected
                        angle = elevation(hip, shoulder, elbow, frame.shape[1] / frame.shape[0])
                        if angle is not None and angle > best_angle:
                            best_angle = angle
                            snapshot = frame.copy()
                            height, width = frame.shape[:2]
                            coords = [(int(p.x * width), int(p.y * height)) for p in selected]
                            for first, second in ((coords[0], coords[1]), (coords[0], coords[2])):
                                cv2.line(snapshot, first, second, (130, 220, 30), 4)
                            for coordinate in coords:
                                cv2.circle(snapshot, coordinate, 7, (130, 220, 30), -1)
                            cv2.putText(snapshot, f"{angle:.0f} deg", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (130, 220, 30), 2)
                samples.append({"time_s": round(seconds, 2), "angle_deg": angle})
        finally:
            capture.release()

    duration = frame_index / fps
    cycles = []
    findings = []
    complete_cycles = int(duration // 4)
    for index in range(complete_cycles):
        start = index * 4
        segment = [sample for sample in samples if start <= sample["time_s"] < start + 4]
        valid = [sample["angle_deg"] for sample in segment if sample["angle_deg"] is not None]
        coverage = len(valid) / len(segment) if segment else 0
        peak = max(valid) if valid else None
        cycle = {
            "start_s": start, "end_s": start + 4,
            "coverage": round(coverage, 2), "peak_angle_deg": peak,
            "minimum_angle_deg": 60,
            "status": "insuficiente" if coverage < 0.6 else "reduzido" if peak < 60 else "atingido",
        }
        cycles.append(cycle)
        if cycle["status"] == "reduzido":
            findings.append({
                "kind": "movimento", "description": "Elevação abaixo de 60 graus na janela de 4 s",
                **cycle,
            })
    if snapshot is not None:
        cv2.imwrite(str(workdir / "pose.jpg"), snapshot)
    return {
        "method": "mediapipe_pose_landmarker_lite", "side": side,
        "duration_s": round(duration, 2), "samples": samples,
        "coverage": round(sum(s["angle_deg"] is not None for s in samples) / max(1, len(samples)), 2),
        "cycles": cycles, "findings": findings, "has_snapshot": snapshot is not None,
        "note": "Regra demonstrativa para elevação lateral do braço em janelas de 4 s. Ângulo estimado na imagem.",
    }

