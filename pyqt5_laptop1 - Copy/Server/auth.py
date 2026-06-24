import bcrypt
from typing import Tuple
from datetime import datetime


class userauth:
    def hash_password(self, password):
        if not isinstance(password, str):
            password = str(password)
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
        return hashed

    def check_password(self, password, hashed):
        if isinstance(hashed, str):
            hashed = hashed.encode('utf-8')
        return bcrypt.checkpw(password.encode(), hashed)

    def __init__(self):
        self.login_attempts = {}
        self.lockout_minutes = 5
        self.max_attempts = 3

    def is_locked(self, username: str) -> Tuple[bool, str]:
        if username not in self.login_attempts:
            return False, ""

        attempts, last_attempt = self.login_attempts[username]
        if attempts >= self.max_attempts:
            minutes_passed = (datetime.now() - last_attempt).total_seconds() / 60
            if minutes_passed < self.lockout_minutes:
                remaining = self.lockout_minutes - int(minutes_passed)
                return True, f"Account is locked. Try again in {remaining} minutes."
        return False, ""

    def track_failed_attempt(self, username: str):
        now = datetime.now()
        if username in self.login_attempts:
            attempts, _ = self.login_attempts[username]
            self.login_attempts[username] = (attempts + 1, now)
        else:
            self.login_attempts[username] = (1, now)

    def reset_attempts(self, username: str):
        if username in self.login_attempts:
            del self.login_attempts[username]