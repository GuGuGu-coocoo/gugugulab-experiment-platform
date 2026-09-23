"""The one researcher-password rule shared by every account entry point (R03).

Confirmed policy (2026-09-23, requirement U07): a researcher password is at
least 6 characters long and contains at least one ASCII uppercase letter, one
ASCII lowercase letter, one ASCII digit and one visible ASCII punctuation
character each. The submitted value is used exactly as it is: it is never
trimmed, and a space never counts as the symbol class (``Aa1!aa`` is accepted,
``Aa1aaa`` is not).

Temporary passwords are generated from ``secrets``: one character of each class
is placed first, the rest is filled up to at least 24 characters and the result
is shuffled with a cryptographically secure Fisher-Yates pass, so all four
classes are guaranteed by construction instead of by sampling luck. The
generated symbol is drawn from the punctuation marks HTML never escapes, so the
one-time secret page renders the exact value.

This module stays free of Django imports so the management command, the
container initializer (Compose) and the developer tool can share it.
"""
import secrets
import string

MINIMUM_LENGTH = 6
TEMPORARY_LENGTH = 24
# The four required classes; a space is deliberately not part of SYMBOLS.
UPPERCASE = string.ascii_uppercase
LOWERCASE = string.ascii_lowercase
DIGITS = string.digits
SYMBOLS = string.punctuation
CLASSES = (UPPERCASE, LOWERCASE, DIGITS, SYMBOLS)
# The generator draws symbols from the visible ASCII punctuation marks that HTML
# never escapes, so the one-time secret page shows and copies the exact value;
# the policy above still accepts every visible ASCII punctuation mark.
HTML_ESCAPED = frozenset('"\'<>&')
SYMBOL_ALPHABET = ''.join(character for character in SYMBOLS if character not in HTML_ESCAPED)
# One stable rejection code for every policy failure, resolved to the user
# message by the calling view layer.
WEAK_CODE = 'password_weak'


def password_problem(password):
    """Return ``None`` when the password satisfies the policy, else a code.

    The value is judged exactly as submitted (no trim) and the four classes are
    matched per character, so non-ASCII characters may accompany the required
    ASCII ones but can never replace them.
    """
    if not isinstance(password, str) or len(password) < MINIMUM_LENGTH:
        return WEAK_CODE
    for characters in CLASSES:
        if not any(character in characters for character in password):
            return WEAK_CODE
    return None


def require_acceptable(password):
    """Reject with the unified policy code unless the password is acceptable."""
    from .protocol import require
    require(password_problem(password) is None, WEAK_CODE)


def generate_temporary_password(length=TEMPORARY_LENGTH):
    """A fresh temporary password of at least 24 characters, all classes present."""
    if not isinstance(length, int) or isinstance(length, bool) or length < TEMPORARY_LENGTH:
        raise ValueError('temporary passwords need at least %d characters' % TEMPORARY_LENGTH)
    alphabet = UPPERCASE + LOWERCASE + DIGITS + SYMBOL_ALPHABET
    characters = [secrets.choice(UPPERCASE), secrets.choice(LOWERCASE),
                  secrets.choice(DIGITS), secrets.choice(SYMBOL_ALPHABET)]
    characters += [secrets.choice(alphabet) for _ in range(length - len(CLASSES))]
    for index in range(len(characters) - 1, 0, -1):
        swap = secrets.randbelow(index + 1)
        characters[index], characters[swap] = characters[swap], characters[index]
    password = ''.join(characters)
    if password_problem(password) is not None:
        raise RuntimeError('temporary password construction must guarantee all four classes')
    return password
