import subprocess
import unittest

import contact_handoff


ROLE = {"company": "Example Green", "title": "CSR Analyst", "location": "Paris (75)",
        "source_url": "javascript:alert(1)", "application": {"apply_url": "https://example.test/advert"}}


class ContactHandoffTest(unittest.TestCase):
    def test_saved_prompt_is_filled_without_its_editing_note(self):
        prompt = contact_handoff.fill_prompt(ROLE)
        self.assertNotIn("<!--", prompt)
        self.assertNotIn("{{", prompt)
        for value in ("Example Green", "CSR Analyst", "Paris (75)", "https://example.test/advert"):
            self.assertIn(value, prompt)
        self.assertIn("Do not send messages", prompt)

    def test_missing_or_unsafe_links_fall_back_to_a_people_search(self):
        role = {**ROLE, "application": {"apply_url": "file:///etc/passwd"}}
        self.assertIn("no advert link saved", contact_handoff.fill_prompt(role))
        self.assertEqual(contact_handoff.start_url(role),
                         "https://www.linkedin.com/search/results/people/?keywords=Example+Green+recruiter")

    def test_mac_handoff_copies_the_prompt_and_opens_chrome(self):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs.get("input")))
            return subprocess.CompletedProcess(args, 0)

        result = contact_handoff.hand_off(ROLE, run=run, platform="darwin")
        self.assertTrue(result["copied"] and result["opened"])
        self.assertEqual(result["model"], "Haiku 5.5")
        self.assertEqual(calls[0], (["pbcopy"], result["prompt"].encode("utf-8")))
        self.assertEqual(calls[1][0], ["open", "-a", "Google Chrome", "https://example.test/advert"])

    def test_other_platforms_return_the_prompt_without_side_effects(self):
        result = contact_handoff.hand_off(ROLE, run=lambda *a, **k: self.fail("no subprocess"), platform="linux")
        self.assertFalse(result["copied"] or result["opened"])
        self.assertIn("Example Green", result["prompt"])


if __name__ == "__main__":
    unittest.main()
