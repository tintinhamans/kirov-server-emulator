"""
Tests for the Peerchat commands the Generals / Zero Hour client (GameSpy peer + chat SDK) uses
on top of the RA3 subset:

SETKEY/GETKEY, SETCHANKEY/GETCHANKEY, SETGROUP, LIST, WHOIS, KICK, ATM, GETCKEY with the
b_* wildcard and request-ordered values, and +l / +k enforcement on JOIN.

Wire shapes follow the SDK handlers in src/chat/chathandlers.c (parameter counts in brackets
include the leading target nick):
  700 GETKEY      [4] me nick cookie :\\v1\\v2
  701 ENDGETKEY   [4] me target cookie :End of GETKEY
  702 GETCKEY     [5] me channel nick cookie :\\v1\\v2   (cookie BCAST for pushes)
  704 GETCHANKEY  [4] me channel cookie :\\v1\\v2        (cookie BCAST for pushes)
  322 LIST        [4] me channel numusers :topic
  311 WHOISUSER   [6] me nick user host * :realname
"""

import pytest

pytest_plugins = ("pytest_asyncio",)

from app.models.irc_types import IRCChannel, IRCMessage
from app.models.peerchat_state import irc_channels, irc_clients, irc_clients_lock
from app.servers.peerchat_handlers import IRCFactory
from app.servers.peerchat_server import IRCClient
from app.test.test_peerchat_server import MockStreamReader, MockStreamWriter

CHANNEL = "#GSP!ccgenzh!XroomX"
GROUP_CHANNEL = "#GPG!597"


def make_client(nick: str, profile_id: int, channels: tuple[str, ...] = ()) -> IRCClient:
    """Create a registered client, join it to the given channels and register it globally."""
    writer = MockStreamWriter()
    client = IRCClient(MockStreamReader([]), writer, ("127.0.0.1", 40000 + profile_id))
    client.user.nickname = nick
    client.user.username = f"XtestX|{profile_id}"
    client.user.realname = f"real{nick}"
    with irc_clients_lock:
        irc_clients[nick] = client
    for name in channels:
        channel = irc_channels.setdefault(name, IRCChannel(name=name))
        if not channel.users:
            channel.operators.add(nick)
        channel.users.add(nick)
        channel.user_stats.setdefault(nick, {})
        client.user.channels.add(name)
    return client


def written(client: IRCClient) -> str:
    return client.writer.get_all_written().decode()


@pytest.fixture(autouse=True)
def clean_state():
    irc_channels.clear()
    with irc_clients_lock:
        irc_clients.clear()
    yield
    irc_channels.clear()
    with irc_clients_lock:
        irc_clients.clear()


class TestSETKEYGETKEY:
    """Global user keys (the non-broadcast way the peer SDK publishes player stats)."""

    @pytest.mark.asyncio
    async def test_setkey_stores_keys_without_reply(self):
        alice = make_client("alice", 1)

        await IRCFactory.handle(alice, IRCMessage.parse("SETKEY :\\locale\\en\\wins\\3"))

        assert alice.user.stats == {"locale": "en", "wins": "3"}
        assert written(alice) == ""

    @pytest.mark.asyncio
    async def test_getkey_single_nick_replies_700_then_701(self):
        alice = make_client("alice", 1)
        bob = make_client("bob", 2)
        await IRCFactory.handle(bob, IRCMessage.parse("SETKEY :\\locale\\en\\wins\\3"))

        await IRCFactory.handle(alice, IRCMessage.parse("GETKEY bob 001 0 :\\wins\\locale\\missing"))

        out = written(alice).split("\r\n")
        assert out[0] == ":s 700 alice bob 001 :\\3\\en\\"
        assert out[1] == ":s 701 alice bob 001 :End of GETKEY"

    @pytest.mark.asyncio
    async def test_getkey_channel_replies_for_every_member(self):
        alice = make_client("alice", 1, (GROUP_CHANNEL,))
        bob = make_client("bob", 2, (GROUP_CHANNEL,))
        await IRCFactory.handle(alice, IRCMessage.parse("SETKEY :\\wins\\1"))
        await IRCFactory.handle(bob, IRCMessage.parse("SETKEY :\\wins\\2"))

        await IRCFactory.handle(alice, IRCMessage.parse(f"GETKEY {GROUP_CHANNEL} 002 0 :\\wins"))

        out = written(alice)
        assert ":s 700 alice alice 002 :\\1" in out
        assert ":s 700 alice bob 002 :\\2" in out
        assert out.rstrip().endswith(f":s 701 alice {GROUP_CHANNEL} 002 :End of GETKEY")

    @pytest.mark.asyncio
    async def test_getkey_unknown_nick_sends_401_and_end(self):
        alice = make_client("alice", 1)

        await IRCFactory.handle(alice, IRCMessage.parse("GETKEY ghost 003 0 :\\wins"))

        out = written(alice)
        assert ":s 401 alice ghost" in out
        assert "701" in out


