"""Тесты памяти (этап 5)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka.memory import Memory  # noqa: E402


class FakeHub:
    def __init__(self, text):
        self.text = text

    def chat(self, *a, **k):
        return {"text": self.text}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.m = Memory(self.d)

    def test_add_search_edit_delete(self):
        mid, st, _ = self.m.add("preference", "Яна любит тёмную тему и розовые акценты", project="general")
        self.assertEqual(st, "added")
        self.m.add("decision", "Облачные модели пока не подключаем", project="general")
        self.m.add("problem", "LM Studio не скачивает модели из-за проверки сертификата антивирусом")
        found = self.m.search("какую тему любит Яна")
        self.assertEqual(found[0]["id"], mid)
        self.assertTrue(self.m.search("сертификаты антивируса"))  # другие окончания
        self.m.edit(mid, text="Яна любит тёмную тему, розовые акценты и барашков")
        self.assertIn("барашков", self.m.get(mid)["text"])
        self.assertTrue(self.m.delete(mid))
        self.assertIsNone(self.m.get(mid))
        self.assertFalse(self.m.search("барашков"))

    def test_no_secrets_no_smalltalk(self):
        self.assertEqual(self.m.add("fact", "ключ OpenAI sk-abcdefghijklmnop123")[1], "skipped")
        self.assertEqual(self.m.add("fact", "мой пароль: kitty123 от почты")[1], "skipped")
        self.assertEqual(self.m.add("fact", "Спасибо!")[1], "skipped")
        self.assertEqual(self.m.add("nonsense", "что-то длинное и нормальное")[1], "skipped")
        self.assertEqual(self.m.list(), [])

    def test_dedupe(self):
        a, _, _ = self.m.add("preference", "Яна хочет отвечать по-русски тепло и по делу")
        b, st, _ = self.m.add("preference", "Яна хочет, чтобы отвечали по-русски тепло и по делу")
        self.assertEqual(a, b)
        self.assertEqual(st, "updated")
        self.assertEqual(len(self.m.list()), 1)

    def test_recall_budget_and_project(self):
        self.m.add("fact", "Видеокарта RTX 3060 Ti с 8 ГБ памяти", pinned=True)
        for i in range(30):
            self.m.add("research", f"Модель номер {i} про видеокарту и скорость ответа вариант {i * 7}")
        self.m.add("project", "Проект игры: платформер в Roblox", project="roblox-game")
        r = self.m.recall("какая видеокарта", max_chars=300)
        self.assertLessEqual(len(r), 300)
        self.assertIn("RTX 3060", r.splitlines()[0])
        self.assertNotIn("платформер", self.m.recall("платформер", project="general"))
        self.assertIn("платформер", self.m.recall("платформер", project="roblox-game"))

    def test_persistence(self):
        self.m.add("decision", "Модели скачиваем только с согласия Яны")
        m2 = Memory(self.d)
        self.assertEqual(len(m2.search("согласия")), 1)

    def test_extract(self):
        hub = FakeHub('Вот: [{"kind": "preference", "text": "Яна хочет самую сильную модель, которая влезет в ПК"},'
                      ' {"kind": "fact", "text": "ок"}, {"kind": "fact", "text": "api_key: sk-1234567890abcdef"}]')
        ids = self.m.extract(hub, [{"role": "user", "content": "..."}])
        self.assertEqual(len(ids), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
