"""Fixture-only process and safety tests. No real Codex, ads or connectors."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import (JobConflict, JobError, JobManager, usage_status,
                     terminate_owned_group, LUNA_APPLY, LUNA_MOTIVATION)


CAPABILITY = {
    "available": True, "authenticated": True, "account_type": "chatgpt", "checked_at": "2026-10-01T12:00:00+00:00",
    "models": [{"id": "gpt-6.1-sol", "listed": True, "efforts": ["medium","high"]}],
    "connectors": {key: {"callable": True, "tools_available": True, "enabled": True}
                   for key in ("gmail", "google_drive")},
    "usage": {"ordinaryUsageAllowed": True, "rateLimits": {"primary": {"usedPercent": 20, "resetsAt": 2000000000}}},
}

FAKE_CODEX = r'''
import json,sys
from pathlib import Path
mode=sys.argv[1]
background_alive=True
background_lists=0
def emit(message):
 print(json.dumps(message),flush=True)
def result(ident,value):
 emit({'id':ident,'result':value})
def event(method,params):
 emit({'method':method,'params':params})
def finish(status='completed'):
 event('turn/completed',{'threadId':'owned-thread','turn':{'id':'owned-turn','status':status}})
for line in sys.stdin:
 message=json.loads(line)
 with Path(__file__).with_suffix('.calls').open('a') as stream:
  stream.write(json.dumps(message)+'\n')
 method=message.get('method'); ident=message.get('id')
 if method=='initialize': result(ident,{'userAgent':'fixture codex'})
 elif method=='thread/start':
  selected=message['params']['model']
  effort=message['params']['config']['model_reasoning_effort']
  result(ident,{'thread':{'id':'owned-thread'},
                'model':'gpt-6-sol' if mode=='substitute_model' else None if mode=='missing_model' else selected,
                'modelProvider':'fixture-paid-provider' if mode=='substitute_provider' else 'openai',
                'reasoningEffort':'high' if mode=='substitute_effort' else effort})
 elif method=='turn/start':
  result(ident,{'turn':{'id':'owned-turn','status':'inProgress'}})
  event('turn/started',{'threadId':'owned-thread','turn':{'id':'owned-turn'}})
  if mode=='exit': sys.exit(7)
  elif mode=='usage':
   emit({'id':900,'method':'item/tool/call','params':{'tool':'studio_usage_limits','arguments':{},'threadId':'owned-thread','turnId':'owned-turn','callId':'usage'}})
  elif mode=='approval':
   emit({'id':700,'method':'item/commandExecution/requestApproval','params':{'threadId':'owned-thread','turnId':'owned-turn','command':'fixture check','reason':'fixture','availableDecisions':['accept','decline','cancel']}})
  elif mode=='question':
   emit({'id':701,'method':'item/tool/requestUserInput','params':{'threadId':'owned-thread','turnId':'owned-turn','questions':[{'id':'choice','header':'Choice','question':'Choose fixture?','options':[{'label':'A','description':'Fixture A'}]}]}})
  elif mode=='rate':
   event('error',{'error':{'message':'Usage limit reached','codexErrorInfo':'rate_limit'},'willRetry':True})
  elif mode in ['success','background_drain','background_glitch']:
   event('item/agentMessage/delta',{'itemId':'msg','delta':'Fixture work ended.'})
   event('thread/tokenUsage/updated',{'threadId':'owned-thread','tokenUsage':{'total':{'inputTokens':100,'outputTokens':20}}});finish()
 elif method=='turn/interrupt':
  result(ident,{})
  if mode not in ['late_background','no_terminal_event']: finish('interrupted')
 elif method=='thread/backgroundTerminals/list':
  background_lists+=1
  if mode=='background_glitch' and background_lists==1: emit({'id':ident,'error':{'code':-1,'message':'Temporary background observation unavailable'}})
  elif mode=='background_glitch': result(ident,{'data':[{'processId':'owned-building-terminal'}] if background_lists==2 else []})
  elif mode=='background_list_error': emit({'id':ident,'error':{'code':-1,'message':'Fixture owned terminal list failed'}})
  elif mode=='late_background':
   result(ident,{'data':[] if background_lists==1 else [{'processId':'late-owned-terminal'}]})
   if background_lists==1: finish('interrupted')
  elif mode=='background_drain': result(ident,{'data':[{'processId':'owned-building-terminal'}] if background_lists<=2 else []})
  elif mode=='background_success': result(ident,{'data':[{'processId':'owned-terminal-1'}] if background_alive else []})
  elif mode in ['background_terminate_error','background_remains']: result(ident,{'data':[{'processId':'owned-terminal-1'}]})
  else: result(ident,{'data':[]})
 elif method=='thread/backgroundTerminals/terminate':
  if mode=='background_terminate_error': emit({'id':ident,'error':{'code':-1,'message':'Fixture owned terminal termination failed'}})
  else: background_alive=False;result(ident,{'terminated':True})
 elif method=='account/rateLimits/read': result(ident,{'ordinaryUsageAllowed':True,'rateLimits':{'primary':{'usedPercent':21,'resetsAt':2000000000}}})
 elif method is None and ident in [700,701]:
  event('serverRequest/resolved',{'requestId':ident,'threadId':'owned-thread'});finish()
 elif method is None and ident==900:
  finish()
'''


class Data:
    active = False

    def active_pipeline(self):
        return {"active": self.active, "reason": "Fixture external batch is active." if self.active else ""}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        compile(FAKE_CODEX, "fixture Codex", "exec")
        self.temp = tempfile.TemporaryDirectory(prefix="cv-studio-runtime-test-")
        self.root = Path(self.temp.name)
        source = self.root / "Master/auto/local/SCHEDULED_PROMPT.md"
        source.parent.mkdir(parents=True)
        source.write_text("Fixture existing pipeline. Preserve policy-4 gates.")
        metrics = self.root / "Master/auto/local/run_metrics.py"
        metrics_source = Path(__file__).resolve().parents[2] / "Master/auto/local/run_metrics.py"
        shutil.copyfile(metrics_source, metrics)
        self.fake = self.root / "fake_codex.py"
        self.fake.write_text(FAKE_CODEX)
        self.data = Data()
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        self.temp.cleanup()

    def manager(self, mode="success", **kwargs):
        def fixture_hunt(job, batch_dir):
            self.assertTrue((batch_dir / "stage-metrics.json").is_file())
            return {"status":"SUBMITTED", "prompt_submitted_at":job["started_at"],
                    "owned_id":"fixture-public-hunt", "fixture_only":True}
        values = dict(codex_command=[sys.executable, str(self.fake), mode],
                      capability_reader=lambda *_: copy.deepcopy(CAPABILITY), pipeline_launcher=fixture_hunt,
                      pipeline_owner_reader=lambda: self.data.active_pipeline())
        values.update(kwargs)
        manager = JobManager(self.root, self.data, **values)
        self.managers.append(manager)
        return manager

    def wait(self, manager, ident, predicate=lambda job: job["finished_at"], timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = manager.get(ident)
            if predicate(job):
                return job
            time.sleep(.02)
        self.fail("Fixture job did not reach expected state: " + repr(manager.get(ident)))

    def calls(self):
        path = self.fake.with_suffix(".calls")
        return [json.loads(row) for row in path.read_text().splitlines() if row] if path.exists() else []

    def test_capabilities_is_offline_and_fast(self):
        probe = mock.Mock(side_effect=AssertionError("Network must not run"))
        manager = self.manager(capability_reader=probe)
        start = time.monotonic()
        capability = manager.capabilities()
        self.assertLess(time.monotonic() - start, .2)
        self.assertFalse(capability["readiness_confirmed"])
        self.assertFalse(capability["can_run"])
        probe.assert_not_called()

    def test_missing_gmail_does_not_block_public_hunt_launch(self):
        metadata = copy.deepcopy(CAPABILITY)
        metadata["connectors"]["gmail"].update(callable=False, tools_available=False)
        metadata["connectors"]["google_drive"].update(callable=False, tools_available=False)
        manager = self.manager(capability_reader=lambda *_: metadata)
        manager._remember_capabilities(metadata)
        capability = manager.capabilities()
        self.assertTrue(capability["can_run"])
        self.assertTrue(capability["readiness_confirmed"])
        self.assertFalse(capability["connected"])
        self.assertEqual(capability["pipeline_dependencies"], {
            "inbox": {"connector": "gmail", "available": False},
            "sync": {"destination": "CV Studio local role catalog", "available": True},
        })

        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "agent_finished")
        self.assertEqual(job["connector_status"], {"gmail": False})
        calls = self.calls()
        self.assertTrue(any(call.get("method") == "turn/start" for call in calls),
                        "missing later-stage connectors still permit the fixture coordinator turn")
        turn = next(call for call in calls if call.get("method") == "turn/start")
        prompt = turn["params"]["input"][0]["text"]
        self.assertIn("dispatch public Hunt first", prompt)
        self.assertIn("Every ordinary manual, Studio or scheduled start is a NEW invocation", prompt)
        self.assertIn("Under-90-minute design target", prompt)
        self.assertNotIn("finish any already-accepted role obligations", prompt)
        self.assertIn("If Gmail is unavailable", prompt)
        self.assertIn("policy-4", prompt)
        self.assertIn("shortlisted-only Hunt Audit", prompt)
        self.assertNotIn("Opus 4.6 Thinking audits Hunt", prompt)
        run_dir = Path(job["evidence_path"])
        metrics = json.loads((run_dir / "stage-metrics.json").read_text())
        launch = json.loads((run_dir / "studio-launch.json").read_text())
        self.assertEqual((metrics["workflow_name"], metrics["workflow_mode"], metrics["policy_version"]),
                         ("CV Pipeline", "manual", 4))
        self.assertEqual(metrics["started_at"], job["started_at"])
        self.assertEqual((launch["workflow_name"], launch["workflow_mode"], launch["policy_version"]),
                         ("CV Pipeline", "manual", 4))
        self.assertEqual(launch["deferred_dependencies"]["inbox"]["available_at_launch"], False)
        self.assertEqual(launch["deferred_dependencies"]["sync"]["available_at_launch"], True)

    def test_metrics_init_failure_prevents_codex_dispatch(self):
        manager = self.manager()
        (self.root / "Master/auto/local/run_metrics.py").unlink()
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("metrics recorder is missing", job["summary"])
        self.assertFalse(self.calls(), "Codex must not start without the original run receipt")

    def test_metrics_init_refuses_a_nonempty_run_folder(self):
        manager = self.manager()
        run_dir = self.root / "Master/auto/evidence/runs/fixture-metrics"
        run_dir.mkdir(parents=True)
        existing = run_dir / "unrelated.json"
        existing.write_text("{}")
        with self.assertRaisesRegex(JobError, "metrics receipt could not be initialized"):
            manager._initialize_run_metrics(run_dir, "2026-10-03T12:00:00+00:00")
        self.assertEqual(existing.read_text(), "{}")
        self.assertFalse((run_dir / "stage-metrics.json").exists())

    def test_application_tool_scope_requires_zero_apps_and_zero_mcp_tools(self):
        class RouteProbe:
            def __init__(self, *, apps=None, mcp=None, web_search="live", fast=False, service_tier=None):
                self.values = {
                    "app/installed": {"apps": apps if apps is not None else [{"enabled": False, "callable": False}]},
                    "mcpServerStatus/list": mcp if mcp is not None else {"data": [{"tools": {}}], "nextCursor": None},
                    "config/read": {"config": {"web_search": web_search, "service_tier": service_tier,
                                                 "features": {"fast_mode": fast}}},
                }

            def request(self, method, _params, timeout=0):
                return self.values[method]

        receipt = JobManager._verify_application_tool_scope(RouteProbe(), Path("/fixture/empty"), "live")
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["enabled_apps"], 0)
        self.assertEqual(receipt["callable_mcp_tools"], 0)
        fast = JobManager._verify_application_tool_scope(
            RouteProbe(web_search="disabled", fast=True, service_tier="priority"), Path("/fixture/empty"),
            "disabled", "priority")
        self.assertTrue(fast["fast_mode_enabled"])
        with self.assertRaisesRegex(JobError, "Fast service tier"):
            JobManager._verify_application_tool_scope(
                RouteProbe(web_search="disabled", fast=False, service_tier="priority"),
                Path("/fixture/empty"), "disabled", "priority")
        for probe in (
            RouteProbe(apps=[{"enabled": True, "callable": False}]),
            RouteProbe(mcp={"data": [{"tools": {"gmail.send": {}}}], "nextCursor": None}),
            RouteProbe(mcp={"data": [{"tools": {}}], "nextCursor": "more"}),
            RouteProbe(web_search="disabled"),
        ):
            with self.subTest(probe=probe.values):
                with self.assertRaises(JobError):
                    JobManager._verify_application_tool_scope(probe, Path("/fixture/empty"), "live")

    def _run_fixture_application_turn(self, spec, kind, completed_turn, *,
                                     thread_optional=True, turn_started_fields=None,
                                     turn_response_fields=None, item_events=None,
                                     rpc_request=None, fixture_servers=None):
        manager = self.manager()
        job = {"id": "a" * 24, "kind": kind, "provider": "sol" if spec["provider"] == "sol" else "openai",
               "application_mode": "review" if kind == "application_contacts" else None,
               "status": "running", "logs": [], "pending_requests": []}
        control = {"stop": threading.Event(), "server": None, "turn_done": threading.Event(),
                   "turn_submitted": False, "background_lock": threading.Lock()}
        class FixtureAppServer:
            def __init__(self, _command, _cwd, event, _diagnostic, **kwargs):
                self.event = event
                self.process = type("Process", (), {"pid": 1234})()
                self.closed = threading.Event()
                self.sent = []
                self.web_search = kwargs["application_web_search"]
                if fixture_servers is not None:
                    fixture_servers.append(self)

            def send(self, message):
                self.sent.append(copy.deepcopy(message))

            def initialize(self):
                return {"userAgent": "fixture app server"}

            def request(self, method, params, timeout=0):
                if method == "turn/interrupt":
                    return {}
                if method == "thread/backgroundTerminals/list":
                    return {"data": [], "nextCursor": None}
                if method == "app/installed":
                    return {"apps": []}
                if method == "mcpServerStatus/list":
                    return {"data": [], "nextCursor": None}
                if method == "config/read":
                    return {"config": {"web_search": self.web_search,
                        "service_tier": "priority" if spec.get("service_tier") else None,
                        "features": {"fast_mode": bool(spec.get("service_tier"))}}}
                if method == "thread/start":
                    assert params["model"] == "gpt-6.1-sol", params
                    assert params["model"] == spec["model"], params
                    response = {"thread": {"id": "fixture-thread"}, "model": spec["model"],
                        "modelProvider": spec["model_provider"]}
                    if thread_optional:
                        response.update(reasoningEffort=spec["effort"], serviceTier=spec.get("service_tier"))
                    return response
                if method == "turn/start":
                    assert params["model"] == "gpt-6.1-sol", params
                    assert params["effort"] == spec["effort"], params
                    started_turn = {"id": "fixture-turn"}
                    started_turn.update(turn_started_fields or {})
                    self.event({"method": "turn/started", "params": {
                        "threadId": "fixture-thread", "turn": started_turn}})
                    user_message = {"type": "userMessage", "id": "fixture-user-message",
                                    "content": [{"type": "text", "text": "Fixture request text."}]}
                    self.event({"method": "item/started", "params": {"item": user_message}})
                    self.event({"method": "item/completed", "params": {"item": user_message}})
                    for row in item_events or []:
                        item = row.get("item") or {"type": row.get("type")}
                        self.event({"method": row.get("method", "item/started"), "params": {"item": item}})
                    if rpc_request:
                        self.event({"id": "fixture-rpc-request", "method": rpc_request,
                                    "params": {"private": "must not enter diagnostics"}})
                    self.event({"method": "item/completed", "params": {"item": {
                        "type": "agentMessage", "text": json.dumps({"text": "Fixture-only route receipt."})}}})
                    self.event({"method": "turn/completed", "params": {
                        "threadId": "fixture-thread", "turn": completed_turn}})
                    response = {"turn": {"id": "fixture-turn", "status": "inProgress"}}
                    response["turn"].update(turn_response_fields or {})
                    return response
                raise AssertionError("Unexpected fixture app-server method: " + method)

        with mock.patch("runtime.AppServer", FixtureAppServer):
            route = manager._run_luna_application(job, control, {"language": "en", "company": "Fixture Co",
                "title": "Fixture role", "candidates": []}, spec=spec, capability={"checked_at": "fixture"},
                disabled_mcp_servers=[])
        return route, job

    def test_user_message_lifecycle_is_allowed_by_fixture_app_server(self):
        spec = LUNA_APPLY
        route, job = self._run_fixture_application_turn(spec, "application_contacts", {
            "id": "fixture-turn", "status": "completed", "model": spec["model"],
            "reasoningEffort": spec["effort"]})
        self.assertEqual(route["payload"]["text"], "Fixture-only route receipt.")
        self.assertNotIn("Fixture request text.", job["last_message"])

    def test_shell_file_mcp_and_unknown_item_types_remain_blocked(self):
        spec = LUNA_APPLY
        completed = {"id": "fixture-turn", "status": "completed", "model": spec["model"],
                     "reasoningEffort": spec["effort"]}
        for item_type in ("commandExecution", "fileChange", "mcpToolCall", "unknownAction"):
            with self.subTest(item_type=item_type):
                with self.assertRaisesRegex(JobError,
                        "Blocked unapproved app-server event.*type " + item_type + ", method item/started"):
                    self._run_fixture_application_turn(spec, "application_contacts", completed,
                        item_events=[{"type": item_type}])

    def test_rpc_requests_remain_blocked_without_recording_payload(self):
        spec = LUNA_APPLY
        completed = {"id": "fixture-turn", "status": "completed", "model": spec["model"],
                     "reasoningEffort": spec["effort"]}
        servers = []
        with self.assertRaisesRegex(JobError,
                "Blocked unapproved app-server event.*type rpc_request, method item/tool/call"):
            self._run_fixture_application_turn(spec, "application_contacts", completed,
                rpc_request="item/tool/call", fixture_servers=servers)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].sent[0]["id"], "fixture-rpc-request")
        self.assertEqual(servers[0].sent[0]["error"]["code"], -32000)
        self.assertNotIn("private", json.dumps(servers[0].sent))

    def test_web_search_is_allowed_only_for_finder_fixture(self):
        finder = LUNA_APPLY
        finder_turn = {"id": "fixture-turn", "status": "completed", "model": finder["model"],
                       "reasoningEffort": finder["effort"]}
        route, _job = self._run_fixture_application_turn(finder, "application_contacts", finder_turn,
            item_events=[{"type": "webSearch"}])
        self.assertEqual(route["payload"]["text"], "Fixture-only route receipt.")


    def test_cleanup_accepts_terminal_turn_and_holds_missing_terminal_proof(self):
        manager = self.manager()

        class FixtureServer:
            process = type("Process", (), {"pid": 4321})()

            def close(self):
                return None

        manager._cleanup_background = mock.Mock(return_value={"safe": True, "fresh_final_readback": True})
        terminal = threading.Event()
        terminal.set()
        child_job = {"id": "terminal-child", "thread_id": "fixture-thread", "turn_id": "fixture-turn"}
        child_control = {"server": FixtureServer(), "turn_submitted": True,
                         "turn_done": terminal, "background_lock": threading.Lock()}
        receipt = manager._cleanup_application_child(child_job, child_control)
        self.assertTrue(receipt["turn_quiescent"])
        self.assertTrue(receipt["safe"])
        manager._cleanup_background.assert_called_once_with(child_job, child_control, force=True)

        class TerminalDuringCleanup:
            def __init__(self):
                self.reads = 0

            def is_set(self):
                self.reads += 1
                return self.reads >= 2

            @staticmethod
            def wait(_timeout=None):
                return True

        race_job = {"id": "race-child", "thread_id": "fixture-thread", "turn_id": "fixture-turn"}
        race_control = {"server": FixtureServer(), "turn_submitted": True,
                        "turn_done": TerminalDuringCleanup(), "background_lock": threading.Lock()}
        race_receipt = manager._cleanup_application_child(race_job, race_control)
        self.assertTrue(race_receipt["turn_quiescent"])
        self.assertTrue(race_receipt["safe"])
        self.assertEqual(manager._cleanup_background.call_count, 2)

        active_job = {"id": "active-child", "thread_id": "fixture-thread", "turn_id": "fixture-turn"}
        active_control = {"server": None, "turn_submitted": True, "turn_done": threading.Event()}
        active_receipt = manager._cleanup_application_child(active_job, active_control)
        self.assertFalse(active_receipt["turn_quiescent"])
        self.assertFalse(active_receipt["safe"])

    def test_completed_event_route_substitution_is_rejected_for_motivation_and_contacts(self):
        for spec, kind in ((LUNA_MOTIVATION, "application_motivation"),
                           (LUNA_APPLY, "application_contacts")):
            base = {"id": "fixture-turn", "status": "completed", "model": spec["model"],
                    "reasoningEffort": spec["effort"], "serviceTier": spec.get("service_tier")}
            mutations = [
                {"model": "gpt-6-sol" if spec["model"] != "gpt-6-sol" else "gpt-6.1-sol"},
                {"reasoningEffort": "low" if spec["effort"] != "low" else "high"},
            ]
            if spec.get("service_tier"):
                mutations.append({"serviceTier": "default"})
            for mutation in mutations:
                with self.subTest(model=spec["model"], mutation=mutation):
                    final = {**base, **mutation}
                    with self.assertRaisesRegex(JobError, "completed with a different"):
                        self._run_fixture_application_turn(spec, kind, final)
                    # Completion may precede a delayed turn/start response.
                    with self.assertRaisesRegex(JobError, "completed with a different"):
                        self._run_fixture_application_turn(spec, kind, final,
                            turn_response_fields={"model": spec["model"],
                                "reasoningEffort": spec["effort"],
                                "serviceTier": spec.get("service_tier")})

    def test_completed_event_is_authoritative_and_missing_optional_fields_stay_truthful(self):
        for spec, kind in ((LUNA_MOTIVATION, "application_motivation"),
                           (LUNA_APPLY, "application_contacts")):
            with self.subTest(model=spec["model"], metadata="reported"):
                completed = {"id": "fixture-turn", "status": "completed", "model": spec["model"],
                    "reasoningEffort": spec["effort"], "serviceTier": spec.get("service_tier")}
                route, job = self._run_fixture_application_turn(spec, kind, completed)
                self.assertEqual(route["actual_model"], spec["model"])
                self.assertEqual(route["actual_effort"], spec["effort"])
                self.assertEqual(route["actual_service_tier"], spec.get("service_tier"))
                self.assertEqual(route["actual_model_phase"], "turn_completed")
                self.assertEqual(route["actual_effort_phase"], "turn_completed")
                self.assertEqual(route["actual_service_tier_phase"],
                                 "turn_completed" if spec.get("service_tier") else None)
                self.assertEqual(job["actual_model"], spec["model"])
                self.assertEqual(job["actual_effort"], spec["effort"])
                self.assertEqual(job["actual_service_tier"], spec.get("service_tier"))

            with self.subTest(model=spec["model"], metadata="completion-omits-start-confirmed"):
                route, job = self._run_fixture_application_turn(spec, kind,
                    {"id": "fixture-turn", "status": "completed"})
                self.assertEqual(route["model_used"], spec["model"])
                self.assertEqual(route["actual_model"], spec["model"])
                self.assertEqual(route["actual_effort"], spec["effort"])
                self.assertEqual(route["actual_service_tier"], spec.get("service_tier"))
                self.assertEqual(route["actual_model_phase"], "thread_start")
                self.assertEqual(route["actual_effort_phase"], "thread_start")
                self.assertEqual(route["actual_service_tier_phase"],
                                 "thread_start" if spec.get("service_tier") else None)
                self.assertEqual(job["actual_model"], spec["model"])
                self.assertEqual(job["actual_effort"], spec["effort"])
                self.assertEqual(job["actual_service_tier"], spec.get("service_tier"))

            with self.subTest(model=spec["model"], metadata="optional-never-reported"):
                route, job = self._run_fixture_application_turn(spec, kind,
                    {"id": "fixture-turn", "status": "completed"}, thread_optional=False)
                self.assertEqual(route["actual_model"], spec["model"])
                self.assertEqual(route["actual_model_phase"], "thread_start")
                self.assertIsNone(route["actual_effort"])
                self.assertIsNone(route["actual_effort_phase"])
                self.assertIsNone(route["actual_service_tier"])
                self.assertIsNone(route["actual_service_tier_phase"])
                self.assertEqual(job["actual_model"], spec["model"])
                self.assertIsNone(job["actual_effort"])
                self.assertIsNone(job["actual_service_tier"])

            with self.subTest(model=spec["model"], metadata="turn-start"):
                turn_start_fields = {"model": spec["model"], "reasoningEffort": spec["effort"]}
                if spec.get("service_tier"):
                    turn_start_fields["serviceTierForTurn"] = spec["service_tier"]
                route, job = self._run_fixture_application_turn(spec, kind,
                    {"id": "fixture-turn", "status": "completed"}, thread_optional=False,
                    turn_response_fields=turn_start_fields)
                self.assertEqual(route["actual_model_phase"], "turn_start")
                self.assertEqual(route["actual_effort_phase"], "turn_start")
                self.assertEqual(route["actual_service_tier_phase"],
                                 "turn_start" if spec.get("service_tier") else None)
                self.assertEqual(route["actual_model"], spec["model"])
                self.assertEqual(route["actual_effort"], spec["effort"])

    def test_application_job_is_deduped_and_records_requested_effort_separately(self):
        entered, release = threading.Event(), threading.Event()

        class ApplicationService:
            saved = None

            @staticmethod
            def status_options():
                return []

            def save_generated_motivation(self, packet, text, **kwargs):
                self.saved = {"packet": packet, "text": text, **kwargs}
                return {"status": "ready", "text": text, "summary": "Fixture motivation read back."}

        service = ApplicationService()

        def fixture_runner(_manager, _job, control, packet):
            entered.set()
            release.wait(2)
            return {"payload": {"text": "Fixture-only company motivation."},
                    "model_used": "gpt-6.1-sol", "requested_effort": "low", "actual_effort": None,
                    "requested_service_tier": "priority", "actual_service_tier": None}

        manager = self.manager(application_runner=fixture_runner)
        manager.set_application_service(service)
        self.data.active = True
        role_id = "a" * 24
        packet = {"role_id": role_id, "language": "fr", "source_hashes": {}}
        first = manager.start_application_job("application_motivation", role_id, "sol", language="fr", packet=packet)
        self.assertTrue(entered.wait(2))
        second = manager.start_application_job("application_motivation", role_id, "sol", language="fr", packet=packet)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(manager.list()["total"], 1)
        self.assertEqual(first["requested_effort"], "low")
        self.assertEqual(first["requested_service_tier"], "priority")
        self.assertIsNone(first["actual_effort"])
        with self.assertRaises(JobConflict):
            manager.start("pipeline")
        release.set()
        job = self.wait(manager, first["id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["model_used"], "gpt-6.1-sol")
        self.assertIsNone(job["actual_model"])
        self.assertEqual(job["requested_effort"], "low")
        self.assertEqual(job["requested_service_tier"], "priority")
        self.assertIsNone(job["actual_effort"])
        self.assertEqual(service.saved["effort"], "low")
        self.assertEqual(service.saved["requested_service_tier"], "priority")
        self.assertIsNone(service.saved["actual_effort"])
        self.assertIsNone(service.saved["actual_service_tier"])

    def test_native_contact_identity_failure_closes_without_remote_provider_hold(self):
        class Server:
            class Process:
                pid = 12345
            process = Process()
            def close(self): pass
        manager = self.manager()
        item = manager.enqueue_manual_job({"url":"https://jobs.example/fail-native", "find_contacts":True})["item"]
        manager.set_application_service(object())
        def fail_native(job, control):
            control["server"] = Server()
            control["turn_done"].set()
            control["turn_submitted"] = True
            raise JobError("The original job identity could not be verified.")
        with mock.patch.object(manager, "_run_application_job", side_effect=fail_native), \
             mock.patch.object(manager, "_cleanup_background", return_value={"safe":True,"remaining":[]}), \
             mock.patch.object(manager, "_interrupt"):
            job = manager.start_queued_contact_research(item["id"], "fixture-run")
            terminal = self.wait(manager, job["id"])
        self.assertEqual(terminal["status"], "failed")
        self.assertTrue(terminal["turn_quiescent"])
        self.assertTrue(terminal["owned_process_cleanup"]["safe"])
        self.assertFalse(terminal["recovery_required"])
        self.assertEqual(terminal["cleanup_status"], "verified")

    def test_optional_contact_failure_and_cancel_leave_cv_queue_independent(self):
        def failure(*args):
            raise JobError("Fixture provider failure; no fallback.")
        manager = self.manager(application_runner=failure)
        manager.set_application_service(object())
        item = manager.enqueue_manual_job({"url":"https://jobs.example/failure", "find_contacts":True})["item"]
        job = manager.start_queued_contact_research(item["id"], "fixture-run")
        self.wait(manager, job["id"])
        saved = manager.manual_jobs()["items"][0]
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(saved["contact_research"]["status"], "held")
        self.assertIn("Fixture provider failure", saved["contact_research"]["summary"])
        with mock.patch.object(manager, "stop") as stop:
            manager.jobs[job["id"]]["status"] = "running"
            manager.set_contact_choice(item["id"], False)
            stop.assert_called_once_with(job["id"])
        with self.assertRaisesRegex(Exception, "cancelled"):
            manager.manual_job_queue.update_contact_research(item["id"], job["id"], status="completed", contacts=[{"name":"Late result"}])
        self.assertFalse(manager.manual_job_queue.get(item["id"])["find_contacts"])
        manager.jobs[job["id"]]["status"] = "failed"

    def test_unqueued_contact_choice_never_dispatches(self):
        manager = self.manager()
        item = manager.enqueue_manual_job({"url":"https://jobs.example/terminal", "find_contacts":True})["item"]
        manager.manual_job_queue.claim(item["id"], "fixture-build")
        manager.manual_job_queue.finish(item["id"], "fixture-build", "completed", "Fixture CV completed")
        with mock.patch.object(manager, "start_application_job") as start:
            self.assertIsNone(manager.start_queued_contact_research(item["id"], "fixture-run"))
            start.assert_not_called()

    def test_pipeline_contact_worker_persists_source_checked_results_without_cv_folder(self):
        class Service:
            def verify_saved_job_contacts(self, packet, provider, model, payload):
                self.asserted = (provider, model, packet["jd"])
                return {"verified_contacts":[], "verified_count":0, "rejected_count":0,
                        "summary":"No verified public contacts found.", "job_evidence":payload["job_evidence"]}
        service = Service()
        def worker(manager, job, control, packet):
            return {"model_used":"gpt-6.1-sol", "actual_model":"gpt-6.1-sol", "requested_effort":"high", "actual_effort":"high",
                    "payload":{"people":[], "job_evidence":{"source_url":packet["source_url"], "company":"Fixture", "title":"Analyst", "quote":"Fixture seeks Analyst"}}}
        manager = self.manager(application_runner=worker)
        manager.set_application_service(service)
        item = manager.enqueue_manual_job({"url":"https://careers.example.test/jobs/analyst", "find_contacts":True})["item"]
        job = manager.start_queued_contact_research(item["id"], "fixture-owned-pipeline")
        terminal = self.wait(manager, job["id"])
        self.assertEqual(terminal["status"], "completed")
        record = manager.manual_jobs()["items"][0]
        self.assertEqual(record["status"], "queued")
        self.assertEqual(record["contact_research"]["status"], "completed")
        self.assertEqual(record["contact_research"]["run_id"], "fixture-owned-pipeline")
        self.assertEqual(service.asserted, ("sol", "gpt-6.1-sol", ""))
        self.assertIsNone(manager.start_queued_contact_research(item["id"], "fixture-owned-pipeline"))

    def test_added_job_optional_contacts_dispatch_once_during_pipeline_and_do_not_block_build(self):
        manager = self.manager()
        with mock.patch.object(manager, "start_application_job", return_value={"status":"queued"}) as start:
            off = manager.enqueue_manual_job({"url":"https://jobs.example/off"})
            on = manager.enqueue_manual_job({"url":"https://jobs.example/on", "find_contacts":True})
            start.assert_not_called()
            self.assertIsNone(manager.start_queued_contact_research(off["item"]["id"], "fixture-run"))
            manager.start_queued_contact_research(on["item"]["id"], "fixture-run")
            manager.start_queued_contact_research(on["item"]["id"], "fixture-run")
            self.assertEqual(start.call_count, 1)
            packet = start.call_args.kwargs["packet"]
            self.assertEqual(packet["jd"], "")
            self.assertEqual(set(packet), {"role_id", "manual_contact_item_id", "company", "title", "source_url", "jd"})
            self.assertEqual(start.call_args.args[:3], ("application_contacts", packet["role_id"], "sol"))
            manager.jobs["fixture-contact"] = {"kind":"application_contacts", "status":"running"}
            manager._guard(local_only=True)
            manager.jobs.pop("fixture-contact")
        held = manager.enqueue_manual_job({"jd":"Full public advert text", "find_contacts":True})
        manager.start_queued_contact_research(held["item"]["id"], "fixture-run")
        saved = manager.manual_job_queue.get(held["item"]["id"])
        self.assertEqual(saved["contact_research"]["status"], "held")
        self.assertEqual(saved["status"], "queued")
        manager.set_contact_choice(on["item"]["id"], False)
        self.assertFalse(manager.manual_job_queue.get(on["item"]["id"])["find_contacts"])

    def test_contact_discovery_offers_only_luna_max_and_rejects_other_providers(self):
        manager = self.manager()
        options = manager.application_options()
        self.assertEqual(set(options["providers"]), {"sol", "motivation"})
        self.assertEqual(options["contact_discovery"]["provider"], "sol")
        self.assertEqual(options["contact_discovery"]["model"], "gpt-6.1-sol")
        self.assertEqual(options["contact_discovery"]["effort"], "high")
        self.assertFalse(options["contact_discovery"]["generation_verified"])
        for provider in ("workflow", "google", "openai"):
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(JobError, "Sol 6.1 high only"):
                    manager.start_application_job("application_contacts", "b" * 24, provider,
                        packet={"role_id": "b" * 24, "company": "Fixture"})
        self.assertEqual(manager.list()["total"], 0)

    def test_contact_job_runs_one_luna_max_turn_then_saves_local_source_check(self):
        class ApplicationService:
            def __init__(self):
                self.saved = None

            @staticmethod
            def status_options():
                return []

            def save_contacts(self, role_id, job_id, provider, model, payload):
                self.saved = {"role_id": role_id, "job_id": job_id, "provider": provider,
                    "model": model, "payload": copy.deepcopy(payload)}
                return {"role_id": role_id, "job_id": job_id, "contacts": [], "items": [],
                    "total": 0, "verified_count": 0, "rejected_count": 0,
                    "review": {"status": "completed", "actual_model": "deterministic-public-source-check-v1"},
                    "summary": "No source-verified public contacts were found."}

        service = ApplicationService()
        route_calls = []

        def fixture_runner(_manager, job, _control, _packet):
            route_calls.append((job["provider"], job["requested_model"], job["requested_effort"]))
            return {"payload": {"people": [], "company_emails": []}, "model_used": "gpt-6.1-sol",
                "actual_model": "gpt-6.1-sol", "requested_effort": "high", "actual_effort": None}

        manager = self.manager(application_runner=fixture_runner)
        manager.set_application_service(service)
        role_id = "c" * 24
        started = manager.start_application_job("application_contacts", role_id, "sol",
            packet={"role_id": role_id, "company": "Fixture Employer", "title": "Analyst",
                "location": "Paris", "jd": "Public fixture source", "source_url": "https://fixture.example/job"})
        job = self.wait(manager, started["id"])
        self.assertEqual(job["status"], "completed", job["summary"])
        self.assertEqual(route_calls, [("sol", "gpt-6.1-sol", "high")])
        self.assertEqual(service.saved["provider"], "sol")
        self.assertEqual(service.saved["model"], "gpt-6.1-sol")
        self.assertEqual(job["requested_model"], "gpt-6.1-sol")
        self.assertEqual(job["actual_model"], "gpt-6.1-sol")
        self.assertEqual(job["requested_effort"], "high")
        self.assertNotIn("contact_workflow", job)
        self.assertNotIn("child_cleanup", job)

    def test_contact_job_dedupes_an_active_historical_workflow_for_same_role(self):
        manager = self.manager()
        role_id = "d" * 24
        legacy = {"id": "e" * 16, "job_id": "e" * 16,
            "kind": "application_contacts", "role_id": role_id,
            "provider": "workflow", "model": "historical-multi-route",
            "language": None, "status": "running", "started_at": "2026-10-05T09:00:00+00:00",
            "finished_at": None, "summary": "An old contact search is still active.",
            "logs": [], "pending_requests": [], "result": None,
            "recovery_required": False}
        manager.jobs[legacy["id"]] = legacy
        started = manager.start_application_job("application_contacts", role_id, "sol",
            packet={"role_id": role_id, "company": "Fixture Employer"})
        self.assertEqual(started["id"], legacy["id"])
        self.assertEqual(started["provider"], "workflow")
        self.assertEqual(manager.list()["total"], 1)

    def test_contact_source_search_uses_web_search_without_connected_tools(self):
        finder_turn = {"id": "fixture-turn", "status": "completed", "model": LUNA_APPLY["model"],
                       "reasoningEffort": LUNA_APPLY["effort"]}
        route, _job = self._run_fixture_application_turn(LUNA_APPLY, "application_contacts", finder_turn,
            item_events=[{"type": "webSearch"}])
        self.assertEqual(route["payload"]["text"], "Fixture-only route receipt.")

    def test_cleanup_sweep_saves_receipt_when_cleanup_raises(self):
        manager = self.manager()
        parent = {"id": "parentcleanup01", "kind": "application_contacts", "role_id": "0" * 24,
                  "status": "running", "logs": [], "pending_requests": []}
        child_job = {"id": "childcleanup01", "status": "running"}
        child_control = {"stop": threading.Event(), "done": threading.Event(), "server": None}
        control = {"children": {child_job["id"]: {"job": child_job, "control": child_control}},
                   "child_cleanup_receipts": []}

        def fail_cleanup(_child_job, _child_control):
            raise RuntimeError("fixture cleanup sweep failed")

        manager._cleanup_application_child = fail_cleanup
        result = manager._cleanup_application_children(parent, control)
        self.assertFalse(result["safe"])
        self.assertEqual(result["children"], 1)
        self.assertEqual(parent["status"], "interrupted")
        self.assertTrue(parent["recovery_required"])
        self.assertEqual(parent["child_cleanup"][0]["reason"], "fixture cleanup sweep failed")
        saved = json.loads((manager.job_dir / (parent["id"] + ".json")).read_text())
        self.assertEqual(saved["child_cleanup"], parent["child_cleanup"])

    def test_external_run_blocks_both_actions_before_spawn(self):
        manager = self.manager()
        self.data.active = True
        for kind in ("pipeline", "check"):
            with self.assertRaises(JobConflict):
                manager.start(kind)
        self.assertFalse(self.fake.with_suffix(".calls").exists())
        self.assertEqual(manager.list()["total"], 0)

    def test_capability_refresh_is_explicit_and_nonblocking(self):
        release = threading.Event()
        def metadata(*_):
            release.wait(3)
            return copy.deepcopy(CAPABILITY)
        manager = self.manager(capability_reader=metadata)
        start = time.monotonic()
        result = manager.refresh_capabilities()
        self.assertLess(time.monotonic() - start, .2)
        self.assertTrue(result["checking"])
        release.set()
        deadline = time.monotonic() + 3
        while manager.capabilities()["checking"] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(manager.capabilities()["readiness_confirmed"])

    def test_document_check_uses_fixture_and_records_output(self):
        manager = self.manager(check_command=[sys.executable, "-c", "print('FIXTURE checks passed')"])
        job = self.wait(manager, manager.start("check")["id"])
        self.assertEqual(job["status"], "completed")
        self.assertFalse(job["pipeline_complete"])
        self.assertTrue(any("FIXTURE" in line["message"] for line in job["logs"]))

    def test_coordinator_finish_is_never_pipeline_success(self):
        manager = self.manager()
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "agent_finished")
        self.assertFalse(job["pipeline_complete"])
        self.assertTrue(job["remote_cleanup"]["safe"])
        self.assertTrue(job["background_cleanup"]["safe"])
        self.assertTrue(job["owned_process_cleanup"]["safe"])
        self.assertEqual(job["cleanup_status"], "verified")
        self.assertEqual(job["token_usage"]["total"]["outputTokens"], 20)
        start = next(call for call in self.calls() if call.get("method") == "thread/start")
        self.assertEqual(start["params"]["approvalPolicy"], "on-request")
        self.assertEqual(start["params"]["sandbox"], "workspace-write")
        self.assertFalse(start["params"]["allowProviderModelFallback"])
        turn = next(call for call in self.calls() if call.get("method") == "turn/start")
        prompt = turn["params"]["input"][0]["text"]
        self.assertIn("Read current AGENTS.md", prompt)
        self.assertIn("does not supersede current AGENTS.md", prompt)
        self.assertNotIn("Fixture existing pipeline", prompt)
        self.assertEqual(turn["params"]["effort"], "medium")

    def test_approval_roundtrip_is_scoped_to_one_action(self):
        manager = self.manager("approval")
        ident = manager.start("pipeline")["id"]
        job = self.wait(manager, ident, lambda job: bool(job["pending_requests"]))
        self.assertEqual(job["status"], "waiting_input")
        with self.assertRaises(JobError):
            manager.respond(ident, "700", {"decision": "acceptForSession"})
        manager.respond(ident, "700", {"decision": "accept"})
        self.assertEqual(self.wait(manager, ident)["status"], "agent_finished")
        self.assertTrue(any(call.get("id") == 700 and call.get("result") == {"decision": "accept"} for call in self.calls()))

    def test_questions_require_matching_ids(self):
        manager = self.manager("question")
        ident = manager.start("pipeline")["id"]
        self.wait(manager, ident, lambda job: bool(job["pending_requests"]))
        with self.assertRaises(JobError):
            manager.respond(ident, "701", {"answers": {"wrong": {"answers": ["A"]}}})
        manager.respond(ident, "701", {"answers": {"choice": {"answers": ["A"]}}})
        self.assertEqual(self.wait(manager, ident)["status"], "agent_finished")

    def test_usage_dynamic_tool_does_not_deadlock(self):
        manager = self.manager("usage")
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "agent_finished")
        self.assertTrue(job["latest_usage"]["available"])
        self.assertEqual(job["latest_usage"]["limits"]["rateLimits"]["primary"]["usedPercent"], 21)

    def test_provider_limit_interrupts_no_retry_and_cleans_up(self):
        cleaner = mock.Mock(return_value={"safe": True, "cancelled": 1})
        manager = self.manager("rate", remote_cleaner=cleaner)
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertFalse(job["pipeline_complete"])
        cleaner.assert_called_once()
        methods = [call.get("method") for call in self.calls()]
        self.assertEqual(methods.count("turn/start"), 1)
        self.assertIn("turn/interrupt", methods)

    def test_unexpected_exit_preserves_unresolved_cleanup_and_blocks(self):
        cleaner = mock.Mock(return_value={"safe": False})
        manager = self.manager("exit", remote_cleaner=cleaner)
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "interrupted")
        self.assertTrue(job["recovery_required"])
        cleaner.assert_called_once()
        with self.assertRaises(JobConflict):
            manager.start("pipeline")

    def test_shutdown_interrupts_only_owned_process(self):
        cleaner = mock.Mock(return_value={"safe": True})
        manager = self.manager("hang", remote_cleaner=cleaner)
        ident = manager.start("pipeline")["id"]
        self.wait(manager, ident, lambda job: bool(job.get("turn_id")))
        manager.close()
        job = self.wait(manager, ident)
        self.assertEqual(job["status"], "stopped")
        self.assertFalse(job["pipeline_complete"])
        interrupts = [call for call in self.calls() if call.get("method") == "turn/interrupt"]
        self.assertTrue(interrupts)
        self.assertEqual(interrupts[0]["params"]["threadId"], "owned-thread")

    def _background_failure(self, mode):
        manager = self.manager(mode, remote_cleaner=lambda _job: {"safe": True})
        ident = manager.start("pipeline")["id"]
        self.wait(manager, ident, lambda job: bool(job.get("turn_id")))
        manager.stop(ident)
        job = self.wait(manager, ident)
        self.assertEqual(job["status"], "interrupted")
        self.assertTrue(job["recovery_required"])
        self.assertFalse(job["background_cleanup"]["safe"])
        self.assertTrue(job["remote_cleanup"]["safe"])
        self.assertTrue(job["owned_process_cleanup"]["safe"])
        self.assertEqual(job["cleanup_status"], "needs_review")
        self.assertTrue(job["background_cleanup"]["errors"])
        with self.assertRaises(JobConflict):
            manager.start("pipeline")
        background_calls = [call for call in self.calls() if str(call.get("method", "")).startswith("thread/backgroundTerminals/")]
        self.assertTrue(background_calls)
        self.assertTrue(all(call["params"]["threadId"] == "owned-thread" for call in background_calls))
        return job

    def test_background_list_failure_requires_recovery(self):
        self._background_failure("background_list_error")

    def test_background_termination_failure_requires_recovery(self):
        self._background_failure("background_terminate_error")

    def test_background_remaining_after_ack_requires_recovery(self):
        job = self._background_failure("background_remains")
        self.assertEqual(job["background_cleanup"]["remaining"], ["owned-terminal-1"])

    def test_background_success_requires_empty_readback(self):
        manager = self.manager("background_success", remote_cleaner=lambda _job: {"safe": True})
        ident = manager.start("pipeline")["id"]
        self.wait(manager, ident, lambda job: bool(job.get("turn_id")))
        manager.stop(ident)
        job = self.wait(manager, ident)
        self.assertEqual(job["status"], "stopped")
        self.assertFalse(job["recovery_required"])
        self.assertTrue(any(check["terminated"] == ["owned-terminal-1"] for check in job["background_cleanup"].get("prior_checks", [])))
        self.assertEqual(job["background_cleanup"]["remaining"], [])
        self.assertTrue(job["background_cleanup"]["fresh_final_readback"])
        self.assertTrue(job["background_cleanup"]["after_turn_quiescence"])
        listings = [call for call in self.calls() if call.get("method") == "thread/backgroundTerminals/list"]
        self.assertGreaterEqual(len(listings), 2)

    def test_boot_reconciles_only_proven_motivation_cleanup_without_rewriting_outcome(self):
        jobs = self.root / "Resources/Studio/.local/jobs"
        jobs.mkdir(parents=True, exist_ok=True)

        def saved_job(ident, kind):
            return {
                "id": ident, "kind": kind, "status": "interrupted", "recovery_required": True,
                "summary": "The original attempt stopped before completion.", "turn_quiescent": True,
                "background_cleanup": {"safe": True, "fresh_final_readback": True,
                    "after_turn_quiescence": True, "checked_at": "2026-10-03T13:35:00+00:00",
                    "remaining": [], "errors": []},
                "owned_process_cleanup": {"safe": True, "verified_at": "2026-10-03T13:35:01+00:00"},
            }

        motivation_id, pipeline_id = "2112d8266db444be", "a112d8266db444be"
        (jobs / (motivation_id + ".json")).write_text(json.dumps(saved_job(motivation_id, "application_motivation")))
        (jobs / (pipeline_id + ".json")).write_text(json.dumps(saved_job(pipeline_id, "pipeline")))

        manager = self.manager()
        motivation = manager.get(motivation_id)
        pipeline = manager.get(pipeline_id)

        self.assertEqual(motivation["status"], "interrupted")
        self.assertFalse(motivation["recovery_required"])
        self.assertEqual(motivation["summary"], "The original attempt stopped before completion.")
        self.assertEqual(motivation["cleanup_status"], "verified")
        self.assertNotIn("result", motivation)
        saved = json.loads((jobs / (motivation_id + ".json")).read_text())
        self.assertFalse(saved["recovery_required"])
        self.assertEqual(saved["status"], "interrupted")

        self.assertEqual(pipeline["status"], "interrupted")
        self.assertTrue(pipeline["recovery_required"], "cleanup proof from application jobs must not unlock pipeline jobs")

    def _contact_cleanup_fixture(self, manager, ident="5183dd17fa96433c", *, saved_children=True):
        started = "2026-10-05T15:18:31+00:00"
        finished = "2026-10-05T15:23:01+00:00"
        lanes = [{"id": lane_id, "purpose": purpose,
                  "job_id": f"{ident}:{lane_id}", "status": "failed" if lane_id == "company_team" else "completed",
                  "started_at": "2026-10-05T15:18:34+00:00", "finished_at": finished}
                 for lane_id, purpose in {
                     "company_team": "Company and team pages",
                     "public_linkedin": "Public LinkedIn profiles",
                     "professional_directories": "Professional directories and press",
                     "wider_crosscheck": "Wider independent cross-check",
                 }.items()]
        children = []
        if saved_children:
            for index in range(4):
                child_id = f"{index + 1:016x}"
                checked = f"2026-10-05T15:2{index}:00+00:00"
                children.append({
                    "child_id": child_id, "safe": index != 1, "checked_at": checked,
                    "turn_quiescent": True,
                    "background": {"safe": True, "thread_id": f"native-thread-{index}",
                        "checked_at": checked, "fresh_final_readback": True,
                        "remaining": [], "errors": []},
                    "process": {"safe": index != 1, "pid": 60000 + index,
                        **({"verified_at": checked} if index != 1 else {})},
                })
        job = {"id": ident, "job_id": ident, "kind": "application_contacts",
            "role_id": "a" * 24, "status": "interrupted", "started_at": started,
            "finished_at": finished,
            "summary": "An isolated child could not be verified stopped; recovery is required.",
            "result": None, "recovery_required": True, "cleanup_status": "needs_review",
            "logs": [], "pending_requests": [],
            "contact_workflow": {"status": "failed", "started_at": started,
                "finished_at": finished, "lanes": lanes}}
        if saved_children:
            job["child_cleanup"] = children
        manager.jobs[ident] = copy.deepcopy(job)
        path = manager.job_dir / (ident + ".json")
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n")
        return job

    @staticmethod
    def _native_cleanup_readbacks(job, *, checked_at="2026-10-06T11:59:30+00:00"):
        lanes = job["contact_workflow"]["lanes"]
        readbacks = []
        for index, lane in enumerate(lanes):
            row = {"child_id": f"{index + 1:016x}",
                "thread_id": f"native-thread-{index}", "turn_id": f"native-turn-{index}",
                "status": "completed", "pending_tool_calls": False,
                "terminal_at": "2026-10-05T15:22:00+00:00", "checked_at": checked_at}
            readbacks.append(row)
        return readbacks

    def test_contact_cleanup_reconciliation_requires_fresh_idle_receipts_and_preserves_failure(self):
        manager = self.manager()
        original = self._contact_cleanup_fixture(manager)
        path = manager.job_dir / (original["id"] + ".json")
        original_children = copy.deepcopy(original["child_cleanup"])
        original_workflow = copy.deepcopy(original["contact_workflow"])
        fixed_now = "2026-10-06T12:00:00+00:00"
        with (mock.patch("runtime.now", return_value=fixed_now),
              mock.patch.object(manager, "_kernel_boot_time", return_value=dt.datetime.fromisoformat("2026-10-05T21:56:14+00:00")),
              mock.patch.object(manager, "_cleanup_pid_present", return_value=False) as pid_check):
            native = self._native_cleanup_readbacks(original)
            result = manager.reconcile_application_contact_cleanup(original["id"], native)
        saved = json.loads(path.read_text())
        self.assertTrue(result["saved"])
        self.assertEqual(result["receipt"]["basis"],
            "saved_child_receipts_plus_fresh_postboot_native_idle_and_pid_absence")
        self.assertEqual(pid_check.call_count, 4)
        self.assertEqual([row["pid"] for row in result["receipt"]["process_checks"]], [60000, 60001, 60002, 60003])
        self.assertTrue(all(row["present"] is False for row in result["receipt"]["process_checks"]))
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["summary"], original["summary"])
        self.assertIsNone(saved["result"])
        self.assertEqual(saved["contact_workflow"], original_workflow)
        self.assertEqual(saved["child_cleanup"], original_children)
        self.assertFalse(saved["recovery_required"])
        self.assertEqual(saved["cleanup_status"], "verified")
        self.assertEqual(saved["cleanup_reconciliation"], result["receipt"])

    def test_contact_cleanup_reconciliation_rejects_stale_or_unbound_native_receipts(self):
        manager = self.manager()
        original = self._contact_cleanup_fixture(manager)
        path = manager.job_dir / (original["id"] + ".json")
        original_bytes = path.read_bytes()
        native = self._native_cleanup_readbacks(original)
        bad_sets = []
        stale = copy.deepcopy(native)
        stale[0]["checked_at"] = "2026-10-06T11:00:00+00:00"
        bad_sets.append(stale)
        mismatched = copy.deepcopy(native)
        mismatched[0]["thread_id"] = "another-thread"
        bad_sets.append(mismatched)
        pending = copy.deepcopy(native)
        pending[0]["pending_tool_calls"] = True
        bad_sets.append(pending)
        for bad in bad_sets:
            with self.subTest(bad=bad[0]):
                with (mock.patch("runtime.now", return_value="2026-10-06T12:00:00+00:00"),
                      mock.patch.object(manager, "_kernel_boot_time", return_value=dt.datetime.fromisoformat("2026-10-05T21:56:14+00:00")),
                      mock.patch.object(manager, "_cleanup_pid_present", return_value=False)):
                    with self.assertRaises(JobConflict):
                        manager.reconcile_application_contact_cleanup(original["id"], bad)
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertTrue(manager.get(original["id"])["recovery_required"])

        # A reboot before the old process receipt is not proof of termination.
        with (mock.patch("runtime.now", return_value="2026-10-06T12:00:00+00:00"),
              mock.patch.object(manager, "_kernel_boot_time", return_value=dt.datetime.fromisoformat("2026-10-05T15:22:30+00:00")),
              mock.patch.object(manager, "_cleanup_pid_present", return_value=False)):
            with self.assertRaises(JobConflict):
                manager.reconcile_application_contact_cleanup(original["id"], native)
        self.assertEqual(path.read_bytes(), original_bytes)

        with (mock.patch("runtime.now", return_value="2026-10-06T12:00:00+00:00"),
              mock.patch.object(manager, "_kernel_boot_time", return_value=dt.datetime.fromisoformat("2026-10-05T21:56:14+00:00")),
              mock.patch.object(manager, "_cleanup_pid_present", return_value=True)):
            with self.assertRaises(JobConflict):
                manager.reconcile_application_contact_cleanup(original["id"], native)
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_contact_cleanup_reconciliation_with_missing_pid_receipt_keeps_pid_unknown(self):
        manager = self.manager()
        ident = "80f0b7823e7b453f"
        original = self._contact_cleanup_fixture(manager, ident, saved_children=False)
        original["status"] = "failed"
        original["contact_workflow"]["status"] = "stopped"
        source_url = "https://www.welcometothejungle.com/fr/companies/nexans/jobs/group-purchasing-decarbonization-project-leader_la_defense"
        company = "Nexans"
        title = "Group Purchasing Decarbonization Project Leader"
        role_id = original["role_id"]
        manager.data.role = lambda requested: {"id": role_id, "company": company, "title": title,
                                               "source_url": source_url}
        path = manager.job_dir / (ident + ".json")
        original["contact_workflow"]["lanes"] = [
            {"id": lane_id, "purpose": purpose, "job_id": f"{ident}:{lane_id}",
             "status": "stopped", "started_at": "2026-10-03T16:57:08+00:00",
             "finished_at": "2026-10-03T16:57:10+00:00"}
            for lane_id, purpose in {
                "company_team": "Company and team pages",
                "public_linkedin": "Public LinkedIn profiles",
                "professional_directories": "Professional directories and press",
                "wider_crosscheck": "Wider independent cross-check",
            }.items()]
        manager.jobs[ident] = copy.deepcopy(original)
        path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n")
        workspaces = manager.job_dir / "workspaces"
        workspaces.mkdir()
        fixed_birth = dt.datetime.fromisoformat("2026-10-03T16:57:08+00:00")
        readbacks = []
        for index, lane in enumerate(original["contact_workflow"]["lanes"]):
            child_id = f"{101 + index:016x}"
            (workspaces / child_id).mkdir()
            readbacks.append({"child_id": child_id, "thread_id": f"native-80-thread-{index}",
                "turn_id": f"native-80-turn-{index}", "status": "interrupted",
                "pending_tool_calls": False, "lane_id": lane["id"],
                "terminal_at": "2026-10-03T16:57:10+00:00",
                "checked_at": "2026-10-06T11:59:30+00:00",
                "prompt_identity": {"company": company, "title": title, "source_url": source_url,
                    "lane_id": lane["id"], "lane_purpose": lane["purpose"]}})
        before = path.read_bytes()
        with (mock.patch("runtime.now", return_value="2026-10-06T12:00:00+00:00"),
              mock.patch.object(manager, "_kernel_boot_time", return_value=dt.datetime.fromisoformat("2026-10-05T21:56:14+00:00")),
              mock.patch.object(manager, "_workspace_birth_time", return_value=fixed_birth),
              mock.patch.object(manager, "_owned_workspace_cwd_matches", return_value=[])):
            early_terminal = copy.deepcopy(readbacks)
            early_terminal[0]["terminal_at"] = "2026-10-03T16:57:07+00:00"
            with self.assertRaises(JobConflict):
                manager.reconcile_application_contact_cleanup(ident, early_terminal)
            self.assertEqual(path.read_bytes(), before)

            booted_at = dt.datetime.fromisoformat("2026-10-05T21:56:14+00:00")
            postboot_finished_job = copy.deepcopy(original)
            postboot_finished_job["contact_workflow"]["lanes"][0]["finished_at"] = "2026-10-05T21:56:15+00:00"
            manager.jobs[ident] = postboot_finished_job
            path.write_text(json.dumps(postboot_finished_job, ensure_ascii=False, indent=2) + "\n")
            postboot_finished_bytes = path.read_bytes()
            with self.assertRaises(JobConflict):
                manager.reconcile_application_contact_cleanup(ident, readbacks)
            self.assertEqual(path.read_bytes(), postboot_finished_bytes)

            borderline_finished_job = copy.deepcopy(original)
            borderline_finished_job["contact_workflow"]["lanes"][0]["finished_at"] = "2026-10-05T21:56:13+00:00"
            manager.jobs[ident] = borderline_finished_job
            path.write_text(json.dumps(borderline_finished_job, ensure_ascii=False, indent=2) + "\n")
            borderline_finished_bytes = path.read_bytes()
            first_workspace = workspaces / readbacks[0]["child_id"]
            with mock.patch.object(manager, "_workspace_birth_time",
                                   side_effect=lambda workspace: booted_at if workspace == first_workspace else fixed_birth):
                with self.assertRaises(JobConflict):
                    manager.reconcile_application_contact_cleanup(ident, readbacks)
            self.assertEqual(path.read_bytes(), borderline_finished_bytes)

            # Restore the exact valid historical fixture before the positive write.
            manager.jobs[ident] = copy.deepcopy(original)
            path.write_bytes(before)
            result = manager.reconcile_application_contact_cleanup(ident, readbacks)
        saved = json.loads(path.read_text())
        self.assertTrue(result["saved"])
        self.assertEqual(result["receipt"]["basis"], "postboot_native_idle_and_owned_workspace_cwd_absence")
        self.assertEqual(result["receipt"]["historical_process_state"], {
            "pid": None, "status": "unknown; the original job has no saved child PID cleanup receipts"})
        self.assertEqual(result["receipt"]["cwd_scan"]["matches"], [])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["contact_workflow"]["status"], "stopped")
        self.assertEqual(saved["contact_workflow"], original["contact_workflow"])
        self.assertNotIn("child_cleanup", saved)
        self.assertFalse(saved["recovery_required"])
        self.assertNotEqual(path.read_bytes(), before)

    def test_missing_pid_cleanup_rejects_wrong_prompt_identity_without_clearing_hold(self):
        manager = self.manager()
        ident = "80f0b7823e7b453f"
        original = self._contact_cleanup_fixture(manager, ident, saved_children=False)
        original["status"] = "failed"
        original["contact_workflow"]["status"] = "stopped"
        source_url = "https://www.welcometothejungle.com/fr/companies/nexans/jobs/group-purchasing-decarbonization-project-leader_la_defense"
        manager.data.role = lambda _requested: {"company": "Nexans", "title": "Group Purchasing Decarbonization Project Leader",
                                                "source_url": source_url}
        lanes = original["contact_workflow"]["lanes"]
        original["contact_workflow"]["lanes"] = [
            {"id": lane_id, "purpose": purpose, "job_id": f"{ident}:{lane_id}",
             "started_at": "2026-10-03T16:57:08+00:00", "finished_at": "2026-10-03T16:57:10+00:00"}
            for lane_id, purpose in {
                "company_team": "Company and team pages",
                "public_linkedin": "Public LinkedIn profiles",
                "professional_directories": "Professional directories and press",
                "wider_crosscheck": "Wider independent cross-check",
            }.items()]
        manager.jobs[ident] = copy.deepcopy(original)
        path = manager.job_dir / (ident + ".json")
        path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n")
        workspaces = manager.job_dir / "workspaces"
        workspaces.mkdir()
        readbacks = []
        for index, lane in enumerate(original["contact_workflow"]["lanes"]):
            child_id = f"{201 + index:016x}"
            (workspaces / child_id).mkdir()
            readbacks.append({"child_id": child_id, "thread_id": f"native-thread-{index}",
                "turn_id": f"native-turn-{index}", "status": "interrupted",
                "pending_tool_calls": False, "lane_id": lane["id"],
                "terminal_at": "2026-10-03T16:57:10+00:00", "checked_at": "2026-10-06T11:59:30+00:00",
                "prompt_identity": {"company": "Other company",
                    "title": "Group Purchasing Decarbonization Project Leader", "source_url": source_url,
                    "lane_id": lane["id"], "lane_purpose": lane["purpose"]}})
        original_bytes = path.read_bytes()
        with (mock.patch("runtime.now", return_value="2026-10-06T12:00:00+00:00"),
              mock.patch.object(manager, "_kernel_boot_time", return_value=dt.datetime.fromisoformat("2026-10-05T21:56:14+00:00")),
              mock.patch.object(manager, "_workspace_birth_time", return_value=dt.datetime.fromisoformat("2026-10-03T16:57:08+00:00")),
              mock.patch.object(manager, "_owned_workspace_cwd_matches", return_value=[])):
            with self.assertRaises(JobConflict):
                manager.reconcile_application_contact_cleanup(ident, readbacks)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertTrue(manager.get(ident)["recovery_required"])

    def test_late_terminal_invalidates_early_empty_listing(self):
        job = self._background_failure("late_background")
        self.assertTrue(job["turn_quiescent"])
        self.assertTrue(job["background_cleanup"]["fresh_final_readback"])
        self.assertTrue(job["background_cleanup"]["prior_checks"][0]["safe"])
        self.assertEqual(job["background_cleanup"]["remaining"], ["late-owned-terminal"])

    def test_no_terminal_turn_event_keeps_recovery_hold(self):
        manager = self.manager("no_terminal_event", remote_cleaner=lambda _job: {"safe": True})
        with mock.patch("runtime.STOP_WAIT_SECONDS", .1), mock.patch("runtime.FINAL_TURN_WAIT_SECONDS", .05):
            ident = manager.start("pipeline")["id"]
            self.wait(manager, ident, lambda job: bool(job.get("turn_id")))
            manager.stop(ident)
            job = self.wait(manager, ident)
        self.assertEqual(job["status"], "interrupted")
        self.assertFalse(job["turn_quiescent"])
        self.assertTrue(job["background_cleanup"]["safe"])
        self.assertFalse(job["background_cleanup"]["after_turn_quiescence"])
        self.assertTrue(job["owned_process_cleanup"]["safe"])
        self.assertTrue(job["recovery_required"])
        with self.assertRaises(JobConflict):
            manager.start("pipeline")

    def test_exhausted_metadata_prevents_generation(self):
        exhausted = copy.deepcopy(CAPABILITY)
        exhausted["usage"]["ordinaryUsageAllowed"] = False
        manager = self.manager(capability_reader=lambda *_: exhausted)
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertFalse(self.fake.with_suffix(".calls").exists())
        self.assertFalse(manager.capabilities()["can_run"])

    def test_api_key_route_cannot_start_generation(self):
        paid = copy.deepcopy(CAPABILITY)
        paid["account_type"] = "apiKey"
        manager = self.manager(capability_reader=lambda *_: paid)
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("ChatGPT", job["summary"])
        self.assertFalse(self.fake.with_suffix(".calls").exists())
        self.assertFalse(manager.capabilities()["can_run"])

    def choice_metadata(self):
        metadata = copy.deepcopy(CAPABILITY)
        metadata["models"] = [
            {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "listed": True, "efforts": ["high", "medium"]},
            {"id": "gpt-6-luna", "name": "GPT-6 Luna", "listed": True, "efforts": ["low", "max"]},
            {"id": "gpt-6-astra", "name": "GPT-6 Astra", "listed": True, "efforts": ["medium","high"]},
        ]
        return metadata

    def test_settings_are_offline_persisted_and_shown_in_capabilities(self):
        metadata = self.choice_metadata()
        probe = mock.Mock(side_effect=AssertionError("Settings must not query or generate"))
        manager = self.manager(capability_reader=probe)
        manager._remember_capabilities(metadata)
        with self.assertRaisesRegex(JobError, "coordinator must be GPT-6.1 Sol at medium"):
            manager.save_run_settings({"model": "gpt-6-luna", "effort": "max"})
        manager.save_run_settings({"model": "gpt-6.1-sol", "effort": "medium"})
        result = manager.run_settings()
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["defaults"], {"model": "gpt-6.1-sol", "effort": "medium"})
        self.assertEqual([row["id"] for row in result["options"]], ["gpt-6.1-sol"])
        self.assertEqual(result["options"][0]["efforts"], ["medium"])
        self.assertFalse(any(row["generation_verified"] for row in result["options"]))
        capability = manager.capabilities()
        self.assertEqual(capability["coordinator"]["model"], "gpt-6.1-sol")
        self.assertEqual(capability["coordinator"]["effort"], "medium")
        self.assertTrue(capability["can_run"])
        second = self.manager(capability_reader=probe)
        self.assertEqual(second.run_settings()["model"], "gpt-6.1-sol")
        self.assertEqual(second.run_settings()["effort"], "medium")
        self.assertEqual((manager.local_dir / "run-settings.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(manager.list()["total"], 0)
        self.assertFalse(self.calls())
        probe.assert_not_called()

    def test_invalid_settings_do_not_write_or_substitute(self):
        manager = self.manager()
        manager._remember_capabilities(self.choice_metadata())
        for payload in (
            {"model": "unlisted-model", "effort": "high"},
            {"model": "gpt-6-luna", "effort": "ultra"},
            {"model": "gpt-6-astra", "effort": "high"},
            {"model": "gpt-6.1-sol", "effort": "high", "provider": "fixture-paid-provider"},
            {"effort": "high"},
        ):
            with self.subTest(payload=payload), self.assertRaises(JobError):
                manager.save_run_settings(payload)
        self.assertFalse((manager.local_dir / "run-settings.json").exists())
        self.assertEqual(manager.run_settings()["model"], "gpt-6.1-sol")
        self.assertFalse(self.calls())

    def test_explicit_reset_repairs_corrupt_preferences_without_network(self):
        probe = mock.Mock(side_effect=AssertionError("Reset must work offline"))
        manager = self.manager(capability_reader=probe)
        (manager.local_dir / "run-settings.json").write_text("{broken")
        invalid = manager.run_settings()
        self.assertFalse(invalid["validation"]["valid"])
        self.assertIsNone(invalid["model"])
        with self.assertRaises(JobError):
            manager.start("pipeline")
        with self.assertRaises(JobError):
            manager.save_run_settings({"model": "gpt-6-luna", "effort": "max"}, reset=True)
        reset = manager.save_run_settings({}, reset=True)
        self.assertEqual((reset["model"], reset["effort"]), ("gpt-6.1-sol", "medium"))
        self.assertFalse(reset["validation"]["valid"])
        self.assertTrue(reset["persisted"])
        self.assertEqual(manager.list()["total"], 0)
        probe.assert_not_called()

    def test_settings_save_requires_cached_chatgpt_metadata(self):
        manager = self.manager()
        with self.assertRaises(JobError):
            manager.save_run_settings({"model": "gpt-6.1-sol", "effort": "medium"})
        paid = self.choice_metadata()
        paid["account_type"] = "apiKey"
        manager._remember_capabilities(paid)
        with self.assertRaisesRegex(JobError, "paid-provider"):
            manager.save_run_settings({"model": "gpt-6-luna", "effort": "max"})
        self.assertFalse((manager.local_dir / "run-settings.json").exists())
        self.assertFalse(self.calls())

    def test_admitted_job_and_receipt_keep_frozen_settings_after_edit(self):
        metadata, entered, release = self.choice_metadata(), threading.Event(), threading.Event()
        def fresh(*_):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Fixture dispatch metadata was not released")
            return copy.deepcopy(metadata)
        manager = self.manager(capability_reader=fresh)
        manager._remember_capabilities(metadata)
        admitted = manager.start("pipeline")
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(JobError, "coordinator must be GPT-6.1 Sol at medium"):
            manager.save_run_settings({"model": "gpt-6.1-sol", "effort": "high"})
        release.set()
        job = self.wait(manager, admitted["id"])
        self.assertEqual(job["status"], "agent_finished")
        self.assertEqual((job["model"], job["effort"]), ("gpt-6.1-sol", "medium"))
        self.assertEqual(job["run_settings"], admitted["run_settings"])
        receipt = json.loads((Path(job["evidence_path"]) / "studio-launch.json").read_text())
        self.assertEqual(receipt["run_settings"], admitted["run_settings"])
        self.assertEqual((receipt["model"], receipt["effort"]), ("gpt-6.1-sol", "medium"))
        self.assertEqual(receipt["final_approval"], {"model": "gpt-6.1-sol", "effort": "high"})
        self.assertEqual(manager.run_settings()["effort"], "medium")
        start = next(call for call in self.calls() if call.get("method") == "thread/start")
        turn = next(call for call in self.calls() if call.get("method") == "turn/start")
        self.assertEqual(start["params"]["model"], "gpt-6.1-sol")
        self.assertEqual(start["params"]["config"]["model_reasoning_effort"], "medium")
        self.assertEqual((turn["params"]["model"], turn["params"]["effort"]), ("gpt-6.1-sol", "medium"))
        prompt = turn["params"]["input"][0]["text"]
        self.assertIn("required policy-4 Studio launch coordinator is GPT-6.1 Sol at medium", prompt)
        self.assertIn("Fresh Sol 6.1 high approval is mandatory", prompt)
        self.assertIn("create, duplicate, change or enable a schedule", prompt)
        self.assertEqual(job["run_settings"], admitted["run_settings"])

    def test_fresh_metadata_rejects_stale_choice_without_fallback(self):
        cached = self.choice_metadata()
        fresh = copy.deepcopy(cached)
        fresh["models"] = [{"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "listed": True,
                            "efforts": ["high"]}]
        manager = self.manager(capability_reader=lambda *_: fresh)
        manager._remember_capabilities(cached)
        manager.save_run_settings({"model": "gpt-6.1-sol", "effort": "medium"})
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("fresh metadata check", job["summary"])
        self.assertEqual((job["model"], job["effort"]), ("gpt-6.1-sol", "medium"))
        self.assertFalse(self.calls())
        self.assertFalse(manager.capabilities()["can_run"])

    def test_incompatible_persisted_model_cannot_bypass_required_route(self):
        manager = self.manager()
        manager.run_settings_store.save({"model": "gpt-6-luna", "effort": "max"},
                                        saved_at="2026-10-03T12:00:00+00:00")
        self.assertFalse(manager.run_settings()["validation"]["valid"])
        with self.assertRaisesRegex(JobError, "coordinator must be GPT-6.1 Sol at medium"):
            manager.start("pipeline")
        self.assertFalse(self.calls())

    def _assert_substitution_rejected(self, mode, reason):
        manager = self.manager(mode)
        job = self.wait(manager, manager.start("pipeline")["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn(reason, job["summary"])
        self.assertFalse(job["pipeline_complete"])
        self.assertEqual(job["thread_id"], "owned-thread")
        methods = [call.get("method") for call in self.calls()]
        self.assertIn("thread/backgroundTerminals/list", methods)
        self.assertNotIn("turn/start", methods)
        self.assertEqual(methods.count("thread/start"), 1)

    def test_reported_model_substitution_is_rejected_before_turn(self):
        self._assert_substitution_rejected("substitute_model", "different model")

    def test_missing_reported_model_is_rejected_before_turn(self):
        self._assert_substitution_rejected("missing_model", "different model")

    def test_reported_effort_substitution_is_rejected_before_turn(self):
        self._assert_substitution_rejected("substitute_effort", "different reasoning effort")

    def test_reported_paid_provider_is_rejected_before_turn(self):
        self._assert_substitution_rejected("substitute_provider", "different provider")

    def test_usage_unknown_is_not_full(self):
        self.assertFalse(usage_status(None)["available"])
        self.assertFalse(usage_status({"rateLimits": None})["available"])
        self.assertTrue(usage_status({"rateLimits": {"primary": {"usedPercent": 100}}})["exhausted"])

    def test_permissions_cannot_add_extra_paths(self):
        message = {"method": "item/permissions/requestApproval", "params": {"permissions": {"network": {"enabled": True}}}}
        answer = JobManager._answer(message, {"decision": "accept", "permissions": {"fileSystem": {"write": ["/"]}}})
        self.assertEqual(answer, {"permissions": {"network": {"enabled": True}}, "scope": "turn"})

    def owned_job(self):
        manager = self.manager()
        ident = "1234567890abcdef"
        started = "2026-10-01T12:00:00+00:00"
        run_id = "2026-10-01-studio-" + ident
        batch = self.root / "Master/auto/evidence/runs" / run_id
        batch.mkdir(parents=True)
        (batch / "studio-launch.json").write_text(json.dumps({"studio_job_id": ident, "invocation_started_at": started}))
        job = {"id": ident, "started_at": started, "run_id": run_id, "evidence_path": str(batch)}
        return manager, job, batch

    def test_missing_or_malformed_owned_receipts_fail_closed(self):
        manager, job, batch = self.owned_job()
        (batch / "terminal-agent-receipt.json").write_text("{truncated")
        self.assertFalse(manager._cancel_remote(job)["safe"])
        (batch / "terminal-agent-receipt.json").unlink()
        (batch / "studio-launch.json").unlink()
        self.assertFalse(manager._cancel_remote(job)["safe"])

    def test_delayed_worker_binds_original_batch_start(self):
        manager, job, batch = self.owned_job()
        contract = {"kind": "weekday_worker_contract", "worker_id": "hunt:hellowork", "worker_deadline": "2026-10-01T12:32:00+00:00",
                    "invocation_started_at": "2026-10-01T12:02:00+00:00", "metadata": {"batch_started_at": job["started_at"]}}
        digest = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        contract.update(contract_id="fixture-contract", contract_sha256=digest)
        (batch / "contract.json").write_text(json.dumps(contract))
        (batch / "terminal-agent-receipt.json").write_text(json.dumps({
            "model_requested": "gemini-3.8-flash-high", "worker_id": contract["worker_id"],
            "contract_id": contract["contract_id"], "contract_sha256": digest, "workspace": str(batch),
            "cascade_id": "owned-remote-fixture", "cleanup_satisfied": True,
        }))
        self.assertTrue(manager._cancel_remote(job)["safe"])

    def test_restarted_unfinished_job_stays_blocked(self):
        manager = self.manager()
        job = {"id": "1234567890abcdef", "kind": "pipeline", "status": "running", "started_at": "2026-10-01T12:00:00+00:00"}
        (manager.job_dir / (job["id"] + ".json")).write_text(json.dumps(job))
        second = self.manager()
        self.assertTrue(second.get(job["id"])["recovery_required"])
        with self.assertRaises(JobConflict):
            second.start("check")

    def test_targeted_reconciliation_manager_can_skip_unrelated_saved_jobs(self):
        manager = self.manager()
        unrelated = {"id": "1234567890abcdef", "kind": "pipeline", "status": "running",
                     "started_at": "2026-10-01T12:00:00+00:00"}
        path = manager.job_dir / (unrelated["id"] + ".json")
        path.write_text(json.dumps(unrelated))
        before = path.read_bytes()
        targeted = self.manager(load_jobs=False)
        self.assertEqual(targeted.jobs, {})
        self.assertEqual(path.read_bytes(), before)

    def test_descendant_ignoring_term_is_killed_after_leader_exits(self):
        pidfile = self.root / "child.pid"
        child = self.root / "child.py"
        child.write_text("import signal,time,os\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath(" + repr(str(pidfile)) + ").write_text(str(os.getpid()))\ntime.sleep(30)\n")
        parent = subprocess.Popen([sys.executable, "-c", "import subprocess,sys,time;subprocess.Popen([sys.executable,sys.argv[1]]);time.sleep(30)", str(child)], start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists())
            self.assertTrue(terminate_owned_group(parent, grace=.2))
            parent.wait(timeout=2)
            status = subprocess.run(["ps", "-o", "stat=", "-p", pidfile.read_text()], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not status or status.startswith("Z"))
        finally:
            terminate_owned_group(parent, grace=.1)


if __name__ == "__main__":
    unittest.main()

class HybridLaunchTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    tearDown = RuntimeTests.tearDown
    manager = RuntimeTests.manager
    wait = RuntimeTests.wait
    calls = RuntimeTests.calls
    """Same isolated fixture, with focus on fresh startup and lifecycle ownership."""

    def test_hunt_submission_precedes_native_metadata_and_coordinator(self):
        events = []
        def hunt(job, batch):
            self.assertTrue((batch / 'stage-metrics.json').is_file())
            self.assertFalse(self.calls(), 'No native probe or model turn before useful Hunt launch')
            events.append('hunt')
            return {'status':'HUNT_SUBMITTED_UNAPPROVED','first_submission_at':job['started_at'],
                    'fixture_only':True}
        def metadata(*_):
            events.append('native_metadata')
            return copy.deepcopy(CAPABILITY)
        manager = self.manager(pipeline_launcher=hunt, capability_reader=metadata)
        result = self.wait(manager,manager.start('pipeline')['id'])
        self.assertEqual(result['status'],'agent_finished')
        self.assertEqual(events[:2],['hunt','native_metadata'])

    def test_fast_launch_does_not_call_historical_active_scan(self):
        self.data.active_pipeline = lambda: (_ for _ in ()).throw(AssertionError('Historical scan forbidden'))
        manager = self.manager(pipeline_owner_reader=lambda: {})
        result = self.wait(manager,manager.start('pipeline')['id'])
        self.assertEqual(result['status'],'agent_finished')

    def test_shared_owner_conflict_does_not_start_coordinator(self):
        manager = self.manager(pipeline_launcher=lambda *_: {'status':'CONFLICT','dispatch':False})
        result = self.wait(manager,manager.start('pipeline')['id'])
        self.assertEqual(result['status'],'failed')
        self.assertFalse(self.calls())
        self.assertIn('live pipeline owner',result['summary'])

    def test_text_final_without_verified_finish_retains_owned_hunt(self):
        manager = self.manager()
        batch = self.root/'Master/auto/evidence/runs/fresh-retention'
        batch.mkdir(parents=True)
        (batch/'launch-receipt.json').write_text('{}')
        job={'status':'agent_finished','evidence_path':str(batch)}
        control={'stop':threading.Event()}
        self.assertTrue(manager._retain_public_hunt(job,control))
        with mock.patch.dict(sys.modules,{'pipeline_launch':mock.Mock()}) as modules:
            manager._release_hunt_launcher(job,control)
            modules['pipeline_launch'].stop_launch.assert_not_called()
        control['stop'].set()
        self.assertFalse(manager._retain_public_hunt(job,control))

    def test_verified_finish_signal_does_not_relabel_it_as_abort(self):
        manager = self.manager()
        batch=self.root/'Master/auto/evidence/runs/fresh-finished'
        batch.mkdir(parents=True)
        (batch/'launch-receipt.json').write_text('{}')
        (batch/'finish-request.json').write_text('{"abort":false}')
        job={'status':'agent_finished','evidence_path':str(batch)}
        control={'stop':threading.Event()}
        self.assertFalse(manager._retain_public_hunt(job,control))
        with mock.patch.dict(sys.modules,{'pipeline_launch':mock.Mock()}) as modules:
            manager._release_hunt_launcher(job,control)
            modules['pipeline_launch'].stop_launch.assert_not_called()

    def test_quota_snapshot_does_not_cancel_active_workers(self):
        manager=self.manager()
        job=manager.start('pipeline')
        self.wait(manager,job['id'])
        control={'stop':threading.Event()}
        with mock.patch.object(manager,'stop') as stop:
            manager._event(job,control,{'method':'account/rateLimits/updated',
                'params':{'ordinaryUsageAllowed':False}})
            stop.assert_not_called()
        self.assertFalse(control['stop'].is_set())
        self.assertNotIn('provider_failure',control)
        self.assertTrue(job['usage_observation']['exhausted'])

    def test_native_quota_admission_hold_preserves_public_hunt(self):
        exhausted=copy.deepcopy(CAPABILITY)
        exhausted['usage']['ordinaryUsageAllowed']=False
        def launcher(job,batch):
            (batch/'launch-receipt.json').write_text('{}')
            return {'status':'HUNT_SUBMITTED_UNAPPROVED'}
        manager=self.manager(capability_reader=lambda *_:exhausted,pipeline_launcher=launcher)
        with mock.patch.dict(sys.modules,{'pipeline_launch':mock.Mock()}) as modules:
            job=self.wait(manager,manager.start('pipeline')['id'])
            modules['pipeline_launch'].stop_launch.assert_not_called()
        self.assertEqual(job['native_admission'],'HELD')
        self.assertFalse(self.calls())

    def test_early_text_final_keeps_native_workers_until_real_idle(self):
        def launcher(job,batch):
            (batch/'launch-receipt.json').write_text('{}')
            return {'status':'HUNT_SUBMITTED_UNAPPROVED'}
        manager=self.manager('background_drain',pipeline_launcher=launcher)
        ident=manager.start('pipeline')['id']
        running=self.wait(manager,ident,lambda job:bool(job.get('background_retention')),timeout=5)
        self.assertEqual(running['status'],'running')
        self.assertFalse(running['finished_at'])
        final=self.wait(manager,ident,timeout=8)
        calls=self.calls()
        self.assertGreaterEqual(sum(c.get('method')=='thread/backgroundTerminals/list' for c in calls),3)
        self.assertFalse(any(c.get('method')=='thread/backgroundTerminals/terminate' for c in calls))
        self.assertTrue(final['background_cleanup']['retained_until_quiescent'])
        self.assertTrue(final['owned_process_cleanup']['safe'])

    def test_unknown_background_observation_keeps_native_process_alive(self):
        def launcher(job,batch):
            (batch/'launch-receipt.json').write_text('{}')
            return {'status':'HUNT_SUBMITTED_UNAPPROVED'}
        manager=self.manager('background_glitch',pipeline_launcher=launcher)
        ident=manager.start('pipeline')['id']
        unknown=self.wait(manager,ident,lambda job:job.get('background_retention',{}).get('owned_ids','none') is None,timeout=5)
        self.assertEqual(unknown['status'],'running')
        self.assertFalse(unknown['finished_at'])
        final=self.wait(manager,ident,timeout=8)
        self.assertFalse(any(c.get('method')=='thread/backgroundTerminals/terminate' for c in self.calls()))
        self.assertTrue(final['background_cleanup']['retained_until_quiescent'])
        self.assertTrue(final['background_cleanup']['observation_failures'])
        self.assertTrue(final['owned_process_cleanup']['safe'])