class TestChannelKeys:
    """Channel keys: SETCHANKEY / GETCHANKEY."""

    @pytest.mark.asyncio
    async def test_setchankey_then_getchankey(self):
        alice = make_client("alice", 1, (CHANNEL,))

        await IRCFactory.handle(alice, IRCMessage.parse(f"SETCHANKEY {CHANNEL} :\\b_mapname\\Alpine\\private\\x"))
        alice.writer.written.clear()
        await IRCFactory.handle(alice, IRCMessage.parse(f"GETCHANKEY {CHANNEL} 004 0 :\\private\\b_mapname"))

        assert written(alice).strip() == f":s 704 alice {CHANNEL} 004 :\\x\\Alpine"

    @pytest.mark.asyncio
    async def test_getchankey_star_returns_all_pairs(self):
        alice = make_client("alice", 1, (CHANNEL,))
        await IRCFactory.handle(alice, IRCMessage.parse(f"SETCHANKEY {CHANNEL} :\\a\\1\\b_b\\2"))
        alice.writer.written.clear()

        await IRCFactory.handle(alice, IRCMessage.parse(f"GETCHANKEY {CHANNEL} 005 0 :*"))

        reply = written(alice).strip()
        assert reply.startswith(f":s 704 alice {CHANNEL} 005 :")
        assert "\\a\\1" in reply
        assert "\\b_b\\2" in reply

    @pytest.mark.asyncio
    async def test_setchankey_broadcasts_only_b_keys_to_members(self):
        alice = make_client("alice", 1, (CHANNEL,))
        bob = make_client("bob", 2, (CHANNEL,))

        await IRCFactory.handle(alice, IRCMessage.parse(f"SETCHANKEY {CHANNEL} :\\b_mapname\\Alpine\\private\\x"))

        pushed = written(bob).strip()
        assert pushed == f":s 704 {CHANNEL} {CHANNEL} BCAST :\\b_mapname\\Alpine"

    @pytest.mark.asyncio
    async def test_setchankey_requires_membership(self):
        alice = make_client("alice", 1)
        irc_channels[CHANNEL] = IRCChannel(name=CHANNEL)

        await IRCFactory.handle(alice, IRCMessage.parse(f"SETCHANKEY {CHANNEL} :\\a\\1"))

        assert " 404 " in written(alice)
        assert irc_channels[CHANNEL].channel_keys == {}

    @pytest.mark.asyncio
    async def test_getchankey_unknown_channel(self):
        alice = make_client("alice", 1)

        await IRCFactory.handle(alice, IRCMessage.parse("GETCHANKEY #nope 006 0 :\\a"))

        assert " 403 " in written(alice)

    @pytest.mark.asyncio
    async def test_setgroup_tags_channel(self):
        alice = make_client("alice", 1, (CHANNEL,))

        await IRCFactory.handle(alice, IRCMessage.parse(f"SETGROUP {CHANNEL} 597"))

        assert irc_channels[CHANNEL].group == "597"


