"""Fixture-only desktop Stop tests. No mutation is sent to the installed app."""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

from desktop_control import (DesktopPipelineController, DesktopIPC, DesktopControlConflict,
                             DesktopControlError, INTERRUPT_METHOD, OWNER_METHOD, FOLLOW_METHOD,
                             STATE_METHOD, installed_protocol, _view)


VERSIONS = {INTERRUPT_METHOD: 4, OWNER_METHOD: 1, FOLLOW_METHOD: 1, STATE_METHOD: 11}


class FixtureTransport:
    def __init__(self, states, calls):
        self.states, self.calls = states, calls
        self.failure = None
        self.wrong_ack = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def find_owner(self, thread):
        self.calls.append(("owner", thread))
        return "fixture-owner"

    def snapshot(self, thread, _owner):
        self.calls.append(("snapshot", thread))
        return copy.deepcopy(self.states[thread])

    def interrupt(self, thread, turn, owner):
        self.calls.append(("interrupt", thread, turn, owner))
        if self.failure:
            raise self.failure
        return {"ok": True, "interruptedTurnId": "other-turn" if self.wrong_ack else turn}


class DesktopControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cv-studio-fixture-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.codex_home = self.base / "codex"
        self.sessions = self.codex_home / "sessions/2026/10/01"
        self.sessions.mkdir(parents=True)
        self.calls = []
        self.states = {"fixture-root": self.state("fixture-root", "root-turn"),
                       "fixture-child": self.state("fixture-child", "child-turn", "fixture-root")}
        self.transport = FixtureTransport(self.states, self.calls)
        self.controller = self.make_controller()

    def make_controller(self, **overrides):
        options = {"codex_home": self.codex_home,
                   "protocol_provider": lambda: {"available": True, "versions": VERSIONS},
                   "transport_factory": lambda *_args: self.transport}
        options.update(overrides)
        return DesktopPipelineController(self.workspace, **options)

    def state(self, thread, turn, parent=None):
        rollout = self.sessions / (thread + ".jsonl")
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": thread, "cwd": str(self.workspace),
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "cli"}}) + "\n" + json.dumps({
            "type": "event_msg", "timestamp": "2026-10-01T12:00:00+00:00",
            "payload": {"type": "task_started", "turn_id": turn}}) + "\n")
        return {"id": thread, "cwd": str(self.workspace), "parentThreadId": parent,
                "rolloutPath": str(rollout), "threadGoal": None,
                "threadRuntimeStatus": {"type": "active"}, "turns": [],
                "turnHistory": {"kind": "canonical", "history": {
                    "islands": [{"entries": [{"value": "turn:" + turn}]}],
                    "entitiesByKey": {"turn:" + turn: {"turnId": turn, "status": "inProgress",
                    "items": [{"type": "agentMessage", "text": "PRIVATE FIXTURE TRANSCRIPT"}]}}}}}

    def descriptor(self, children=True):
        return {"workspace": str(self.workspace), "thread_id": "fixture-root",
                "current_turn_id": "root-turn", "run_id": "fixture-run",
                "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "children": [{"thread_id": "fixture-child", "current_turn_id": "child-turn",
                              "parent_thread_id": "fixture-root"}] if children else []}

    def interrupts(self):
        return [call for call in self.calls if call[0] == "interrupt"]

    def terminal(self, thread, turn, kind="turn_aborted", **extra):
        path = Path(self.states[thread]["rolloutPath"])
        with path.open("a") as stream:
            stream.write(json.dumps({"type": "event_msg", "timestamp": "2026-10-01T12:03:00+00:00",
                                     "payload": {"type": kind, "turn_id": turn, **extra}}) + "\n")

    def assert_rejected_without_mutation(self, descriptor):
        with self.assertRaises(DesktopControlConflict):
            self.controller.stop(descriptor)
        self.assertEqual(self.interrupts(), [])

    def test_construct_and_plain_capability_never_connect_or_stop(self):
        self.assertFalse(self.controller.capabilities()["can_stop"])
        self.assertEqual(self.controller.list(), [])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.controller.receipt_dir.exists())

    def test_live_capability_is_read_only_and_has_no_private_content(self):
        capability = self.controller.capabilities(self.descriptor())
        self.assertTrue(capability["can_stop"])
        self.assertTrue(capability["verified"])
        self.assertEqual(capability["safe_identity"]["current_turn_id"], "root-turn")
        self.assertEqual(self.interrupts(), [])
        self.assertNotIn("PRIVATE", json.dumps(capability))
        self.assertFalse(self.controller.receipt_dir.exists())

    def test_explicit_stop_validates_all_before_scoped_interrupts(self):
        receipt = self.controller.stop(self.descriptor())
        self.assertEqual(self.interrupts(), [("interrupt", "fixture-root", "root-turn", "fixture-owner")])
        first_mutation = next(i for i, call in enumerate(self.calls) if call[0] == "interrupt")
        self.assertTrue(all(call[0] != "interrupt" for call in self.calls[:first_mutation]))
        self.assertFalse(any(call[1] == "fixture-child" for call in self.calls))
        self.assertTrue(receipt["targets"][1]["native_descendant_cleanup_requested"])
        self.assertFalse(receipt["targets"][1]["request_sent"])
        self.assertEqual(receipt["status"], "stop_requested")
        self.assertFalse(receipt["parent_stop_confirmed"])
        self.assertFalse(receipt["native_children_stop_confirmed"])
        self.assertTrue(receipt["recovery_required"])
        self.assertFalse(receipt["pipeline_complete"])
        self.assertNotIn("PRIVATE", json.dumps(receipt))

    def test_descriptor_workspace_is_rejected(self):
        value = self.descriptor()
        value["workspace"] = str(self.base / "another-workspace")
        self.assert_rejected_without_mutation(value)

    def test_old_or_naive_observation_is_rejected(self):
        for stamp in ("2020-01-01T00:00:00+00:00", "2026-10-01T12:00:00", None):
            value = self.descriptor()
            value["observed_at"] = stamp
            self.assert_rejected_without_mutation(value)

    def test_changed_parent_turn_is_rejected_before_mutation(self):
        self.states["fixture-root"] = self.state("fixture-root", "new-root-turn")
        self.assert_rejected_without_mutation(self.descriptor())

    def test_snapshot_other_workspace_is_rejected(self):
        self.states["fixture-root"]["cwd"] = str(self.base / "other")
        self.assert_rejected_without_mutation(self.descriptor())

    def test_changed_child_turn_rejects_parent_stop_too(self):
        self.states["fixture-child"] = self.state("fixture-child", "new-child-turn", "fixture-root")
        self.assert_rejected_without_mutation(self.descriptor())

    def test_foreign_child_snapshot_is_rejected(self):
        self.states["fixture-child"] = self.state("fixture-child", "child-turn", "unrelated-root")
        self.assert_rejected_without_mutation(self.descriptor())

    def test_foreign_or_cyclic_child_descriptor_is_rejected(self):
        for parent in ("unrelated-root", "fixture-child"):
            value = self.descriptor()
            value["children"][0]["parent_thread_id"] = parent
            self.assert_rejected_without_mutation(value)

    def test_active_goal_requires_native_user_stop_to_pause_it(self):
        self.states["fixture-root"]["threadGoal"] = {"status": "active"}
        self.assert_rejected_without_mutation(self.descriptor())

    def test_fresh_server_descriptor_is_rechecked(self):
        fresh = self.descriptor()
        fresh["current_turn_id"] = "new-turn"
        self.controller = self.make_controller(descriptor_provider=lambda: fresh)
        self.assert_rejected_without_mutation(self.descriptor())
        self.assertEqual(self.calls, [])

    def test_parent_terminal_cannot_claim_child_cleanup(self):
        receipt = self.controller.stop(self.descriptor())
        self.terminal("fixture-root", "root-turn")
        receipt = self.controller.get(receipt["id"])
        self.assertTrue(receipt["parent_stop_confirmed"])
        self.assertFalse(receipt["native_children_stop_confirmed"])
        self.assertEqual(receipt["status"], "stop_requested")
        self.assertEqual(receipt["cleanup_status"], "needs_review")
        self.assertTrue(receipt["recovery_required"])

    def test_even_all_native_terminal_events_keep_remote_cleanup_hold(self):
        receipt = self.controller.stop(self.descriptor())
        self.terminal("fixture-root", "root-turn")
        self.terminal("fixture-child", "child-turn")
        receipt = self.controller.get(receipt["id"])
        self.assertTrue(receipt["parent_stop_confirmed"])
        self.assertTrue(receipt["native_children_stop_confirmed"])
        self.assertEqual(receipt["status"], "needs_review")
        self.assertTrue(receipt["recovery_required"])
        self.assertFalse(receipt["pipeline_complete"])
        self.assertNotEqual(receipt["status"], "stopped")

    def test_different_terminal_turn_cannot_confirm_stop(self):
        receipt = self.controller.stop(self.descriptor(False))
        self.terminal("fixture-root", "unrelated-turn")
        self.assertFalse(self.controller.get(receipt["id"])["parent_stop_confirmed"])

    def test_assistant_final_prose_cannot_confirm_stop(self):
        receipt = self.controller.stop(self.descriptor(False))
        with Path(self.states["fixture-root"]["rolloutPath"]).open("a") as stream:
            stream.write(json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "assistant", "phase": "final",
                "content": [{"type": "output_text", "text": "Stopped everything!"}]}}) + "\n")
        self.assertFalse(self.controller.get(receipt["id"])["parent_stop_confirmed"])

    def test_later_turn_is_not_covered_by_old_stop(self):
        receipt = self.controller.stop(self.descriptor(False))
        self.terminal("fixture-root", "root-turn")
        self.terminal("fixture-root", "later-turn", "task_started")
        refreshed = self.controller.get(receipt["id"])
        self.assertFalse(refreshed["parent_stop_confirmed"])
        self.assertTrue(refreshed["targets"][0]["terminal_readback"]["new_turn_detected"])

    def test_late_old_terminal_does_not_hide_already_started_successor(self):
        receipt = self.controller.stop(self.descriptor(False))
        self.terminal("fixture-root", "later-turn", "task_started")
        self.terminal("fixture-root", "root-turn")
        self.assertFalse(self.controller.get(receipt["id"])["parent_stop_confirmed"])

    def test_mismatched_ack_is_not_exact_turn_confirmation(self):
        self.transport.wrong_ack = True
        receipt = self.controller.stop(self.descriptor(False))
        self.assertFalse(receipt["targets"][0]["acknowledged"])
        self.assertFalse(receipt["parent_stop_confirmed"])
        self.assertEqual(receipt["status"], "needs_review")

    def test_lost_ack_preserves_unresolved_receipt(self):
        self.transport.failure = TimeoutError("Fixture ACK loss")
        receipt = self.controller.stop(self.descriptor(False))
        self.assertTrue(receipt["action_sent"])
        self.assertTrue(receipt["recovery_required"])
        self.assertFalse(receipt["parent_stop_confirmed"])
        self.assertEqual(receipt["status"], "needs_review")
        self.assertTrue((self.controller.receipt_dir / (receipt["id"] + ".json")).is_file())

    def test_restart_reconciles_receipt_without_replaying_interrupts(self):
        receipt = self.controller.stop(self.descriptor(False))
        count = len(self.interrupts())
        controller = self.make_controller()
        self.assertEqual(controller.list()[0]["id"], receipt["id"])
        self.assertTrue(controller.get(receipt["id"])["recovery_required"])
        self.assertEqual(len(self.interrupts()), count)

    def test_invalid_receipt_path_is_rejected(self):
        with self.assertRaises(DesktopControlError):
            self.controller.get("../other")
        self.assertEqual(self.interrupts(), [])

    def test_corrupt_saved_receipt_keeps_restart_recovery_hold(self):
        receipt = self.controller.stop(self.descriptor(False))
        (self.controller.receipt_dir / (receipt["id"] + ".json")).write_text("{corrupt fixture")
        found = self.make_controller().list()
        self.assertEqual(found[0]["id"], receipt["id"])
        self.assertTrue(found[0]["recovery_required"])
        self.assertEqual(found[0]["status"], "needs_review")

    def test_valid_json_corruption_also_keeps_restart_recovery_hold(self):
        receipt = self.controller.stop(self.descriptor(False))
        path = self.controller.receipt_dir / (receipt["id"] + ".json")
        for changes in ({"targets": None}, {"targets": [None]}, {"recovery_required": False}, {"pipeline_complete": True}):
            path.write_text(json.dumps({**receipt, **changes}))
            found = self.make_controller().list()
            self.assertTrue(found[0]["recovery_required"])
            self.assertEqual(found[0]["status"], "needs_review")

    def test_rollout_identity_or_path_mismatch_cannot_confirm_stop(self):
        receipt = self.controller.stop(self.descriptor(False))
        self.terminal("fixture-root", "root-turn")
        path = Path(self.states["fixture-root"]["rolloutPath"])
        content = path.read_text().replace('"id": "fixture-root"', '"id": "unrelated-root"')
        path.write_text(content)
        self.assertFalse(self.controller.get(receipt["id"])["parent_stop_confirmed"])

    def test_projection_uses_canonical_history_and_drops_private_content(self):
        view = _view(self.states["fixture-root"])
        self.assertEqual(view["current_turn_id"], "root-turn")
        self.assertNotIn("PRIVATE", json.dumps(view))


