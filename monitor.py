"""Replay synthetic patient events and route alerts to a simulated care team."""

import argparse
import hashlib
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fixtures import DATA, ROOT, SCENARIOS


DATABASE = ROOT / "data" / "monitoramento.sqlite3"
WINDOW_SECONDS = 10
LIMITS = {
    "heart_rate": (50, 110, "bpm"),
    "systolic": (100, 160, "mmHg"),
    "diastolic": (65, 100, "mmHg"),
    "spo2": (94, 100, "%"),
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Horários precisam incluir o fuso.")
    return result.astimezone(timezone.utc)


def iso(value):
    return value.isoformat()


def open_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            patient TEXT NOT NULL,
            id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            payload TEXT NOT NULL,
            valid INTEGER NOT NULL,
            PRIMARY KEY (patient, id)
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            patient TEXT NOT NULL,
            first_event TEXT NOT NULL,
            last_event TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            displayed_at TEXT,
            latency_ms REAL
        );
        CREATE TABLE IF NOT EXISTS findings (
            patient TEXT NOT NULL,
            id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            kind TEXT NOT NULL,
            evidence TEXT NOT NULL,
            alert_id TEXT NOT NULL REFERENCES alerts(id),
            PRIMARY KEY (patient, id)
        );
        CREATE TABLE IF NOT EXISTS acknowledgments (
            id INTEGER PRIMARY KEY,
            alert_id TEXT NOT NULL REFERENCES alerts(id),
            actor TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS warnings (
            patient TEXT NOT NULL,
            event_id TEXT NOT NULL,
            message TEXT NOT NULL,
            PRIMARY KEY (patient, event_id)
        );
    """)
    return connection


def prepare_events(manifest, report):
    patient = manifest["patient_id"]
    if report["patient_id"] != patient:
        raise ValueError("Paciente do relatório difere do cenário.")
    events = list(manifest["events"])
    missing = [name for name in ("video", "audio", "text") if not report.get(name)]
    if missing:
        anchor = min(events, key=lambda event: timestamp(event["timestamp"]))
        events.append({
            "id": "analysis:missing", "patient_id": patient,
            "source": "missing_analysis", "timestamp": anchor["timestamp"],
            "missing": missing,
        })
    by_id = {event["id"]: event for event in events}
    for event in events:
        if event["patient_id"] != patient:
            raise ValueError("O cenário contém pacientes diferentes.")
        timestamp(event["timestamp"])

    video = report.get("video")
    if video:
        origin = by_id[video["event_id"]]
        if origin["source"] != "video" or timestamp(origin["timestamp"]) != timestamp(video["timestamp"]):
            raise ValueError("Referência de vídeo inconsistente.")
        for index, finding in enumerate(video["findings"]):
            end = float(finding["end_s"])
            start = float(finding["start_s"])
            if not 0 <= start < end <= float(origin["duration_s"]):
                raise ValueError("Intervalo de vídeo inválido.")
            events.append({
                "id": f"analysis:{origin['id']}:{index}", "patient_id": patient,
                "source": "video_analysis",
                "timestamp": iso(timestamp(origin["timestamp"]) + timedelta(seconds=end)),
                "finding": finding, "origin_id": origin["id"],
            })
    audio = report.get("audio")
    text = report.get("text")
    if audio and text:
        origin = by_id[audio["event_id"]]
        if (
            origin["source"] != "audio"
            or text["audio_event_id"] != origin["id"]
            or timestamp(origin["timestamp"]) != timestamp(audio["timestamp"])
        ):
            raise ValueError("Referência de áudio inconsistente.")
        events.append({
            "id": f"analysis:{origin['id']}", "patient_id": patient,
            "source": "audio_text_analysis",
            "timestamp": iso(timestamp(origin["timestamp"]) + timedelta(seconds=audio["duration_s"])),
            "origin_id": origin["id"], "audio": audio, "text": text,
        })
    for event in events:
        if not event.get("id") or not event.get("source"):
            raise ValueError("Evento sem ID ou origem.")
    return sorted(events, key=lambda event: timestamp(event["timestamp"]))


def abnormal_vitals(event):
    deviations = []
    for name, (lower, upper, unit) in LIMITS.items():
        measurement = event.get(name)
        if measurement is None:
            continue
        value = measurement.get("value")
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or measurement.get("unit") != unit
        ):
            raise ValueError(f"Medição inválida em {name}.")
        if not lower <= value <= upper:
            deviations.append({
                "metric": name, "value": value, "unit": unit,
                "expected_demo_range": [lower, upper],
            })
    return deviations


def detect(connection, event):
    patient = event["patient_id"]
    when = iso(timestamp(event["timestamp"]))
    source = event["source"]
    if source == "missing_analysis":
        raise ValueError("Análises ausentes: " + ", ".join(event["missing"]))
    if source == "monitor":
        deviations = abnormal_vitals(event)
        previous = connection.execute(
            """SELECT payload FROM events WHERE patient=? AND source='monitor'
               AND valid=1 AND timestamp<=? AND id<>?
               ORDER BY timestamp DESC, rowid DESC LIMIT 1""",
            (patient, when, event["id"]),
        ).fetchone()
        if previous and deviations:
            prior = json.loads(previous["payload"])
            gap = (timestamp(when) - timestamp(prior["timestamp"])).total_seconds()
            prior_deviations = abnormal_vitals(prior)
            prior_metrics = {item["metric"] for item in prior_deviations}
            persistent = [item for item in deviations if item["metric"] in prior_metrics]
            if persistent and 0 < gap <= 4:
                return "sinais_vitais", {
                    "event_ids": [prior["id"], event["id"]],
                    "deviations": persistent,
                    "rule": "Mesma medida fora da faixa em duas amostras separadas por até 4 s",
                }
    elif source == "prescricao":
        value = event.get("value")
        if (
            not event.get("item_id") or not event.get("unit")
            or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0
        ):
            raise ValueError("Prescrição incompleta ou valor inválido.")
        rows = connection.execute(
            """SELECT payload FROM events WHERE patient=? AND source='prescricao'
               AND valid=1 AND timestamp<=? AND id<>?
               ORDER BY timestamp DESC, rowid DESC""",
            (patient, when, event["id"]),
        )
        prior = next(
            (item for row in rows if (item := json.loads(row["payload"]))["item_id"] == event["item_id"]),
            None,
        )
        if prior:
            if prior["unit"] != event["unit"]:
                raise ValueError("Unidades de prescrição incompatíveis.")
            ratio = value / prior["value"]
            if ratio >= 2 or ratio <= 0.5:
                return "prescricao", {
                    "event_ids": [prior["id"], event["id"]],
                    "item_id": event["item_id"], "before": prior["value"], "after": value,
                    "unit": event["unit"], "rule": "Valor duplicou ou foi reduzido à metade",
                }
    elif source == "video_analysis":
        return "movimento", {
            "event_ids": [event["origin_id"]], **event["finding"],
            "rule": "Desvio do relatório de movimento",
        }
    elif source == "audio_text_analysis":
        terms = [term for term in event["text"]["critical_terms"] if not term["negated"]]
        if terms:
            return "audio_texto", {
                "event_ids": [event["origin_id"]],
                "terms": terms, "transcript": event["audio"]["transcript"],
                "words_per_minute": event["audio"]["words_per_minute"],
                "rule": "Termos críticos afirmados na transcrição",
            }
    return None


def save_finding(connection, event, finding):
    kind, evidence = finding
    patient = event["patient_id"]
    when = timestamp(event["timestamp"])
    lower = iso(when - timedelta(seconds=WINDOW_SECONDS))
    # A group spans at most ten seconds from its first finding, even across retries.
    alert = connection.execute(
        """SELECT * FROM alerts WHERE patient=? AND first_event>=? AND last_event<=?
           ORDER BY first_event DESC LIMIT 1""",
        (patient, lower, iso(when)),
    ).fetchone()
    if alert:
        alert_id = alert["id"]
    else:
        digest = hashlib.sha256(f"{patient}|{event['id']}".encode()).hexdigest()[:16]
        alert_id = f"ALT-{digest}"
        connection.execute(
            """INSERT INTO alerts
               (id,patient,first_event,last_event,severity,status,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (alert_id, patient, iso(when), iso(when), "atencao", "aberto", iso(datetime.now(timezone.utc))),
        )
    connection.execute(
        "INSERT INTO findings VALUES (?,?,?,?,?,?)",
        (patient, event["id"], iso(when), kind, canonical(evidence), alert_id),
    )
    count = connection.execute(
        "SELECT COUNT(DISTINCT kind) FROM findings WHERE alert_id=?", (alert_id,),
    ).fetchone()[0]
    connection.execute(
        """UPDATE alerts SET last_event=?, severity=?, status='aberto', displayed_at=NULL, latency_ms=NULL WHERE id=?""",
        (iso(when), "alta" if count >= 2 else "atencao", alert_id),
    )
    return alert_id


def alert_details(connection, alert_id):
    row = connection.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if row is None:
        raise ValueError("Alerta não encontrado.")
    alert = dict(row)
    alert["evidence"] = [
        {"kind": item["kind"], "timestamp": item["timestamp"], **json.loads(item["evidence"])}
        for item in connection.execute(
            "SELECT * FROM findings WHERE alert_id=? ORDER BY timestamp", (alert_id,),
        )
    ]
    alert["acknowledgments"] = [
        dict(item) for item in connection.execute(
            "SELECT actor,timestamp FROM acknowledgments WHERE alert_id=? ORDER BY id", (alert_id,),
        )
    ]
    return alert


def process_event(connection, event):
    patient = event["patient_id"]
    payload = canonical(event)
    when = iso(timestamp(event["timestamp"]))
    with connection:
        # Serialize concurrent writers before checking event identity.
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT payload FROM events WHERE patient=? AND id=?", (patient, event["id"]),
        ).fetchone()
        if existing:
            if existing["payload"] != payload:
                raise ValueError(f"Evento {event['id']} já existe com conteúdo diferente.")
            return None, "duplicado"
        latest = connection.execute(
            "SELECT MAX(timestamp) FROM events WHERE patient=?", (patient,),
        ).fetchone()[0]
        if latest and when < latest:
            raise ValueError("Evento atrasado. Reproduza eventos em ordem temporal.")
        connection.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,1)",
            (patient, event["id"], when, event["source"], payload),
        )
        try:
            finding = detect(connection, event)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            message = str(error)
            connection.execute(
                "UPDATE events SET valid=0 WHERE patient=? AND id=?", (patient, event["id"]),
            )
            connection.execute("INSERT INTO warnings VALUES (?,?,?)", (patient, event["id"], message))
            return None, message
        if event["source"] == "monitor":
            missing = sorted(set(LIMITS) - event.keys())
            if missing:
                connection.execute(
                    "INSERT INTO warnings VALUES (?,?,?)",
                    (patient, event["id"], "Medidas ausentes: " + ", ".join(missing)),
                )
        return save_finding(connection, event, finding) if finding else None, "processado"


