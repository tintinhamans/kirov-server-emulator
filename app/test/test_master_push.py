"""
Tests for list updates pushed to open master server connections.

The Generals / Zero Hour lobby asks for the game list once (SDK option PUSH_UPDATES) and keeps the connection
open; rooms created later reach it only as pushed messages (SDK ProcessPushServer / ProcessDeleteServer).
"""

import struct

import pytest

from app.servers.query_master_parsing import (
    DELETE_SERVER_MESSAGE,
    PUSH_SERVER_MESSAGE,
    GameEntry,
    build_delete_server_message,
    build_game_entry,
    build_push_server_message,
    ip_to_bytes,
)
from app.servers.query_master_tcp import QueryMasterHandler, QueryMasterServer
from app.servers.sessions import GameSessionRegistry


class StubCipher:
    """Leaves the bytes alone so the plaintext of a push can be checked."""

    def encrypt(self, data: bytes) -> bytes:
        return data


class StubTransport:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def get_extra_info(self, name):
        return ("127.0.0.1", 40000) if name == "peername" else None

    def close(self):
        pass


def make_game() -> GameEntry:
    return GameEntry(
        public_ip="127.0.0.1",
        public_port=6501,
        private_ip="192.168.1.5",
        private_port=6501,
        fields={"hostname": "host", "gamemode": "openstaging", "groupid": "571", "mapname": "Alpine"},
    )


def make_subscriber(conditions=None, fields=None) -> QueryMasterServer:
    server = QueryMasterServer()
    server.transport = StubTransport()
    server.peername = ("127.0.0.1", 40000)
    server.cipher = StubCipher()
    server.push_fields = fields or ["hostname", "mapname"]
    server.push_conditions = conditions or []
    return server


class TestPushMessages:
    def test_entry_without_popular_list_has_no_prefix(self):
        game = make_game()

        entry = build_game_entry(game, ["hostname", "mapname"], popular_list=False)

        assert entry.endswith(b"host\x00Alpine\x00")
        assert b"\xffhost" not in entry

    def test_entry_with_popular_list_keeps_the_inline_marker(self):
        entry = build_game_entry(make_game(), ["hostname"], popular_list=True)

        assert entry.endswith(b"\xffhost\x00")

    def test_push_server_message_framing(self):
        message = build_push_server_message(make_game(), ["hostname"])

        length = struct.unpack("!H", message[:2])[0]
        assert length == len(message)
        assert message[2] == PUSH_SERVER_MESSAGE
        # flags, public ip and public port come first, as in a list entry
        assert message[3] == 0x7E
        assert message[4:8] == ip_to_bytes("127.0.0.1")
        assert message[8:10] == struct.pack("!H", 6501)

    def test_delete_message_framing(self):
        message = build_delete_server_message(make_game())

        assert message == struct.pack("!H", 9) + bytes([DELETE_SERVER_MESSAGE]) + ip_to_bytes(
            "127.0.0.1"
        ) + struct.pack("!H", 6501)


class TestSubscribers:
    def test_push_game_writes_an_update(self):
        subscriber = make_subscriber()

        subscriber.push_game("update", make_game())

        assert len(subscriber.transport.written) == 1
        assert subscriber.transport.written[0][2] == PUSH_SERVER_MESSAGE

    def test_push_game_writes_a_delete(self):
        subscriber = make_subscriber()

        subscriber.push_game("delete", make_game())

        assert subscriber.transport.written[0][2] == DELETE_SERVER_MESSAGE

    def test_filter_is_applied(self):
        handler = QueryMasterHandler()
        QueryMasterServer.set_handler(handler)
        from app.servers.query_master_parsing import parse_filter_string

        subscriber = make_subscriber(conditions=parse_filter_string("(groupid=999)"))

        subscriber.push_game("update", make_game())

        assert subscriber.transport.written == []

    def test_on_game_event_reaches_subscribers_and_connection_lost_removes_them(self):
        QueryMasterServer._subscribers.clear()
        subscriber = make_subscriber()
        QueryMasterServer._subscribers.add(subscriber)

        QueryMasterServer.on_game_event("update", make_game())
        assert len(subscriber.transport.written) == 1

        subscriber.connection_lost(None)
        QueryMasterServer.on_game_event("update", make_game())
        assert len(subscriber.transport.written) == 1

    def test_only_generals_games_subscribe(self):
        handler = QueryMasterHandler()
        QueryMasterServer.set_handler(handler)
        QueryMasterServer._subscribers.clear()
        server = QueryMasterServer()
        server.transport = StubTransport()
        server.peername = ("127.0.0.1", 40000)

        class Request:
            game_name = "redalert3pc"
            fields = ["hostname", "gamemode"]
            filter_string = "(groupid=571)"
            tail = struct.pack("!I", 4)

        handler.last_request = Request()
        handler.last_cipher = StubCipher()
        server._maybe_subscribe(handler)
        assert server not in QueryMasterServer._subscribers

        Request.game_name = "ccgenzh"
        server._maybe_subscribe(handler)
        assert server in QueryMasterServer._subscribers
        # the subscription must not hide the push method
        assert callable(server.push_game)
        server.push_game("update", make_game())
        assert len(server.transport.written) == 1
        QueryMasterServer._subscribers.clear()

    def test_group_list_does_not_subscribe(self):
        handler = QueryMasterHandler()
        QueryMasterServer.set_handler(handler)
        QueryMasterServer._subscribers.clear()
        server = QueryMasterServer()
        server.peername = ("127.0.0.1", 40000)

        class Request:
            game_name = "ccgenzh"
            fields = ["hostname", "numwaiting", "maxwaiting"]
            filter_string = ""
            tail = struct.pack("!I", 32)

        handler.last_request = Request()
        handler.last_cipher = StubCipher()
        server._maybe_subscribe(handler)

        assert QueryMasterServer._subscribers == set()


@pytest.fixture
def registry():
    registry = GameSessionRegistry.get_instance()
    registry.clear()
    events = []

    def listener(event, game):
        events.append((event, game.fields.get("hostname")))

    GameSessionRegistry._listeners.clear()
    GameSessionRegistry.add_listener(listener)
    yield registry, events
    GameSessionRegistry._listeners.clear()
    registry.clear()


class TestRegistryEvents:
    def test_new_game_is_announced(self, registry):
        reg, events = registry

        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})

        assert events == [("update", "host")]

    def test_identical_heartbeat_is_not_announced_again(self, registry):
        reg, events = registry
        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host", "numplayers": "1"})

        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host", "numplayers": "1"})

        assert len(events) == 1

    def test_changed_heartbeat_is_announced(self, registry):
        reg, events = registry
        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host", "numplayers": "1"})

        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host", "numplayers": "2"})

        assert [e[0] for e in events] == ["update", "update"]

    def test_unregister_announces_delete(self, registry):
        reg, events = registry
        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})

        reg.unregister_game(1)

        assert events[-1] == ("delete", "host")

    def test_expiry_announces_delete(self, registry):
        reg, events = registry
        reg.register_game(1, "127.0.0.1", 6501, {"hostname": "host"})
        reg.get_game(1).last_seen = 0.0

        reg.expire_stale()

        assert events[-1] == ("delete", "host")