class InstalledProtocolTests(unittest.TestCase):
    def fixture_app(self, root, *, version=4, guard=True):
        protocol = json.dumps({INTERRUPT_METHOD: version, OWNER_METHOD: 1,
                               FOLLOW_METHOD: 1, STATE_METHOD: 11}, separators=(",", ":")).encode()
        handler = ("expectedTurnId sendInterruptRequest interruptedTurnId " +
                   ("if(s!=null&&(s!==f||d?.status!==`inProgress`))return null" if guard else "missingGuard")).encode()
        tree = {"files": {".vite": {"files": {"build": {"files": {
            "src-fixture.js": {"size": len(protocol), "offset": "0"},
            "bootstrap-fixture.js": {"size": len(handler), "offset": str(len(protocol))}}}}}}}
        header = json.dumps(tree, separators=(",", ":")).encode()
        archive = root / "Contents/Resources/app.asar"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(struct.pack("<4I", 4, len(header) + 8, len(header) + 4, len(header)) + header + protocol + handler)
        return root

    def test_known_installed_protocol_guard_is_required(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            app = self.fixture_app(Path(temp) / "fixture.app")
            self.assertTrue(installed_protocol(app)["available"])

    def test_protocol_change_or_removed_guard_fails_closed(self):
        for version, guard in ((3, True), (5, True), (4, False)):
            with tempfile.TemporaryDirectory(dir="/tmp") as temp:
                app = self.fixture_app(Path(temp) / "fixture.app", version=version, guard=guard)
                self.assertFalse(installed_protocol(app)["available"])


class IPCWireTests(unittest.TestCase):
    def test_real_framing_exact_owner_and_expected_turn_only_on_fixture_socket(self):
        with tempfile.TemporaryDirectory(prefix="cv-ipc-fixture-", dir="/tmp") as temp:
            path = Path(temp) / "fixture.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            server.listen(1)
            requests, errors = [], []

            def serve():
                try:
                    with server.accept()[0] as peer:
                        buffer = b""
                        while True:
                            while len(buffer) < 4:
                                raw = peer.recv(65536)
                                if not raw:
                                    return
                                buffer += raw
                            size = struct.unpack("<I", buffer[:4])[0]
                            while len(buffer) < 4 + size:
                                buffer += peer.recv(65536)
                            row = json.loads(buffer[4:4 + size])
                            buffer = buffer[4 + size:]
                            requests.append(row)

                            def send(message):
                                raw = json.dumps(message).encode()
                                frame = struct.pack("<I", len(raw)) + raw
                                # Exercise reads split inside both header/body.
                                for piece in (frame[:2], frame[2:7], frame[7:]):
                                    peer.sendall(piece)

                            if row.get("type") == "request":
                                method = row["method"]
                                result = {"clientId": "fixture-client"} if method == "initialize" else {"supportsUntrustedAppInput": True} if method == OWNER_METHOD else {"ok": True, "interruptedTurnId": row["params"]["expectedTurnId"]}
                                send({"type": "response", "requestId": row["requestId"], "resultType": "success",
                                      "method": method, "handledByClientId": "fixture-owner", "result": result})
                            elif row.get("method") == FOLLOW_METHOD and row["params"]["following"]:
                                send({"type": "broadcast", "method": STATE_METHOD, "version": 11,
                                      "sourceClientId": "fixture-owner", "params": {"hostId": "local",
                                      "conversationId": "fixture-thread", "change": {"type": "snapshot",
                                      "conversationState": {"id": "fixture-thread", "cwd": temp,
                                      "turns": [{"turnId": "fixture-turn", "status": "inProgress"}]}}}})
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                with DesktopIPC(path, VERSIONS, timeout=2) as client:
                    owner = client.find_owner("fixture-thread")
                    self.assertEqual(_view(client.snapshot("fixture-thread", owner))["current_turn_id"], "fixture-turn")
                    self.assertTrue(client.interrupt("fixture-thread", "fixture-turn", owner)["ok"])
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                mutations = [row for row in requests if row.get("method") == INTERRUPT_METHOD]
                self.assertEqual(len(mutations), 1)
                self.assertEqual(mutations[0]["params"], {"conversationId": "fixture-thread", "mode": "user-stop", "expectedTurnId": "fixture-turn"})
                self.assertEqual(mutations[0]["targetClientId"], "fixture-owner")
                self.assertEqual(mutations[0]["version"], 4)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
