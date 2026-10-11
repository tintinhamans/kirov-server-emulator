"""The private address of a listed game matches the one the host's peer library hashes into the room name."""

from app.servers.sessions import GameSessionRegistry, _pick_private_ip


def test_first_rfc1918_address_wins_over_a_vpn_adapter():
    info = {"localip0": "26.161.189.179", "localip1": "172.22.40.228"}
    assert _pick_private_ip(info, "127.0.0.1") == "172.22.40.228"


def test_localip_order_is_numeric():
    info = {"localip0": "8.8.8.8", "localip2": "192.168.1.5", "localip10": "10.0.0.1"}
    assert _pick_private_ip(info, "127.0.0.1") == "192.168.1.5"


def test_falls_back_to_localip0_without_a_private_address():
    assert _pick_private_ip({"localip0": "26.1.2.3"}, "127.0.0.1") == "26.1.2.3"


def test_falls_back_to_the_source_address_without_localips():
    assert _pick_private_ip({}, "127.0.0.1") == "127.0.0.1"


def test_registered_game_uses_the_chosen_address():
    registry = GameSessionRegistry.get_instance()
    registry.clear()
    registry.register_game(1, "127.0.0.1", 6501, {"localip0": "26.161.189.179", "localip1": "172.22.40.228", "localport": "6501"})
    assert registry.get_games()[0].private_ip == "172.22.40.228"
    registry.clear()
