"""命令执行器必须拒绝 shell 字符串，并且不能打开任意程序。"""

import inspect
import unittest

from app.commands.executor import CommandRejected, run_command


class RunCommandTests(unittest.TestCase):
    def test_reject_shell_string(self):
        with self.assertRaises(CommandRejected):
            run_command("uname -s && id")

    def test_reject_metacharacters(self):
        with self.assertRaises(CommandRejected):
            run_command(["uname", ";rm"])

    def test_reject_command_substitution(self):
        with self.assertRaises(CommandRejected):
            run_command(["uname", "$(id)"])

    def test_reject_unlisted_binary(self):
        with self.assertRaises(CommandRejected):
            run_command(["bash", "-c", "id"])

    def test_reject_absolute_path(self):
        with self.assertRaises(CommandRejected):
            run_command(["/bin/uname", "-s"])

    def test_reject_bad_timeout(self):
        with self.assertRaises(CommandRejected):
            run_command(["uname", "-s"], timeout=0)

    def test_signature_cannot_enable_shell(self):
        params = inspect.signature(run_command).parameters
        self.assertNotIn("shell", params)
        self.assertNotIn("cmd", params)

    def test_uname_runs_as_argv(self):
        result = run_command(["uname", "-s"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(result.stdout.strip(), {"Darwin", "Linux"})
        self.assertEqual(result.argv, ("uname", "-s"))


if __name__ == "__main__":
    unittest.main()
