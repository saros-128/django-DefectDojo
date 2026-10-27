"""Docker-only credential routing regression checks; no real secrets needed."""
import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("fixture_askpass", Path(__file__).with_name("git_askpass.py"))
askpass = importlib.util.module_from_spec(spec)
spec.loader.exec_module(askpass)


class AskpassTests(unittest.TestCase):
    def invoke(self, prompt):
        output = io.StringIO()
        with patch.object(askpass.sys, "argv", ["git_askpass.py", prompt]), redirect_stdout(output):
            code = askpass.main()
        return code, output.getvalue()

    def test_username_and_password_for_fixture_gitlab(self):
        self.assertEqual(self.invoke("Username for 'http://gitlab': "), (0, "oauth2\n"))
        with patch.object(askpass.Path, "read_text", return_value="fixture-only-token\n") as read:
            self.assertEqual(self.invoke("Password for 'http://oauth2@gitlab': "), (0, "fixture-only-token\n"))
            read.assert_called_once_with(encoding="utf-8")

    def test_other_destinations_never_read_a_secret(self):
        prompts = [
            "Password for 'https://github.com': ",
            "Password for 'http://gitlab.example': ",
            "Password for 'http://gitlab@evil.example': ",
            "Password for 'http://gitlab:8080': ",
            "Password for 'http://gitlab:bad': ",
            "Unexpected prompt", "Token for 'http://gitlab': ",
        ]
        with patch.object(askpass.Path, "read_text") as read:
            for prompt in prompts:
                with self.subTest(prompt=prompt):
                    self.assertEqual(self.invoke(prompt), (1, ""))
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
