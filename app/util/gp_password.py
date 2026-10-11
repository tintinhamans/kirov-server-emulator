"""
GameSpy Presence (GP) password obfuscation used by the \\newuser\\ command.

The GP SDK (gpiEncodeString) XORs the password with a fixed pseudo random stream and
base64 encodes it with the "alternate" alphabet. This is obfuscation, not encryption; the
server needs the clear password to derive the MD5 used by the login challenge/response.

Reference: GameSpy SDK src/gp/gpiutility.c gpiEncodeString, src/common/gsplatformutil.c
(Util_RandSeed / Util_RandInt / B64Encode / B64Decode).
"""

GP_XOR_SEED = 0x79707367  # "gspy"

_RAN_A = 16807
_LONGRAND_MAX = 2147483647

# Alternate base64 alphabet: '+' -> '[', '/' -> ']', '=' -> '_'
_STD_TO_ALT = str.maketrans("+/=", "[]_")
_ALT_TO_STD = str.maketrans("[]_", "+/=")


def _xor_stream(count: int):
    """Yield the SDK's Util_RandInt(0, 0xFF) sequence for GP_XOR_SEED."""
    state = (GP_XOR_SEED & _LONGRAND_MAX) or 1
    for _ in range(count):
        lo = _RAN_A * (state & 0xFFFF)
        hi = _RAN_A * (state >> 16)
        lo += (hi & 0x7FFF) << 16
        if lo > _LONGRAND_MAX:
            lo &= _LONGRAND_MAX
            lo += 1
        lo += hi >> 15
        if lo > _LONGRAND_MAX:
            lo &= _LONGRAND_MAX
            lo += 1
        state = lo
        yield state % 255


def encode_gp_password(password: str) -> str:
    """Encode a clear password the way the GP SDK does (used by tests and tools)."""
    import base64

    raw = password.encode("latin-1")
    xored = bytes(b ^ r for b, r in zip(raw, _xor_stream(len(raw)), strict=True))
    return base64.b64encode(xored).decode("ascii").translate(_STD_TO_ALT)


def decode_gp_password(encoded: str) -> str:
    """Decode a \\passwordenc\\ value back to the clear password."""
    import base64

    standard = encoded.translate(_ALT_TO_STD)
    standard += "=" * (-len(standard) % 4)
    try:
        xored = base64.b64decode(standard)
    except ValueError:
        return ""
    raw = bytes(b ^ r for b, r in zip(xored, _xor_stream(len(xored)), strict=True))
    return raw.decode("latin-1")
