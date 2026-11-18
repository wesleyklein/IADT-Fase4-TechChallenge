"""Regression checks for replay persistence, fusion and acknowledgments."""

import copy
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

from fixtures import DATA
from monitor import (
    acknowledge, alert_details, iso, open_database, prepare_events,
    process_event, replay, timestamp,
)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.connection = open_database(self.path)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def events(self, scenario="anomalo"):
        folder = DATA / scenario
        manifest = json.loads((folder / "cenario.json").read_text(encoding="utf-8"))
        report = json.loads((folder / "analise_fase2.json").read_text(encoding="utf-8"))
        return prepare_events(manifest, report)

    def count(self, table):
        return self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_replay_fusion_and_retry(self):
        with redirect_stdout(io.StringIO()):
            replay(self.connection, "normal")
            replay(self.connection, "anomalo")
        self.assertEqual(self.count("alerts"), 1)
        self.assertEqual(self.count("findings"), 4)
        row = self.connection.execute("SELECT * FROM alerts").fetchone()
        alert = alert_details(self.connection, row["id"])
        self.assertEqual(
            {evidence["kind"] for evidence in alert["evidence"]},
            {"movimento", "sinais_vitais", "prescricao", "audio_texto"},
        )
        self.assertEqual(alert["severity"], "alta")
        self.assertGreaterEqual(alert["latency_ms"], 0)
        self.assertTrue(acknowledge(self.connection, row["id"], "Equipe de teste"))
        self.assertFalse(acknowledge(self.connection, row["id"], "Equipe de teste"))
        before = self.count("events")
        with redirect_stdout(io.StringIO()):
            replay(self.connection, "anomalo")
        self.assertEqual(self.count("events"), before)
        self.assertEqual(self.count("alerts"), 1)
        self.assertEqual(self.count("acknowledgments"), 1)
        self.assertEqual(alert_details(self.connection, row["id"])["status"], "confirmado")

    def test_new_evidence_reopens_alert(self):
        events = self.events()
        for event in events:
            process_event(self.connection, event)
            if event["id"] == "rx-2":
                break
        alert_id = self.connection.execute("SELECT id FROM alerts").fetchone()[0]
        acknowledge(self.connection, alert_id, "Equipe")
        audio = next(event for event in events if event["source"] == "audio_text_analysis")
        process_event(self.connection, audio)
        alert = alert_details(self.connection, alert_id)
        self.assertEqual(alert["status"], "aberto")
        self.assertIsNone(alert["displayed_at"])
        self.assertEqual(len(alert["acknowledgments"]), 1)

    def test_conflicting_duplicate_rolls_back(self):
        event = self.events()[0]
        process_event(self.connection, event)
        changed = {**event, "file": "changed.mp4"}
        with self.assertRaises(ValueError):
            process_event(self.connection, changed)
        self.assertEqual(self.count("events"), 1)
        self.assertEqual(process_event(self.connection, event)[1], "duplicado")

    def test_missing_vital_is_reported_and_not_imputed(self):
        events = [event for event in self.events() if event["source"] == "monitor"]
        for event in events:
            event = copy.deepcopy(event)
            event.pop("spo2")
            process_event(self.connection, event)
        self.assertEqual(self.count("warnings"), len(events))
        alert_id = self.connection.execute("SELECT id FROM alerts").fetchone()[0]
        evidence = alert_details(self.connection, alert_id)["evidence"][0]
        self.assertNotIn("spo2", {item["metric"] for item in evidence["deviations"]})

    def test_invalid_unit_does_not_become_a_baseline(self):
        events = [event for event in self.events() if event["source"] == "prescricao"]
        events[0]["unit"] = ""
        for event in events:
            process_event(self.connection, event)
        self.assertEqual(self.count("warnings"), 1)
        self.assertEqual(self.count("alerts"), 0)

    def test_out_of_order_is_rejected(self):
        events = self.events()
        process_event(self.connection, events[-1])
        with self.assertRaises(ValueError):
            process_event(self.connection, events[0])

    def test_fusion_window_is_bounded(self):
        findings = [event for event in self.events() if event["source"] == "video_analysis"]
        event = findings[0]
        process_event(self.connection, event)
        later = {
            **event, "id": "second-video",
            "timestamp": iso(timestamp(event["timestamp"]) + timedelta(seconds=11)),
        }
        process_event(self.connection, later)
        self.assertEqual(self.count("alerts"), 2)

    def test_missing_modality_keeps_other_signals(self):
        folder = DATA / "anomalo"
        manifest = json.loads((folder / "cenario.json").read_text(encoding="utf-8"))
        report = json.loads((folder / "analise_fase2.json").read_text(encoding="utf-8"))
        del report["video"]
        for event in prepare_events(manifest, report):
            process_event(self.connection, event)
        self.assertEqual(self.count("warnings"), 1)
        self.assertEqual(self.count("findings"), 3)

    def test_concurrent_retries_are_idempotent(self):
        event = self.events()[0]

        def write_event(_):
            connection = open_database(self.path)
            try:
                return process_event(connection, event)[1]
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(write_event, range(2)))
        self.assertCountEqual(statuses, ["processado", "duplicado"])
        self.assertEqual(self.count("events"), 1)


if __name__ == "__main__":
    unittest.main()

