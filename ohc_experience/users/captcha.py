"""A small arithmetic captcha for the sign-up form, held in the session.

No third-party service, no images: the challenge is "What is 7 + 3?", the
answer lives server-side in the session, and a wrong answer issues a fresh
challenge so a guess never gets a second try at the same sum. Enough to stop
casual form-fillers, which is what an invite-only sandbox needs.
"""

from __future__ import annotations

import secrets

SESSION_KEY = "signup_captcha"
MAX_OPERAND = 9


def issue_challenge(session) -> str:
    """Store a new challenge in the session and return its question."""
    first = secrets.randbelow(MAX_OPERAND) + 1
    second = secrets.randbelow(MAX_OPERAND) + 1
    if second > first:
        first, second = second, first
    if secrets.randbelow(2):
        question, answer = f"{first} + {second}", first + second
    else:
        question, answer = f"{first} - {second}", first - second
    session[SESSION_KEY] = {"question": question, "answer": answer}
    return question


def current_question(session) -> str:
    """The question to print, issuing one if the session has none yet."""
    challenge = session.get(SESSION_KEY) if session is not None else None
    if challenge is None:
        return issue_challenge(session) if session is not None else ""
    return str(challenge["question"])


def verify(session, answer) -> bool:
    """True when the answer matches. Either way the challenge is spent."""
    challenge = session.get(SESSION_KEY) if session is not None else None
    if challenge is None:
        return False
    try:
        correct = int(answer) == int(challenge["answer"])
    except TypeError, ValueError:
        correct = False
    issue_challenge(session)
    return correct
