"""Тесты: зрение (PNG, маршрут картинок), Google Gemini, бесплатное облако только по просьбе, голос без пакетов."""
import json
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka import vision, voice  # noqa: E402
from yanka.app import _strip_tag  # noqa: E402
from yanka.providers import Hub  # noqa: E402
from yanka.router import Router  # noqa: E402

SEEN = []


class FakeGemini(BaseHTTPRequestHandler):
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
        if self.headers.get("x-goog-api-key") != "AQ.test-key":
            return self._send(401, {"error": {"message": "bad key"}})
        self._send(200, {"models": [
            {"name": "models/gemini-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.headers.get("x-goog-api-key") != "AQ.test-key":
            return self._send(401, {"error": {"message": "bad key"}})
        self._send(200, {"modelVersion": "gemini-flash-001", "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7},
                         "candidates": [{"finishReason": "STOP", "content": {"parts": [
                             {"text": "думаю…", "thought": True}, {"text": "Привет от Gemini 🐑"}]}}]})


class FakeHub(Hub):
    def __init__(self, d, local_ok=True):
        super().__init__(d)
        self.local_ok = local_ok
        self.store.upsert_provider("lmstudio", model="qwen/qwen3.5-9b")

    def check(self, pid, timeout=4):
        return {"ok": self.local_ok if pid == "lmstudio" else True}


class NextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeGemini)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1beta"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    # --- зрение ---
    def test_png_roundtrip(self):
        w, h = 3, 2
        bgra = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255] * 2)  # синий, зелёный, красный
        rgb = vision.bgra_to_rgb(bgra, w, h)
        self.assertEqual(rgb[:9], bytes([0, 0, 255, 0, 255, 0, 255, 0, 0]))
        png = vision.png_bytes(w, h, rgb)
        self.assertTrue(png.startswith(b"\x89PNG"))
        ln = struct.unpack(">I", png[8:12])[0]
        ww, hh = struct.unpack(">II", png[16:24])
        self.assertEqual((ln, ww, hh), (13, 3, 2))
        idat = png.index(b"IDAT")
        size = struct.unpack(">I", png[idat - 4:idat])[0]
        raw = zlib.decompress(png[idat + 4:idat + 4 + size])
        self.assertEqual(raw, b"\x00" + rgb[:9] + b"\x00" + rgb[9:])

    def test_data_url_and_limits(self):
        p = Path(tempfile.mkdtemp()) / "a.png"
        p.write_bytes(vision.png_bytes(1, 1, b"\x01\x02\x03"))
        self.assertTrue(vision.data_url(p).startswith("data:image/png;base64,"))
        self.assertTrue(vision.is_image("x.JPG"))
        self.assertFalse(vision.is_image("x.txt"))

    def test_images_go_to_local_yanka(self):
        r = Router(FakeHub(tempfile.mkdtemp()))
        d = r.route("Что тут за ошибка?", programs=set(), images=True)
        self.assertEqual(d["executor"], "local")
        self.assertIn("не уходит в интернет", d["reason"])
        self.assertEqual(r.route("@клодик посмотри", programs=set(), images=True)["executor"], "klodik")

    def test_strip_tag(self):
        self.assertEqual(_strip_tag("@gemini привет"), "привет")
        self.assertEqual(_strip_tag("@Блендер сделай куб"), "сделай куб")
        self.assertEqual(_strip_tag("просто текст"), "просто текст")

    # --- Google Gemini ---
    def _hub_with_google(self):
        hub = FakeHub(tempfile.mkdtemp())
        hub.store.upsert_provider("google", key="AQ.test-key", base_url=self.url, model="gemini-flash")
        return hub

    def test_gemini_adapter(self):
        hub = self._hub_with_google()
        SEEN.clear()
        r = hub.chat("google", [{"role": "system", "content": "Ты Янка"}, {"role": "user", "content": "привет"}])
        self.assertEqual(r["text"], "Привет от Gemini 🐑")  # «мысли» модели не показываем
        self.assertEqual((r["in"], r["out"], r["usd"]), (12, 7, 0.0))
        sent = SEEN[-1]
        self.assertTrue(sent["path"].endswith("/models/gemini-flash:generateContent"))
        self.assertNotIn("Authorization", sent["headers"])
        self.assertEqual(sent["body"]["systemInstruction"]["parts"][0]["text"], "Ты Янка")
        self.assertEqual(Hub.check(hub, "google")["models"], ["gemini-flash"])
        img = [{"type": "text", "text": "что это?"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        hub.chat("google", [{"role": "user", "content": img}])
        self.assertEqual(SEEN[-1]["body"]["contents"][0]["parts"][1]["inline_data"], {"mime_type": "image/png", "data": "AAAA"})

    def test_free_cloud_only_on_request(self):
        hub = self._hub_with_google()  # лимит $0
        r = Router(hub)
        big = "Объясни сложную тему целиком и подробно:\n1. а\n2. б\n3. в\n" + "очень " * 300
        self.assertNotEqual(r.route(big, programs=set())["executor"], "cloud")  # сама в облако не идёт
        d = r.route("@gemini расскажи сказку", programs=set())
        self.assertEqual((d["executor"], d["provider"]), ("cloud", "google"))
        self.assertEqual(r.route("@gemini личное", programs=set(), private=True)["executor"], "local")

    def test_private_blocked_for_free_cloud(self):
        hub = self._hub_with_google()
        from yanka.providers import ProviderError
        with self.assertRaises(ProviderError):
            hub.chat("google", [{"role": "user", "content": "x"}], private=True)

    # --- голос ---
    def test_voice_status_without_packages(self):
        st = voice.status()
        self.assertIn("ready", st)
        if not st["vosk"]:
            self.assertFalse(st["ready"])

    def test_key_import_seals_and_deletes(self):
        from yanka.app import YankaService

        class B:
            TOOLS = []
            call_tool = call_tool_as = ask_user = list_backups = restore_backup = None
        d = Path(tempfile.mkdtemp())
        (d / "import_keys.json").write_text(json.dumps({"providers": [{"id": "google", "key": "AQ.secret-1234567", "model": "gemini-flash-latest"}]}), encoding="utf-8")
        y = YankaService(d, B(), log=lambda *a: None)
        self.assertFalse((d / "import_keys.json").exists())
        self.assertEqual(y.hub.store.key("google"), "AQ.secret-1234567")
        self.assertNotIn("AQ.secret-1234567", (d / "yanka.json").read_text(encoding="utf-8"))
        self.assertTrue(y.hub.store.provider("google")["free"])

    def test_programs_need_local_model(self):
        hub = FakeHub(tempfile.mkdtemp(), local_ok=False)
        d = Router(hub).route("Сделай куб в Blender", programs={"blender"})
        self.assertEqual(Router(FakeHub(tempfile.mkdtemp())).route("Сделай куб в Blender", programs={"blender"})["model"],
                         "qwen/qwen3.5-9b")
        self.assertEqual(d["executor"], "klodik")


if __name__ == "__main__":
    unittest.main(verbosity=2)
