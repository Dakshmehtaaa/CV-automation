"""Fixture-only checks for external pipeline observation. No real chat is read."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from data import StudioData
from live import BoundedSessionReader, HEADER_BYTES, TAIL_BYTES, MAX_LINE_BYTES, _number
from test_data import make_fixture, write_json


NOW = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
OWNER = "00000000-0000-4000-8000-000000000001"
TURN = "00000000-0000-4000-8000-000000000101"


def row(kind, payload, at=None):
    return {"timestamp": (at or NOW).isoformat(), "type": kind, "payload": payload}


def message(text, *, phase="commentary", role="assistant", at=None):
    return row("response_item", {"type": "message", "role": role, "phase": phase,
                                "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}]}, at)


class LivePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cv-studio-live-test-")
        self.base = Path(self.temp.name)
        self.root = make_fixture(self.base / "CV")
        self.resources = self.root / "Resources"
        self.run = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday"
        self.sessions = self.base / "sessions"
        self.sessions.mkdir()
        self.data = StudioData(self.root, sessions_dir=self.sessions)
        self.now_patch = patch("data._now", return_value=NOW)
        self.now_patch.start()
        self.ps_patch = patch.object(StudioData, "_active_process", return_value=None)
        self.ps_patch.start()

    def tearDown(self):
        self.ps_patch.stop()
        self.now_patch.stop()
        self.temp.cleanup()

    def session(self, name, *, thread=OWNER, parent=None, agent_path="", extra=None, cwd=None, active=True, last=NOW, turn=TURN):
        path = self.sessions / (name + ".jsonl")
        source = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_path": agent_path}}} if parent else "vscode"
        rows = [row("session_meta", {"id": thread, "cwd": str(cwd or self.root), "timestamp": NOW.isoformat(), "source": source})]
        if active:
            rows.append(row("event_msg", {"type": "task_started", "turn_id": turn}, NOW - timedelta(minutes=2)))
        rows.extend(extra or [row("event_msg", {"type": "item_completed", "turn_id": turn, "item": {"type": "reasoning", "text": "HIDDEN THOUGHT"}}, last)])
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def bind(self, path, *, thread=OWNER):
        write_json(self.run / "actual-coordinator-runtime.json", {"thread_id": thread, "source": str(path),
                   "actual_current_context": {"model": "gpt-6.1-sol", "effort": "high"}})

    def child(self, lane, number=2, *, parent=OWNER, stage="hunt", active=True):
        thread = f"00000000-0000-4000-8000-{number:012d}"
        turn = f"00000000-0000-4000-8000-{number + 100:012d}"
        path = self.session(lane, thread=thread, parent=parent, agent_path="/root/" + stage + "_" + lane, active=active, turn=turn)
        contexts = self.data._json(self.run / "native-actual-model-contexts.json") or []
        contexts.append({"id": thread, "rollout_path": str(path), "latest": {"model": "gpt-6-luna", "effort": "max"}})
        write_json(self.run / "native-actual-model-contexts.json", contexts)
        return path, thread, turn

    def append(self, path, value):
        with path.open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    def test_numeric_newer_terminal_closes_old_unmatched_start_without_context(self):
        path = self.session("owner")
        self.bind(path)
        newer = "00000000-0000-4000-8000-000000000199"
        self.append(path, row("event_msg", {"type": "turn_aborted", "turn_id": newer,
                    "started_at": int((NOW-timedelta(seconds=60)).timestamp()),
                    "completed_at": int(NOW.timestamp())}))
        live = self.data.overview()["live_pipeline"]
        self.assertFalse(live["coordinator_open_turn"])
        self.assertFalse(live["admission_blocked"])

    def test_numeric_older_terminal_cannot_close_genuine_newer_turn(self):
        path = self.session("owner")
        self.bind(path)
        older = "00000000-0000-4000-8000-000000000099"
        self.append(path, row("event_msg", {"type": "turn_aborted", "turn_id": older,
                    "started_at": int((NOW-timedelta(minutes=3)).timestamp()),
                    "completed_at": int(NOW.timestamp())}))
        live = self.data.overview()["live_pipeline"]
        self.assertTrue(live["coordinator_open_turn"])
        self.assertTrue(live["admission_blocked"])

    def test_fresh_owned_hunt_overrides_held_receipt_and_expired_window(self):
        owner = self.session("owner", extra=[message("Preparing settings before the next final review.")])
        self.bind(owner)
        for number, lane in enumerate(("hellowork", "france_travail", "linkedin", "apec", "wttj", "mail"), 2):
            self.child(lane, number)
        live = self.data.overview()["live_pipeline"]
        self.assertTrue(live["active"])
        self.assertEqual((live["current_stage"], live["stage_position"]), ("hunt", 2))
        self.assertEqual(len([w for w in live["workers"] if w["status"] == "running"]), 6)
        hunt = next(s for s in live["stages"] if s["id"] == "hunt")
        self.assertEqual(hunt["status"], "running")
        self.assertEqual(hunt["model"], "GPT-6 Luna · MAX")
        self.assertTrue(live["read_only"])
        self.assertEqual(self.data.active_pipeline()["evidence"], "external_session")
        overview = self.data.overview()
        self.assertEqual(overview["latest_run"]["status"], "held")
        self.assertEqual(next(s for s in overview["pipeline"]["stages"] if s["id"] == "hunt")["status"], "running")
        self.assertEqual(next(s for s in live["stages"] if s["id"] == "build")["status"], "not_run")
        self.assertFalse((self.resources / "Studio/.local").exists())

    def test_new_public_activity_and_explicit_counts_update_without_restart(self):
        owner = self.session("owner")
        self.bind(owner)
        child, _, _ = self.child("apec")
        summary = self.run / "native-workers/apec/site_summary.json"
        write_json(summary, {"full_relevant_ads": 0})
        before = self.data.live_pipeline()
        self.assertEqual(next(s for s in before["sources"] if s["id"] == "apec")["screened"], 0)
        self.append(child, message("Now reading public adverts for Hunt.", at=NOW + timedelta(seconds=1)))
        write_json(summary, {"full_relevant_ads": 8})
        after = self.data.live_pipeline()
        self.assertEqual(next(w for w in after["workers"] if w["lane"] == "apec")["screened"], 8)
        self.assertTrue(any(log["kind"] == "commentary" for log in after["logs"]))
        self.assertNotIn("Now reading", json.dumps(after))

    def test_child_ownership_and_cwd_are_validated(self):
        self.bind(self.session("owner"))
        self.child("apec", parent="00000000-0000-4000-8000-000000000099")
        bad = self.session("wrong-cwd", thread="00000000-0000-4000-8000-000000000003", parent=OWNER, agent_path="/root/hunt_wttj", cwd=self.base)
        contexts = self.data._json(self.run / "native-actual-model-contexts.json")
        contexts.append({"id": "00000000-0000-4000-8000-000000000003", "rollout_path": str(bad)})
        write_json(self.run / "native-actual-model-contexts.json", contexts)
        self.assertEqual(self.data.live_pipeline()["workers"], [])

    def test_unrelated_ui_chat_in_same_workspace_is_not_a_pipeline(self):
        self.session("ui", extra=[message("Hunt is running; this UI should show CV Weekday Pipeline.")])
        self.assertFalse(self.data.live_pipeline()["active"])
        self.assertIsNone(self.data.live_pipeline()["thread_id"])

    def test_explicit_automation_header_can_bind_without_a_runtime_receipt(self):
        path = self.session("scheduled", extra=[message("# Automation Run: CV Weekday Pipeline\nPrivate scheduled prompt", role="user"),
                                                row("event_msg", {"type": "item_completed", "turn_id": TURN})])
        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertEqual(live["thread_id"], OWNER)
        self.assertIsNone(live["run_id"])
        self.assertNotIn("Private scheduled", json.dumps(live))

    def test_cv_pipeline_automation_header_is_also_a_current_binding(self):
        self.session("scheduled", extra=[message("# Automation Run: CV Pipeline\nPrivate scheduled prompt", role="user"),
                                          row("event_msg", {"type": "item_completed", "turn_id": TURN})])
        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertEqual(live["thread_id"], OWNER)
        self.assertIsNone(live["run_id"])
        self.assertNotIn("Private scheduled", json.dumps(live))

    def test_manifest_bound_open_run_wins_over_older_left_open_chat(self):
        old_dir = self.resources / "Master/auto/evidence/runs/2026-10-01-old-left-open"
        old_owner = self.session("old-left-open", thread=OWNER, last=NOW - timedelta(minutes=5))
        write_json(old_dir / "batch-manifest.json", {
            "batch_started_at": "2026-10-01T19:00:00+00:00", "global_deadline": "2026-10-01T21:00:00+00:00"})
        write_json(old_dir / "terminal.json", {"status": "PARTIAL", "finished_at": "2026-10-01T21:00:00+00:00"})
        write_json(old_dir / "actual-coordinator-runtime.json", {"thread_id": OWNER, "source": str(old_owner)})

        current_id = "00000000-0000-4000-8000-000000000002"
        current_turn = "00000000-0000-4000-8000-000000000102"
        current_dir = self.resources / "Master/auto/evidence/runs/2026-10-02-weekday-policy3-live"
        current_owner = self.session("current-live", thread=current_id, turn=current_turn,
                                     last=NOW - timedelta(seconds=181))
        write_json(current_dir / "batch-manifest.json", {
            "started_at": "2026-10-02T09:30:00+00:00", "global_deadline": "2026-10-02T11:30:00+00:00",
            "actual_coordinator_id": current_id})
        write_json(current_dir / "stage-metrics.json", {"records": [{"stage": "inbox", "status": "running",
            "started_at": "2026-10-02T09:35:00+00:00"}]})

        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertFalse(live["activity_confirmed"])
        self.assertEqual(live["run_id"], current_dir.name)
        self.assertEqual(live["thread_id"], current_id)
        self.assertEqual(live["current_stage"], "inbox")
        self.assertEqual(live["current_stage_source"], "saved_stage_receipt")
        self.assertEqual(live["other_open_work_count"], 1)
        self.assertTrue(live["admission_blocked"])
        self.assertTrue(self.data.active_pipeline()["active"])

    def test_stale_turn_is_explicit_and_terminal_events_close_it(self):
        owner = self.session("owner", last=NOW - timedelta(seconds=181))
        self.bind(owner)
        stale = self.data.live_pipeline()
        self.assertFalse(stale["active"])
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["stale"])
        self.append(owner, row("event_msg", {"type": "turn_aborted", "turn_id": TURN}))
        stopped = self.data.live_pipeline()
        self.assertFalse(stopped["active"])
        self.assertEqual(stopped["status"], "interrupted")
        self.assertFalse(stopped["stale"])

    def test_active_coordinator_keeps_quiet_worker_position_explicitly_unconfirmed(self):
        self.bind(self.session("owner"))
        child, _, turn = self.child("apec")
        child.write_text(child.read_text().replace(NOW.isoformat(), (NOW - timedelta(minutes=5)).isoformat()))
        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertEqual(live["current_stage"], "hunt")
        self.assertFalse(live["current_stage_confirmed"])
        self.assertEqual(next(s for s in live["stages"] if s["id"] == "hunt")["status"], "stale")

    def test_completed_parent_does_not_hide_active_owned_child_or_approve_stages(self):
        owner = self.session("owner", extra=[row("event_msg", {"type": "task_complete", "turn_id": TURN, "last_agent_message": "PRIVATE RESULT"})])
        self.bind(owner)
        child, _, turn = self.child("apec")
        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertEqual(live["coordinator_status"], "completed")
        self.assertEqual(live["current_stage"], "hunt")
        self.assertEqual(next(s for s in live["stages"] if s["id"] == "sol_review")["status"], "not_recorded")
        self.append(child, row("event_msg", {"type": "task_failed", "turn_id": turn}))
        failed = self.data.live_pipeline()
        self.assertFalse(failed["active"])
        self.assertEqual(failed["workers"][0]["status"], "failed")
        self.assertNotIn("PRIVATE RESULT", json.dumps(failed))

    def test_older_final_message_closes_chat_but_never_approves_pipeline(self):
        owner = self.session("owner", extra=[message("All done", phase="final")])
        self.bind(owner)
        live = self.data.live_pipeline()
        self.assertEqual(live["status"], "completed")
        self.assertFalse(live["active"])
        self.assertFalse(any(s["status"] == "done" for s in live["stages"]))

    def test_reused_thread_resets_inferred_stage_at_new_turn(self):
        owner = self.session("owner", extra=[message("Now Hunt is running."),
                                               row("event_msg", {"type": "task_complete", "turn_id": TURN})])
        self.bind(owner)
        self.data.live_pipeline()
        next_turn = "00000000-0000-4000-8000-000000000199"
        self.append(owner, row("event_msg", {"type": "task_started", "turn_id": next_turn}))
        self.append(owner, message("Preparing the workspace."))
        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertIsNone(live["current_stage"])
        self.assertEqual(live["current_turn_id"], next_turn)

    def test_newer_turn_context_supersedes_older_open_turn_and_final_answer_closes_it(self):
        self.finalize_run()
        owner = self.session("owner", active=False)
        self.bind(owner)
        child, child_id, old_turn = self.child("linkedin", 3)
        current_turn = "00000000-0000-4000-8000-000000000303"
        self.session("linkedin", thread=child_id, parent=OWNER, agent_path="/root/hunt_linkedin",
                     active=True, turn=old_turn, extra=[
                         row("turn_context", {"turn_id": current_turn, "model": "gpt-6-luna", "effort": "max"},
                             NOW - timedelta(seconds=5)),
                         message("Done", phase="final_answer", at=NOW)])

        live = self.data.live_pipeline()
        worker = next(worker for worker in live["workers"] if worker["lane"] == "linkedin")
        self.assertEqual(worker["current_turn_id"], current_turn)
        self.assertFalse(worker["open_turn"])
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(live["left_open_workers"], 0)
        self.assertFalse(live["admission_blocked"])

    def test_newer_turn_context_without_terminal_remains_open(self):
        current_turn = "00000000-0000-4000-8000-000000000304"
        path = self.session("latest-open", active=True, turn=TURN, extra=[
            row("turn_context", {"turn_id": current_turn, "model": "gpt-6.1-sol", "effort": "high"},
                NOW - timedelta(seconds=10))])
        state = BoundedSessionReader(self.sessions, self.root, self.resources).read(path)
        self.assertEqual(state.turn_id, current_turn)
        self.assertTrue(state.open_turn)
        self.assertEqual(state.status, "running")

    def test_privacy_projection_excludes_pasted_commands_recruiter_prose_and_all_raw_payloads(self):
        secret = "We would like to interview you on Friday."
        command = "python3 Master/auto/state.py start hunt"
        owner = self.session("owner", extra=[
            message("Run " + command + ". Recruiter wrote: " + secret),
            message("I heard back: Their team is keen to speak next week."),
            message("SECRET INBOX BODY", phase="analysis"),
            message("PRIVATE INPUT", role="user"),
            row("response_item", {"type": "reasoning", "summary": "SECRET REASONING"}),
            row("response_item", {"type": "function_call", "name": "functions.exec", "arguments": "SECRET COMMAND"}),
            row("response_item", {"type": "function_call_output", "output": "SECRET TOOL OUTPUT"}),
            row("event_msg", {"type": "agent_message", "message": "SECRET OLDER MESSAGE"}),
        ])
        self.bind(owner)
        output = json.dumps(self.data.live_pipeline())
        for private in (secret, command, "Their team", "SECRET", "PRIVATE INPUT"):
            self.assertNotIn(private, output)
        self.assertIn("Assistant progress updated", output)

    def test_source_counts_distinguish_recorded_zero_captures_and_missing(self):
        self.bind(self.session("owner"))
        self.child("apec")
        self.child("mail", 3)
        write_json(self.run / "site-lane-outcomes.json", {"lanes": []})
        write_json(self.run / "native-workers/apec/site_summary.json", {"full_relevant_ads": 0})
        write_json(self.run / "native-workers/linkedin/site_summary.json", {"counts": {"unique_full_live_ads_read": 5}})
        write_json(self.run / "native-workers/wttj/site_summary.json", {"counts": {"unique_full_live_relevant_reads": 4}})
        write_json(self.run / "native-workers/mail/site_summary.json", {"durable_full_ad_captures": 6})
        write_json(self.run / "native-workers/hellowork/full_ads.json", [{"url": "https://example.org/1"}, {"url": "https://example.org/2"}])
        lanes = {s["id"]: s for s in self.data.live_pipeline()["sources"]}
        self.assertEqual(lanes["apec"]["screened"], 0)
        self.assertEqual(lanes["linkedin"]["screened"], 5)
        self.assertEqual(lanes["wttj"]["screened"], 4)
        for lane, captures in (("mail", 6), ("hellowork", 2)):
            self.assertIsNone(lanes[lane]["screened"])
            self.assertEqual(lanes[lane]["captured"], captures)
            self.assertEqual(lanes[lane]["count_label"], "Saved full-ad captures")
        self.assertIsNone(lanes["france_travail"]["screened"])

    def test_count_values_are_finite_nonnegative_integers_and_zero_target_is_retained(self):
        for invalid in (True, -1, 1.25, float("nan"), float("inf"), "3", 10 ** 1000):
            self.assertIsNone(_number(invalid))
        self.assertEqual(_number(0), 0)
        self.assertEqual(_number(2.0), 2)
        self.bind(self.session("owner"))
        self.child("apec")
        self.child("linkedin", 7)
        write_json(self.run / "native-workers/apec/site_summary.json", {"full_relevant_ads": 0, "full_ad_goal": 0})
        source = next(s for s in self.data.live_pipeline()["sources"] if s["id"] == "apec")
        self.assertEqual(source["target"], 0)
        write_json(self.run / "site-lane-outcomes.json", {"lanes": [{"worker_id": "hunt:linkedin", "full_live_ads_reviewed": 1.25, "target": float("inf")}]})
        source = next(s for s in self.data.live_pipeline()["sources"] if s["id"] == "linkedin")
        self.assertIsNone(source["screened"])
        self.assertIsNone(source["target"])
        worker = next(w for w in self.data.live_pipeline()["workers"] if w["lane"] == "linkedin")
        self.assertIsNone(worker["target"], "An invalid saved target stays unknown instead of falling back to policy 3's 20")

    def test_saved_read_count_keeps_its_label_when_separate_captures_exist(self):
        self.bind(self.session("owner"))
        write_json(self.run / "site-lane-outcomes.json", {"lanes": [{"worker_id": "hunt:hellowork", "status": "PARTIAL", "full_live_ads_reviewed": 5}]})
        write_json(self.run / "native-workers/hellowork/full_ads.json", [{"url": "https://example.org/1"}])
        source = next(s for s in self.data.live_pipeline()["sources"] if s["id"] == "hellowork")
        self.assertEqual(source["screened"], 5)
        self.assertEqual(source["count"], 5)
        self.assertEqual(source["captured"], 1)
        self.assertEqual(source["count_label"], "Full relevant adverts read")

    def test_new_captures_are_visible_above_older_held_zero_count(self):
        self.bind(self.session("owner"))
        self.child("hellowork")
        write_json(self.run / "native-workers/hellowork/full_ads.json", {"ads": [{"url": "https://example.org/1"}]})
        source = next(s for s in self.data.live_pipeline()["sources"] if s["id"] == "hellowork")
        self.assertIsNone(source["screened"])
        self.assertEqual(source["captured"], 1)
        self.assertEqual(source["saved_screened"], 0)
        self.assertEqual(source["status"], "running")

    def test_incomplete_inbox_receipt_does_not_become_done(self):
        self.bind(self.session("owner"))
        write_json(self.run / "inbox/inbox_receipt.v3.json", {"status": "REVIEWED", "reviewed": True,
                   "complete": False, "pagination_complete": True, "private": "SECRET PRIVATE REPLY"})
        stage = next(s for s in self.data.live_pipeline()["stages"] if s["id"] == "inbox")
        self.assertEqual(stage["status"], "partial")
        self.assertNotIn("SECRET", json.dumps(self.data.live_pipeline()))
        outcomes = self.data._json(self.run / "stage-outcomes.json")
        outcomes["inbox"] = "REVIEWED"
        write_json(self.run / "stage-outcomes.json", outcomes)
        stage = next(s for s in self.data.live_pipeline()["stages"] if s["id"] == "inbox")
        self.assertEqual(stage["status"], "partial")
        self.assertEqual(stage["saved_status"], "done")

    def test_path_containment_expected_id_and_fixture_default(self):
        outside = self.base / "outside.jsonl"
        outside.write_text(self.session("owner").read_text())
        self.bind(outside)
        self.assertFalse(self.data.live_pipeline()["active"])
        self.bind(self.sessions / "owner.jsonl", thread="00000000-0000-4000-8000-000000000099")
        self.assertFalse(self.data.live_pipeline()["active"])
        (self.sessions / "escape.jsonl").symlink_to(outside)
        self.bind(self.sessions / "escape.jsonl")
        self.assertFalse(self.data.live_pipeline()["active"])
        self.assertIsNone(StudioData(self.root)._live.reader)

    def test_stop_descriptor_is_identity_only_and_read_only(self):
        self.bind(self.session("owner"))
        _, child_id, child_turn = self.child("apec")
        descriptor = self.data.external_stop_descriptor()
        self.assertEqual(descriptor["thread_id"], OWNER)
        self.assertEqual(descriptor["current_turn_id"], TURN)
        self.assertEqual({k: descriptor["children"][0][k] for k in ("thread_id", "current_turn_id", "parent_thread_id")},
                         {"thread_id": child_id, "current_turn_id": child_turn, "parent_thread_id": OWNER})
        self.assertNotIn("source", descriptor)
        self.assertFalse((self.resources / "Studio/.local").exists())

    def test_bounded_tail_incremental_partial_malformed_and_replacement(self):
        owner = self.session("owner", extra=[row("response_item", {"type": "function_call_output", "output": "PRIVATE" * 600_000}),
                                               row("event_msg", {"type": "item_completed", "turn_id": TURN})])
        reader = BoundedSessionReader(self.sessions, self.root, self.resources)
        state = reader.read(owner, expected_id=OWNER)
        self.assertTrue(state.open_turn)
        self.assertEqual(state.proof_turn_id, TURN)
        self.assertLess(reader.bytes_read, HEADER_BYTES + TAIL_BYTES + 70_000)
        used = reader.bytes_read
        self.append(owner, message("Now Hunt is running."))
        reader.read(owner, expected_id=OWNER)
        self.assertLess(reader.bytes_read - used, 3000)
        with owner.open("ab") as stream:
            stream.write(b'{malformed}\n')
            stream.write(json.dumps(message("Progress changed.")).encode()[:-2])
        previous_logs = len(reader.read(owner).logs)
        with owner.open("ab") as stream:
            stream.write(b'not-json\n')
        self.assertEqual(len(reader.read(owner).logs), previous_logs)
        incomplete = json.dumps(row("event_msg", {"type": "task_complete", "turn_id": TURN})).encode()
        with owner.open("ab") as stream:
            stream.write(incomplete[:-1])
        self.assertTrue(reader.read(owner).open_turn)
        with owner.open("ab") as stream:
            stream.write(incomplete[-1:] + b'\n')
        self.assertFalse(reader.read(owner).open_turn)
        original_meta = json.loads(owner.read_text().splitlines()[0])
        owner.write_text(json.dumps(original_meta) + "\n" + json.dumps(row("event_msg", {"type": "task_started", "turn_id": TURN})) + "\n")
        reset = reader.read(owner)
        self.assertTrue(reset.open_turn)
        self.assertFalse(any(log["kind"] == "commentary" for log in reset.logs))

    def test_large_unobserved_gap_needs_recent_matching_turn_evidence(self):
        owner = self.session("owner", extra=[row("response_item", {"type": "function_call_output", "output": "PRIVATE" * 600_000}),
                                               message("Preparing the workspace.")])
        self.bind(owner)
        self.assertFalse(self.data.live_pipeline()["active"])
        self.append(owner, row("event_msg", {"type": "item_completed", "turn_id": TURN}))
        self.assertTrue(self.data.live_pipeline()["active"])

    def test_new_owned_child_is_discovered_without_a_context_receipt(self):
        self.bind(self.session("owner"))
        self.data.live_pipeline()
        thread = "00000000-0000-4000-8000-000000000077"
        self.session("new-owned", thread=thread, parent=OWNER, agent_path="/root/hunt_linkedin")
        with patch("data._now", return_value=NOW + timedelta(seconds=11)):
            live = self.data.live_pipeline()
        self.assertEqual([worker["id"] for worker in live["workers"]], [thread])
        self.assertEqual(live["current_stage"], "hunt")

    def test_active_grandchild_keeps_completed_parent_chain_reserved(self):
        self.bind(self.session("owner", extra=[row("event_msg", {"type": "task_complete", "turn_id": TURN})]))
        child, child_id, child_turn = self.child("apec")
        self.append(child, row("event_msg", {"type": "task_complete", "turn_id": child_turn}))
        grand_id = "00000000-0000-4000-8000-000000000088"
        self.session("grandchild", thread=grand_id, parent=child_id, agent_path="/root/hunt_apec/reader")
        live = self.data.live_pipeline()
        self.assertTrue(live["active"])
        self.assertEqual(live["current_stage"], "hunt")
        grand = next(w for w in live["workers"] if w["id"] == grand_id)
        self.assertEqual(grand["parent_thread_id"], child_id)
        self.assertEqual(grand["ownership_depth"], 2)
        self.assertEqual(grand["ownership_chain"], [OWNER, child_id, grand_id])
        self.assertTrue(self.data.active_pipeline()["edit_locked"])
        descriptor = self.data.external_stop_descriptor()
        self.assertEqual([w["thread_id"] for w in descriptor["children"]], [grand_id])
        self.assertEqual(descriptor["children"][0]["ownership_chain"], [OWNER, child_id, grand_id])

    def test_unavailable_verified_chat_holds_admission_until_fresh_terminal(self):
        owner = self.session("owner")
        self.bind(owner)
        self.assertTrue(self.data.live_pipeline()["active"])
        original = owner.read_bytes()
        owner.unlink()
        missing = self.data.live_pipeline()
        self.assertFalse(missing["active"])
        self.assertTrue(missing["stale"])
        self.assertTrue(missing["uncertain"])
        self.assertTrue(missing["admission_blocked"])
        self.assertEqual(missing["thread_id"], OWNER)
        self.assertTrue(self.data.active_pipeline()["edit_locked"])
        self.assertEqual(self.data.active_pipeline()["evidence"], "external_session_uncertain")
        owner.write_bytes(original)
        later = NOW + timedelta(minutes=4)
        with patch("data._now", return_value=later):
            self.assertTrue(self.data.live_pipeline()["admission_blocked"])
            self.append(owner, row("event_msg", {"type": "task_complete", "turn_id": TURN}, later))
            closed = self.data.live_pipeline()
            self.assertFalse(closed["admission_blocked"])
            self.assertFalse(self.data.active_pipeline()["active"])

    def test_read_failure_and_invalid_new_path_do_not_share_cached_identity(self):
        owner = self.session("owner")
        self.bind(owner)
        self.assertTrue(self.data.live_pipeline()["active"])
        with patch.object(self.data._live.reader, "read", return_value=None):
            self.assertTrue(self.data.live_pipeline()["admission_blocked"])
        outside = self.base / "outside.jsonl"
        outside.write_bytes(owner.read_bytes())
        self.bind(outside)
        invalid = self.data.live_pipeline()
        self.assertFalse(invalid["active"])
        self.assertIsNone(invalid["thread_id"])

    def test_permission_failure_after_freshness_expiry_keeps_verified_work_reserved(self):
        owner = self.session("owner")
        self.bind(owner)
        self.assertTrue(self.data.live_pipeline()["active"])
        original_open = Path.open

        def unavailable(path, *args, **kwargs):
            if path.resolve() == owner.resolve():
                raise PermissionError("Fixture unavailable")
            return original_open(path, *args, **kwargs)

        with patch("data._now", return_value=NOW + timedelta(minutes=4)), patch.object(Path, "open", unavailable):
            live = self.data.live_pipeline()
            self.assertFalse(live["active"])
            self.assertTrue(live["uncertain"])
            self.assertTrue(live["admission_blocked"])
            self.assertTrue(self.data.active_pipeline()["edit_locked"])

    def test_unrelated_parent_cycle_is_not_admitted(self):
        self.bind(self.session("owner"))
        first = "00000000-0000-4000-8000-000000000090"
        second = "00000000-0000-4000-8000-000000000091"
        self.session("cycle-first", thread=first, parent=second, agent_path="/root/hunt_apec/reader")
        self.session("cycle-second", thread=second, parent=first, agent_path="/root/hunt_apec/reader")
        self.assertEqual(self.data.live_pipeline()["workers"], [])


    def finalize_run(self, *, finished=NOW - timedelta(minutes=30), deadline=NOW - timedelta(minutes=20)):
        write_json(self.run / "batch-manifest.json", {"batch_id": self.run.name, "policy_version": 3,
                   "batch_started_at": (finished - timedelta(hours=2)).isoformat(), "batch_deadline_at": deadline.isoformat()})
        write_json(self.run / "terminal.json", {"status": "PARTIAL", "administratively_finalized_at": finished.isoformat(),
                   "hunt": {"six_lanes": "USER_STOPPED_HELD_PARTIAL"}, "build": "HELD_CLOCK_EXHAUSTED",
                   "final_review": "METRICS_ONLY_APPROVED_FULL_DELIVERABLES_HELD", "sheet_sync": "SIX_VALUES_VERIFIED"})
        (self.run / "REPORT.md").write_text("CV weekday — PARTIAL\n\n55 complete advert reviews, 1 accepted role.\n")

    def test_follow_up_turn_after_finalized_run_is_not_pipeline_progress(self):
        self.finalize_run()
        owner = self.session("owner", extra=[message("Progress updated for Hunt runner patch.")])
        self.bind(owner)
        self.child("apec", 2, active=True)
        live = self.data.live_pipeline()
        self.assertFalse(live["active"])
        self.assertTrue(live["finalized"])
        self.assertEqual(live["status"], "follow_up")
        self.assertIsNone(live["current_stage"])
        self.assertIsNone(live["stage_position"])
        self.assertTrue(live["admission_blocked"])
        self.assertTrue(self.data.active_pipeline()["active"])
        self.assertIsNotNone(self.data.external_stop_descriptor())
        stages = {s["id"]: s for s in live["stages"]}
        self.assertEqual(stages["build"]["status"], "held")
        self.assertEqual(stages["hunt"]["raw_status"], "USER_STOPPED_HELD_PARTIAL")
        self.assertEqual(stages["sol_review"]["raw_status"], "METRICS_ONLY_APPROVED_FULL_DELIVERABLES_HELD")
        self.assertEqual(stages["sync"]["raw_status"], "SIX_VALUES_VERIFIED")
        self.assertNotEqual(stages["hunt"]["status"], "running")

    def test_finalized_run_marks_quiet_open_workers_left_open(self):
        self.finalize_run()
        owner = self.session("owner", active=False)
        self.bind(owner)
        path, _, turn = self.child("linkedin", 3)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for value in rows:
            value["timestamp"] = (NOW - timedelta(hours=2)).isoformat()
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        live = self.data.live_pipeline()
        self.assertEqual(live["status"], "left_open")
        self.assertEqual(live["left_open_workers"], 1)
        self.assertEqual(next(w for w in live["workers"] if w["lane"] == "linkedin")["status"], "left_open")
        self.assertTrue(live["admission_blocked"])

    def test_terminal_before_deadline_closes_run_but_preserves_follow_up_lock(self):
        self.finalize_run(finished=NOW - timedelta(minutes=5), deadline=NOW + timedelta(minutes=30))
        owner = self.session("owner")
        self.bind(owner)
        self.child("apec", 2)
        live = self.data.live_pipeline()
        self.assertFalse(live["active"])
        self.assertTrue(live["finalized"])
        self.assertTrue(live["admission_blocked"])
        self.assertEqual(live["status"], "follow_up")
        self.assertEqual(live["run_raw_status"], "PARTIAL")

    def test_finalized_summary_uses_report_not_superseded_outcomes(self):
        self.finalize_run()
        summary = self.data._run_summary(self.run)
        self.assertEqual(summary["summary"], "55 complete advert reviews, 1 accepted role.")
        self.assertEqual(summary["ended_at"], (NOW - timedelta(minutes=30)).isoformat())
        self.assertNotIn("Fixture capacity", summary["summary"])


    def test_follow_up_names_an_open_manual_build_without_changing_locks(self):
        self.finalize_run()
        owner = self.session("owner")
        self.bind(owner)
        auto = self.resources / "Master/auto"
        paris = (NOW - timedelta(minutes=10)).astimezone(ZoneInfo("Europe/Paris")).replace(tzinfo=None)
        (auto / "events.jsonl").write_text(json.dumps({"ts": paris.isoformat(timespec="seconds"), "date": "2026-10-02", "stage": "build",
                                                      "event": "start", "trigger": "manual", "run": 1}) + "\n")
        ledger = self.resources / "Master/auto/evidence/runs/2026-10-02/build-1.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"event": "run_started", "expected_roles": [{"role": "Example Role"}]}) + "\n")
        live = self.data.live_pipeline()
        self.assertEqual(live["manual_stage"]["role"], "Example Role")
        self.assertIn("manual CV build for Example Role", live["summary"])
        self.assertTrue(live["admission_blocked"])
        with (auto / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"ts": paris.isoformat(timespec="seconds"), "stage": "build", "event": "done", "trigger": "manual", "run": 1}) + "\n")
        self.assertNotIn("manual_stage", self.data.live_pipeline())


if __name__ == "__main__":
    unittest.main()