def display_alert(connection, alert_id, received_at):
    alert = alert_details(connection, alert_id)
    kinds = sorted({item["kind"] for item in alert["evidence"]})
    print(
        f"ALERTA {alert_id} | {alert['patient']} | {alert['severity']} | "
        f"{alert['last_event']} | {', '.join(kinds)} | aberto",
        flush=True,
    )
    for evidence in alert["evidence"]:
        print(f"  {evidence['kind']}: {canonical(evidence)}", flush=True)
    latency = (time.perf_counter() - received_at) * 1000
    with connection:
        connection.execute(
            "UPDATE alerts SET displayed_at=?, latency_ms=? WHERE id=?",
            (iso(datetime.now(timezone.utc)), latency, alert_id),
        )
    print(f"  Recebimento até exibição no terminal: {latency:.2f} ms", flush=True)


def replay(connection, scenario, speed=0):
    folder = DATA / scenario
    manifest = json.loads((folder / "cenario.json").read_text(encoding="utf-8"))
    report = json.loads((folder / "analise_fase2.json").read_text(encoding="utf-8"))
    events = prepare_events(manifest, report)
    previous = timestamp(events[0]["timestamp"])
    for event in events:
        current = timestamp(event["timestamp"])
        if speed:
            time.sleep((current - previous).total_seconds() / speed)
        previous = current
        received_at = time.perf_counter()
        alert_id, status = process_event(connection, event)
        if alert_id:
            display_alert(connection, alert_id, received_at)
        elif status not in ("processado", "duplicado"):
            print(f"Dado incompleto ou inválido em {event['id']}: {status}", flush=True)
    # Retry-safe delivery if a previous process stopped after commit and before printing.
    pending = connection.execute(
        "SELECT id FROM alerts WHERE patient=? AND displayed_at IS NULL",
        (manifest["patient_id"],),
    ).fetchall()
    for row in pending:
        display_alert(connection, row["id"], time.perf_counter())
    count = connection.execute(
        "SELECT COUNT(*) FROM alerts WHERE patient=?", (manifest["patient_id"],),
    ).fetchone()[0]
    print(f"{scenario}: {len(events)} eventos reproduzidos, {count} alerta(s) persistido(s).", flush=True)


