"""SECRET_KEY: without a real one the app refuses to boot.

The key signs every session cookie, and with passwordless login a cookie that
says `authed` with an admin id IS that admin's session. config.py used to fall
back to 'dev-insecure-change-me', which is in this public repository's history,
so a deployment that never set SECRET_KEY accepted cookies anyone could sign.
Now the app stops at startup instead. These tests hold it to that at both
levels: the check in config.py, and a real `import backend.app` in a fresh
interpreter, which is all gunicorn does before it starts serving.
"""

import contextlib
import os
import re
import secrets
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import REPO_ROOT, config  # noqa: E402

OLD_PUBLIC_DEFAULT = "dev-insecure-change-me"
# Generated the way the error message tells an operator to generate one.
GOOD_KEY = secrets.token_hex(32)
# Exactly the minimum length, with no character repeated, and one short of it.
MINIMUM_KEY = "0123456789abcdefghijklmnopqrstuv"
SHORT_KEY = MINIMUM_KEY[:31]
# The generation command the refusal shows, e.g. python3 -c "import secrets; ...".
GENERATE_COMMAND_RE = re.compile(r'python3 -c "([^"]+)"')
# Nothing listens on port 1, and connect_timeout bounds the attempt: booted with
# this, a child process can't reach any real database even if the check is gone.
UNREACHABLE_DATABASE_URL = "postgresql://127.0.0.1:1/none?connect_timeout=1"


@contextlib.contextmanager
def secret_key_variable(raw):
    """config as it is after reading SECRET_KEY=raw (None: the variable is absent).

    config.py reads an empty value as unset, so the parsed key is then None
    while the variable itself is still there.
    """
    with mock.patch.object(config, "SECRET_KEY", raw or None), mock.patch.dict(os.environ):
        os.environ.pop("SECRET_KEY", None)
        if raw is not None:
            os.environ["SECRET_KEY"] = raw
        yield


class SecretKeyProblemTests(unittest.TestCase):
    """secret_key_problem() says what is wrong with a key, or returns None."""

    def test_unset_key_is_refused(self):
        with secret_key_variable(None):
            self.assertIn("not set", config.secret_key_problem())

    def test_empty_key_is_refused(self):
        self.assertIsNotNone(config.secret_key_problem(""))
        # An empty value in Render's dashboard. The message says so, because the
        # fix differs: fill in the field that exists rather than add one.
        with secret_key_variable(""):
            self.assertIn("set but empty", config.secret_key_problem())

    def test_key_shorter_than_32_characters_is_refused(self):
        problem = config.secret_key_problem(SHORT_KEY)
        self.assertIn("31", problem)
        self.assertIn("32", problem)
        self.assertIsNone(config.secret_key_problem(MINIMUM_KEY))

    def test_long_but_repetitive_key_is_refused(self):
        self.assertIn("distinct", config.secret_key_problem("x" * 64))

    def test_old_public_default_is_refused(self):
        self.assertIn("published", config.secret_key_problem(OLD_PUBLIC_DEFAULT))
        with secret_key_variable(OLD_PUBLIC_DEFAULT):
            self.assertIsNotNone(config.secret_key_problem())

    def test_random_64_hex_key_is_accepted(self):
        self.assertIsNone(config.secret_key_problem(GOOD_KEY))
        with secret_key_variable(GOOD_KEY):
            self.assertIsNone(config.secret_key_problem())


class RequireSecretKeyTests(unittest.TestCase):
    def test_returns_a_usable_key(self):
        with secret_key_variable(GOOD_KEY):
            self.assertEqual(config.require_secret_key(), GOOD_KEY)

    def test_unusable_keys_raise_naming_the_variable_and_the_fix(self):
        for raw in (None, "", OLD_PUBLIC_DEFAULT, SHORT_KEY):
            with self.subTest(raw=raw), secret_key_variable(raw):
                with self.assertRaises(RuntimeError) as caught:
                    config.require_secret_key()
                message = str(caught.exception)
                self.assertIn("SECRET_KEY", message)
                self.assertRegex(message, GENERATE_COMMAND_RE)

    def test_the_suggested_command_generates_an_accepted_key(self):
        with secret_key_variable(None), self.assertRaises(RuntimeError) as caught:
            config.require_secret_key()
        code = GENERATE_COMMAND_RE.search(str(caught.exception)).group(1)
        generated = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                   text=True, check=True, timeout=60).stdout.strip()
        self.assertIsNone(config.secret_key_problem(generated))


def boot(secret_key):
    """`import backend.app` in a fresh interpreter, as gunicorn does on Render.

    SECRET_KEY is always present in the child, even when empty, so the
    developer's .env can't supply one: python-dotenv never overrides a variable
    that is already set.
    """
    environment = dict(os.environ, SECRET_KEY=secret_key, DATABASE_URL=UNREACHABLE_DATABASE_URL)
    return subprocess.run([sys.executable, "-c", "import backend.app"], cwd=REPO_ROOT,
                          env=environment, capture_output=True, text=True, timeout=120)


class BootTests(unittest.TestCase):
    """The real boot: an unusable key stops it before it touches a database."""

    def assert_refused(self, secret_key, reason):
        result = boot(secret_key)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("RuntimeError: SECRET_KEY", result.stderr)
        self.assertIn(reason, result.stderr)
        # app.py prints "Database: ..." just before init_db(). Neither that line
        # nor a failed connection means the check stopped the boot first.
        self.assertNotIn("Database:", result.stdout)
        self.assertNotIn("OperationalError", result.stderr)
        return result

    def test_empty_key_stops_the_boot(self):
        self.assert_refused("", "set but empty")

    def test_old_public_default_stops_the_boot(self):
        self.assert_refused(OLD_PUBLIC_DEFAULT, "published")

    def test_short_key_stops_the_boot(self):
        result = self.assert_refused(SHORT_KEY, "31 characters")
        # The refusal describes the key; it never prints it.
        self.assertNotIn(SHORT_KEY, result.stdout + result.stderr)

    def test_good_key_gets_as_far_as_the_database(self):
        """The control. With a usable key the same boot passes the check and
        fails at init_db() on the unreachable database, which is what makes 'no
        OperationalError' above evidence that the check ran first."""
        result = boot(GOOD_KEY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Database: Postgres", result.stdout)
        self.assertIn("OperationalError", result.stderr)
        self.assertNotIn("SECRET_KEY", result.stderr)
        self.assertNotIn(GOOD_KEY, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
