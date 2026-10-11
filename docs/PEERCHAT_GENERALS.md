# Peerchat commands used by Generals / Zero Hour

Generals and Zero Hour use the GameSpy **peer** SDK on top of the **chat** SDK. They connect with
`chatConnectSecure` (no login command), publish player stats with broadcast keys, and host games as
staging rooms. This page lists the commands beyond the RA3 subset described in [PEERCHAT.md](PEERCHAT.md).

Parameter counts below include the leading target nickname (the SDK asserts on them).

## Connection

```
C: CRYPT des 1 ccgenzh              (plain; ccgenerals for Generals)
S: :s 705 * <challenge1> <challenge2>
   -- from here on both directions are encrypted with the game secret key --
C: USRIP
S: :s 302  :=+@<ip>
C: NICK <nick>
C: USER X<encoded ip>X|<profileid> 127.0.0.1 <server> :<name>
S: :s 001 <nick> :Welcome ...
```

No `CDKEY`, `LOGIN` or `LOGINPREAUTH` is sent on this path.

## Lobby and staging rooms

| Room | Channel | Created by |
|---|---|---|
| Group room | `#GPG!<groupid>` (ids from the master server room list) | first `JOIN` |
| Staging room | `#GSP!<gamename>!M<hash>M` | host's `JOIN`, then `MODE`, `TOPIC` |

The staging room hash is derived from the host's public IP, private IP and query port, so the master
server must report those values (public = heartbeat source, private = `localip0`/`localport`) exactly as
the host sent them. Joiners compute the same name from the server list entry.

## Commands

| Command | Format | Reply |
|---|---|---|
| `SETKEY` | `SETKEY :\key\value\key\value` | none |
| `GETKEY` | `GETKEY <nick or #channel> <cookie> 0 :\k1\k2` | `700 <nick> <cookie> :\v1\v2` per user, then `701 <target> <cookie> :End of GETKEY` |
| `SETCHANKEY` | `SETCHANKEY <channel> :\key\value` | none; `b_` keys are pushed to members as `704 <chan> <chan> BCAST :\b_key\value` |
| `GETCHANKEY` | `GETCHANKEY <channel> <cookie> 0 :\k1\k2` (or `:*`) | `704 <channel> <cookie> :\v1\v2` |
| `GETCKEY` | `GETCKEY <channel> <nick or *> <cookie> 0 :\k1\username\b_*` | `702` per user with values in request order (`username` = the encoded user name, `b_*` = append all broadcast keys as key/value pairs), then `703` |
| `SETCKEY` | `SETCKEY <channel> <nick> :\b_flags\sr` | only `b_` keys are broadcast as `702 ... BCAST` |
| `SETGROUP` | `SETGROUP <channel> <group>` | none |
| `LIST` | `LIST [mask]` | `321`, `322 <channel> <users> :<topic>` per match, `323` |
| `WHOIS` | `WHOIS <nick>` | `311`, `319`, `318` |
| `KICK` | `KICK <channel> <nick> :<reason>` (operator) | `KICK` broadcast to the whole channel, then the victim is removed |
| `UTM` / `ATM` | `<cmd> <nick or channel> :<text>` | relayed with the same command name; used for `NAT/` port exchange and `SL/` slot lists |
| `MODE` | `MODE <channel> -i-p-s-m-n-t+l+e 8`, `+k <password>` | `+l` and `+k` are enforced on `JOIN` with `471` / `475`; changes are relayed to the other members |

## Game list and group rooms

Both come from the master server on TCP 28910 (see [MASTER_SERVER.md](MASTER_SERVER.md)): group rooms with
the options value 32, games with options 4 and a `groupid=<id>` filter. Hosts register with the UDP heartbeat
on port 27900 (qr2).

## Account creation

The in-game *Create Account* dialog sends `\newuser\` on the GP port; see [GP_SERVER.md](GP_SERVER.md).
The password arrives obfuscated (`app/util/gp_password.py`).
