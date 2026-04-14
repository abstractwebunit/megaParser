from cryptography.fernet import Fernet, InvalidToken


class Crypto:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError(
                "FERNET_KEY is empty. Generate one: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, value: str | None) -> bytes | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, token: bytes | None) -> str | None:
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("Cannot decrypt — FERNET_KEY mismatch or corrupted data") from e


_crypto: Crypto | None = None


def get_crypto(key: str | None = None) -> Crypto:
    global _crypto
    if _crypto is None:
        if key is None:
            from app.settings import get_settings

            key = get_settings().fernet_key
        _crypto = Crypto(key)
    return _crypto


def generate_key() -> str:
    return Fernet.generate_key().decode()