class TestGETCKEYExtensions:
    """GETCKEY returns values in request order and supports the b_* wildcard."""

    @pytest.mark.asyncio
    async def test_values_follow_request_order(self):
        alice = make_client("alice", 1, (GROUP_CHANNEL,))
        irc_channels[GROUP_CHANNEL].user_stats["alice"] = {"b_flags": "s", "b_wins": "9"}

        await IRCFactory.handle(alice, IRCMessage.parse(f"GETCKEY {GROUP_CHANNEL} alice 007 0 :\\b_flags\\username\\b_wins"))

        assert f":s 702 alice {GROUP_CHANNEL} alice 007 :\\s\\XtestX|1\\9" in written(alice)

    @pytest.mark.asyncio
    async def test_broadcast_wildcard_appends_pairs(self):
        alice = make_client("alice", 1, (GROUP_CHANNEL,))
        irc_channels[GROUP_CHANNEL].user_stats["alice"] = {"b_flags": "s", "b_wins": "9", "other": "x"}

        await IRCFactory.handle(alice, IRCMessage.parse(f"GETCKEY {GROUP_CHANNEL} alice 008 0 :\\username\\b_*"))

        reply = written(alice).split("\r\n")[0]
        assert reply.startswith(f":s 702 alice {GROUP_CHANNEL} alice 008 :\\XtestX|1\\b_flags\\s\\b_wins\\9")
        assert "other" not in reply

    @pytest.mark.asyncio
    async def test_setckey_does_not_broadcast_non_broadcast_keys(self):
        alice = make_client("alice", 1, (GROUP_CHANNEL,))
        bob = make_client("bob", 2, (GROUP_CHANNEL,))

        await IRCFactory.handle(alice, IRCMessage.parse(f"SETCKEY {GROUP_CHANNEL} alice :\\plain\\1"))

        assert written(bob) == ""
        assert irc_channels[GROUP_CHANNEL].user_stats["alice"]["plain"] == "1"


class TestLISTCommand:
    @pytest.mark.asyncio
    async def test_list_all_channels(self):
        alice = make_client("alice", 1, (GROUP_CHANNEL, CHANNEL))
        irc_channels[CHANNEL].topic = "Room One"

        await IRCFactory.handle(alice, IRCMessage.parse("LIST"))

        out = written(alice)
        assert ":s 321 alice" in out
        assert f":s 322 alice {CHANNEL} 1 :Room One" in out
        assert f":s 322 alice {GROUP_CHANNEL} 1 :" in out
        assert out.rstrip().endswith(":s 323 alice :End of /LIST")

    @pytest.mark.asyncio
    async def test_list_mask_filters(self):
        alice = make_client("alice", 1, (GROUP_CHANNEL, CHANNEL))

        await IRCFactory.handle(alice, IRCMessage.parse("LIST #GSP!ccgenzh!*"))

        out = written(alice)
        assert CHANNEL in out
        assert GROUP_CHANNEL not in out


class TestWHOISCommand:
    @pytest.mark.asyncio
    async def test_whois_known_user(self):
        alice = make_client("alice", 1)
        make_client("bob", 2, (GROUP_CHANNEL,))

        await IRCFactory.handle(alice, IRCMessage.parse("WHOIS bob"))

        out = written(alice).split("\r\n")
        assert out[0] == ":s 311 alice bob XtestX|2 127.0.0.1 * :realbob"
        assert out[1] == f":s 319 alice bob :{GROUP_CHANNEL}"
        assert out[2] == ":s 318 alice bob :End of /WHOIS list"

    @pytest.mark.asyncio
    async def test_whois_unknown_user_still_ends(self):
        alice = make_client("alice", 1)

        await IRCFactory.handle(alice, IRCMessage.parse("WHOIS ghost"))

        out = written(alice)
        assert ":s 401 alice ghost" in out
        assert ":s 318 alice ghost :End of /WHOIS list" in out


class TestKICKCommand:
    @pytest.mark.asyncio
    async def test_operator_kicks_member(self):
        host = make_client("host", 1, (CHANNEL,))
        guest = make_client("guest", 2, (CHANNEL,))

        await IRCFactory.handle(host, IRCMessage.parse(f"KICK {CHANNEL} guest :bye"))

        assert f"KICK {CHANNEL} guest :bye" in written(guest)
        assert f"KICK {CHANNEL} guest :bye" in written(host)
        assert "guest" not in irc_channels[CHANNEL].users
        assert CHANNEL not in guest.user.channels

    @pytest.mark.asyncio
    async def test_non_operator_cannot_kick(self):
        make_client("host", 1, (CHANNEL,))
        guest = make_client("guest", 2, (CHANNEL,))
        other = make_client("other", 3, (CHANNEL,))

        await IRCFactory.handle(guest, IRCMessage.parse(f"KICK {CHANNEL} other :no"))

        assert " 482 " in written(guest)
        assert "other" in irc_channels[CHANNEL].users
        assert written(other) == ""

    @pytest.mark.asyncio
    async def test_kick_user_not_in_channel(self):
        host = make_client("host", 1, (CHANNEL,))
        make_client("away", 2)

        await IRCFactory.handle(host, IRCMessage.parse(f"KICK {CHANNEL} away"))

        assert " 441 " in written(host)


