import io
import unittest
from unittest.mock import patch

import main


class FrozenHelperStdioTests(unittest.TestCase):
    @patch("main.runpy.run_module")
    @patch("main._attach_child_pipe")
    def test_helper_reconnects_missing_stdout_and_stderr(self, attach, run_module):
        stdout = io.StringIO()
        stderr = io.StringIO()
        attach.side_effect = [stdout, stderr]
        with patch("sys.stdout", None), patch("sys.stderr", None), patch(
            "sys.argv", ["app.exe", "--gallery-dl-helper", "--resolve-json", "https://example.com"]
        ):
            main.run_gallery_dl_helper()
            self.assertIs(__import__("sys").stdout, stdout)
            self.assertIs(__import__("sys").stderr, stderr)
            self.assertEqual(
                __import__("sys").argv,
                ["gallery-dl", "--resolve-json", "https://example.com"]
            )
        self.assertEqual(attach.call_args_list[0].args, (1, -11))
        self.assertEqual(attach.call_args_list[1].args, (2, -12))
        run_module.assert_called_once_with("gallery_dl", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    unittest.main()
