"""
IRC command factory/handler.
Processes IRC commands following the Factory pattern used in FESL.
"""

import fnmatch
import time
from typing import TYPE_CHECKING

from app.config.app_settings import app_config
from app.models.fesl_types import GAMESPY_GAME_KEY_MAP
from app.models.irc_types import GameSpyCommand, IRCCommand, IRCMessage, IRCNumeric
from app.models.peerchat_state import irc_channels, irc_clients, irc_clients_lock, join_channel, part_channel
from app.util.logging_helper import get_logger
from app.util.peerchat_crypt import PeerchatCipherFactory

if TYPE_CHECKING:
    from app.servers.peerchat_server import IRCClient

logger = get_logger(__name__)


class IRCFactory:
    """
    Factory for handling IRC commands (similar to AcctFactory and FsysFactory).
    """

    @staticmethod
    async def handle(client: "IRCClient", message: IRCMessage):
        """
        Main entry point for handling IRC commands.

        Args:
            client: IRCClient that sent the command
            message: Parsed IRCMessage
        """
        command = message.command.upper()

        try:
            # Route to appropriate handler
            match command:
                # Connection registration
                case IRCCommand.NICK:
                    await IRCFactory.handle_nick(client, message)
                case IRCCommand.USER:
                    await IRCFactory.handle_user(client, message)
                case IRCCommand.PASS:
                    await IRCFactory.handle_pass(client, message)

                # Channel operations
                case IRCCommand.JOIN:
                    await IRCFactory.handle_join(client, message)
                case IRCCommand.PART:
                    await IRCFactory.handle_part(client, message)
                case IRCCommand.TOPIC:
                    await IRCFactory.handle_topic(client, message)
                case IRCCommand.NAMES:
                    await IRCFactory.handle_names(client, message)
                case IRCCommand.MODE:
                    await IRCFactory.handle_mode(client, message)

                # Messaging
                case IRCCommand.PRIVMSG:
                    await IRCFactory.handle_privmsg(client, message)
                case IRCCommand.NOTICE:
                    await IRCFactory.handle_notice(client, message)

                # Connection management
                case IRCCommand.PING:
                    await IRCFactory.handle_ping(client, message)
                case IRCCommand.PONG:
                    await IRCFactory.handle_pong(client, message)
                case IRCCommand.QUIT:
                    await IRCFactory.handle_quit(client, message)

                # GameSpy extensions
                case GameSpyCommand.CRYPT:
                    await IRCFactory.handle_crypt(client, message)
                case GameSpyCommand.CDKEY:
                    await IRCFactory.handle_cdkey(client, message)
                case GameSpyCommand.GETCKEY:
                    await IRCFactory.handle_getckey(client, message)
                case GameSpyCommand.SETCKEY:
                    await IRCFactory.handle_setckey(client, message)
                case GameSpyCommand.UTM:
                    await IRCFactory.handle_utm(client, message)
                case GameSpyCommand.USRIP:
                    await IRCFactory.handle_usrip(client, message)
                case GameSpyCommand.ATM:
                    await IRCFactory.handle_utm(client, message)
                case GameSpyCommand.SETKEY:
                    await IRCFactory.handle_setkey(client, message)
                case GameSpyCommand.GETKEY:
                    await IRCFactory.handle_getkey(client, message)
                case GameSpyCommand.SETCHANKEY:
                    await IRCFactory.handle_setchankey(client, message)
                case GameSpyCommand.GETCHANKEY:
                    await IRCFactory.handle_getchankey(client, message)
                case GameSpyCommand.SETGROUP:
                    await IRCFactory.handle_setgroup(client, message)

                # Channel / user queries
                case IRCCommand.LIST:
                    await IRCFactory.handle_list(client, message)
                case IRCCommand.WHOIS:
                    await IRCFactory.handle_whois(client, message)
                case IRCCommand.KICK:
                    await IRCFactory.handle_kick(client, message)

                # User queries
                case IRCCommand.WHO:
                    await IRCFactory.handle_who(client, message)

                case _:
                    # Unknown command
                    await client.send_numeric(IRCNumeric.ERR_UNKNOWNCOMMAND, command, "Unknown command")
                    logger.warning(f"Unknown IRC command from {client.addr}: {command}")

        except Exception as e:
            logger.error(f"Error handling IRC command {command}: {e}")

    # --- Connection Registration Commands ---

    @staticmethod
    async def handle_pass(client: "IRCClient", message: IRCMessage):
        """
        Handle PASS command (password/session token).
        GameSpy uses this to pass the session token from FESL authentication.
        """
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "PASS", "Not enough parameters")
            return

        # Store session token for later authentication
        client.user.session_token = message.params[0]
        logger.debug(f"IRC client {client.addr} sent PASS")

    @staticmethod
    async def handle_nick(client: "IRCClient", message: IRCMessage):
        """Handle NICK command."""
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        new_nick = message.params[0]

        # Validate nickname
        if not new_nick or len(new_nick) > 30:
            await client.send_numeric(IRCNumeric.ERR_ERRONEUSNICKNAME, new_nick, "Erroneous nickname")
            return

        # Check if nickname is already in use
        with irc_clients_lock:
            if new_nick in irc_clients and irc_clients[new_nick] != client:
                await client.send_numeric(IRCNumeric.ERR_NICKNAMEINUSE, new_nick, "Nickname is already in use")
                return

            # Remove old nickname
            old_nick = client.user.nickname
            if old_nick and old_nick in irc_clients:
                del irc_clients[old_nick]

            # Set new nickname
            client.user.nickname = new_nick
            irc_clients[new_nick] = client

        logger.info(f"IRC client {client.addr} set nickname to {new_nick}")

        # If this completes registration, send welcome
        if client.user.is_registered() and not client.user.authenticated:
            await IRCFactory.send_welcome(client)

    @staticmethod
    async def handle_user(client: "IRCClient", message: IRCMessage):
        """
        Handle USER command.
        Format: USER <username> <mode> <unused> :<realname>
        GameSpy format: USER <encoded_ip|profile_id> <local_ip> <server> :<auth_token>
        Example: USER random|123 127.0.0.1 peerchat.gamespy.com :ff70dbb93425a35226fd1fe8f052623c
        """
        if len(message.params) < 4:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "USER", "Not enough parameters")
            return

        if client.user.username:
            await client.send_numeric(IRCNumeric.ERR_ALREADYREGISTRED, "You may not reregister")
            return

        username = message.params[0]
        client.user.username = username
        client.user.realname = message.params[3]

        # Parse GameSpy-specific data from username field
        # Format: <encoded_ip>|<profile_id>
        if "|" in username:
            parts = username.split("|", 1)
            # First part is encoded IP (used for identification)
            # Second part is profile ID
            try:
                client.user.profile_id = int(parts[1])
            except ValueError:
                pass  # Not a valid profile ID, ignore

        logger.info(
            f"IRC client {client.addr} set USER to {client.user.username} (profile_id: {client.user.profile_id})"
        )

        # If this completes registration, send welcome
        if client.user.is_registered() and not client.user.authenticated:
            await IRCFactory.send_welcome(client)

    @staticmethod
    async def send_welcome(client: "IRCClient"):
        """Send welcome messages to newly registered client."""
        server_name = "s"  # Short server name like real GameSpy
        if hasattr(app_config, "irc"):
            server_name = getattr(app_config.irc, "server_name", "s")

        nick = client.user.nickname

        # RPL_WELCOME (001)
        await client.send_numeric(IRCNumeric.RPL_WELCOME, f"Welcome to the Matrix {nick}")

        # RPL_YOURHOST (002)
        await client.send_numeric(IRCNumeric.RPL_YOURHOST, f"Your host is {server_name}, running version 1.0")

        # RPL_CREATED (003)
        await client.send_numeric(IRCNumeric.RPL_CREATED, "This server was created for Red Alert 3")

        # RPL_MYINFO (004) - format: server version user_modes channel_modes
        await client.send_numeric(IRCNumeric.RPL_MYINFO, server_name, "1.0", "iq", "biklmnopqustvhe")

        # MOTD sequence
        await client.send_numeric(IRCNumeric.RPL_MOTDSTART, "- (M) Message of the day - ")
        await client.send_numeric(IRCNumeric.RPL_MOTD, "- Welcome to GameSpy")
        await client.send_numeric(IRCNumeric.RPL_ENDOFMOTD, "End of MOTD command")

        client.user.authenticated = True
        logger.info(f"IRC client {client.user.nickname} completed registration")

    # --- GameSpy Extension Commands ---

    @staticmethod
    async def handle_crypt(client: "IRCClient", message: IRCMessage):
        """
        Handle CRYPT command (GameSpy encryption initialization).
        Format: CRYPT <cipher> <version> <gamekey>
        Example: CRYPT des 1 redalertpc
        """
        if len(message.params) < 3:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "CRYPT", "Not enough parameters")
            return

        message.params[0]
        message.params[1]
        peerchat_game_name = message.params[2]

        # Look up the gamekey from config using the peerchat game name
        config_key = GAMESPY_GAME_KEY_MAP.get(peerchat_game_name)
        game_key = app_config.game.gamekeys.get(config_key, "") if config_key else ""
        if not game_key:
            logger.warning("No gamekey found for peerchat game: %s", peerchat_game_name)

        # Initialize cipher factory
        client.cipher_factory = PeerchatCipherFactory(game_key)
        client.send_cipher = client.cipher_factory.getCipher()
        client.recv_cipher = client.cipher_factory.getCipher()

        # Send encryption challenges (705)
        await client.send_numeric(
            IRCNumeric.RPL_CRYPT_CHALLENGE, client.recv_cipher.challenge, client.send_cipher.challenge
        )

        # Enable encryption for all future messages
        client.encryption_enabled = True

        logger.info(f"IRC client {client.user.nickname} enabled encryption")

    @staticmethod
    async def handle_cdkey(client: "IRCClient", message: IRCMessage):
        """
        Handle CDKEY command (GameSpy authentication).
        Format: CDKEY <hash>
        The hash is computed from session token + challenge.
        """
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "CDKEY", "Not enough parameters")
            return

        cdkey_hash = message.params[0]
        client.user.cdkey_hash = cdkey_hash

        # TODO: Verify hash against session token
        # For now, accept all connections

        # Send authentication success (706)
        await client.send_numeric(IRCNumeric.RPL_CDKEY_OK, "1", "Authenticated")

        # Send PING to start keep-alive cycle
        ping_message = IRCMessage(command="PING", params=["s"])
        await client.send_message(ping_message)

        logger.info(f"IRC client {client.user.nickname} authenticated with CDKEY")

    @staticmethod
    async def handle_getckey(client: "IRCClient", message: IRCMessage):
        r"""
        Handle GETCKEY command (get user stats in channel).
        Format: GETCKEY <channel> <target|*> <requestid> <flags> :<keys>
        Response: 702 <requester> <channel> <target_nick> <requestid> \<values>...
        """
        if len(message.params) < 4:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "GETCKEY", "Not enough parameters")
            return

        channel_name = message.params[0]
        target = message.params[1]  # * for all users, or specific nickname
        request_id = message.params[2]
        # params[3] is flags (usually 0)
        keys_string = message.params[4] if len(message.params) > 4 else ""

        # Parse requested keys (format: \key1\key2\key3...)
        if keys_string.startswith("\\"):
            keys_string = keys_string[1:]
        requested_keys = [k for k in keys_string.split("\\") if k]

        # Check if channel exists
        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        channel = irc_channels[channel_name]

        # Determine which users to query
        if target == "*":
            # All users in channel
            target_nicks = list(channel.users)
        else:
            target_nicks = [target] if target in channel.users else []

        # Process each target user
        for target_nick in target_nicks:
            stats = channel.user_stats.get(target_nick, {})
            response_string = IRCFactory._build_ckey_values(target_nick, stats, requested_keys)

            # Send 702 response: :s 702 <requester> <channel> <target> <requestid> \values\
            await client.send_numeric(
                IRCNumeric.RPL_GETCKEY_RESPONSE, channel_name, target_nick, request_id, response_string
            )

        # Send end of GETCKEY (703)
        await client.send_numeric(IRCNumeric.RPL_GETCKEY_END, channel_name, request_id, "End of GETCKEY")

    @staticmethod
    async def handle_setckey(client: "IRCClient", message: IRCMessage):
        """
        Handle SETCKEY command (set user stats in channel).
        Format: SETCKEY <channel> <nickname> :<keys>
        Keys format: \\key\value\\key\value...
        """
        if len(message.params) < 3:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "SETCKEY", "Not enough parameters")
            return

        channel_name = message.params[0]
        target_nick = message.params[1]
        keys_string = message.params[2]

        # Check if channel exists
        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        channel = irc_channels[channel_name]

        # Verify the user can only set their own keys
        if target_nick != client.user.nickname:
            await client.send_numeric(IRCNumeric.ERR_CHANOPRIVSNEEDED, channel_name, "Cannot set keys for other users")
            return

        if client.user.nickname not in channel.users:
            await client.send_numeric(IRCNumeric.ERR_CANNOTSENDTOCHAN, channel_name, "Cannot send to channel")
            return

        # Parse key-value pairs (format: \key\value\key\value...)
        # Strip leading backslash if present
        if keys_string.startswith("\\"):
            keys_string = keys_string[1:]

        parts = keys_string.split("\\")
        stats = {}
        for i in range(0, len(parts) - 1, 2):
            key = parts[i]
            value = parts[i + 1] if i + 1 < len(parts) else ""
            stats[key] = value

        # Update user stats
        if client.user.nickname not in channel.user_stats:
            channel.user_stats[client.user.nickname] = {}

        channel.user_stats[client.user.nickname].update(stats)

        # Broadcast only the broadcast (b_) keys to all channel members (702 with BCAST)
        # Format: :s 702 #channel #channel nickname BCAST \key\value...
        bcast = {k: v for k, v in stats.items() if k.startswith("b_")}
        if bcast:
            bcast_string = "".join(f"\\{k}\\{v}" for k, v in bcast.items())
            broadcast_message = IRCMessage(
                command=IRCNumeric.RPL_GETCKEY_RESPONSE,
                params=[channel_name, channel_name, client.user.nickname, "BCAST", bcast_string],
                prefix="s",
            )

            await client.broadcast_to_channel(channel_name, broadcast_message, exclude_self=False)

        logger.debug(f"IRC client {client.user.nickname} set CKEY in {channel_name}: {stats}")

    @staticmethod
    async def handle_utm(client: "IRCClient", message: IRCMessage):
        """
        Handle UTM command (GameSpy unified text message).
        Format: UTM <target> :<message>
        Target can be:
        - A channel (#...)
        - A single nickname
        - Comma-separated nicknames (e.g., "sokiee,sokie")
        Used for game-specific data like map selection, player info, NAT negotiation, etc.
        """
        if len(message.params) < 2:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "UTM", "Not enough parameters")
            return

        target = message.params[0]
        text = message.params[1]
        # ATM is the authenticated twin of UTM and is relayed with the same shape
        wire_command = "ATM" if message.command.upper() == "ATM" else "UTM"

        if target.startswith("#"):
            # Channel message - broadcast to all members

            if target not in irc_channels:
                await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, target, "No such channel")
                return

            if target not in client.user.channels:
                await client.send_numeric(IRCNumeric.ERR_CANNOTSENDTOCHAN, target, "Cannot send to channel")
                return

            # Build UTM message with sender prefix
            utm_message = IRCMessage(command=wire_command, params=[target, text], prefix=client.user.get_prefix())
            await client.broadcast_to_channel(target, utm_message, exclude_self=True)
        else:
            # Direct message to one or more users (comma-separated)

            # Split comma-separated targets
            targets = [t.strip() for t in target.split(",") if t.strip()]

            for target_nick in targets:
                with irc_clients_lock:
                    if target_nick not in irc_clients:
                        # Skip non-existent users silently for multi-target
                        if len(targets) == 1:
                            await client.send_numeric(IRCNumeric.ERR_NOSUCHNICK, target_nick, "No such nick/channel")
                        continue

                    target_client = irc_clients[target_nick]

                # Build message with individual target nick (not the comma list)
                utm_message = IRCMessage(
                    command=wire_command, params=[target_nick, text], prefix=client.user.get_prefix()
                )
                try:
                    await target_client.send_message(utm_message)
                except Exception as e:
                    logger.warning(f"Error sending UTM to {target_nick}: {e}")

        logger.debug(f"IRC client {client.user.nickname} sent UTM to {target}: {text}")

    @staticmethod
    async def handle_usrip(client: "IRCClient", message: IRCMessage):
        """
        Handle USRIP command (GameSpy - get user's IP address).
        Response format: :s 302 <blank> :=+@<ip>
        """
        ip = client.user.hostname or client.addr[0]

        # Send 302 RPL_USERHOST response in GameSpy format
        # Format: :s 302  :=+@<ip>
        response = IRCMessage(command=IRCNumeric.RPL_USERHOST, params=["", f"=+@{ip}"], prefix="s")
        await client.send_message(response)

        logger.debug(f"IRC client {client.user.nickname} requested USRIP, returning IP {ip}")

    @staticmethod
    async def handle_who(client: "IRCClient", message: IRCMessage):
        """
        Handle WHO command (get information about users).
        Format: WHO <channel|nickname>
        Response: 352 RPL_WHOREPLY for each user, then 315 RPL_ENDOFWHO
        """
        if len(message.params) < 1:
            # No target specified, end immediately
            await client.send_numeric(IRCNumeric.RPL_ENDOFWHO, "*", "End of /WHO list")
            return

        target = message.params[0]

        if target.startswith("#"):
            # WHO for channel
            if target not in irc_channels:
                await client.send_numeric(IRCNumeric.RPL_ENDOFWHO, target, "End of /WHO list")
                return

            channel = irc_channels[target]
            server_name = "peerchat.ea.com"
            if hasattr(app_config, "irc"):
                server_name = app_config.irc.server_name

            with irc_clients_lock:
                for nickname in channel.users:
                    if nickname in irc_clients:
                        user_client = irc_clients[nickname]
                        user = user_client.user

                        # Format: 352 <channel> <user> <host> <server> <nick> <H|G>[*][@|+] :<hopcount> <realname>
                        # H = Here, G = Gone (away), * = IRC op, @ = channel op, + = voice
                        flags = "H"
                        if channel.is_operator(nickname):
                            flags += "@"

                        await client.send_numeric(
                            IRCNumeric.RPL_WHOREPLY,
                            target,
                            user.username or "unknown",
                            user.hostname or "unknown",
                            server_name,
                            nickname,
                            flags,
                            f"0 {user.realname or nickname}",
                        )

            await client.send_numeric(IRCNumeric.RPL_ENDOFWHO, target, "End of /WHO list")

        else:
            # WHO for specific user
            with irc_clients_lock:
                if target in irc_clients:
                    user_client = irc_clients[target]
                    user = user_client.user
                    server_name = "peerchat.ea.com"
                    if hasattr(app_config, "irc"):
                        server_name = app_config.irc.server_name

                    await client.send_numeric(
                        IRCNumeric.RPL_WHOREPLY,
                        "*",
                        user.username or "unknown",
                        user.hostname or "unknown",
                        server_name,
                        target,
                        "H",
                        f"0 {user.realname or target}",
                    )

            await client.send_numeric(IRCNumeric.RPL_ENDOFWHO, target, "End of /WHO list")

    # --- Channel Commands ---

    @staticmethod
    async def handle_join(client: "IRCClient", message: IRCMessage):
        """Handle JOIN command."""
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "JOIN", "Not enough parameters")
            return

        channels = message.params[0].split(",")

        for channel_name in channels:
            if not channel_name.startswith("#"):
                await client.send_numeric(IRCNumeric.ERR_BADCHANMASK, channel_name, "Bad channel mask")
                continue

            # Enforce +l (user limit) and +k (password) set by the channel creator
            existing = irc_channels.get(channel_name)
            if existing and client.user.nickname not in existing.users:
                if existing.limit and len(existing.users) >= existing.limit:
                    await client.send_numeric(IRCNumeric.ERR_CHANNELISFULL, channel_name, "Channel is full")
                    continue
                supplied_key = message.params[1] if len(message.params) > 1 else ""
                if existing.key and supplied_key != existing.key:
                    await client.send_numeric(IRCNumeric.ERR_BADCHANNELKEY, channel_name, "Bad channel key")
                    continue

            # Join channel
            await join_channel(client, channel_name)

            # Send JOIN notification to all channel members
            join_message = IRCMessage(command="JOIN", params=[channel_name], prefix=client.user.get_prefix())
            await client.broadcast_to_channel(channel_name, join_message, exclude_self=False)

            # Send NAMES list
            await IRCFactory.send_names(client, channel_name)

    @staticmethod
    async def handle_part(client: "IRCClient", message: IRCMessage):
        """Handle PART command."""
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "PART", "Not enough parameters")
            return

        channel_name = message.params[0]
        reason = message.params[1] if len(message.params) > 1 else ""

        if channel_name not in client.user.channels:
            await client.send_numeric(IRCNumeric.ERR_NOTONCHANNEL, channel_name, "You're not on that channel")
            return

        # Send PART notification to all channel members
        part_message = IRCMessage(
            command="PART", params=[channel_name, reason] if reason else [channel_name], prefix=client.user.get_prefix()
        )
        await client.broadcast_to_channel(channel_name, part_message, exclude_self=False)

        # Remove from channel
        await part_channel(client, channel_name, reason)

    @staticmethod
    async def handle_names(client: "IRCClient", message: IRCMessage):
        """Handle NAMES command."""
        if len(message.params) < 1:
            return

        channel_name = message.params[0]
        await IRCFactory.send_names(client, channel_name)

    @staticmethod
    async def send_names(client: "IRCClient", channel_name: str):
        """Send NAMES list for a channel."""

        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        channel = irc_channels[channel_name]

        # Build names list with operator prefixes
        names = []
        for nickname in channel.users:
            if channel.is_operator(nickname):
                names.append(f"@{nickname}")
            else:
                names.append(nickname)

        # Send RPL_NAMREPLY (353)
        await client.send_numeric(
            IRCNumeric.RPL_NAMREPLY,
            "=",  # Public channel
            channel_name,
            " ".join(names),
        )

        # Send RPL_ENDOFNAMES (366)
        await client.send_numeric(IRCNumeric.RPL_ENDOFNAMES, channel_name, "End of /NAMES list")

    @staticmethod
    async def handle_topic(client: "IRCClient", message: IRCMessage):
        """Handle TOPIC command."""
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "TOPIC", "Not enough parameters")
            return

        channel_name = message.params[0]

        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        channel = irc_channels[channel_name]

        # Get topic
        if len(message.params) == 1:
            if channel.topic:
                await client.send_numeric(IRCNumeric.RPL_TOPIC, channel_name, channel.topic)
            else:
                await client.send_numeric(IRCNumeric.RPL_NOTOPIC, channel_name, "No topic is set")
        else:
            # Set topic (requires operator)
            if not channel.is_operator(client.user.nickname):
                await client.send_numeric(IRCNumeric.ERR_CHANOPRIVSNEEDED, channel_name, "You're not channel operator")
                return

            new_topic = message.params[1]
            channel.topic = new_topic

            # Broadcast topic change
            topic_message = IRCMessage(
                command="TOPIC", params=[channel_name, new_topic], prefix=client.user.get_prefix()
            )
            await client.broadcast_to_channel(channel_name, topic_message, exclude_self=False)

    @staticmethod
    async def handle_mode(client: "IRCClient", message: IRCMessage):
        """
        Handle MODE command for channels.
        Query: MODE #channel - returns current mode
        Set: MODE #channel <modes> [params] - sets mode (requires op)
        Example modes: +l 6, -i-p-s-m-n-t+l+e 6
        """
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "MODE", "Not enough parameters")
            return

        target = message.params[0]

        if target.startswith("#"):
            # Channel mode

            if target not in irc_channels:
                await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, target, "No such channel")
                return

            channel = irc_channels[target]

            if len(message.params) == 1:
                # Query mode - return current mode
                # Format: :s 324 nick #channel + <params>
                mode_str = channel.modes if channel.modes else "+"
                await client.send_numeric(IRCNumeric.RPL_CHANNELMODEIS, target, mode_str, "")
            else:
                # Set mode - requires operator (or first joiner on GSP channels)
                mode_changes = message.params[1]
                mode_params = " ".join(message.params[2:]) if len(message.params) > 2 else ""

                # For GSP channels, the creator (first user) is always operator
                # For regular channels, check operator status
                is_gsp = target.startswith("#GSP!")
                if not is_gsp and not channel.is_operator(client.user.nickname):
                    await client.send_numeric(IRCNumeric.ERR_CHANOPRIVSNEEDED, target, "You're not channel operator")
                    return

                # Parse and update channel modes
                # Store the full mode string for now (simplified)
                channel.modes = mode_changes
                if mode_params:
                    channel.modes += " " + mode_params

                IRCFactory._apply_mode_changes(channel, mode_changes, message.params[2:])

                # Tell the other members; the setter does not need an echo
                mode_message = IRCMessage(
                    command="MODE", params=[target, mode_changes, *message.params[2:]], prefix=client.user.get_prefix()
                )
                await client.broadcast_to_channel(target, mode_message, exclude_self=True)
                logger.debug(f"Channel {target} mode set to: {channel.modes}")
        else:
            # User mode
            if len(message.params) == 1:
                # Query user mode - return current mode (221 RPL_UMODEIS)
                user_mode = getattr(client.user, "mode", "+")
                await client.send_numeric("221", user_mode)
            else:
                # Set user mode (e.g., MODE sokiee +q)
                # Store the mode and accept silently (no response needed)
                mode_change = message.params[1]
                if not hasattr(client.user, "mode"):
                    client.user.mode = ""
                # Simple mode tracking - just store it
                if mode_change.startswith("+"):
                    client.user.mode += mode_change[1:]
                elif mode_change.startswith("-"):
                    for char in mode_change[1:]:
                        client.user.mode = client.user.mode.replace(char, "")
                logger.debug(f"User {target} mode set to: {client.user.mode}")

    # --- GameSpy key / channel commands (Generals / Zero Hour peer SDK) ---

    @staticmethod
    def _parse_key_pairs(keys_string: str) -> dict[str, str]:
        r"""Parse \key\value\key\value into a dict (leading backslash optional)."""
        if keys_string.startswith("\\"):
            keys_string = keys_string[1:]
        parts = keys_string.split("\\")
        return {parts[i]: (parts[i + 1] if i + 1 < len(parts) else "") for i in range(0, len(parts), 2) if parts[i]}

    @staticmethod
    def _build_ckey_values(nick: str, stats: dict[str, str], requested_keys: list[str]) -> str:
        r"""
        Build the value string of a 702 reply: one \value per requested key, in request order.
        The special key 'username' yields the encoded username and 'b_*' appends every
        broadcast key of the user as \key\value pairs after the requested values.
        """
        values: list[str] = []
        want_broadcast = False
        for key in requested_keys:
            if key == "b_*":
                want_broadcast = True
            elif key == "username":
                with irc_clients_lock:
                    target_client = irc_clients.get(nick)
                values.append((target_client.user.username if target_client else None) or nick)
            else:
                values.append(stats.get(key, ""))

        result = "".join("\\" + v for v in values)
        if want_broadcast:
            result += "".join(f"\\{k}\\{v}" for k, v in stats.items() if k.startswith("b_"))
        return result or "\\"

    @staticmethod
    def _apply_mode_changes(channel, mode_string: str, params: list[str]):
        """Track the parts of a MODE change that affect JOIN: +l <limit> and +k <password>."""
        sign = "+"
        remaining = list(params)
        for char in mode_string:
            if char in "+-":
                sign = char
            elif char == "l":
                if sign == "+" and remaining:
                    try:
                        channel.limit = max(0, int(remaining.pop(0)))
                    except ValueError:
                        channel.limit = 0
                else:
                    channel.limit = 0
            elif char == "k":
                if remaining:
                    password = remaining.pop(0)
                    channel.key = password if sign == "+" else ""
                elif sign == "-":
                    channel.key = ""

    @staticmethod
    async def handle_setkey(client: "IRCClient", message: IRCMessage):
        r"""
        Handle SETKEY command (set global user keys).
        Format: SETKEY :\key\value\key\value...
        No reply; the keys are read back with GETKEY.
        """
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "SETKEY", "Not enough parameters")
            return

        client.user.stats.update(IRCFactory._parse_key_pairs(message.params[-1]))

    @staticmethod
    async def handle_getkey(client: "IRCClient", message: IRCMessage):
        r"""
        Handle GETKEY command (get global user keys of a user or of everyone in a channel).
        Format: GETKEY <nick|#channel> <cookie> <flags> :\key1\key2...
        Response: one 700 <nick> <cookie> :\v1\v2 per user, then 701 <target> <cookie> :End of GETKEY
        """
        if len(message.params) < 3:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "GETKEY", "Not enough parameters")
            return

        target = message.params[0]
        cookie = message.params[1]
        keys_string = message.params[3] if len(message.params) > 3 else ""
        if keys_string.startswith("\\"):
            keys_string = keys_string[1:]
        requested_keys = [k for k in keys_string.split("\\") if k]

        if target.startswith("#"):
            if target not in irc_channels:
                await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, target, "No such channel")
                return
            nicks = sorted(irc_channels[target].users)
        else:
            nicks = [target]

        for nick in nicks:
            with irc_clients_lock:
                target_client = irc_clients.get(nick)
            if target_client is None:
                if not target.startswith("#"):
                    await client.send_numeric(IRCNumeric.ERR_NOSUCHNICK, nick, "No such nick/channel")
                continue
            values = "".join("\\" + target_client.user.stats.get(k, "") for k in requested_keys) or "\\"
            await client.send_numeric(IRCNumeric.RPL_GETKEY_RESPONSE, nick, cookie, values)

        await client.send_numeric(IRCNumeric.RPL_GETKEY_END, target, cookie, "End of GETKEY")

    @staticmethod
    async def handle_setchankey(client: "IRCClient", message: IRCMessage):
        r"""
        Handle SETCHANKEY command (set channel keys).
        Format: SETCHANKEY <channel> :\key\value...
        Broadcast (b_) keys are pushed to the channel as 704 BCAST.
        """
        if len(message.params) < 2:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "SETCHANKEY", "Not enough parameters")
            return

        channel_name = message.params[0]
        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        channel = irc_channels[channel_name]
        if client.user.nickname not in channel.users:
            await client.send_numeric(IRCNumeric.ERR_CANNOTSENDTOCHAN, channel_name, "Cannot send to channel")
            return

        pairs = IRCFactory._parse_key_pairs(message.params[1])
        channel.channel_keys.update(pairs)

        bcast = {k: v for k, v in pairs.items() if k.startswith("b_")}
        if bcast:
            bcast_string = "".join(f"\\{k}\\{v}" for k, v in bcast.items())
            broadcast_message = IRCMessage(
                command=IRCNumeric.RPL_GETCHANKEY_RESPONSE,
                params=[channel_name, channel_name, "BCAST", bcast_string],
                prefix="s",
            )
            await client.broadcast_to_channel(channel_name, broadcast_message, exclude_self=False)

    @staticmethod
    async def handle_getchankey(client: "IRCClient", message: IRCMessage):
        r"""
        Handle GETCHANKEY command (get channel keys).
        Format: GETCHANKEY <channel> <cookie> <flags> :\key1\key2...   (or :* for every key)
        Response: 704 <channel> <cookie> :\v1\v2
        """
        if len(message.params) < 3:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "GETCHANKEY", "Not enough parameters")
            return

        channel_name = message.params[0]
        cookie = message.params[1]
        keys_string = message.params[3] if len(message.params) > 3 else ""

        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return
        channel = irc_channels[channel_name]

        if keys_string.startswith("\\"):
            keys_string = keys_string[1:]
        tokens = [k for k in keys_string.split("\\") if k]
        want_all = "*" in tokens
        want_broadcast = "b_*" in tokens
        requested_keys = [k for k in tokens if k not in ("*", "b_*")]

        result = "".join("\\" + channel.channel_keys.get(k, "") for k in requested_keys)
        if want_all or want_broadcast:
            result += "".join(f"\\{k}\\{v}" for k, v in channel.channel_keys.items() if want_all or k.startswith("b_"))

        await client.send_numeric(IRCNumeric.RPL_GETCHANKEY_RESPONSE, channel_name, cookie, result or "\\")

    @staticmethod
    async def handle_setgroup(client: "IRCClient", message: IRCMessage):
        """Handle SETGROUP command: tag a channel with a group id. Format: SETGROUP <channel> <group>"""
        if len(message.params) < 2:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "SETGROUP", "Not enough parameters")
            return

        channel_name = message.params[0]
        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        irc_channels[channel_name].group = message.params[1]

    @staticmethod
    async def handle_list(client: "IRCClient", message: IRCMessage):
        """
        Handle LIST command.
        Format: LIST [<mask>]   (mask supports * and ? wildcards)
        Response: 321, one 322 <channel> <numusers> :<topic> per match, 323
        """
        mask = message.params[0] if message.params else "*"

        await client.send_numeric(IRCNumeric.RPL_LISTSTART, "Channel", "Users  Name")
        for name, channel in sorted(irc_channels.items()):
            if mask not in ("", "*") and not fnmatch.fnmatchcase(name.lower(), mask.lower()):
                continue
            await client.send_numeric(IRCNumeric.RPL_LIST, name, str(len(channel.users)), channel.topic or "")
        await client.send_numeric(IRCNumeric.RPL_LISTEND, "End of /LIST")

    @staticmethod
    async def handle_whois(client: "IRCClient", message: IRCMessage):
        """
        Handle WHOIS command.
        Response: 311 <nick> <user> <host> * :<realname>, 319 <nick> :<channels>, 318 <nick> :End of /WHOIS list
        """
        if len(message.params) < 1:
            await client.send_numeric(IRCNumeric.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        nick = message.params[0]
        with irc_clients_lock:
            target_client = irc_clients.get(nick)

        if target_client is None:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHNICK, nick, "No such nick/channel")
        else:
            user = target_client.user
            await client.send_numeric(
                IRCNumeric.RPL_WHOISUSER,
                nick,
                user.username or "unknown",
                user.hostname or "unknown",
                "*",
                user.realname or nick,
            )
            if user.channels:
                await client.send_numeric(IRCNumeric.RPL_WHOISCHANNELS, nick, " ".join(sorted(user.channels)))

        await client.send_numeric(IRCNumeric.RPL_ENDOFWHOIS, nick, "End of /WHOIS list")

    @staticmethod
    async def handle_kick(client: "IRCClient", message: IRCMessage):
        """
        Handle KICK command (channel operator only).
        Format: KICK <channel> <nick> [:<reason>]
        The KICK is broadcast to the whole channel (including the victim) before the victim is removed.
        """
        if len(message.params) < 2:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "KICK", "Not enough parameters")
            return

        channel_name = message.params[0]
        victim = message.params[1]
        reason = message.params[2] if len(message.params) > 2 else client.user.nickname

        if channel_name not in irc_channels:
            await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        channel = irc_channels[channel_name]
        if client.user.nickname not in channel.users:
            await client.send_numeric(IRCNumeric.ERR_NOTONCHANNEL, channel_name, "You're not on that channel")
            return
        if not channel.is_operator(client.user.nickname):
            await client.send_numeric(IRCNumeric.ERR_CHANOPRIVSNEEDED, channel_name, "You're not channel operator")
            return
        if victim not in channel.users:
            await client.send_numeric(
                IRCNumeric.ERR_USERNOTINCHANNEL, victim, channel_name, "They aren't on that channel"
            )
            return

        kick_message = IRCMessage(
            command="KICK", params=[channel_name, victim, reason], prefix=client.user.get_prefix()
        )
        await client.broadcast_to_channel(channel_name, kick_message, exclude_self=False)

        with irc_clients_lock:
            victim_client = irc_clients.get(victim)
        if victim_client is not None:
            await part_channel(victim_client, channel_name, reason)
        else:
            channel.users.discard(victim)
            channel.operators.discard(victim)
            channel.user_stats.pop(victim, None)

    # --- Messaging Commands ---

    @staticmethod
    async def handle_privmsg(client: "IRCClient", message: IRCMessage):
        """Handle PRIVMSG command."""
        if len(message.params) < 2:
            await client.send_numeric(IRCNumeric.ERR_NEEDMOREPARAMS, "PRIVMSG", "Not enough parameters")
            return

        target = message.params[0]
        text = message.params[1]

        if not text:
            await client.send_numeric(IRCNumeric.ERR_NOTEXTTOSEND, "No text to send")
            return

        # Channel message
        if target.startswith("#"):
            if target not in irc_channels:
                await client.send_numeric(IRCNumeric.ERR_NOSUCHCHANNEL, target, "No such channel")
                return

            if target not in client.user.channels:
                await client.send_numeric(IRCNumeric.ERR_CANNOTSENDTOCHAN, target, "Cannot send to channel")
                return

            # Broadcast to channel
            privmsg_message = IRCMessage(command="PRIVMSG", params=[target, text], prefix=client.user.get_prefix())
            await client.broadcast_to_channel(target, privmsg_message, exclude_self=True)

        # Private message
        else:
            with irc_clients_lock:
                if target not in irc_clients:
                    await client.send_numeric(IRCNumeric.ERR_NOSUCHNICK, target, "No such nick/channel")
                    return

                target_client = irc_clients[target]

            privmsg_message = IRCMessage(command="PRIVMSG", params=[target, text], prefix=client.user.get_prefix())
            await target_client.send_message(privmsg_message)

    @staticmethod
    async def handle_notice(client: "IRCClient", message: IRCMessage):
        """
        Handle NOTICE command (similar to PRIVMSG but no auto-reply).
        Used for game countdown timer messages like:
        NOTICE #GSP!redalert3pc!Mzhhzq1h0M :Type,LAN:GameStartTimerSingular,5
        """
        if len(message.params) < 2:
            return  # NOTICE shouldn't generate error responses

        target = message.params[0]
        text = message.params[1]

        if not text:
            return

        # Channel notice
        if target.startswith("#"):
            if target not in irc_channels:
                return  # Silently ignore

            if target not in client.user.channels:
                return  # Silently ignore

            # Broadcast to channel as NOTICE (not PRIVMSG)
            notice_message = IRCMessage(command="NOTICE", params=[target, text], prefix=client.user.get_prefix())
            await client.broadcast_to_channel(target, notice_message, exclude_self=True)

        # Private notice
        else:
            with irc_clients_lock:
                if target not in irc_clients:
                    return  # Silently ignore

                target_client = irc_clients[target]

            notice_message = IRCMessage(command="NOTICE", params=[target, text], prefix=client.user.get_prefix())
            await target_client.send_message(notice_message)

    # --- Connection Management ---

    @staticmethod
    async def handle_ping(client: "IRCClient", message: IRCMessage):
        """Handle PING command."""
        server = message.params[0] if message.params else "s"

        pong_message = IRCMessage(command="PONG", params=[server], prefix="s")
        await client.send_message(pong_message)

    @staticmethod
    async def handle_pong(client: "IRCClient", message: IRCMessage):
        """Handle PONG command."""
        client.last_pong_time = time.time()

    @staticmethod
    async def handle_quit(client: "IRCClient", message: IRCMessage):
        """Handle QUIT command."""
        reason = message.params[0] if message.params else "Client quit"

        # Broadcast QUIT to all channels
        quit_message = IRCMessage(command="QUIT", params=[reason], prefix=client.user.get_prefix())

        for channel_name in list(client.user.channels):
            await client.broadcast_to_channel(channel_name, quit_message, exclude_self=True)

        # Disconnect
        client.disconnect()
