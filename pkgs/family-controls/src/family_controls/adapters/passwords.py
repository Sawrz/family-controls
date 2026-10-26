"""Argon2id password hashing; credentials never enter the policy model."""

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class ArgonPasswords:
    def __init__(self) -> None:
        self.hasher = PasswordHasher()
        self.capacity = threading.BoundedSemaphore(2)

    def hash(self, password: str) -> str:
        with self.capacity:
            return self.hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        try:
            with self.capacity:
                return self.hasher.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False
