"""
Tests for the GameSpy Protocol Server \\newuser\\ command and the GP password obfuscation.

\\newuser\\ is what the Generals / Zero Hour in-game "Create Account" dialog sends
(SDK gpConnectNewUser). The same connection logs in afterwards with \\login\\.
"""

import hashlib
from unittest.mock import MagicMock, patch

from app.servers.gp_server import GpServer
from app.util.gp_password import decode_gp_password, encode_gp_password

# Reference vectors produced by compiling the SDK's own gpiEncodeString
# (Util_RandSeed/Util_RandInt/B64Encode from gsplatformutil.c) with GP_XOR_SEED.
SDK_PASSWORD_VECTORS = {
    "a": "dw__",
    "pw12": "ZoXFwA__",
    "secret123": "ZZeXgE6v3Z5K",
    "p@ss w0rd!": "ZrKHgQus3N4dLg__",
    "abcdefghijklmnopqrst": "d5CXlk69i8QQZRWGXsPXBK0bC6U_",
}


class MockSessionManager:
    """Minimal session manager for constructing a GpServer."""

    def register_user(self, sesskey, protocol):
        pass


class MockTransport:
    """Mock transport for testing."""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def get_extra_info(self, name):
        if name == "peername":
            return ("127.0.0.1", 12345)
        return None

    def close(self):
        pass


class TestGpPasswordEncoding:
    """Test the SDK's XOR + alternate base64 password obfuscation."""

    def test_round_trip(self):
        for password in ("a", "pw12", "secret123", "p@ss w0rd!", "x" * 30):
            assert decode_gp_password(encode_gp_password(password)) == password

    def test_alternate_alphabet_has_no_standard_symbols(self):
        # Standard base64 symbols '+', '/', '=' are replaced by '[', ']', '_'
        for password in ("a", "ab", "abc", "zzzzzzzz", "?>?>?>"):
            encoded = encode_gp_password(password)
            assert "+" not in encoded
            assert "/" not in encoded
            assert "=" not in encoded

    def test_sdk_vectors(self):
        for password, encoded in SDK_PASSWORD_VECTORS.items():
            assert encode_gp_password(password) == encoded
            assert decode_gp_password(encoded) == password

    def test_decode_garbage_returns_empty(self):
        assert decode_gp_password("!!!") == ""


class TestGpServerNewUser:
    """Test the \\newuser\\ command handler."""

    def setup_method(self):
        """Set up test fixtures."""
        self.gp_server = GpServer(MockSessionManager())
        self.gp_server.transport = MockTransport()
        self.gp_server.peername = ("127.0.0.1", 12345)
        self.gp_server.server_challenge = "ABCDEFGHIJ"
        self.password = "pw12"
        self.request = {
            "email": "new@test.com",
            "nick": "NewPlayer",
            "passwordenc": encode_gp_password(self.password),
            "productid": "10011",
            "gamename": "ccgenzh",
            "namespaceid": "0",
            "partnerid": "0",
            "id": "1",
        }

    def test_newuser_creates_account(self):
        """A new email and nickname creates the user with the decoded password and returns \\nur\\."""
        mock_user = MagicMock()
        mock_user.id = 7
        mock_persona = MagicMock()
        mock_persona.id = 70
        mock_persona.user_id = 7

        with (
            patch("app.servers.gp_server.get_user_by_email", return_value=None),
            patch("app.servers.gp_server.get_user_by_username", return_value=None),
            patch("app.servers.gp_server.get_persona_by_name", side_effect=[None, mock_persona]),
            patch("app.servers.gp_server.create_new_user", return_value=mock_user) as mock_create,
        ):
            response = self.gp_server.handle_newuser(self.request)

        created = mock_create.call_args[0][1]
        assert created.username == "NewPlayer"
        assert created.email == "new@test.com"
        assert created.password == self.password
        assert response.startswith("\\nur\\\\userid\\7\\profileid\\70\\id\\1")
        assert response.endswith("\\final\\")

    def test_newuser_existing_email_wrong_password(self):
        """An existing email with a different password is rejected."""
        mock_user = MagicMock()
        mock_user.gamespy_password_md5 = hashlib.md5(b"other").hexdigest()

        with patch("app.servers.gp_server.get_user_by_email", return_value=mock_user):
            response = self.gp_server.handle_newuser(self.request)

        assert response.startswith("\\error\\\\err\\")
        assert "already exists" in response

    def test_newuser_existing_email_same_password_adds_nick(self):
        """An existing email with the matching password may add another nickname."""
        mock_user = MagicMock()
        mock_user.id = 7
        mock_user.gamespy_password_md5 = hashlib.md5(self.password.encode()).hexdigest()
        mock_persona = MagicMock()
        mock_persona.id = 71

        with (
            patch("app.servers.gp_server.get_user_by_email", return_value=mock_user),
            patch("app.servers.gp_server.get_persona_by_name", return_value=None),
            patch("app.servers.gp_server.create_persona_for_user", return_value=mock_persona) as mock_persona_create,
        ):
            response = self.gp_server.handle_newuser(self.request)

        mock_persona_create.assert_called_once_with(self.gp_server.db_session, mock_user, "NewPlayer")
        assert "\\profileid\\71" in response

    def test_newuser_nick_taken(self):
        """A nickname already used by another account is rejected."""
        with (
            patch("app.servers.gp_server.get_user_by_email", return_value=None),
            patch("app.servers.gp_server.get_user_by_username", return_value=MagicMock()),
        ):
            response = self.gp_server.handle_newuser(self.request)

        assert "\\error\\" in response
        assert "nickname is already in use" in response

    def test_newuser_missing_fields(self):
        """Missing email, nick or password yields an error."""
        response = self.gp_server.handle_newuser({"id": "1"})

        assert "\\error\\" in response
        assert "Invalid account data" in response
