import unittest

from med_agent.document_structurer import structure_document


class DocumentStructurerTests(unittest.TestCase):
    def test_recognizes_headings_paragraphs_table_and_references(self):
        extracted = {
            "source": "memory.pdf",
            "metadata": {},
            "pages": [
                {
                    "page": 1,
                    "width": 595,
                    "height": 842,
                    "blocks": [
                        {
                            "type": "text",
                            "bbox": [72, 50, 500, 80],
                            "text": "A Medical Study",
                            "style": {"max_font_size": 16, "is_bold": True},
                        },
                        {
                            "type": "text",
                            "bbox": [72, 90, 500, 110],
                            "text": "Jane Doe, MD; John Smith, PhD",
                            "style": {"max_font_size": 9, "is_bold": False},
                        },
                        {
                            "type": "text",
                            "bbox": [72, 130, 500, 150],
                            "text": "Abstract",
                            "style": {"max_font_size": 12, "is_bold": True},
                        },
                        {
                            "type": "text",
                            "bbox": [72, 155, 500, 220],
                            "text": "This is the abstract.",
                            "style": {"max_font_size": 9, "is_bold": False},
                        },
                        {
                            "type": "text",
                            "bbox": [72, 240, 500, 260],
                            "text": "1. Introduction",
                            "style": {"max_font_size": 12, "is_bold": True},
                        },
                    ],
                }
            ],
        }

        result = structure_document(extracted)
        roles = [block["role"] for block in result["pages"][0]["blocks"]]

        self.assertEqual(roles, ["title", "authors", "heading", "paragraph", "heading"])
        self.assertEqual([section["heading"] for section in result["sections"]], ["Abstract", "1. Introduction"])
        self.assertEqual(result["metadata"]["inferred"]["title"], "A Medical Study")


if __name__ == "__main__":
    unittest.main()
