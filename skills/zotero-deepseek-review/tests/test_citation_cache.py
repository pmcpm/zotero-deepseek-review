import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from citation_cache import invalidate_batch, invalidate_render_cache, main


class CitationCacheTests(unittest.TestCase):
    def citation(self, **properties):
        return {"citationID": "中文-citation-1", "citationItems": [
            {"id": 123, "uris": ["http://zotero.org/users/1/items/ITEM0001"],
             "itemData": {"issued": {"date-parts": [[2026, 10, 26]]},
                          "title": "技术与创新", "customDate": "2026-10-26T03:00:00Z"}}],
                "properties": {"formattedCitation": "{\\super [1]}", "plainCitation": "[1]",
                               "noteIndex": 0, "custom": {"date": "2026-01-02", "value": 0}, **properties},
                "schema": "https://example.org/citation-schema", "customRoot": [True, None, "保留"]}

    def field(self, citation=None):
        return " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(
            self.citation() if citation is None else citation, ensure_ascii=False, indent=2) + " \r\n"

    def payload(self, code):
        start = code.index("{")
        return json.JSONDecoder().raw_decode(code, start)[0]

    def test_only_render_cache_changes_and_all_other_semantics_survive(self):
        citation = self.citation()
        code = self.field(citation)
        output = invalidate_render_cache(code)
        expected = json.loads(json.dumps(citation))
        del expected["properties"]["formattedCitation"]
        self.assertEqual(self.payload(output), expected)
        self.assertEqual(citation["properties"]["formattedCitation"], "{\\super [1]}")
        self.assertEqual(invalidate_render_cache(output), output)

    def test_unicode_dates_and_flexible_prefix_whitespace_are_preserved(self):
        prefix = "\t ADDIN\t ZOTERO_ITEM\r\n  CSL_CITATION   "
        trailing = " \t\r\n"
        citation = self.citation()
        output = invalidate_render_cache(prefix + json.dumps(citation, ensure_ascii=False) + trailing)
        self.assertTrue(output.startswith(prefix))
        self.assertTrue(output.endswith(trailing))
        self.assertIn("技术与创新", output)
        result = self.payload(output)
        self.assertEqual(result["citationItems"][0]["itemData"]["customDate"], "2026-10-26T03:00:00Z")
        self.assertEqual(result["properties"]["custom"]["date"], "2026-01-02")
        self.assertIsInstance(result["properties"]["custom"]["date"], str)

    def test_uncached_field_is_returned_exactly_and_plain_citation_is_retained(self):
        citation = self.citation()
        del citation["properties"]["formattedCitation"]
        code = self.field(citation)
        self.assertEqual(invalidate_render_cache(code), code)
        self.assertEqual(invalidate_render_cache(' ADDIN ZOTERO_ITEM CSL_CITATION {  } '),
                         ' ADDIN ZOTERO_ITEM CSL_CITATION {  } ')
        self.assertEqual(self.payload(code)["properties"]["plainCitation"], "[1]")

    def test_removing_first_middle_last_or_only_cache_member_keeps_valid_json(self):
        objects = ['{ "formattedCitation" : "cache", "plainCitation":"[1]" }',
                   '{ "a":true, "formattedCitation" : "cache", "plainCitation":"[1]" }',
                   '{ "plainCitation":"[1]", "formattedCitation" : "cache" }',
                   '{ "formattedCitation" : "cache" }']
        for obj in objects:
            with self.subTest(properties=obj):
                code = ' ADDIN ZOTERO_ITEM CSL_CITATION {"properties":' + obj + ',"items":[]} \n'
                expected = self.payload(code)
                del expected['properties']['formattedCitation']
                self.assertEqual(self.payload(invalidate_render_cache(code)), expected)

    def test_other_json_lexemes_and_high_precision_numbers_are_not_rewritten(self):
        code = (' ADDIN ZOTERO_ITEM CSL_CITATION { "customNumber":1.2345678901234567890123456789,'
                '"veryLargeExponent":1e999, "customString":"\\u4e2d\\u6587",'
                '"properties":{"formattedCitation":"cache", "plainCitation":"[1]"} } \n')
        output = invalidate_render_cache(code)
        self.assertEqual(output, code.replace('"formattedCitation":"cache",', ''))

    def test_dont_update_refuses_cache_override_even_if_cache_is_absent(self):
        for has_cache in (False, True):
            citation = self.citation(dontUpdate=True)
            if not has_cache:
                del citation["properties"]["formattedCitation"]
            with self.subTest(has_cache=has_cache), self.assertRaisesRegex(ValueError, "dontUpdate"):
                invalidate_render_cache(self.field(citation))

    def test_malformed_or_non_citation_fields_are_rejected(self):
        prefix = " ADDIN ZOTERO_ITEM CSL_CITATION "
        variants = [None, 1, "", " ADDIN ZOTERO_BIBL {}", " ZOTERO_ITEM CSL_CITATION {}",
                    " ADDIN ZOTERO_ITEM OTHER {}", " ADDIN ZOTERO_ITEM CSL_CITATION_EXTRA {}",
                    prefix + "{", prefix + "[]", prefix + '{} trailing', prefix + '{}{}',
                    prefix + '{"properties":null}', prefix + '{"properties":[]}',
                    prefix + '{"properties":{},"properties":{}}', prefix + '{"value":NaN}']
        for code in variants:
            with self.subTest(code=code), self.assertRaises(ValueError):
                invalidate_render_cache(code)

    def test_batch_validates_indices_and_hashes_exact_original_codes(self):
        cached = self.field()
        uncached = invalidate_render_cache(cached)
        output = invalidate_batch([{"index": 7, "code": cached}, {"index": 3, "code": uncached}])
        self.assertEqual([row["index"] for row in output], [7, 3])
        self.assertEqual([row["changed"] for row in output], [True, False])
        self.assertEqual(output[0]["before_sha256"], hashlib.sha256(cached.encode("utf8")).hexdigest())
        self.assertEqual(output[1]["before_sha256"], hashlib.sha256(uncached.encode("utf8")).hexdigest())
        for invalid in (True, False, 0, -1, 1.5, "1", None):
            with self.subTest(index=invalid), self.assertRaises(ValueError):
                invalidate_batch([{"index": invalid, "code": cached}])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            invalidate_batch([{"index": 1, "code": cached}, {"index": 1, "code": uncached}])
        for invalid in ({}, "array", [None], [{"index": 1, "code": None}]):
            with self.subTest(batch=invalid), self.assertRaises(ValueError):
                invalidate_batch(invalid)

    def test_cli_validates_whole_batch_before_creating_or_replacing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root / "input.json";output = root / "output.json"
            source.write_text(json.dumps([{"index": 1, "code": self.field()},
                                          {"index": 2, "code": " ADDIN ZOTERO_BIBL {}"}]), encoding="utf8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 2)
            self.assertFalse(output.exists())
            output.write_bytes(b"previous-output")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 2)
            self.assertEqual(output.read_bytes(), b"previous-output")
            self.assertEqual(list(root.glob(".citation-cache-*.tmp")), [])

    def test_cli_roundtrip_and_utf8_hashes_match_the_pure_function(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root / "input.json";output = root / "output.json"
            rows = [{"index": 1, "code": self.field()}]
            source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8-sig")
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf8")), invalidate_batch(rows))
            self.assertEqual(json.loads(stdout.getvalue()), {"fields": 1, "changed": 1})
            self.assertEqual(list(root.glob(".citation-cache-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
