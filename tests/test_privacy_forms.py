"""Secret forms the review of Tasks 10b-12 still found, and the code it wrongly flagged (jevkit/privacy.py).

Offline and hermetic: no key, no network. Fake secrets are spelled so they trip the detector but not
scripts/check_release.py: a literal key shape in a test is a key shape in the release.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevkit import privacy  # noqa: E402

# Each went out unmasked after Task 10c: the text, and the value that must not survive redact.
MORE_SECRETS = [
    ('{"db_password": "Welkom01!"}', "Welkom01"),
    ('{"smtp_password": "zonnebloem"}', "zonnebloem"),
    ('{"pass": "Welkom01!"}', "Welkom01"),
    ('{"api_token": "d8f7a6s5d4f3"}', "d8f7a6s5d4f3"),
    ("Mijn bankwachtwoord is Zonnebloem12", "Zonnebloem12"),
    ("Het wifiwachtwoord is Zonnebloem12", "Zonnebloem12"),
    ("Mijn wachtwoord is veranderd in Zonnebloem12", "Zonnebloem12"),
    ("The password is set to hunter22", "hunter22"),
    ("My password was changed to Welkom01", "Welkom01"),
    ("export PGPASSWORD=Welkom01", "Welkom01"),
    ("DBPASS=Welkom01", "Welkom01"),
    ("inlogcode: Welkom01", "Welkom01"),
    ("mijn toegangscode is Welkom01", "Welkom01"),
    ("redis-cli -a Welkom01", "Welkom01"),
    ("docker login -p Welkom01", "Welkom01"),
    ("pw: Welkom01!", "Welkom01"),
]

# Code that handles a password without holding one, questions about one, commands that name a
# variable rather than a value, and words that only contain "pass". Each became highly sensitive,
# so an ordinary coding turn was never handed over.
CODE_AND_QUESTIONS = [
    "self.password = password",
    "psycopg2.connect(host=host, user=user, password=password)",
    'password = getpass.getpass("Password: ")',
    "password = models.CharField(max_length=128)",
    "password: string;",
    "pub password: String,",
    "'password' => Hash::make($request->password)",
    "pin = Pin(2, Pin.OUT)",
    "pin = board.D18",
    "Mijn wachtwoord is kwijt, hoe kom ik weer binnen?",
    "Mijn wachtwoord is geblokkeerd na drie pogingen",
    "My password is locked after three tries",
    "git clone https://oauth2:${GITLAB_TOKEN}@gitlab.com/group/repo.git",
    "mysql -h 127.0.0.1 -P3306 -u root -p",
    'sshpass -p "$SSHPASS" ssh host',
    "bypass = True",
    "compass: north",
]

HOSTILE_SIZE = 50_000
HOSTILE_SHAPES = ("a-", "a.", "a_", "a:", "a@", "a/")
TIME_LIMIT = 2.0


class MoreSecretFormsTests(unittest.TestCase):
    def test_each_form_holds_a_secret_value(self):
        for text, _ in MORE_SECRETS:
            with self.subTest(text=text):
                self.assertTrue(privacy.has_secret_value(text))

    def test_redact_removes_the_value_itself(self):
        for text, value in MORE_SECRETS:
            with self.subTest(text=text):
                self.assertNotIn(value, privacy.redact(text))

    def test_the_word_may_end_a_name_of_any_length(self):
        text = "A_B_C_D_E_F_G_H_I_J_TOKEN=nietecht123"      # ten parts in front of the word
        self.assertTrue(privacy.has_secret_value(text))
        self.assertTrue(privacy.is_sensitive(text))
        self.assertNotIn("nietecht123", privacy.redact(text))

    def test_a_password_ending_in_pass_is_not_a_variable_name(self):
        self.assertTrue(privacy.has_secret_value("password: Welkom01pass"))
        self.assertFalse(privacy.has_secret_value("password = new_password"))

    def test_a_jwt_after_a_hyphen_is_still_a_token(self):
        text = "x-eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + "." + "c2lnbmF0dXJl"
        self.assertTrue(privacy.is_sensitive(text))
        self.assertNotIn("eyJzdWIiOiIxIn0", privacy.redact(text))


class CodeAndQuestionsTests(unittest.TestCase):
    def test_code_questions_and_variable_names_hold_no_secret_value(self):
        for text in CODE_AND_QUESTIONS:
            with self.subTest(text=text):
                self.assertFalse(privacy.has_secret_value(text))


class IbanFormTests(unittest.TestCase):
    def test_a_space_after_the_country_two_separators_or_dots(self):
        for text in ("NL 91 ABNA 0417 1643 00", "NL91  ABNA  0417  1643  00", "NL91.ABNA.0417.1643.00"):
            with self.subTest(text=text):
                self.assertTrue(privacy.has_iban(text))

    def test_redact_masks_an_iban(self):
        self.assertNotIn("0417", privacy.redact("Stort het op NL91 ABNA 0417 1643 00 graag"))

    def test_a_version_string_followed_by_words_is_still_not_an_iban(self):
        self.assertFalse(privacy.has_iban("Set the target to es2023 so that optional chaining works"))

    def test_a_number_in_front_does_not_take_the_iban_into_a_code_that_fails(self):
        for text in ("factuur nr 12 NL91 ABNA 0417 1643 00", "AB12 NL91 ABNA 0417 1643 00"):
            with self.subTest(text=text):
                self.assertTrue(privacy.has_iban(text))


class MobileNumberFormTests(unittest.TestCase):
    def test_dots_brackets_and_a_spaced_hyphen(self):
        for text in ("06.12.34.56.78", "(06) 12345678", "06 - 1234 5678"):
            with self.subTest(text=text):
                self.assertTrue(privacy.has_contact_details(text))
                out = privacy.redact(f"bel {text} morgen")
                self.assertFalse(any(char.isdigit() for char in out), out)


class DutchWordTests(unittest.TestCase):
    def test_a_dutch_question_about_a_password_is_sensitive(self):
        self.assertTrue(privacy.is_sensitive("Hoe reset ik mijn wachtwoord van de bank?"))


class LinearTimeTests(unittest.TestCase):
    """A pattern that backtracks over a run of short words took 5-50 s on 50 KB: a hung turn."""

    CHECKS = (privacy.has_secret_value, privacy.redact, privacy.has_contact_details,
              privacy.is_sensitive, privacy.has_iban)

    def assert_linear(self, label, text):
        for check in self.CHECKS:
            with self.subTest(shape=label, check=check.__name__):
                started = time.perf_counter()
                check(text)
                self.assertLess(time.perf_counter() - started, TIME_LIMIT)

    def test_hostile_input_is_read_in_linear_time(self):
        for shape in HOSTILE_SHAPES:
            self.assert_linear(shape, shape * (HOSTILE_SIZE // len(shape)))

    def test_runs_of_command_and_label_words_are_read_in_linear_time(self):
        """Beyond the six shapes: patterns that re-read the rest of a line from every command or label word."""
        self.assert_linear("spaces after a password word", "password is" + " " * HOSTILE_SIZE)
        for word in ("curl ", "mysql ", "BEGIN ", "eyJ-", "_secret"):
            self.assert_linear(word, "x" + word * (HOSTILE_SIZE // len(word)))


if __name__ == "__main__":
    unittest.main()
