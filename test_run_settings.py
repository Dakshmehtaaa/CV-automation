"""Fixture-only local preference and catalog tests; no generation or connectors."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_settings import (DEFAULTS, RunSettingsError, RunSettingsStore,
                          model_options, selection, validate, validation)


METADATA = {
    "available": True, "authenticated": True, "account_type": "chatgpt",
    "models": [
        {"id": "gpt-6.1-sol", "name": "Sol", "listed": True, "efforts": ["low", "medium", "high"]},
        {"id": "gpt-6-luna", "name": "Luna", "listed": True, "efforts": ["max"]},
        {"id": "gpt-6-astra", "name": "Astra", "listed": True, "efforts": ["high"]},
    ],
}


class RunSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cv-studio-settings-test-")
        self.path = Path(self.temp.name) / "run-settings.json"
        self.store = RunSettingsStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_read_uses_explicit_defaults_and_does_not_persist(self):
        result = self.store.read()
        self.assertEqual({key: result[key] for key in DEFAULTS}, DEFAULTS)
        self.assertFalse(result["persisted"])
        self.assertFalse(self.path.exists())

    def test_selection_rejects_astra_unknown_fields_and_invalid_types(self):
        for payload in (
            {"model": "gpt-6-astra", "effort": "high"},
            {"model": "gpt-6.1-sol", "effort": "high", "modelProvider": "paid"},
            {"model": "gpt-6.1-sol", "effort": None},
            {"model": ["gpt-6.1-sol"], "effort": "high"},
            {"model": "", "effort": "high"},
            {"model": "gpt-6.1-sol"},
            None,
        ):
            with self.subTest(payload=payload), self.assertRaises(RunSettingsError):
                selection(payload)

    def test_catalog_filters_for_native_listed_choices_and_preserves_efforts(self):
        metadata = copy.deepcopy(METADATA)
        metadata["models"].extend([
            {"id": "hidden", "listed": False, "efforts": ["high"]},
            {"id": "paid", "listed": True, "provider": "custom-provider", "efforts": ["high"]},
            {"id": "paid-other", "listed": True, "modelProvider": "custom-provider", "efforts": ["high"]},
            {"id": "no-effort", "listed": True, "efforts": []},
            {"id": "bad-effort", "listed": True, "efforts": "high"},
            {"id": "gpt-6.1-sol", "listed": True, "efforts": ["max"]},
            None,
        ])
        options = model_options(metadata)
        self.assertEqual([row["id"] for row in options], ["gpt-6.1-sol", "gpt-6-luna"])
        self.assertEqual(options[0]["efforts"], ["low", "medium", "high"])
        self.assertEqual(options[1]["efforts"], ["max"])
        self.assertTrue(all(row["listed"] and not row["generation_verified"] for row in options))
        self.assertEqual(model_options({"models": None}), [])
        self.assertEqual(model_options(None), [])

    def test_validate_uses_only_the_selected_models_own_efforts(self):
        self.assertEqual(validate({"model": "gpt-6-luna", "effort": "max"}, METADATA),
                         {"model": "gpt-6-luna", "effort": "max"})
        for chosen in ({"model": "gpt-6-luna", "effort": "high"},
                       {"model": "gpt-6.1-sol", "effort": "max"},
                       {"model": "missing", "effort": "high"}):
            with self.subTest(chosen=chosen), self.assertRaises(RunSettingsError):
                validate(chosen, METADATA)
            self.assertFalse(validation(chosen, METADATA)["valid"])

    def test_paid_unavailable_and_unauthenticated_metadata_do_not_validate(self):
        for key, value in (("account_type", "apiKey"), ("account_type", "custom-provider"),
                           ("available", False), ("authenticated", False)):
            metadata = copy.deepcopy(METADATA)
            metadata[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(RunSettingsError):
                validate(DEFAULTS, metadata)
        self.assertFalse(validation(DEFAULTS, None)["valid"])

    def test_store_keeps_exact_selection_and_private_permissions(self):
        chosen = {"model": "gpt-6-luna", "effort": "max"}
        saved = self.store.save(chosen, saved_at="2026-10-02T00:00:00+00:00", metadata_checked_at="fixture-time")
        self.assertEqual(self.store.read(), saved)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.path.read_text())["model"], chosen["model"])
        chosen["model"] = "gpt-6.1-sol"
        self.assertEqual(self.store.read()["model"], "gpt-6-luna")
        self.assertFalse(list(self.path.parent.glob(".run-settings-*.tmp")))

    def test_corrupt_or_rejected_saved_choices_never_use_defaults(self):
        for value in ("{broken", "[]", json.dumps({"schema_version": 2}),
                      json.dumps({"schema_version": 1, "model": "gpt-6-astra", "effort": "high"}),
                      json.dumps({"schema_version": 1, "model": "gpt-6.1-sol", "effort": None})):
            self.path.write_text(value)
            with self.subTest(value=value), self.assertRaises(RunSettingsError):
                self.store.read()
            self.assertEqual(self.path.read_text(), value)

    def test_symlink_settings_cannot_read_or_overwrite_an_outside_file(self):
        outside = self.path.parent / "outside.json"
        outside.write_text("fixture outside file")
        self.path.symlink_to(outside)
        with self.assertRaises(RunSettingsError):
            self.store.read()
        with self.assertRaises(RunSettingsError):
            self.store.save(DEFAULTS, saved_at="fixture-time")
        self.assertEqual(outside.read_text(), "fixture outside file")
        self.assertTrue(self.path.is_symlink())

    def test_fifo_preferences_fail_without_waiting_for_a_writer(self):
        os.mkfifo(self.path)
        with self.assertRaises(RunSettingsError):
            self.store.read()


if __name__ == "__main__":
    unittest.main()
