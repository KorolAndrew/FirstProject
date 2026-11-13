"""Тесты задач (этап 6)."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka.tasks import Tasks, make_verify, plan  # noqa: E402


class FakeHub:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def chat(self, *a, **k):
        self.calls += 1
        return {"text": self.text if isinstance(self.text, str) else self.text[min(self.calls - 1, len(self.text) - 1)]}


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = Tasks(self.d)

    def test_run_all_steps(self):
        task = self.t.create("Сделать открытку", ["придумать текст", "оформить"])
        out = self.t.run(task["id"], lambda t, s: {"ok": True, "result": "готово: " + s["text"]})
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.t.progress(out), "2/2")

    def test_retry_then_ok(self):
        tries = {"n": 0}

        def ex(t, s):
            tries["n"] += 1
            return {"ok": tries["n"] > 1, "result": "x", "error": "сбой"}
        task = self.t.create("цель", ["шаг"])
        out = self.t.run(task["id"], ex, max_attempts=3)
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["steps"][0]["attempts"], 2)

    def test_fail_after_attempts_and_crash_is_caught(self):
        task = self.t.create("цель", ["шаг 1", "шаг 2"])
        out = self.t.run(task["id"], lambda t, s: 1 / 0, max_attempts=2)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["steps"][1]["status"], "pending")
        self.assertIn("ZeroDivisionError", out["log"][-2]["text"])

    def test_verify_contains(self):
        task = self.t.create("цель", [{"text": "напиши стих про барашка", "check": {"contains": "барашек"}}])
        answers = iter(["стих про котика", "стих: барашек спит"])
        out = self.t.run(task["id"], lambda t, s: {"ok": True, "result": next(answers)}, verify=make_verify(),
                         max_attempts=2)
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["steps"][0]["attempts"], 2)

    def test_verify_file_and_model(self):
        root = Path(self.d)
        (root / "out.txt").write_text("hi")
        v = make_verify(hub=FakeHub("НЕТ — нет рифмы"), root=root)
        self.assertTrue(v({}, {"check": {"file_exists": "out.txt"}}, "")[0])
        self.assertFalse(v({}, {"check": {"file_exists": "nope.txt"}}, "")[0])
        ok, why = v({}, {"text": "стих", "check": {"model": "есть рифма"}}, "текст")
        self.assertFalse(ok)
        self.assertIn("рифмы", why)

    def test_waiting_and_resume(self):
        task = self.t.create("цель", [{"text": "Клодик делает код", "executor": "klodik"}, "проверить"])
        ex = lambda t, s: {"waiting": True, "result": "передала Клодику"} if s["executor"] == "klodik" else {"ok": True}
        out = self.t.run(task["id"], ex)
        self.assertEqual(out["status"], "waiting")
        self.t.resume(task["id"], "Клодик сделал")
        out = self.t.run(task["id"], ex)
        self.assertEqual(out["status"], "done")

    def test_interrupted_resumes_after_restart(self):
        task = self.t.create("цель", ["a", "b"])
        task["steps"][0]["status"] = "done"
        task["steps"][1]["status"] = "running"
        task["status"] = "running"
        self.t.save()
        t2 = Tasks(self.d)
        tt = t2.get(task["id"])
        self.assertEqual(tt["steps"][1]["status"], "pending")
        seen = []
        t2.run(tt["id"], lambda t, s: (seen.append(s["n"]), {"ok": True})[1])
        self.assertEqual(seen, [2])

    def test_stop(self):
        ev = threading.Event()
        task = self.t.create("цель", ["a", "b", "c"])

        def ex(t, s):
            ev.set()
            return {"ok": True}
        out = self.t.run(task["id"], ex, stop=ev)
        self.assertEqual(out["status"], "pending")
        self.assertEqual(self.t.progress(out), "1/3")

    def test_plan_parse_and_fallback(self):
        hub = FakeHub('План: [{"text": "Набросать сцену", "executor": "blender"}, {"text": "Описать", "executor": "xx"}]')
        steps = plan(hub, "Сделай сцену")
        self.assertEqual([s["executor"] for s in steps], ["blender", "local"])
        self.assertEqual(plan(FakeHub("не знаю"), "Цель"), [{"text": "Цель", "executor": "local"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
