import unittest

from med_agent.context_retriever import build_context_bundles, is_translatable_text


class ContextRetrieverTests(unittest.TestCase):
    def test_skips_symbols_and_numbers(self):
        self.assertFalse(is_translatable_text("***"))
        self.assertFalse(is_translatable_text("2 500 ****"))
        self.assertFalse(is_translatable_text("20 nm"))
        self.assertTrue(is_translatable_text("Sepsis is a life-threatening condition."))

    def test_bundle_excludes_non_translatable_blocks(self):
        structured = {
            "source": "memory.pdf",
            "sections": [{"id": "sec-1", "heading": "Results"}],
            "pages": [
                {
                    "page": 1,
                    "blocks": [
                        {
                            "id": "blk-1",
                            "page": 1,
                            "type": "text",
                            "role": "paragraph",
                            "section_id": "sec-1",
                            "text": "***",
                        },
                        {
                            "id": "blk-2",
                            "page": 1,
                            "type": "text",
                            "role": "paragraph",
                            "section_id": "sec-1",
                            "text": "Sepsis was associated with mortality.",
                        },
                    ],
                }
            ],
        }
        term_memory = {"terms": []}

        result = build_context_bundles(structured, term_memory)

        self.assertEqual(result["bundle_count"], 1)
        self.assertEqual(result["bundles"][0]["block_id"], "blk-2")


if __name__ == "__main__":
    unittest.main()
