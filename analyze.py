"""Analyze the two synthetic scenarios and write a JSON report for each."""

import argparse
import json
import re
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import cv2
import numpy as np

from fixtures import DATA, ROOT, SCENARIOS


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def azure_config():
    values = {}
    path = ROOT / ".env"
    if not path.is_file():
        raise ValueError("Crie o arquivo .env a partir de .env.example.")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")
    required = (
        "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION",
        "AZURE_LANGUAGE_KEY", "AZURE_LANGUAGE_ENDPOINT",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ValueError("Campos ausentes no .env: " + ", ".join(missing))
    region = values["AZURE_SPEECH_REGION"]
    if not re.fullmatch(r"[a-z0-9]+", region):
        raise ValueError("AZURE_SPEECH_REGION deve conter o código da região.")
    endpoint = urlsplit(values["AZURE_LANGUAGE_ENDPOINT"])
    if (
        endpoint.scheme != "https"
        or not re.fullmatch(r"[\w-]+\.cognitiveservices\.azure\.com", endpoint.netloc, re.ASCII)
        or endpoint.path not in ("", "/")
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("AZURE_LANGUAGE_ENDPOINT deve ser o endereço HTTPS do recurso Azure.")
    return values


def post_json(service, url, key, content_type, body):
    request = Request(url, data=body, headers={
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": content_type,
        "Accept": "application/json",
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(f"{service}: Azure retornou HTTP {error.code}.") from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise ValueError(f"{service}: falha de conexão ou resposta inválida.") from None


def transcribe(audio, locale, config):
    region = config["AZURE_SPEECH_REGION"]
    path = "/speech/recognition/conversation/cognitiveservices/v1"
    query = urlencode({"language": locale})
    result = post_json(
        "Speech", f"https://{region}.stt.speech.microsoft.com{path}?{query}",
        config["AZURE_SPEECH_KEY"], "audio/wav; codecs=audio/pcm; samplerate=16000",
        audio.read_bytes(),
    )
    if result.get("RecognitionStatus") != "Success" or not result.get("DisplayText"):
        raise ValueError("Speech não retornou uma transcrição.")
    return result["DisplayText"]


def analyze_text(transcript, config):
    endpoint = config["AZURE_LANGUAGE_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/language/:analyze-text?api-version=2024-11-01"
    analysis = {}
    for kind, field in (("SentimentAnalysis", "sentiment"), ("KeyPhraseExtraction", "keyPhrases")):
        body = {
            "kind": kind,
            "analysisInput": {"documents": [{"id": "1", "language": "pt", "text": transcript}]},
        }
        result = post_json(
            kind, url, config["AZURE_LANGUAGE_KEY"], "application/json",
            json.dumps(body).encode("utf-8"),
        )
        documents = result.get("results", {}).get("documents", [])
        if not documents or field not in documents[0]:
            raise ValueError(f"Language não retornou resultado para {kind}.")
        analysis[field] = documents[0][field]

    terms = r"falta de ar|cansaço|cansad[oa]s?|dificuldade (?:para|em) falar"
    for match in re.finditer(rf"\b(?:{terms})\b", transcript, re.IGNORECASE):
        # Negation is scoped to the current phrase, up to five words before a term.
        prefix = re.split(r"[.!?,;:\n]|\b(?:mas|porém)\b", transcript[:match.start()], flags=re.I)[-1]
        negated = bool(re.search(r"\b(?:sem|não|nego|nega)\b(?:\s+\w+){0,5}\s+$", prefix, re.I))
        analysis.setdefault("critical_terms", []).append({
            "term": match.group(),
            "start_char": match.start(),
            "end_char": match.end(),
            "negated": negated,
        })
    analysis.setdefault("critical_terms", [])
    return analysis


def acoustic_features(path, transcript):
    with wave.open(str(path)) as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("O áudio precisa ser PCM mono, 16 bits, 16 kHz.")
        sample_rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    window = sample_rate // 50  # 20 ms
    frame_count = len(samples) // window
    if not frame_count or len(samples) > sample_rate * 60:
        raise ValueError("O WAV precisa conter entre 20 ms e 60 s de áudio.")
    frames = samples[:frame_count * window].astype(np.float64).reshape(-1, window)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    active = rms >= max(1, float(rms.max()) * 0.08)
    indices = np.flatnonzero(active)
    if not len(indices):
        raise ValueError("O áudio está silencioso.")
    gaps = np.diff(indices) - 1
    pause_lengths = gaps[gaps >= 10] * 0.02
    active_db = 20 * np.log10(rms[active] / 32768)
    span = (indices[-1] - indices[0] + 1) * 0.02
    word_count = len(re.findall(r"\b\w+\b", transcript))
    return {
        "duration_s": round(len(samples) / sample_rate, 3),
        "speech_span_s": round(float(span), 3),
        "words_per_minute": round(word_count * 60 / span, 1),
        "pause_count_at_least_200ms": len(pause_lengths),
        "total_pause_s": round(float(pause_lengths.sum()), 3),
        "intensity_p90_minus_p10_db": round(float(np.percentile(active_db, 90) - np.percentile(active_db, 10)), 2),
    }


def analyze_video(path):
    # The fixtures use a fixed camera and a dark right arm on a light background.
    capture = cv2.VideoCapture(str(path))
    heights = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or fps <= 0:
            raise ValueError("Não foi possível ler o vídeo.")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[:2] != (360, 480):
                raise ValueError("O analisador sintético requer vídeo de 480 x 360.")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Exclude the head, shoulder, trunk and legs from the right-arm region.
            rows, _ = np.where(gray[128:220, 251:330] < 125)
            heights.append(max(0, 212 - (128 + int(rows.min()))) if rows.size else 0)
    finally:
        capture.release()

    cycle_frames = round(fps * 4)
    if len(heights) < cycle_frames * 2 or len(heights) % cycle_frames:
        raise ValueError("O vídeo precisa conter pelo menos dois ciclos completos de 4 s.")
    cycles = []
    findings = []
    threshold = 70
    for index in range(0, len(heights), cycle_frames):
        peak = max(heights[index:index + cycle_frames])
        evidence = {
            "start_s": round(index / fps, 3),
            "end_s": round((index + cycle_frames) / fps, 3),
            "peak_elevation_px": peak,
            "minimum_elevation_px": threshold,
        }
        cycles.append(evidence)
        if peak < threshold:
            findings.append({"kind": "movimento", "description": "Elevação reduzida no ciclo", **evidence})
    return {"method": "opencv_synthetic_arm", "cycles": cycles, "findings": findings}


def run(scenario, config):
    folder = DATA / scenario
    manifest = json.loads((folder / "cenario.json").read_text(encoding="utf-8"))
    sources = {event["source"]: event for event in manifest["events"]}
    video = sources["video"]
    audio = sources["audio"]
    video_report = analyze_video(folder / video["file"])
    audio_path = folder / audio["file"]
    transcript = transcribe(audio_path, audio["voice_locale"], config)
    report = {
        "scenario": scenario,
        "patient_id": manifest["patient_id"],
        "video": {"event_id": video["id"], "timestamp": video["timestamp"], **video_report},
        "audio": {
            "event_id": audio["id"], "timestamp": audio["timestamp"],
            "locale": audio["voice_locale"], "transcript": transcript,
            **acoustic_features(audio_path, transcript),
        },
        "text": {"audio_event_id": audio["id"], **analyze_text(transcript, config)},
    }
    output = folder / "analise_fase2.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"{scenario}: {len(video_report['findings'])} desvio(s) de vídeo; relatório em {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=(*SCENARIOS, "todos"), default="todos", nargs="?")
    args = parser.parse_args()
    try:
        config = azure_config()
        for scenario in SCENARIOS if args.scenario == "todos" else (args.scenario,):
            run(scenario, config)
    except FileNotFoundError:
        parser.exit(1, "Dados ausentes. Execute fixtures.py generate primeiro.\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

