"""Тесты маршрутизации (этап 4). Без сети: доступность провайдеров подменяется."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka.providers import Hub  # noqa: E402
from yanka.router import Router, classify, explain  # noqa: E402


class FakeHub(Hub):
    def __init__(self, d, local_ok=True):
        super().__init__(d)
        self.local_ok = local_ok
        self.store.upsert_provider("lmstudio", model="qwen3.5-9b")

    def check(self, pid, timeout=4):
        return {"ok": self.local_ok if pid == "lmstudio" else True}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub(tempfile.mkdtemp())
        self.r = Router(self.hub)

    def go(self, text, **kw):
        return self.r.route(text, programs=kw.pop("programs", {"blender", "roblox"}), **kw)

    def test_classify(self):
        self.assertEqual(classify("Привет! Как дела?")[0], "chat")
        self.assertEqual(classify("Исправь ошибку в скрипте python")[0], "code")
        self.assertEqual(classify("Сделай сцену в Блендере")[0], "blender")
        self.assertEqual(classify("Построй обби в Roblox")[0], "roblox")
        self.assertEqual(classify("Почисти папку загрузок")[0], "files")
        self.assertEqual(classify("Что лежит в папке проекта?")[0], "look")
        self.assertEqual(self.go("Покажи, какие файлы в папке")["executor"], "local")
        self.assertEqual(self.go("Удали старые файлы из папки")["executor"], "klodik")

    def test_chat_goes_local(self):
        d = self.go("Привет, Янка! Расскажи сказку")
        self.assertEqual(d["executor"], "local")
        self.assertEqual(d["model"], "qwen3.5-9b")
        self.assertIn("бесплатно", explain(d))

    def test_code_goes_to_klodik(self):
        self.assertEqual(self.go("Напиши скрипт на питоне, который переименует фото")["executor"], "klodik")

    def test_programs(self):
        self.assertEqual(self.go("Сделай 3D-модель домика в Blender")["executor"], "blender")
        d = self.go("Построй обби в Roblox", programs={"blender"})
        self.assertEqual(d["executor"], "klodik")
        self.assertEqual(d["fallback_from"], "roblox")

    def test_manual(self):
        d = self.go("@пк напиши скрипт на питоне")
        self.assertEqual(d["executor"], "local")
        self.assertTrue(d["manual"])
        self.assertEqual(self.go("@клодик привет")["executor"], "klodik")

    def test_cloud_off_by_default(self):
        big = "Придумай сложную архитектуру целиком:\n1. раз\n2. два\n3. три\n" + "подробно " * 80
        d = self.go(big)
        self.assertNotEqual(d["executor"], "cloud")
        d = self.go("@облако привет")
        self.assertEqual(d["executor"], "local")
        self.assertEqual(d["fallback_from"], "cloud")

    def test_cloud_when_enabled_but_not_private(self):
        self.hub.store.upsert_provider("deepseek", key="sk-x-1234567890", model="deepseek-chat")
        self.hub.store.cfg["budget"] = {"daily_usd": 1, "monthly_usd": 5}
        big = "Объясни сложную тему целиком и подробно:\n1. а\n2. б\n3. в\n" + "очень " * 300
        self.assertEqual(self.go(big)["executor"], "cloud")
        d = self.go(big, private=True)
        self.assertEqual(d["executor"], "local")

    def test_local_down(self):
        self.hub.local_ok = False
        d = self.go("Привет!")
        self.assertEqual(d["executor"], "klodik")
        self.assertEqual(d["fallback_from"], "local")


if __name__ == "__main__":
    unittest.main(verbosity=2)
