import unittest
from unittest.mock import patch

import ratings

FRENCH = "Vous rejoignez notre équipe et vous travaillez avec les responsables de la société pour les projets de notre groupe. Nous recherchons une personne pour la mission dans les équipes."
ENGLISH = "You will join our team and work with the leaders of our company. We are looking for you to lead the projects with your colleagues in our team."


class RatingsTests(unittest.TestCase):
    def setUp(self):
        with ratings._english_cache_lock:
            ratings._english_cache.clear()

    def test_english_cache_reuses_exact_inputs_and_rechecks_changes(self):
        with patch.object(ratings, "advert_body", wraps=ratings.advert_body) as parse:
            first = ratings.english_rating(FRENCH, "FR")
            self.assertEqual(ratings.english_rating(FRENCH, "FR"), first)
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(ratings.english_rating(FRENCH + "\nAnglais courant requis.", "FR")["score"], 4)
            self.assertEqual(ratings.english_rating(FRENCH, "EN")["score"], 5)
            # Even a same-rating whitespace or locale spelling change gets its
            # own exact-input calculation; inputs are never normalized as keys.
            self.assertEqual(ratings.english_rating(FRENCH + "\n", "FR")["score"], 1)
            self.assertEqual(ratings.english_rating(FRENCH, "fr")["score"], 1)
            self.assertEqual(parse.call_count, 5)

    def test_english_cache_isolated_from_caller_mutations(self):
        with patch.object(ratings, "advert_body", wraps=ratings.advert_body) as parse:
            first = ratings.english_rating(FRENCH, "FR")
            expected = dict(first)
            first.update(score=5, reason="Caller changed the answer", extra=["caller field"])
            second = ratings.english_rating(FRENCH, "FR")
            self.assertEqual(second, expected)
            second.clear()
            self.assertEqual(ratings.english_rating(FRENCH, "FR"), expected)
            self.assertEqual(parse.call_count, 1)

    def test_english_cache_evicts_oldest_unused_input_at_its_limit(self):
        with patch.object(ratings, "_ENGLISH_CACHE_LIMIT", 2), \
                patch.object(ratings, "advert_body", wraps=ratings.advert_body) as parse:
            ratings.english_rating(FRENCH, "FR")
            ratings.english_rating(ENGLISH, "EN")
            ratings.english_rating(FRENCH, "FR")  # Keep the recently used input.
            ratings.english_rating(FRENCH + "\nAnglais courant requis.", "FR")
            self.assertEqual(parse.call_count, 3)
            ratings.english_rating(FRENCH, "FR")
            self.assertEqual(parse.call_count, 3)
            ratings.english_rating(ENGLISH, "EN")  # The unused input was evicted.
            self.assertEqual(parse.call_count, 4)
            self.assertEqual(len(ratings._english_cache), 2)

    def test_french_without_english_is_one(self):
        self.assertEqual(ratings.english_rating(FRENCH, "FR")["score"], 1)

    def test_english_advert_is_five(self):
        self.assertEqual(ratings.english_rating(ENGLISH)["score"], 5)

    def test_strong_english_requirement_is_four(self):
        self.assertEqual(ratings.english_rating(FRENCH+"\nAnglais courant requis.", "French")["score"], 4)

    def test_english_mention_is_three(self):
        self.assertEqual(ratings.english_rating(FRENCH+"\nDes échanges en anglais.", "FR")["score"], 3)

    def test_optional_english_is_two(self):
        self.assertEqual(ratings.english_rating(FRENCH+"\nUn anglais courant serait un atout.", "FR")["score"], 2)

    def test_explicit_working_language_is_five(self):
        self.assertEqual(ratings.english_rating(FRENCH+"\nL'anglais est notre langue de travail.", "FR")["score"], 5)

    def test_negated_english_is_not_required(self):
        self.assertEqual(ratings.english_rating(FRENCH+"\nL'anglais n'est pas nécessaire.", "FR")["score"], 1)

    def test_candidate_languages_do_not_change_advert(self):
        jd = "# Job\n## Full advert\n"+FRENCH+"\n## Fit and gap note\nEnglish fluent C2; French B2 from PROFILE.md."
        self.assertEqual(ratings.english_rating(jd,"FR")["score"], 1)

    def test_fit_notes_before_advert_are_skipped(self):
        jd = "## Fit notes and gaps\nEnglish fluency C2.\n## Full original advert\n"+FRENCH
        self.assertEqual(ratings.english_rating(jd,"FR")["score"], 1)

    def test_original_french_locale_beats_translated_body(self):
        self.assertEqual(ratings.english_rating(ENGLISH,"FR")["score"], 1)

    def test_missing_language_evidence_is_unknown(self):
        self.assertIsNone(ratings.english_rating("Short note")["score"])

    def test_existing_fit_is_preserved(self):
        result=ratings.role_ratings({"company":"Unknown fixture", "fit":3},FRENCH,"FR")
        self.assertEqual(result["profile_fit"]["score"],3)
        self.assertEqual(result["jd_english"]["score"],1)
        self.assertIsNone(result["company_english"]["score"])

    def test_user_benchmarks_are_exact_and_separate(self):
        for company, score in [("L'Oréal",5),("Back Market",5),("Dataiku",5),("Thales",3),("Pollen Robotics",2)]:
            result=ratings.company_rating(company)
            self.assertEqual(result["score"],score,company)
            self.assertEqual(result["basis"],"user_benchmark",company)

    def test_anonymous_client_does_not_inherit_recruiter_score(self):
        self.assertIsNone(ratings.company_rating("LHH Recruitment Solutions, for an aerospace-industry client")["score"])


if __name__ == "__main__":
    unittest.main()