def acknowledge(connection, alert_id, actor):
    actor = actor.strip()
    if not actor:
        raise ValueError("Informe quem recebeu o alerta.")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        alert = alert_details(connection, alert_id)
        if alert["status"] == "confirmado":
            return False
        connection.execute(
            "INSERT INTO acknowledgments (alert_id,actor,timestamp) VALUES (?,?,?)",
            (alert_id, actor, iso(datetime.now(timezone.utc))),
        )
        connection.execute("UPDATE alerts SET status='confirmado' WHERE id=?", (alert_id,))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE)
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("scenario", choices=(*SCENARIOS, "todos"))
    replay_parser.add_argument("--speed", type=float, default=0, help="0 imediato; 1 tempo original")
    listing = commands.add_parser("list")
    listing.add_argument("--patient")
    ack = commands.add_parser("ack")
    ack.add_argument("alert_id")
    ack.add_argument("--by", required=True)
    args = parser.parse_args()
    if args.command == "replay" and (not math.isfinite(args.speed) or args.speed < 0):
        parser.error("--speed deve ser finito e maior ou igual a zero")
    connection = open_database(args.db)
    try:
        if args.command == "replay":
            for scenario in SCENARIOS if args.scenario == "todos" else (args.scenario,):
                replay(connection, scenario, args.speed)
        elif args.command == "ack":
            updated = acknowledge(connection, args.alert_id, args.by)
            print("Recebimento confirmado." if updated else "Recebimento já estava confirmado.")
        else:
            query = "SELECT id FROM alerts"
            params = ()
            if args.patient:
                query += " WHERE patient=?"
                params = (args.patient,)
            ids = connection.execute(query + " ORDER BY created_at,id", params).fetchall()
            print(json.dumps(
                [alert_details(connection, row["id"]) for row in ids],
                ensure_ascii=False, indent=2,
            ))
    except FileNotFoundError:
        parser.exit(1, "Gere os cenários e execute analyze.py todos antes do monitoramento.\n")
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Falha no monitoramento: {error}\n")
    finally:
        connection.close()


if __name__ == "__main__":
    main()

