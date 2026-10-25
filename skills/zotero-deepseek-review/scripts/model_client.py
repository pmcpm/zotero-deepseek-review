"""Writing-model transports. CSTCloud is the backward-compatible default.

Generic adapters use the OpenAI Chat Completions protocol, not provider SDKs.
Credentials are resolved from the process environment and never serialized.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class NoRedirect(HTTPRedirectHandler):
    # Never forward a provider credential to a redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, task: dict, mock: Path | None = None, validate_only: bool = False):
        self.task = task
        self.mock = read_json(mock) if mock else None
        self.config = task.get("model_api", {"provider": "cstcloud"})
        if not isinstance(self.config, dict):
            raise ValueError("model_api must be an object")
        self.provider = self.config.get("provider", "cstcloud")
        self.validate_only = validate_only
        if self.provider not in ("cstcloud", "openai_compatible"):
            raise ValueError("Supported providers: cstcloud, openai_compatible")
        if not isinstance(task.get("model"), str) or not task["model"].strip():
            raise ValueError("model must be a nonempty model ID")
        self.helper = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills/cstcloud-model-api/scripts/cstcloud_api.py"
        self.secret = ""
        if self.provider == "openai_compatible":
            self._configure_generic()
        elif set(self.config) - {"provider"}:
            raise ValueError("CSTCloud adapter options are managed by cstcloud-model-api; use openai_compatible for custom parameters")

    def _configure_generic(self):
        allowed = {"provider", "base_url", "api_key_env", "timeout_seconds", "transport_retries", "trust_env",
                   "discover_models", "temperature", "max_tokens", "max_tokens_field", "extra_body"}
        if set(self.config) - allowed:
            raise ValueError("Unknown model_api option; keys and authorization headers belong in environment variables")
        self.base_url = str(self.config.get("base_url", "")).rstrip("/")
        parsed = urlsplit(self.base_url)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or (parsed.scheme == "http" and not local)):
            raise ValueError("base_url requires HTTPS, or HTTP on loopback; no embedded credentials/query/fragment")
        self.key_env = self.config.get("api_key_env", "REVIEW_MODEL_API_KEY")
        if self.key_env is None:
            if not local:
                raise ValueError("Unauthenticated providers must use loopback")
        elif not isinstance(self.key_env, str) or not self.key_env.isidentifier():
            raise ValueError("api_key_env must name an environment variable, or be null for loopback")
        elif self.mock is None and not self.validate_only:
            self.secret = os.environ.get(self.key_env, "")
            if not self.secret:
                raise ValueError("Missing API credential environment variable: " + self.key_env)
            if "\n" in self.secret or "\r" in self.secret:
                raise ValueError("API credential contains a newline")
        self.timeout = self.config.get("timeout_seconds", 240)
        self.retries = self.config.get("transport_retries", 1)
        if type(self.timeout) not in (int, float) or not 1 <= self.timeout <= 1800:
            raise ValueError("timeout_seconds must be between 1 and 1800")
        if type(self.retries) is not int or not 0 <= self.retries <= 3:
            raise ValueError("transport_retries must be an integer from 0 to 3")
        for option in ("trust_env", "discover_models"):
            if type(self.config.get(option, False if option == "trust_env" else True)) is not bool:
                raise ValueError(option + " must be boolean")
        self.token_field = self.config.get("max_tokens_field", "max_tokens")
        if self.token_field not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("Unsupported max_tokens_field")
        self.token_limit = self.config.get("max_tokens", min(16000, max(2048, (self.task.get("word_limit") or 1000) * 5)))
        if type(self.token_limit) is not int or self.token_limit < 1:
            raise ValueError("max_tokens must be a positive integer")
        self.temperature = self.config.get("temperature", 0.3)
        if self.temperature is not None and (type(self.temperature) not in (int, float) or not 0 <= self.temperature <= 2):
            raise ValueError("temperature must be null or a number from 0 to 2")
        self.extra = self.config.get("extra_body", {})
        reserved = {"model", "messages", "stream", "temperature", "max_tokens", "max_completion_tokens"}
        if not isinstance(self.extra, dict) or reserved.intersection(self.extra):
            raise ValueError("extra_body cannot override model, messages, stream, temperature or token limits")
        def check_credentials(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if any(word in key.lower().replace("-", "_") for word in ("api_key", "authorization", "password", "secret", "access_token")):
                        raise ValueError("Credentials cannot appear in extra_body")
                    check_credentials(item)
            elif isinstance(value, list):
                for item in value:
                    check_credentials(item)
        check_credentials(self.extra)
        json.dumps(self.extra, allow_nan=False)
        self.opener = build_opener(ProxyHandler() if self.config.get("trust_env", False) else ProxyHandler({}), NoRedirect())

    def command(self, args: list[str]) -> subprocess.CompletedProcess:
        if not self.helper.is_file():
            raise ValueError("Required cstcloud-model-api skill is missing; install it or configure openai_compatible")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([sys.executable, str(self.helper), *args], capture_output=True, env=env)

    def _request(self, suffix: str, payload=None):
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["Authorization"] = "Bearer " + self.secret
        if payload is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf8") if payload is not None else None
        req = Request(self.base_url + suffix, data=data, headers=headers)
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8-sig")
                # Fail closed if a misconfigured service echoes its credential.
                if self.secret and self.secret in raw:
                    raise RuntimeError("Provider response echoed credential; response discarded")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("Provider response must be a JSON object")
                return value
            except HTTPError as error:
                status = error.code
                error.close()
                if status in (408, 425, 429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                # Do not persist response bodies/headers which may echo credentials.
                raise RuntimeError(f"Model API HTTP {status}; check endpoint, credentials and model configuration") from None
            except URLError:
                raise RuntimeError("Model API connection failed; request result may be uncertain, inspect provider before retrying") from None
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("Model API returned invalid JSON; check OpenAI compatibility") from None

    def request_metadata(self):
        if self.provider == "cstcloud":
            return {"provider": "cstcloud", "model": self.task["model"], "temperature": 0.3, "thinking": True,
                    "max_output_length": min(16000, max(2048, (self.task.get("word_limit") or 1000) * 5)),
                    "timeout_seconds": 240, "transport_retries": 1, "trust_env": False}
        return {"provider": self.provider, "model": self.task["model"], "base_url": self.base_url,
                "api_key_env": self.key_env, "temperature": self.temperature,
                self.token_field: self.token_limit, "timeout_seconds": self.timeout,
                "transport_retries": self.retries, "trust_env": self.config.get("trust_env", False),
                "discover_models": self.config.get("discover_models", True), "extra_body": self.extra, "stream": False}

    def models(self, diagnostic_dir: Path | None = None) -> dict:
        if self.mock is not None:
            return {"data": [{"id": self.task["model"]}], "simulation": True}
        if self.provider == "openai_compatible":
            if not self.config.get("discover_models", True):
                return {"data": [], "discovery_skipped": True, "requested_model": self.task["model"]}
            payload = self._request("/models")
        else:
            result = self.command(["models", "--timeout", "120", "--retries", "1"])
            if result.returncode:
                diagnosis = self.command(["diagnose", "--model", self.task["model"], "--operation", "chat"])
                if diagnostic_dir:
                    diagnostic_dir.mkdir(parents=True, exist_ok=True)
                    stamp = uuid.uuid4().hex[:12]
                    (diagnostic_dir / f"models-error-{stamp}.log").write_bytes(result.stdout + result.stderr)
                    (diagnostic_dir / f"models-diagnose-{stamp}.log").write_bytes(diagnosis.stdout + diagnosis.stderr)
                raise RuntimeError("CSTCloud model discovery failed; run the dependency's diagnose command")
            payload = json.loads(result.stdout.decode("utf-8-sig"))
        entries = payload if isinstance(payload, list) else payload.get("data", [])
        if not isinstance(entries, list) or self.task["model"] not in [x.get("id") for x in entries if isinstance(x, dict)]:
            raise ValueError("Requested model unavailable; ask user to choose from current model list")
        return payload

    def chat(self, attempt: Path, index: int) -> dict:
        if self.mock is not None:
            if index >= len(self.mock):
                raise ValueError("Mock response exhausted")
            item = self.mock[index]
            return item if isinstance(item, dict) else {"choices": [{"message": {"content": item}, "finish_reason": "stop"}], "usage": {}, "simulation": True}
        if self.provider == "openai_compatible":
            messages = read_json(attempt / "messages.json")
            payload = {"model": self.task["model"], "messages": messages, "stream": False, self.token_field: self.token_limit, **self.extra}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
            (attempt / "provider-request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
            raw = self._request("/chat/completions", payload)
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
                raise ValueError("Provider must return choices[0].message.content")
            if not isinstance(choices[0]["message"].get("content"), str) or not choices[0]["message"]["content"].strip():
                raise ValueError("Provider returned empty final content")
            return raw
        args = ["chat", "--model", self.task["model"], "--messages-file", str(attempt / "messages.json"),
                "--thinking", "on", "--temperature", "0.3", "--max-length", str(self.request_metadata()["max_output_length"]), "--timeout", "240", "--retries", "1"]
        result = self.command(args)
        (attempt / "transport.log").write_bytes(result.stderr)
        if result.returncode:
            diagnosis = self.command(["diagnose", "--model", self.task["model"], "--operation", "chat"])
            (attempt / "diagnose.log").write_bytes(diagnosis.stdout + diagnosis.stderr)
            (attempt / "error.log").write_bytes(result.stdout)
            raise RuntimeError("CSTCloud inference failed; sanitized diagnostics retained in attempt directory")
        return json.loads(result.stdout.decode("utf-8-sig"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe configured model availability; no drafting request")
    parser.add_argument("task", type=Path, help="task.json or a provider example JSON")
    args = parser.parse_args()
    client = ModelClient(read_json(args.task))
    print(json.dumps(client.models(), ensure_ascii=False, indent=2))
