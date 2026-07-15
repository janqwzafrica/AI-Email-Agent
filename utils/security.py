import bcrypt

BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12


def _to_secret_bytes(secret):
    return (secret or "").encode("utf-8")


def hash_secret(secret):
    secret_bytes = _to_secret_bytes(secret)
    if len(secret_bytes) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Secret must be {BCRYPT_MAX_BYTES} bytes or fewer.")

    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(secret_bytes, salt).decode("ascii")


def check_secret_hash(secret_hash, secret):
    secret_bytes = _to_secret_bytes(secret)
    if len(secret_bytes) > BCRYPT_MAX_BYTES:
        return False

    return bcrypt.checkpw(secret_bytes, secret_hash.encode("ascii"))