class TestATMCommand:
    @pytest.mark.asyncio
    async def test_atm_to_user_keeps_atm_command(self):
        alice = make_client("alice", 1)
        bob = make_client("bob", 2)

        await IRCFactory.handle(alice, IRCMessage.parse("ATM bob :NAT/ PORT0 9301 7F000001"))

        assert written(bob).strip() == ":alice!XtestX|1@* ATM bob :NAT/ PORT0 9301 7F000001"

    @pytest.mark.asyncio
    async def test_utm_still_uses_utm(self):
        alice = make_client("alice", 1)
        bob = make_client("bob", 2)

        await IRCFactory.handle(alice, IRCMessage.parse("UTM bob :NAT/ PROBED1"))

        assert " UTM bob :NAT/ PROBED1" in written(bob)

    @pytest.mark.asyncio
    async def test_atm_to_channel(self):
        alice = make_client("alice", 1, (CHANNEL,))
        bob = make_client("bob", 2, (CHANNEL,))

        await IRCFactory.handle(alice, IRCMessage.parse(f"ATM {CHANNEL} :SL/ slotlist"))

        assert f" ATM {CHANNEL} :SL/ slotlist" in written(bob)
        assert written(alice) == ""


class TestStagingRoomJoinRules:
    """+l and +k set by the staging room host are enforced on JOIN."""

    @pytest.mark.asyncio
    async def test_limit_blocks_extra_player(self):
        host = make_client("host", 1, (CHANNEL,))
        guest = make_client("guest", 2)
        late = make_client("late", 3)

        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} -i-p-s-m-n-t+l+e 2"))
        await IRCFactory.handle(guest, IRCMessage.parse(f"JOIN {CHANNEL}"))
        await IRCFactory.handle(late, IRCMessage.parse(f"JOIN {CHANNEL}"))

        assert irc_channels[CHANNEL].limit == 2
        assert "guest" in irc_channels[CHANNEL].users
        assert "late" not in irc_channels[CHANNEL].users
        assert ":s 471 late" in written(late)

    @pytest.mark.asyncio
    async def test_password_required(self):
        host = make_client("host", 1, (CHANNEL,))
        guest = make_client("guest", 2)

        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} +k secret"))
        await IRCFactory.handle(guest, IRCMessage.parse(f"JOIN {CHANNEL} wrong"))
        assert ":s 475 guest" in written(guest)
        assert "guest" not in irc_channels[CHANNEL].users

        await IRCFactory.handle(guest, IRCMessage.parse(f"JOIN {CHANNEL} secret"))
        assert "guest" in irc_channels[CHANNEL].users

    @pytest.mark.asyncio
    async def test_removing_password_and_limit(self):
        host = make_client("host", 1, (CHANNEL,))
        guest = make_client("guest", 2)

        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} +k secret"))
        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} -k secret"))
        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} +l 1"))
        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} -l"))
        await IRCFactory.handle(guest, IRCMessage.parse(f"JOIN {CHANNEL}"))

        assert irc_channels[CHANNEL].key == ""
        assert irc_channels[CHANNEL].limit == 0
        assert "guest" in irc_channels[CHANNEL].users

    @pytest.mark.asyncio
    async def test_mode_change_is_relayed_to_other_members(self):
        host = make_client("host", 1, (CHANNEL,))
        guest = make_client("guest", 2, (CHANNEL,))

        await IRCFactory.handle(host, IRCMessage.parse(f"MODE {CHANNEL} +l 4"))

        assert f" MODE {CHANNEL} +l :4" in written(guest)
        assert written(host) == ""
