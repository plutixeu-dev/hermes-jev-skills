"""The outbound boundary. Everything sent to Jev passes through here first.

Two tools: ``redact`` masks things that look like secrets or contact details, and
``is_sensitive`` says "do not send this at all". Callers that get a True from
``is_sensitive`` must skip Jev and take their fail-open path.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List

_SECRET_WORDS = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|authorization\s*:|bearer\s+[a-z0-9._-]{8,}|password|passwd|"
    r"client[_ -]?secret|session[_ -]?cookie|credit[_ -]?card|card[_ -]?number|"
    r"\bcvv\b|\bssn\b|private[_ -]?key|BEGIN [A-Z ]*PRIVATE KEY)"
)
# An env-var name is how a secret usually appears in agent output: AWS_SECRET_ACCESS_KEY,
# STRIPE_SECRET, DB_PASSWORD, GITHUB_TOKEN. Matching only `secret_key` missed every one of
# them, because the revealing word sits in the middle of the name, not at its end.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)*[_-]"
    r"(?:SECRET|SECRET[_-]?\w*KEY|API[_-]?KEY|KEY|TOKEN|PASSWORD|PASSWD|CREDENTIALS?|AUTH)\b"
    r"\s*[:=]\s*\S*"
)
_SECRET_NAME = re.compile(r"(?i)\bsecret[_ -](?:access[_ -])?key\b|\bsecret[_ -]?key\b")
_TOKEN_SHAPES = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|apikey_[A-Za-z0-9_]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})\b"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
# 06 1234 5678, 06-12345678, +31 (0)6 12345678, 0031 6 ...: the North American shape above misses them all.
_NL_MOBILE = re.compile(r"(?<![\d+])(?:\+31\s?(?:\(0\)\s?)?|0031\s?|0)6[\s-]?(?:\d[\s-]?){7}\d(?!\d)")
# The keyword rules above only fire on a label. A bank alert or an order receipt carries
# the card number with no trigger word anywhere near it, and "4111 1111 1111 1111" went
# out verbatim. Luhn is what keeps this from eating order and reference numbers — the
# same trap the _TRACKING comment below documents, reached from the other direction.
_CARD = re.compile(r"(?<![\d.-])(?:\d[ -]?){12,18}\d(?![\d.-])")
# _PHONE is a North American shape: three, three, four. Two lines of a European signature
# ("+44 20 7946 0958", "+33 1 70 18 99 00") walked straight past it.
_INTL_PHONE = re.compile(r"(?<![\d+])\+\d{1,3}[\s.-]?(?:\d[\s.-]?){7,13}\d(?!\d)")
# A credential with no label at all: an AWS secret access key is 40 base64 characters and
# the word "secret" never appears beside it in a mail. Mixed case AND a digit is what
# separates it from a word, a hex digest (already [hex] by the time this runs) or a slug.
_HIGH_ENTROPY = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9+/=_-])")
# A UPS tracking number's digit tail parses as country-code + 3 + 3 + 4, so the phone rule
# ate it: "1Z999AA10123456784" became "1Z999AA[phone]". Those numbers are the operational
# spine of a shipping desk and a redactor that silently destroys them looks like it worked.
#
# The first fix was to widen the phone lookbehind to exclude letters too. That was wrong:
# it stopped redacting "x8505550134", "ext8505550134" and "Phone8505550134" — trading a
# data-loss bug for a privacy leak. Protect the specific thing instead of blunting the
# general rule. Pure-digit carrier formats (FedEx 12/15/20, USPS 20-22) are already safe,
# because the phone pattern's trailing (?!\d) refuses to match a prefix of a longer run.
_TRACKING = re.compile(r"\b1Z[0-9A-Z]{16}\b", re.IGNORECASE)
_LONG_HEX = re.compile(r"\b[a-fA-F0-9]{32,}\b")


def _luhn(digits: str) -> bool:
    """The check digit every payment card carries. An order number almost never passes it."""
    total, alternate = 0, False
    for char in reversed(digits):
        value = ord(char) - 48
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0


def _mask_card(match: "re.Match[str]") -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[card]" if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)


def _mask_credential(match: "re.Match[str]") -> str:
    run = match.group(0)
    mixed = (any(c.isupper() for c in run) and any(c.islower() for c in run)
             and any(c.isdigit() for c in run))
    return "[secret]" if mixed else run


def normalize(text: str) -> str:
    """Fold look-alike and invisible characters so a gate cannot be dodged with Unicode."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(c for c in folded if unicodedata.category(c) not in {"Cf", "Cc"} or c in "\n\t")


