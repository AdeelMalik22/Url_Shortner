import secrets
import string


SHORT_CODE_ALPHABET = string.ascii_letters + string.digits


def generate_code(length: int = 10) -> str:
    if length < 1:
        raise ValueError("Short-code length must be positive")

    return "".join(
        secrets.choice(SHORT_CODE_ALPHABET)
        for _ in range(length)
    )
