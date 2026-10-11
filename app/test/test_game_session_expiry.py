"""
Tests for expiry of game sessions whose host stopped sending heartbeats.

A Generals / Zero Hour host that crashes never sends the exit heartbeat (statechanged=2), so its staging
room would stay in the game list forever. Hosts send a heartbeat or keepalive at least every 20 seconds.
"""

import time

import pytest

from app.servers.query_master_udp import HeartbeatMaster
from app.servers.sessions import GameSessionRegistry


@pytest.fixture
def registry():
    registry = GameSessionRegistry.get_instance()
    registry.clear()
    yield registry
    registry.clear()


class TestGameSessionExpiry:
    def test_fresh_session_is_listed(self, registry):
        registry.register_game(1, "127.0.0.1", 6501, {"hostname": "host", "groupid": "571"})

        assert len(registry.get_games()) == 1

    def test_silent_session_expires(self, registry):
        registry.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})
        registry.get_game(1).last_seen = time.time() - GameSessionRegistry.SESSION_TTL_SECONDS - 1

        assert registry.get_games() == []
        assert registry.get_game(1) is None

    def test_heartbeat_refreshes_session(self, registry):
        registry.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})
        registry.get_game(1).last_seen = time.time() - GameSessionRegistry.SESSION_TTL_SECONDS - 1

        registry.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})

        assert len(registry.get_games()) == 1

    def test_keepalive_refreshes_session(self, registry):
        registry.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})
        registry.get_game(1).last_seen = time.time() - GameSessionRegistry.SESSION_TTL_SECONDS + 5

        HeartbeatMaster()._handle_keepalive(b"", ("127.0.0.1", 6501), 1)

        assert time.time() - registry.get_game(1).last_seen < 2

    def test_expire_stale_only_removes_old_sessions(self, registry):
        registry.register_game(1, "127.0.0.1", 6501, {"hostname": "old"})
        registry.register_game(2, "127.0.0.1", 6502, {"hostname": "new"})
        registry.get_game(1).last_seen = 0.0

        removed = registry.expire_stale()

        assert removed == 1
        assert [g.fields["hostname"] for g in registry.get_games()] == ["new"]
