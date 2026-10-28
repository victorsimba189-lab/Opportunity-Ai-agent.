from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scout import timeutil
from scout.db import DB


@pytest.fixture
def freeze(monkeypatch):
    """freeze("2026-10-29T04:00:00Z") pins the clock (UTC instant)."""
    def _freeze(iso: str):
        instant = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        monkeypatch.setattr(timeutil, "_clock", lambda: instant)
        return instant
    _freeze("2026-10-29T03:45:00Z")  # 09:15 IST default
    return _freeze


@pytest.fixture
def db(tmp_path, freeze):
    d = DB(tmp_path / "test.db")
    yield d
    d.close()


class FakeTelegram:
    def __init__(self, fail_on: int | None = None):
        self.sent: list[tuple[str, str]] = []
        self.fail_on = fail_on
        self.updates: list[dict] = []
        self.acked: int | None = None
        self.commands = None

    def send_message(self, chat_id, text, html=True):
        from scout.telegram import TelegramError

        if self.fail_on is not None and len(self.sent) == self.fail_on:
            self.fail_on = None
            raise TelegramError("sendMessage: 502 Bad Gateway")
        self.sent.append((str(chat_id), text))
        return 1000 + len(self.sent)

    def get_updates(self, offset=None, limit=100):
        if offset is not None:
            self.acked = offset
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
            return []
        return list(self.updates)

    def set_commands(self, commands):
        self.commands = commands


@pytest.fixture
def tg():
    return FakeTelegram()