def is_sensitive(text: str) -> bool:
    probe = normalize(text)
    return bool(_SECRET_WORDS.search(probe) or _SECRET_NAME.search(probe)
                or _SECRET_ASSIGNMENT.search(probe) or _TOKEN_SHAPES.search(probe))


# What a credential looks like as a value, as opposed to a word about one. `is_sensitive` answers
# the broader question for Jev, whose call can be skipped at no cost. A handoff to another agent
# cannot be skipped that cheaply, and "how do I hash a password?" holds no password.
#
# After a password word, any value that is not an ordinary word counts, letters-only included:
# "my password is sunshine" leaves no doubt. After a key or token word, or in NAME=value, a value
# counts only when it does not look like code, because `cache_key = f(x)` is everyday code.
_PASSWORD_LABEL = r"(?:password|passwd|passphrase|wachtwoord|pincode|pin)"
_KEY_LABEL = r"(?:secret|token|api[_ -]?key|access[_ -]?key|private[_ -]?key|client[_ -]?secret)"
_LABELLED_VALUE = re.compile(                                       # JSON, YAML, PHP, prose
    r"(?i)[\"']?\b(" + _PASSWORD_LABEL + r"|" + _KEY_LABEL + r")\b[\"']?"
    r"(?:\s*(?:=>|[:=])\s*|\s+(?:is|was|=|:)\s*:?\s*)[\"']?([^\s\"',;})]+)")
_PASSWORD_SENTENCE = re.compile(                                    # "wachtwoord van mijn bank is X"
    r"(?i)\b" + _PASSWORD_LABEL + r"\b[^.\n?!]{0,40}?\b(?:is|was|luidt|=)\b\s*:?\s*[\"']?([^\s\"',;.!?)]+)")
_NAMED_VALUE = re.compile(                                          # DB_PASS=, MYSQL_PWD=, pwd=
    r"(?i)\b(?:[a-z0-9]+[_-])*(?:pass|pwd|passwd|password|secret|token|key|apikey|auth|credentials?)\b"
    r"\s*[:=]\s*[\"']?([^\s\"',;}]+)")
_URL_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@")
_AUTH_HEADER = re.compile(r"(?i)\b(?:proxy-)?authorization\s*:\s*[a-z]+\s+[A-Za-z0-9._~+/=-]{6,}")
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_CLI_SECRET = re.compile(
    r"(?i)(?:--password[= ]\s*(?![-$])\S+"
    r"|\bmysql\w*\b[^\n]*?\s-p(?![\s$])\S+"
    r"|\bsshpass\s+-p\s*(?!\$)\S+"
    r"|\bcurl\b[^\n]*?\s(?:-u|--user)\s+[^\s:]+:(?!\$)\S+)")
_PRIVATE_KEY_BLOCK = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")
_CODE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?")
_NOT_A_VALUE = {
    "true", "false", "none", "null", "nil", "undefined", "required", "incorrect", "invalid", "missing", "expired",
    "wrong", "correct", "empty", "blank", "set", "unset", "reset", "changed", "hashed", "encrypted", "stored",
    "saved", "sent", "shown", "visible", "hidden", "weak", "strong", "long", "short", "too", "not", "no", "also",
    "still", "now", "being", "the", "a", "an", "my", "your", "our", "their", "this", "that", "same", "different",
    "used", "needed", "checked", "validated", "rejected", "accepted", "ok", "okay", "fine", "leaked", "compromised",
    "secure", "insecure", "niet", "fout", "goed", "verkeerd", "leeg", "gewijzigd", "veranderd", "verlopen",
    "vergeten", "onjuist", "juist", "te", "nog", "ook", "het", "de", "een", "mijn", "jouw", "sterk", "zwak", "kort",
    "lang", "opgeslagen", "versleuteld"}


def _value_like(value: str, strict: bool) -> bool:
    """Could this be the credential itself. `strict` after key and token words: not if it looks like code."""
    value = value.strip().strip("\"'")
    if len(value) < 4 or value.lower() in _NOT_A_VALUE:
        return False
    if value.startswith(("$", "{{", "<", "%", "os.", "process.env", "env.", "config.", "settings.")):
        return False
    if not strict:
        return True
    if len(value) < 6 or any(char in value for char in "([{"):
        return False
    return not (_CODE_NAME.fullmatch(value) and not any(char.isdigit() for char in value))


