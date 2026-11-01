"""Persistent FirstClub session state, stored at ~/.secrets/firstclub/session.json (mode 600)."""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

SECRETS_DIR = Path(os.environ.get("FIRSTCLUB_SECRETS_DIR", Path.home() / ".secrets" / "firstclub"))
SESSION_FILE = SECRETS_DIR / "session.json"


@dataclass
class Session:
    device_id: str = field(default_factory=lambda: secrets.token_hex(8))  # Android-ID-like, stable per install
    session_id: str | None = None
    user_id: str | None = None          # login body.user_id  -> x-userId / userContext.userId
    account_user_id: str | None = None  # userAccountInformation.userId -> X-User-Id
    account_id: str | None = None       # userAccountInformation.accountId
    phone: str | None = None
    pending_otp_id: str | None = None
    # location / store
    address_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    store_id: str | None = None          # warehouseId (FCH...)
    store_location_id: str | None = None  # LOCID...
    cafe_store_id: str | None = None      # CAFE vertical store, when serviceable

    @property
    def logged_in(self) -> bool:
        return bool(self.session_id and self.user_id)

    @classmethod
    def load(cls) -> "Session":
        if SESSION_FILE.exists():
            data = json.loads(SESSION_FILE.read_text())
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        s = cls()
        s.save()
        return s

    def save(self) -> None:
        """Atomic, private write: unique temp file (0600), fsync, rename, under an exclusive lock so two
        server processes (e.g. Claude Code and Claude Desktop) don't clobber each other."""
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(SECRETS_DIR, 0o700)
        with open(SECRETS_DIR / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fd, tmp = tempfile.mkstemp(dir=SECRETS_DIR, prefix=".session.", suffix=".tmp")
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(asdict(self), f, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, SESSION_FILE)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def clear_auth(self) -> None:
        """Forget the login and everything tied to that account (address, store)."""
        self.session_id = self.user_id = self.account_user_id = self.account_id = None
        self.pending_otp_id = None
        self.address_id = self.store_id = self.store_location_id = self.cafe_store_id = None
        self.lat = self.lng = None
        self.save()
