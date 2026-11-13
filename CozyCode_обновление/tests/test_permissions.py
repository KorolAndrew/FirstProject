"""Тесты разрешений (этап 7). Настоящий bridge не используется - подставной бэкенд."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yanka.permissions import Denied, Gate, classify  # noqa: E402


class Backend:
    def __init__(self, answer=True):
        self.answer, self.asked, self.calls, self.backups, self.restored = answer, [], [], [], []

    def call_tool(self, name, args):
        self.calls.append(name)
        if name == "write_file":
            self.backups.append({"id": f"b{len(self.backups) + 1}"})
        if name == "delete_path" and args.get("path") == "nope":
            return {"content": [{"type": "text", "text": "Действие отклонено пользователем (или не было ответа вовремя)."}],
                    "isError": True}
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    def as_dict(self):
        return {"call_tool": self.call_tool, "ask": lambda t, m: (self.asked.append(m), self.answer)[1],
                "list_backups": lambda: list(self.backups), "restore_backup": lambda b: self.restored.append(b) or b}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.be = Backend()
        self.g = Gate(tempfile.mkdtemp(), self.be.as_dict())

    def test_classify(self):
        self.assertEqual(classify("read_file", {"path": "a"}), "read")
        self.assertEqual(classify("run_command", {"command": "pip install requests"}), "install")
        self.assertEqual(classify("run_command", {"command": "reg add HKCU\\Software\\X /v a"}), "system")
        self.assertEqual(classify("run_command", {"command": "Invoke-WebRequest https://x -Method Post"}), "external")
        self.assertEqual(classify("run_command", {"command": "Remove-Item a.txt"}), "delete")
        self.assertEqual(classify("run_command", {"command": "python main.py"}), "command")
        self.assertEqual(classify("mcp_call", {"program": "blender", "tool": "get_scene_info"}), "read")
        self.assertEqual(classify("mcp_call", {"program": "blender", "tool": "execute_blender_code"}), "program")
        self.assertEqual(classify("format_disk", {}), "unknown")

    def test_read_allowed_without_asking(self):
        self.g.call("read_file", {"path": "a.txt"})
        self.assertEqual(self.be.asked, [])
        self.assertEqual(self.g.journal()[-1]["decision"], "allowed")

    def test_system_and_unknown_denied(self):
        with self.assertRaises(Denied):
            self.g.call("run_command", {"command": "bcdedit /set x"})
        with self.assertRaises(Denied):
            self.g.call("format_disk", {})
        self.assertEqual(self.be.calls, [])

    def test_install_asks_and_rejection_is_remembered(self):
        self.be.answer = False
        with self.assertRaises(Denied):
            self.g.call("run_command", {"command": "pip install requests"}, task_id="t1")
        self.assertEqual(len(self.be.asked), 1)
        with self.assertRaises(Denied):  # второй раз уже не спрашиваем
            self.g.call("run_command", {"command": "pip install requests"}, task_id="t1")
        self.assertEqual(len(self.be.asked), 1)
        self.assertEqual(self.be.calls, [])

    def test_install_approved(self):
        self.g.call("run_command", {"command": "pip install requests"}, reason="нужно для скрипта")
        self.assertIn("нужно для скрипта", self.be.asked[0])
        self.assertEqual(self.be.calls, ["run_command"])
        self.assertEqual(self.g.journal()[-1]["decision"], "approved")

    def test_bridge_rejection_remembered(self):
        with self.assertRaises(Denied):
            self.g.call("delete_path", {"path": "nope"}, task_id="t2")
        with self.assertRaises(Denied):
            self.g.call("delete_path", {"path": "nope"}, task_id="t2")
        self.assertEqual(self.be.calls, ["delete_path"])

    def test_undo(self):
        r = self.g.call("write_file", {"path": "a.txt", "content": "x"})
        self.assertEqual(self.g.journal()[-1]["backups"], ["b1"])
        self.g.undo(r["decision_id"])
        self.assertEqual(self.be.restored, ["b1"])
        r2 = self.g.call("read_file", {"path": "a.txt"})
        with self.assertRaises(ValueError):
            self.g.undo(r2["decision_id"])

    def test_model_does_not_see_forbidden(self):
        tools = [{"name": "read_file"}, {"name": "run_command"}, {"name": "secret_tool"}]
        names = [t["name"] for t in self.g.tools_for_model(tools)]
        self.assertEqual(names, ["read_file", "run_command"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