def has_secret_value(text: str) -> bool:
    """A credential itself in the text, in any of the forms people and programs write one."""
    probe = normalize(text)
    for shape in (_TOKEN_SHAPES, _PRIVATE_KEY_BLOCK, _BEARER_VALUE, _AUTH_HEADER, _URL_USERINFO, _CLI_SECRET):
        if shape.search(probe):
            return True
    for match in _LABELLED_VALUE.finditer(probe):
        if _value_like(match.group(2), strict=not re.fullmatch(r"(?i)" + _PASSWORD_LABEL, match.group(1))):
            return True
    if any(_value_like(match.group(1), strict=False) for match in _PASSWORD_SENTENCE.finditer(probe)):
        return True
    if any(_value_like(match.group(1), strict=True) for match in _NAMED_VALUE.finditer(probe)):
        return True
    return any(_mask_credential(match) == "[secret]" for match in _HIGH_ENTROPY.finditer(probe))


# Stricter than the redaction rules, on purpose: a class that fires on an epoch timestamp or a
# `git@github.com` URL turns an ordinary coding turn private. Redaction still masks those shapes
# in whatever leaves; this only decides the class.
_CONTACT_EMAIL = re.compile(
    r"(?<![\w.%+-])(?!git@)[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}\b")
_CONTACT_PHONE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?(?:\d[\s.-]?){7,13}\d"   # international, with a plus
    r"|(?:\+31|0031|0)6[\s-]?\d{8}"                        # Dutch mobile
    r"|0\d{1,3}[\s-]\d{6,8}"                               # Dutch landline, with its separator
    r"|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?!\d)")          # North American, with separators


def has_contact_details(text: str) -> bool:
    """An email address or a phone number as a person writes one: data about a person."""
    probe = _LONG_HEX.sub(" ", _TRACKING.sub(" ", normalize(text)))
    return bool(_CONTACT_EMAIL.search(probe) or _CONTACT_PHONE.search(probe) or _NL_MOBILE.search(probe))


# The IBAN registry: country and length. A wrong or missing entry only means a miss for that
# country: the check digits decide, so a longer table adds no false positives.
_IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BI": 27,
    "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DJ": 27, "DK": 18, "DO": 28,
    "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FK": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23,
    "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27,
    "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25,
    "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MN": 20, "MR": 27, "MT": 31, "MU": 30, "NI": 28, "NL": 18,
    "NO": 15, "OM": 23, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "RU": 33,
    "SA": 24, "SC": 31, "SD": 18, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "SO": 23, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20, "YE": 30,
}
# Written whole, or in groups of four split by one space or hyphen, and ending at a word
# boundary: the next word never joins the number, which is what let "es2023 so that …" pass.
_IBAN_CANDIDATE = re.compile(
    r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){2,7}(?:[ -]?[A-Z0-9]{1,3})?(?![A-Z0-9])")


def has_iban(text: str) -> bool:
    """An IBAN from the registry, grouped or not, in any case.

    The check digits decide, as Luhn does for cards: a code that merely looks like an IBAN
    almost never passes mod 97.
    """
    for match in _IBAN_CANDIDATE.finditer(normalize(text).upper()):
        compact = re.sub(r"[ -]", "", match.group(0))
        if len(compact) != _IBAN_LENGTHS.get(compact[:2], -1) or not compact.isascii():
            continue
        if int("".join(str(int(char, 36)) for char in compact[4:] + compact[:4])) % 97 == 1:
            return True
    return False


def redact(text: str, limit: int = 4000) -> str:
    out = normalize(text)
    # Hold tracking numbers aside so the phone rule cannot reach their digits, then put
    # them back before any truncation can cut a placeholder in half.
    held: List[str] = []

    def _hold(match: "re.Match[str]") -> str:
        held.append(match.group(0))
        return f"\x00TRK{len(held) - 1}\x00"

    out = _TRACKING.sub(_hold, out)
    out = _TOKEN_SHAPES.sub("[secret]", out)
    # Keep the variable's NAME (it is often the useful signal) and mask only its value.
    out = _SECRET_ASSIGNMENT.sub(lambda m: re.split(r"[:=]", m.group(0), maxsplit=1)[0].rstrip() + "=[secret]", out)
    out = _LONG_HEX.sub("[hex]", out)
    # After [hex], so a digest stays a digest, and before the phone rules, so a spaced
    # card number is not shredded into a "phone" and a remainder.
    out = _HIGH_ENTROPY.sub(_mask_credential, out)
    out = _CARD.sub(_mask_card, out)
    out = _EMAIL.sub("[email]", out)
    out = _NL_MOBILE.sub("[phone]", out)
    out = _PHONE.sub("[phone]", out)
    out = _INTL_PHONE.sub("[phone]", out)
    for index, value in enumerate(held):
        out = out.replace(f"\x00TRK{index}\x00", value)
    if len(out) > limit:
        half = limit // 2
        out = out[:half] + "\n[…]\n" + out[-half:]
    return out
