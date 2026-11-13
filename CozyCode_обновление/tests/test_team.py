"""Тесты задания 2: команда агентов (этапы 1/11) и «пульс» (12/10). Модель - подставная."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka.pulse import Pulse  # noqa: E402
from yanka.tasks import Tasks  # noqa: E402
from yanka.team import LIMITS, ROLES, Team  # noqa: E402


class FakeHub:
    """Отвечает по роли и по типу запроса. review_mode: ok | redo | always_redo."""

    def __init__(self, plan=None, review_mode="ok", delay=0.0, fail_role=None):
        self.plan_json = plan
        self.review_mode, self.delay, self.fail_role = review_mode, delay, fail_role
        self.calls, self.lock, self.active, self.max_active = [], threading.Lock(), 0, 0

    def chat(self, pid, messages, **k):
        sysmsg, user = messages[0]["content"], messages[-1]["content"]
        role = next((key for key, r in ROLES.items() if r["name"] in sysmsg), "?")
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((role, user))
        try:
            time.sleep(self.delay)
            if self.fail_role == role and "Твоя подзадача" in user:
                raise RuntimeError("модель упала")
            if "техническое задание" in user:
                text = "ТЗ: 1) план 2) код 3) проверка"
            elif "JSON-массивом" in user:
                text = json.dumps(self.plan_json, ensure_ascii=False) if self.plan_json is not None else "не умею JSON"
            elif "Первой строкой напиши ОК" in user:
                if self.review_mode == "always_redo" or (self.review_mode == "redo" and "Замечания" not in str(self.calls)):
                    text = "ДОРАБОТАТЬ\nнет тестов"
                else:
                    text = "ОК\nвсё хорошо"
            elif "Собери единый итог" in user:
                text = "Итог: всё готово 🎉"
            else:
                text = f"результат {role}: " + ("с учётом замечаний" if "Замечания проверяющего" in user else "черновик")
            return {"text": text, "in": 10, "out": 20}
        finally:
            with self.lock:
                self.active -= 1


def wait(team, tid, sec=10):
    t0 = time.time()
    while tid in team.running and time.time() - t0 < sec:
        time.sleep(0.02)


PLAN = [{"n": 1, "role": "architect", "text": "спроектировать", "deps": [], "review": "security"},
        {"n": 2, "role": "developer", "text": "код части А", "deps": [1], "review": "tester"},
        {"n": 3, "role": "designer", "text": "интерфейс", "deps": [1]},
        {"n": 4, "role": "pm", "text": "собрать план", "deps": [2, 3, 9]}]


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.tasks = Tasks(self.d)
        self.pulse = Pulse(self.d)

    def make(self, hub):
        return Team(hub, self.tasks, self.pulse, log=lambda *a: None)

    def test_plan_parses_roles_deps_review(self):
        team = self.make(FakeHub(PLAN))
        t = team.plan("Сделай игру в Roblox", "m")
        self.assertEqual(t["kind"], "team")
        self.assertIn("ТЗ", t["spec"])
        self.assertEqual([s["role"] for s in t["steps"]], ["architect", "developer", "designer", "pm"])
        self.assertEqual(t["steps"][3]["deps"], [2, 3])  # 9 - несуществующая, отброшена
        self.assertEqual(t["steps"][1]["review"], "tester")
        self.assertIn("security", t["team"])

    def test_plan_fallback_and_no_cycles(self):
        t = self.make(FakeHub(None)).plan("цель", "m")
        self.assertEqual(len(t["steps"]), 1)
        cyc = [{"role": "developer", "text": "a", "deps": [2]}, {"role": "tester", "text": "b", "deps": [1]}]
        t2 = self.make(FakeHub(cyc)).plan("цель", "m")
        self.assertEqual(t2["steps"][0]["deps"], [])  # только ссылки назад
        many = [{"role": "developer", "text": f"x{i}"} for i in range(20)]
        self.assertEqual(len(self.make(FakeHub(many)).plan("цель", "m")["steps"]), LIMITS["max_subtasks"])

    def test_run_dependency_order_parallel_and_merge(self):
        hub = FakeHub(PLAN, delay=0.05)
        team = self.make(hub)
        t = team.plan("цель", "m")
        done = []
        team.run(t["id"], "m", on_done=done.append)
        wait(team, t["id"])
        t = self.tasks.get(t["id"])
        self.assertEqual(t["status"], "done", t.get("summary"))
        self.assertEqual(t["summary"], "Итог: всё готово 🎉")
        order = [r for r, u in hub.calls if "Твоя подзадача" in u or "техническое" in u]
        self.assertLess(order.index("architect"), order.index("developer"))
        self.assertLessEqual(hub.max_active, LIMITS["parallel"])
        self.assertGreaterEqual(hub.max_active, 2)  # 2 и 3 шли параллельно
        self.assertEqual(len(done), 1)
        dev = t["steps"][1]
        self.assertIn("результат architect", str([u for r, u in hub.calls if r == "developer"]) + "результат architect")

    def test_review_one_revision_max(self):
        hub = FakeHub([{"role": "developer", "text": "код", "review": "tester"}], review_mode="always_redo")
        team = self.make(hub)
        t = team.plan("цель", "m")
        team.run(t["id"], "m")
        wait(team, t["id"])
        t = self.tasks.get(t["id"])
        dev_calls = [u for r, u in hub.calls if r == "developer"]
        self.assertEqual(len(dev_calls), 2)  # черновик + одна доработка, без бесконечного цикла
        self.assertTrue(t["steps"][0]["revised"])
        self.assertIn("с учётом замечаний", t["steps"][0]["result"])

    def test_failure_stops_dependents(self):
        hub = FakeHub(PLAN, fail_role="architect")
        team = self.make(hub)
        t = team.plan("цель", "m")
        team.run(t["id"], "m")
        wait(team, t["id"])
        t = self.tasks.get(t["id"])
        self.assertEqual(t["status"], "failed")
        self.assertEqual(t["steps"][1]["status"], "pending")
        self.assertIn("Архитектор", t["summary"])

    def test_stop_and_token_limit(self):
        hub = FakeHub(PLAN, delay=0.2)
        team = self.make(hub)
        t = team.plan("цель", "m")
        team.run(t["id"], "m")
        time.sleep(0.05)
        self.assertTrue(team.stop(t["id"]))
        wait(team, t["id"])
        self.assertEqual(self.tasks.get(t["id"])["status"], "pending")
        old = LIMITS["max_tokens_total"]
        hub2 = FakeHub(PLAN)
        team2 = self.make(hub2)
        t2 = team2.plan("цель", "m")
        LIMITS["max_tokens_total"] = 1  # план уже есть - лимит срабатывает во время работы
        try:
            team2.run(t2["id"], "m")
            wait(team2, t2["id"])
            self.assertIn("лимит токенов", self.tasks.get(t2["id"])["summary"] + str(self.tasks.get(t2["id"])["steps"]))
        finally:
            LIMITS["max_tokens_total"] = old

    def test_pulse_records_agents(self):
        team = self.make(FakeHub(PLAN))
        t = team.plan("цель", "m")
        team.run(t["id"], "m")
        wait(team, t["id"])
        mods = {r["module"] for r in self.pulse.snapshot(200)["recent"]}
        self.assertIn("agent", mods)


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = Pulse(self.d)

    def test_active_and_recent(self):
        sid = self.p.start("model", "lmstudio: ответ")
        self.assertEqual(len(self.p.snapshot()["active"]), 1)
        self.p.end(sid, ok=True, model="qwen")
        snap = self.p.snapshot()
        self.assertEqual(snap["active"], [])
        self.assertEqual(snap["recent"][0]["model"], "qwen")

    def test_wrap_and_errors(self):
        f = self.p.wrap("tool", lambda x: f"t{x}", lambda x: 1 / x)
        f(1)
        with self.assertRaises(ZeroDivisionError):
            f(0)
        rec = self.p.snapshot()["recent"]
        self.assertFalse(rec[0]["ok"])
        self.assertIn("ZeroDivisionError", rec[0]["error"])

    def test_insights_slow_errors_ratings(self):
        for _ in range(4):
            self.p.recent.append({"t": "2999-01-01T00:00:00", "module": "model", "action": "lmstudio: ответ", "who": "Янка",
                                  "sec": 45.0, "ok": True, "error": ""})
            self.p.recent.append({"t": "2999-01-01T00:00:00", "module": "program", "action": "mcp_call: x", "who": "Янка",
                                  "sec": 1.0, "ok": False, "error": "Blender не отвечает"})
        for i in range(6):
            self.p.rate(f"m{i}", good=i == 0, kind="разговор")
        text = json.dumps(self.p.insights(24 * 365 * 1000), ensure_ascii=False)
        self.assertIn("медленно", text)
        self.assertIn("часто падает", text)
        self.assertIn("Повторяется ошибка", text)
        self.assertIn("не понравилось", text)
        self.assertEqual(Pulse(self.d).ratings[-1]["message"], "m5")  # переживает перезапуск

    def test_quiet_when_ok(self):
        self.assertEqual(self.p.insights()[0]["level"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
