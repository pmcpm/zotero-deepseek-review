import copy
import importlib.util
import json
import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts/workflow.py"
sys.path.insert(0,str(MODULE.parent))
spec = importlib.util.spec_from_file_location("workflow", MODULE)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
sys.path.insert(0,str(MODULE.parent))
import prepare_note as pn


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task = {
            "title": "测试技术的研究进展", "audience": "中文学术论文读者", "word_limit": 200,
            "reference_target": 1, "language_ratio": {"zh": 1, "en": 1, "strict": False},
            "citation_style": "apa", "hide_doi": True, "bilingual": False,
            "date_range": {"start": "2015-01-01", "end": "2026-10-25"}, "source_scope": "all_journals",
            "supplement_dir": str(self.root / "supplements"), "output_docx": str(self.root / "output.docx"),
            "zotero": {"library_id": 1, "collection_key": "C001", "collection_name": "研究测试"},
            "outline": [{"title": "1 研究进展", "budget": 200, "reference_ids": ["R001"], "argument": "比较证据A支持的方法路径"}],
        }
        w.init_task(self.root, self.task)
        self.task = w.load(self.root / "task.json")
        self.cards = [{
            "reference_id": "R001", "metadata": {"itemType": "journalArticle", "title": "方法A研究", "creators": [{"creatorType": "author", "name": "某科研团队"}], "date": "2020", "publicationTitle": "测试期刊", "pages": "101575", "DOI": "10.1234/test.1", "language": "zh-CN"},
            "sources": [{"url": "https://example.org/paper", "type": "publisher"}],
            "verification": {"metadata_checked": True, "fulltext_checked": True, "evidence_location": "p.2, Methods"},
            "research_object": "数据A", "method": "方法A", "findings": "给出证据A", "supported_claims": ["已有研究采用方法A"], "zotero_key": "ITEM0001",
        }]
        w.save(self.root / "evidence.json", self.cards)
        self.readback = [{"key": "ITEM0001", "data": {**self.cards[0]["metadata"], "collections": ["C001"]}}]
        self.post_audit()

    def tearDown(self):
        self.tmp.cleanup()

    def post_audit(self):
        result = w.audit_metadata(self.cards, self.readback, "C001")
        self.assertTrue(result["passed"], result)
        w.save(self.root / "metadata-post-import.json", result)

    def mocks(self, responses):
        path = self.root / "mock.json"
        w.save(path, responses)
        return path

    def approve_files(self, body):
        review = self.root / "review.json"
        count = self.root / "count.json"
        w.save(review, {"body_sha256": w.digest(body), "passed": True, "issues": []})
        w.save(count, {"body_sha256": w.digest(body), "count": 20, "basis": "word", "exact": True})
        return review, count

    def test_missing_questions_preserve_existing_answers(self):
        result = w.check_task(self.task)
        self.assertTrue(result["ready"])
        self.task["word_limit"] = None
        result = w.check_task(self.task)
        self.assertEqual(result["missing"], ["word_limit"])
        self.assertNotIn("hide_doi", result["missing"])

    def test_stable_ids_and_doi_dedup(self):
        cards = [{"reference_id": "R009"}, {}, {"reference_id": "R002"}]
        self.assertEqual([c["reference_id"] for c in w.assign_ids(cards)], ["R009", "R010", "R002"])
        dupe = copy.deepcopy(self.cards[0]);dupe["reference_id"] = "R002"
        dupe["metadata"]["title"] = "An alternate English title"
        dupe["metadata"]["DOI"] = "https://doi.org/10.1234/TEST.1"
        audit = w.audit_metadata([self.cards[0], dupe])
        self.assertFalse(audit["passed"])
        self.assertTrue(any(d["basis"] == "DOI" for d in audit["duplicates"]))

    def test_readback_checks_author_order_collection_and_attachment_parent(self):
        cards = copy.deepcopy(self.cards)
        cards[0]["attachment_keys"] = ["ATT00001"]
        rows = copy.deepcopy(self.readback)
        rows[0]["data"]["creators"] = [{"creatorType": "author", "name": "错误作者"}]
        rows[0]["data"]["collections"] = []
        rows.append({"key": "ATT00001", "data": {"parentItem": "WRONGKEY", "itemType": "attachment"}})
        audit = w.audit_metadata(cards, rows, "C001")
        fields = {i.get("field") for i in audit["issues"]}
        self.assertTrue({"creators", "collections", "attachment_keys"}.issubset(fields))
        self.assertEqual(w.audit_metadata(self.cards, self.readback)["issues"], [])

    def test_supplement_resume_requires_actual_fulltext_review(self):
        base = Path(self.task["supplement_dir"]);base.mkdir()
        w.save(self.root / "supplements.json", [{"reference_id": "R001", "expected_filename": "R001.pdf", "status": "waiting_file", "title": "方法A研究", "urls": ["https://example.org/paper"]}])
        self.assertEqual(w.supplement_status(self.root)["pending"][0]["status"], "waiting_file")
        (base / "R001.pdf").write_bytes(b"%PDF-1.7\nfixture")
        pending = w.supplement_status(self.root)
        self.assertEqual(pending["pending"][0]["status"], "received_requires_fulltext_review")
        entries = w.load(self.root / "supplements.json");entries[0]["status"] = "fulltext_verified"
        w.save(self.root / "supplements.json", entries)
        self.assertTrue(w.supplement_status(self.root)["ready"])
        (base / "R001.pdf").write_bytes(b"%PDF-1.7\nchanged")
        self.assertEqual(w.supplement_status(self.root)["pending"][0]["status"], "changed_requires_review")

    def test_model_correction_preserves_bodies_and_mock_cannot_deliver(self):
        bad = "1 研究进展\n不代表研究结论[[R999]]。"
        good = "1 研究进展\r\n已有研究采用方法A[[R001]]。"
        mock = self.mocks([bad, good])
        state = w.write_draft(self.root, mock)
        self.assertEqual(state["attempt"], 1)
        self.assertEqual((self.root / "writing/attempt-00/body.txt").read_bytes(), bad.encode())
        self.assertEqual((self.root / "writing/attempt-01/body.txt").read_bytes(), good.encode())
        messages = w.load(self.root / "writing/attempt-01/messages.json")
        self.assertEqual(messages[-2]["content"], bad)
        self.assertIn("unknown_reference", messages[-1]["content"])
        review, count = self.approve_files(good)
        selected = w.review_draft(self.root, review, count, mock)
        self.assertEqual(selected["body_sha256"], w.digest(good))
        self.assertFalse(selected["output_document_allowed"])
        self.assertEqual(w.load_selected(self.root, allow_simulation=True)["body_sha256"], w.digest(good))
        with self.assertRaisesRegex(ValueError, "Simulation"):
            w.load_selected(self.root)

    def test_revision_budget_stops_at_three_originals(self):
        state = w.write_draft(self.root, self.mocks(["不代表[[R999]]。"] * 3))
        self.assertEqual(state["phase"], "needs_user_decision")
        self.assertEqual(len(list((self.root / "writing").glob("attempt-*"))), 3)
        self.assertFalse((self.root / "selected.json").exists())

    def test_semantic_failure_returns_to_model_and_hash_is_bound(self):
        first = "1 研究进展\n已有研究采用方法A[[R001]]。"
        final = "1 研究进展\n已有研究围绕数据A采用方法A[[R001]]。"
        mock = self.mocks([first, final])
        w.write_draft(self.root, mock)
        review = self.root / "review.json"
        w.save(review, {"body_sha256": "wrong", "passed": True, "issues": []})
        with self.assertRaisesRegex(ValueError, "current body hash"):
            w.review_draft(self.root, review, mock=mock)
        w.save(review, {"body_sha256": w.digest(first), "passed": False, "issues": [{"reference_id": "R001", "location": "p.2", "problem": "补充研究对象数据A"}]})
        state = w.review_draft(self.root, review, mock=mock)
        self.assertEqual(state["attempt"], 1)
        self.assertEqual((self.root / "writing/attempt-00/body.txt").read_text(encoding="utf8"), first)

    def test_missing_or_stale_postimport_audit_blocks_writing(self):
        self.cards[0]["metadata"]["pages"] = "22-30"
        w.save(self.root / "evidence.json", self.cards)
        with self.assertRaisesRegex(ValueError, "stale"):
            w.write_draft(self.root, self.mocks(["正文[[R001]]。 "]))
        self.assertFalse((self.root / "writing").exists())

    def test_selection_rejects_changed_task_or_raw_body(self):
        body = "1 研究进展\n已有研究采用方法A[[R001]]。"
        mock = self.mocks([body]);w.write_draft(self.root, mock)
        review, count = self.approve_files(body)
        w.review_draft(self.root, review, count, mock)
        task = w.load(self.root / "task.json");task["context"] = "Changed scope";w.save(self.root / "task.json", task)
        with self.assertRaisesRegex(ValueError, "stale"):
            w.load_selected(self.root, allow_simulation=True)
        w.save(self.root / "task.json", self.task)
        (self.root / "writing/attempt-00/body.txt").write_text("Rewritten by caller", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "body changed"):
            w.load_selected(self.root, allow_simulation=True)

    def test_review_rejects_changed_evidence_even_after_new_metadata_audit(self):
        body = "1 研究进展\n已有研究采用方法A[[R001]]。"
        mock = self.mocks([body]);w.write_draft(self.root, mock)
        review, count = self.approve_files(body)
        self.cards[0]["metadata"]["pages"] = "22-30"
        self.readback[0]["data"]["pages"] = "22-30"
        w.save(self.root / "evidence.json", self.cards);self.post_audit()
        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            w.review_draft(self.root, review, count, mock)
        self.assertFalse((self.root / "selected.json").exists())

    def test_selected_gate_rechecks_supplement_files(self):
        body = "1 研究进展\n已有研究采用方法A[[R001]]。"
        base = Path(self.task["supplement_dir"]);base.mkdir()
        pdf = base / "R001.pdf";pdf.write_bytes(b"%PDF-1.7\nfixture")
        w.save(self.root / "supplements.json", [{"reference_id": "R001", "expected_filename": "R001.pdf", "status": "fulltext_verified", "sha256": w.hashlib.sha256(pdf.read_bytes()).hexdigest()}])
        mock = self.mocks([body]);w.write_draft(self.root, mock)
        review, count = self.approve_files(body);w.review_draft(self.root, review, count, mock)
        pdf.write_bytes(b"%PDF-1.7\nchanged")
        with self.assertRaisesRegex(ValueError, "changed full text"):
            w.load_selected(self.root, allow_simulation=True)

    def test_unterminated_or_stray_citation_delimiters_are_rejected(self):
        for body in ("采用方法A[[R001]]，另见[[R999", "采用方法A[[R001]]。]]", "采用方法A[[R001]]，[[[R001]]"):
            audit = w.audit_body(body, self.task, self.cards)
            self.assertFalse(audit["mechanical_passed"])
            self.assertTrue(any(x["type"].startswith("malformed_reference") for x in audit["issues"]))

    def test_semantic_boolean_and_positive_integer_count_types_are_strict(self):
        body = "1 研究进展\n已有研究采用方法A[[R001]]。"
        mock = self.mocks([body]);w.write_draft(self.root, mock)
        review, count = self.approve_files(body)
        semantic = w.load(review);semantic["passed"] = "false";w.save(review, semantic)
        with self.assertRaisesRegex(ValueError, "passed flag"):
            w.review_draft(self.root, review, count, mock)
        semantic["passed"] = True;w.save(review, semantic)
        for invalid in (0, True, -1, "20"):
            w.save(count, {"body_sha256": w.digest(body), "basis": "word", "exact": True, "count": invalid})
            with self.assertRaisesRegex(ValueError, "positive integer"):
                w.review_draft(self.root, review, count, mock)

    def test_exact_word_count_and_semantic_approval_are_both_required(self):
        body = "1 研究进展\n已有研究采用方法A[[R001]]。"
        mock = self.mocks([body]);w.write_draft(self.root, mock)
        review, count = self.approve_files(body)
        with self.assertRaisesRegex(ValueError, "Exact Word count"):
            w.review_draft(self.root, review, mock=mock)
        count_data = w.load(count);count_data["body_sha256"] = "stale";w.save(count, count_data)
        with self.assertRaisesRegex(ValueError, "immutable body hash"):
            w.review_draft(self.root, review, count, mock)
        self.assertFalse((self.root / "selected.json").exists())

    def test_prepare_note_preserves_prose_groups_and_durable_task_scoped_request(self):
        body = "1 研究进展\nA & B采用方法A[[R001]]。相关证据来自数据A[[R001]]。"
        mock = self.mocks([body]);w.write_draft(self.root,mock)
        review,count=self.approve_files(body);w.review_draft(self.root,review,count,mock)
        calls=[]
        class FakeBridge:
            def __init__(this,profile):pass
            def call(this,request):
                calls.append(request)
                if request['op']=='getItem':
                    return {'state':'completed','result':{'data':self.readback[0]['data'],'uri':'http://zotero.org/users/1/items/ITEM0001','csl':{'id':'ITEM0001','title':'方法A研究'}}}
                self.assertEqual(w.load(self.root/'word/create-note.request.json'),request)
                return {'state':'completed','result':{'ok':True,'key':'NOTE0001'}}
        with patch.object(pn,'Bridge',FakeBridge),patch.object(pn,'load_selected',side_effect=lambda root:w.load_selected(root,allow_simulation=True)):
            plan=pn.prepare(self.root)
        note=calls[-1]['html']
        self.assertIn('A &amp; B',note)
        self.assertIn('<h2>1 研究进展</h2>',note)
        self.assertEqual(note.count('class="citation"'),2)
        self.assertEqual(plan['citation_clusters'],2)
        self.assertEqual(plan['body_sha256'],w.digest(body))
        self.assertEqual((self.root/'writing/attempt-00/body.txt').read_bytes(),body.encode())
        expected='note_'+w.digest({'task_id':self.task['task_id'],'html':note})
        self.assertEqual(calls[-1]['requestID'],expected)

    def test_prepare_note_rejects_live_collection_change_and_uncertain_write(self):
        body='1 研究进展\n采用方法A[[R001]]。'
        mock=self.mocks([body]);w.write_draft(self.root,mock)
        review,count=self.approve_files(body);w.review_draft(self.root,review,count,mock)
        for changed in (True,False):
            calls=[]
            class FakeBridge:
                def __init__(this,profile):pass
                def call(this,request):
                    calls.append(request)
                    if request['op']=='getItem':
                        data=copy.deepcopy(self.readback[0]['data'])
                        if changed:data['collections']=[]
                        return {'state':'completed','result':{'data':data,'uri':'http://zotero.org/users/1/items/ITEM0001','csl':{'id':'ITEM0001'}}}
                    return {'state':'started','result':{'ok':True,'key':'NOTE0001'}}
            with patch.object(pn,'Bridge',FakeBridge),patch.object(pn,'load_selected',side_effect=lambda root:w.load_selected(root,allow_simulation=True)):
                with self.assertRaisesRegex(ValueError,'collection membership' if changed else 'outcome uncertain'):
                    pn.prepare(self.root)
            if changed:self.assertEqual(len(calls),1)
            self.assertFalse((self.root/'word/note-plan.json').exists())


if __name__ == "__main__":
    unittest.main()
