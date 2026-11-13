"""Тесты ядра Янки (этапы 2-3). Запуск: python -m unittest discover -s tests -v  (из папки CozyCode)
Не нужен интернет и настоящие модели: поднимаются фальшивые серверы OpenAI/Anthropic на 127.0.0.1."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka import local, store  # noqa: E402
from yanka.providers import Hub, ProviderError  # noqa: E402

CALLS = []
MODE = {"openai": "ok", "anthropic": "ok", "flaky": 0}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": "fake-model"}, {"id": "fake-2"}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path.endswith("/chat/completions"):
            m = MODE["openai"]
            if m == "401":
                return self._send(401, {"error": "bad key"})
            if m == "flaky" and MODE["flaky"] < 1:
                MODE["flaky"] += 1
                return self._send(503, {"error": "busy"})
            msg = {"role": "assistant", "content": "Привет, Яна! 🐑"}
            if body.get("tools"):
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": "{\"path\": \"a.txt\"}"}}]}
            usage = {} if m == "nousage" else {"prompt_tokens": 1000, "completion_tokens": 500}
            return self._send(200, {"model": body["model"], "choices": [{"message": msg, "finish_reason": "stop"}],
                                    "usage": usage})
        if self.path.endswith("/messages"):
            if self.headers.get("x-api-key") != "sk-ant-test":
                return self._send(401, {"error": "bad key"})
            return self._send(200, {"model": body["model"], "stop_reason": "end_turn",
                                    "content": [{"type": "text", "text": "Ответ Claude"},
                                                {"type": "tool_use", "id": "t1", "name": "list_dir",
                                                 "input": {"path": "."}}],
                                    "usage": {"input_tokens": 2000, "output_tokens": 100}})
        self._send(404, {})


class YankaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        CALLS.clear()
        MODE.update(openai="ok", flaky=0)
        self.dir = tempfile.mkdtemp()
        self.hub = Hub(self.dir)
        s = self.hub.store
        s.upsert_provider("lmstudio", base_url=self.url, model="fake-model")
        s.upsert_provider("deepseek", key="sk-test-123456789", base_url=self.url, model="deepseek-chat")
        s.upsert_provider("anthropic", key="sk-ant-test", base_url=self.url, model="claude-x")
        s.cfg["budget"] = {"daily_usd": 1.0, "monthly_usd": 5.0}
        s.save()

    def msgs(self, text="привет"):
        return [{"role": "system", "content": "Ты Янка"}, {"role": "user", "content": text}]

    # --- этап 2: провайдеры ---
    def test_local_chat_free(self):
        r = self.hub.chat("lmstudio", self.msgs())
        self.assertEqual(r["text"], "Привет, Яна! 🐑")
        self.assertTrue(r["local"])
        self.assertEqual(r["usd"], 0.0)
        self.assertNotIn("Authorization", CALLS[0]["headers"])

    def test_cloud_cost_and_ledger(self):
        r = self.hub.chat("deepseek", self.msgs(), max_tokens=500)
        # 1000 * 0.3/1e6 + 500 * 1.2/1e6
        self.assertAlmostEqual(r["usd"], 0.0009, places=6)
        self.assertEqual(CALLS[0]["headers"]["Authorization"], "Bearer sk-test-123456789")
        self.assertAlmostEqual(self.hub.store.spent()["day"], 0.0009, places=6)

    def test_anthropic_adapter(self):
        r = self.hub.chat("anthropic", self.msgs(), max_tokens=100)
        self.assertEqual(r["text"], "Ответ Claude")
        self.assertEqual(r["tool_calls"][0]["name"], "list_dir")
        sent = CALLS[0]["body"]
        self.assertEqual(sent["system"], "Ты Янка")
        self.assertTrue(all(m["role"] != "system" for m in sent["messages"]))

    def test_tool_calls_openai(self):
        tools = [{"name": "read_file", "description": "", "parameters": {"type": "object"}}]
        r = self.hub.chat("lmstudio", self.msgs(), tools=tools)
        self.assertEqual(r["tool_calls"][0]["arguments"], {"path": "a.txt"})

    def test_budget_blocks_cloud_not_local(self):
        self.hub.store.cfg["budget"] = {"daily_usd": 0.0, "monthly_usd": 0.0}
        with self.assertRaises(ProviderError) as e:
            self.hub.chat("deepseek", self.msgs())
        self.assertEqual(e.exception.kind, "budget")
        self.assertEqual(CALLS, [])
        self.hub.chat("lmstudio", self.msgs())  # локальная бесплатна - лимит не мешает

    def test_private_data_stays_local(self):
        with self.assertRaises(ProviderError) as e:
            self.hub.chat("deepseek", self.msgs(), private=True)
        self.assertEqual(e.exception.kind, "private")
        self.hub.chat("lmstudio", self.msgs(), private=True)
        self.hub.store.cfg["cloud_private_ok"] = True
        self.hub.chat("deepseek", self.msgs(), private=True)

    def test_disabled_provider(self):
        self.hub.store.upsert_provider("deepseek", enabled=False)
        with self.assertRaises(ProviderError) as e:
            self.hub.chat("deepseek", self.msgs())
        self.assertEqual(e.exception.kind, "disabled")

    def test_auth_error_and_logged(self):
        MODE["openai"] = "401"
        with self.assertRaises(ProviderError) as e:
            self.hub.chat("deepseek", self.msgs())
        self.assertEqual(e.exception.kind, "auth")
        self.assertFalse(self.hub.store.usage()[-1]["ok"])

    def test_retry_on_503(self):
        MODE["openai"] = "flaky"
        r = self.hub.chat("lmstudio", self.msgs())
        self.assertEqual(r["text"], "Привет, Яна! 🐑")
        self.assertEqual(len(CALLS), 2)

    def test_unavailable(self):
        self.hub.store.upsert_provider("ollama")
        h = self.hub.check("ollama", timeout=1)
        if not h["ok"]:  # на машине без Ollama
            self.assertEqual(h["kind"], "unavailable")

    def test_estimate_when_no_usage(self):
        MODE["openai"] = "nousage"
        r = self.hub.chat("lmstudio", self.msgs("x" * 350))
        self.assertGreater(r["in"], 90)

    def test_check_and_status_hide_keys(self):
        self.assertTrue(self.hub.check("deepseek")["ok"])
        st = json.dumps(self.hub.status(), ensure_ascii=False)
        self.assertNotIn("sk-test-123456789", st)
        self.assertIn("sk-…6789", st)
        raw = Path(self.dir, "yanka.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-test-123456789", raw)

    def test_reasoning_off_for_local_only(self):
        self.hub.chat("lmstudio", self.msgs())
        self.assertEqual(CALLS[-1]["body"].get("reasoning_effort"), "none")
        self.hub.chat("deepseek", self.msgs())
        self.assertNotIn("reasoning_effort", CALLS[-1]["body"])
        self.hub.chat("lmstudio", self.msgs(), reasoning="medium")
        self.assertEqual(CALLS[-1]["body"]["reasoning_effort"], "medium")

    def test_no_key_cloud(self):
        self.hub.store.upsert_provider("openai")
        with self.assertRaises(ProviderError) as e:
            self.hub.chat("openai", self.msgs(), model="gpt")
        self.assertEqual(e.exception.kind, "no_key")

    def test_seal_roundtrip(self):
        t = store.seal("секрет-ключ")
        self.assertNotIn("секрет", t)
        self.assertEqual(store.unseal(t), "секрет-ключ")

    # --- этап 3: локальные модели ---
    def test_fit(self):
        res = {"vram_gb": 8.0, "ram_gb": 16.0}
        coder = {"modelKey": "qwen2.5-coder-7b-instruct", "paramsString": "7B", "sizeBytes": 6254199584,
                 "quantization": {"name": "Q6_K", "bits": 6}}
        big1bit = {"modelKey": "qwen3.8-27b", "paramsString": "27B", "sizeBytes": 6192222208,
                   "quantization": {"name": "IQ1_S", "bits": 1}}
        huge = {"modelKey": "x-70b", "paramsString": "70B", "sizeBytes": 40 * 1024 ** 3}
        self.assertEqual(local.fit(coder, res, 4096)["where"], "gpu")
        f27 = local.fit(big1bit, res, 8192)
        self.assertNotEqual(f27["where"], "gpu")
        self.assertTrue(any("бит" in n for n in f27["notes"]))
        self.assertEqual(local.fit(huge, res)["where"], "no")
        rep = {"lmstudio": {"models": [
            {"key": "qwen3.8-27b", "tools": True, "fit": local.fit(big1bit, res, 4096)},
            {"key": "qwen2.5-coder-7b-instruct", "tools": False, "fit": local.fit(coder, res, 4096)}]}}
        self.assertEqual(local.best_downloaded(rep), "qwen2.5-coder-7b-instruct")
        q35 = {"modelKey": "qwen/qwen3.5-9b", "paramsString": "9B", "sizeBytes": 7033000000,
               "quantization": {"name": "Q4_K_M", "bits": 4}}
        rep["lmstudio"]["models"].append({"key": "qwen/qwen3.5-9b", "tools": True, "fit": local.fit(q35, res, 8192)})
        self.assertEqual(local.best_downloaded(rep), "qwen/qwen3.5-9b")

    def test_download_needs_consent(self):
        lm = local.LMStudio()
        lm.exe = "lms"
        with self.assertRaises(PermissionError):
            lm.download("some-model")


if __name__ == "__main__":
    unittest.main(verbosity=2)
