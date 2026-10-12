import unittest

from med_agent.quality_checker import build_quality_report, check_translation


class QualityCheckerTests(unittest.TestCase):
    def test_detects_missing_number_and_locked_term(self):
        item = {
            "block_id": "blk-1",
            "page": 1,
            "role": "paragraph",
            "source_text": "Sepsis mortality was 12% in 120 patients.",
            "translation": "共有患者发生死亡。",
        }
        bundle = {
            "matched_terms": [
                {"source": "sepsis", "target": "脓毒症", "locked": True},
                {"source": "mortality", "target": "死亡率", "locked": True},
            ]
        }

        result = check_translation(item, bundle)
        codes = {error["code"] for error in result["errors"]}

        self.assertFalse(result["passed"])
        self.assertIn("missing_numbers", codes)
        self.assertIn("missing_locked_term", codes)

    def test_accepts_number_unit_term_and_negation(self):
        item = {
            "block_id": "blk-2",
            "page": 1,
            "role": "paragraph",
            "source_text": "There was no sepsis in 20 patients after 28 days.",
            "translation": "28天后，20例患者中未发现脓毒症。",
        }
        bundle = {
            "matched_terms": [
                {"source": "sepsis", "target": "脓毒症", "locked": True},
            ]
        }

        result = check_translation(item, bundle)
        self.assertTrue(result["passed"], result["errors"])

    def test_accepts_lack_of_translated_as_que_fa(self):
        # "lack of targeting" -> "缺乏靶向性" is correct. The English negation
        # list once omitted "lack", so this was flagged, retranslated in vain,
        # and then blocked the whole document at the approval gate.
        item = {
            "block_id": "blk-3",
            "page": 1,
            "role": "paragraph",
            "source_text": (
                "While exogenous DNase can degrade NETs, its clinical utility is "
                "limited by short half-life and lack of targeting."
            ),
            "translation": (
                "尽管外源性DNase可以降解NETs，但其临床应用受到半衰期短和缺乏靶向性的限制。"
            ),
        }
        result = check_translation(item, {})
        self.assertTrue(result["passed"], result["errors"])

    def test_accepts_unaffected_translated_as_wei_shou_ying_xiang(self):
        item = {
            "block_id": "blk-4",
            "page": 2,
            "role": "paragraph",
            "source_text": (
                "In contrast, TLR9 expression in the negative control virus group "
                "remained unaffected."
            ),
            "translation": "相比之下，阴性对照病毒（NC）组的TLR9表达未受影响。",
        }
        result = check_translation(item, {})
        self.assertTrue(result["passed"], result["errors"])

    def test_still_reports_negation_added_by_translation(self):
        # The check must keep catching a negation the source never had.
        item = {
            "block_id": "blk-5",
            "page": 1,
            "role": "paragraph",
            "source_text": "Treatment significantly improved the survival rate in all patients.",
            "translation": "治疗未显著改善所有患者的生存率。",
        }
        result = check_translation(item, {})
        codes = {error["code"] for error in result["errors"]}
        self.assertIn("negation_mismatch", codes)

    def test_does_not_treat_unique_or_under_as_negation(self):
        item = {
            "block_id": "blk-6",
            "page": 1,
            "role": "paragraph",
            "source_text": "The unique structure lies under the curve of the same group.",
            "translation": "这种独特结构位于同一组的曲线下方。",
        }
        result = check_translation(item, {})
        codes = {error["code"] for error in result["errors"]}
        self.assertNotIn("negation_mismatch", codes)


    def test_report_ignores_blocks_outside_the_context(self):
        # A DOI line filtered out of the context can still linger in an older
        # translations.json. Checking it failed the report, which blocked a
        # document whose translated blocks were all fine.
        translations = {
            "items": [
                {
                    "block_id": "blk-good",
                    "page": 1,
                    "role": "paragraph",
                    "source_text": "Sepsis mortality was 12% in 120 patients.",
                    "translation": "120例患者的脓毒症死亡率为12%。",
                },
                {
                    "block_id": "blk-doi",
                    "page": 1,
                    "role": "paragraph",
                    "source_text": "https://doi.org/10.1038/s41368-026-00454-3",
                    "translation": "https://doi.org/10.1038/s41368-026-00454-3",
                },
            ]
        }
        context = {
            "source_document": "input.pdf",
            "bundles": [
                {
                    "block_id": "blk-good",
                    "page": 1,
                    "role": "paragraph",
                    "text": "Sepsis mortality was 12% in 120 patients.",
                    "matched_terms": [],
                    "section_terms": [],
                }
            ],
        }
        report = build_quality_report(translations, context)
        self.assertEqual(report["translated_count"], 1)
        self.assertEqual(report["failed_count"], 0)
        self.assertTrue(all(check["passed"] for check in report["checks"]))


if __name__ == "__main__":
    unittest.main()
