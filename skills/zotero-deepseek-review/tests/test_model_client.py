import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from model_client import ModelClient
import workflow as w


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, method):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))) if method == "POST" else None
        self.server.requests.append((method, self.path, self.headers.get("Authorization"), body))
        status, result = self.server.reply(method, self.path, body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status in (301, 302, 307, 308):
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/unexpected-destination")
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf8"))

    def do_GET(self):
        self.respond("GET")

    def do_POST(self):
        self.respond("POST")


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests = []
        self.server.reply = lambda method, path, body: (200, {"data": [{"id": "custom-model"}]}) if method == "GET" else (200, {"choices": [{"message": {"content": "模型原文[[R001]]"}, "finish_reason": "stop"}], "usage": {"total_tokens": 42}})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.task = {"model": "custom-model", "word_limit": 500, "model_api": {
            "provider": "openai_compatible", "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
            "api_key_env": "REVIEW_TEST_KEY", "transport_retries": 0}}
        self.env = patch.dict(os.environ, {"REVIEW_TEST_KEY": "test-credential-never-persist", "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1"})
        self.env.start()
        (self.root / "messages.json").write_text(json.dumps([{"role": "user", "content": "写作测试"}], ensure_ascii=False), encoding="utf8")

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_generic_models_chat_parameters_and_secret_exclusion(self):
        client = ModelClient(self.task)
        self.assertEqual(client.models()["data"][0]["id"], "custom-model")
        raw = client.chat(self.root, 0)
        self.assertEqual(raw["choices"][0]["message"]["content"], "模型原文[[R001]]")
        method, endpoint, auth, body = self.server.requests[-1]
        self.assertEqual((method, endpoint), ("POST", "/v1/chat/completions"))
        self.assertEqual(auth, "Bearer test-credential-never-persist")
        self.assertFalse(body["stream"])
        self.assertEqual(body["model"], "custom-model")
        self.assertEqual(body["max_tokens"], 2500)
        self.assertNotIn("thinking", body)
        self.assertNotIn("test-credential-never-persist", (self.root / "provider-request.json").read_text(encoding="utf8") + json.dumps(client.request_metadata()))

    def test_parameter_variants(self):
        self.task["model_api"].update(temperature=None, max_tokens_field="max_completion_tokens", max_tokens=800, extra_body={"reasoning_effort": "low"})
        ModelClient(self.task).chat(self.root, 0)
        body = self.server.requests[-1][3]
        self.assertNotIn("temperature", body)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(body["max_completion_tokens"], 800)
        self.assertEqual(body["reasoning_effort"], "low")

    def test_no_auth_local_and_explicit_discovery_skip(self):
        self.task["model_api"].update(api_key_env=None, discover_models=False)
        client = ModelClient(self.task)
        self.assertTrue(client.models()["discovery_skipped"])
        self.assertEqual(len(self.server.requests), 0)
        client.chat(self.root, 0)
        self.assertIsNone(self.server.requests[-1][2])

    def test_missing_model_is_not_substituted(self):
        self.task["model"] = "absent-model"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            ModelClient(self.task).models()
        self.assertEqual(len(self.server.requests), 1)

    def test_auth_failure_does_not_log_echoed_key(self):
        self.server.reply = lambda *_: (401, {"error": "test-credential-never-persist"})
        with self.assertRaisesRegex(RuntimeError, "HTTP 401") as error:
            ModelClient(self.task).chat(self.root, 0)
        self.assertNotIn("test-credential", str(error.exception))
        self.assertEqual(len(self.server.requests), 1)

    def test_credential_echo_in_success_is_discarded(self):
        self.server.reply = lambda *_: (200, {"choices": [{"message": {"content": "test-credential-never-persist"}}]})
        with self.assertRaisesRegex(RuntimeError, "echoed credential"):
            ModelClient(self.task).chat(self.root, 0)
        self.assertFalse((self.root / "raw.json").exists())

    def test_retry_bound_and_schema_error(self):
        self.task["model_api"]["transport_retries"] = 1
        self.server.reply = lambda *_: (503, {"error": "busy"})
        with patch("model_client.time.sleep"), self.assertRaisesRegex(RuntimeError, "503"):
            ModelClient(self.task).models()
        self.assertEqual(len(self.server.requests), 2)
        self.server.requests.clear()
        self.server.reply = lambda *_: (400, {"error": "bad schema"})
        with self.assertRaisesRegex(RuntimeError, "400"):
            ModelClient(self.task).chat(self.root, 0)
        self.assertEqual(len(self.server.requests), 1)

    def test_redirect_cannot_forward_credential(self):
        # No redirect follows: the configured URL is the sole credential destination.
        self.server.reply = lambda *_: (302, {})
        with self.assertRaisesRegex(RuntimeError, "302"):
            ModelClient(self.task).models()
        self.assertEqual(len(self.server.requests), 1)

    def test_non_chat_response_rejected(self):
        self.server.reply = lambda *_: (200, {"result": "different protocol"})
        with self.assertRaisesRegex(ValueError, "choices"):
            ModelClient(self.task).chat(self.root, 0)

    def test_missing_credential_fails_before_network(self):
        with patch.dict(os.environ, {"REVIEW_TEST_KEY": ""}), self.assertRaisesRegex(ValueError, "Missing API credential"):
            ModelClient(self.task)
        self.assertEqual(self.server.requests, [])

    def test_provider_switch_uses_full_workflow_and_preserves_corrections(self):
        import test_workflow as fixtures
        fixture = fixtures.WorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        task = {**fixture.task, **self.task}
        w.save(fixture.root / "task.json", task)
        bad = "超出给定来源的结论[[R999]]。"
        good = "1 研究进展\n已有研究采用方法A[[R001]]。"
        responses = iter([bad, good])
        self.server.reply = lambda method, path, body: (200, {"data": [{"id": "custom-model"}]}) if method == "GET" else (200, {"choices": [{"message": {"content": next(responses)}, "finish_reason": "stop"}], "usage": {"total_tokens": 42}})
        state = w.write_draft(fixture.root)
        self.assertEqual(state["attempt"], 1)
        self.assertEqual(state["phase"], "awaiting_exact_count_and_semantic_review")
        first, second = [fixture.root / f"writing/attempt-0{i}" for i in (0, 1)]
        self.assertEqual((first / "body.txt").read_bytes(), bad.encode("utf8"))
        self.assertEqual((second / "body.txt").read_bytes(), good.encode("utf8"))
        request = w.load(second / "request.json")
        self.assertEqual(request["provider"], "openai_compatible")
        self.assertEqual(request["model"], "custom-model")
        self.assertNotIn("thinking", request)
        self.assertEqual(w.load(second / "raw.json")["usage"]["total_tokens"], 42)
        messages = w.load(second / "provider-request.json")["messages"]
        self.assertEqual(messages[-2]["content"], bad)
        self.assertIn("unknown_reference", messages[-1]["content"])
        self.assertFalse((fixture.root / "selected.json").exists())

    def test_validation_rejects_unsafe_config_before_task_save(self):
        variants = [
            {"api_key": "inline-key"}, {"base_url": "http://external.example/v1"},
            {"base_url": "https://user:pass@example.org/v1"}, {"api_key_env": None, "base_url": "https://example.org/v1"},
            {"extra_body": {"messages": []}}, {"extra_body": {"nested": {"api_key": "inline"}}},
            {"transport_retries": 4}, {"temperature": float("nan")},
        ]
        for variant in variants:
            task = {**self.task, "model_api": {**self.task["model_api"], **variant}}
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                w.init_task(self.root / "bad", task)
            self.assertFalse((self.root / "bad/task.json").exists())

    def test_defaults_and_legacy_cstcloud_adapter(self):
        w.init_task(self.root / "defaults")
        task = w.load(self.root / "defaults/task.json")
        self.assertEqual(task["model_api"], {"provider": "cstcloud"})
        self.assertEqual(task["model"], "deepseek-v4-flash")
        legacy = ModelClient({"model": "deepseek-v4-flash", "word_limit": 2000})
        self.assertEqual(legacy.provider, "cstcloud")
        result = type("Result", (), {"returncode": 0, "stdout": b'{"data":[{"id":"deepseek-v4-flash"}]}', "stderr": b""})()
        with patch.object(legacy, "command", return_value=result) as call:
            self.assertEqual(legacy.models()["data"][0]["id"], "deepseek-v4-flash")
            self.assertEqual(call.call_args.args[0], ["models", "--timeout", "120", "--retries", "1"])


if __name__ == "__main__":
    unittest.main()
