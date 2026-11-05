"""ID token check (developers.openai.com/siwc sign-in): signature against the issuer's JWKS, then iss, aud = the
issued client id, exp and nonce. RS256 only, verified with the standard library (PKCS#1 v1.5 over SHA-256).
ponytail: another alg is refused with its name in the error - add it here if OpenAI's JWKS ever uses one."""
import base64
import hashlib
import hmac
import json
import time

SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


class IdTokenError(Exception):
    pass


def b64url(s):
    s = s.encode() if isinstance(s, str) else s
    return base64.urlsafe_b64decode(s + b"=" * (-len(s) % 4))


def _int(s):
    return int.from_bytes(b64url(s), "big")


def rs256_ok(signing_input, sig, n, e):
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    want = b"\x00\x01" + b"\xff" * (k - 3 - len(SHA256_DIGEST_INFO) - 32) + b"\x00" + SHA256_DIGEST_INFO + \
        hashlib.sha256(signing_input).digest()
    return hmac.compare_digest(em, want)


def verify(token, jwks, issuer, client_id, nonce, now=None, leeway_s=60):
    """The validated claims, or IdTokenError saying why not."""
    try:
        h64, p64, s64 = token.split(".")
        header, claims, sig = json.loads(b64url(h64)), json.loads(b64url(p64)), b64url(s64)
    except ValueError as x:
        raise IdTokenError(f"malformed id token: {x}") from None
    if header.get("alg") != "RS256":
        raise IdTokenError(f"id token alg {header.get('alg')!r} is not supported (RS256 only)")
    keys = [k for k in (jwks or {}).get("keys", []) if k.get("kty") == "RSA" and
            (header.get("kid") is None or k.get("kid") == header.get("kid"))]
    if not any(rs256_ok(f"{h64}.{p64}".encode(), sig, _int(k["n"]), _int(k["e"])) for k in keys):
        raise IdTokenError("id token signature does not match the issuer's keys")
    now = time.time() if now is None else now
    aud = claims.get("aud")
    if claims.get("iss") != issuer:
        raise IdTokenError(f"id token issuer {claims.get('iss')!r} is not {issuer!r}")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise IdTokenError("id token audience is not this app's issued client id")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] + leeway_s < now:
        raise IdTokenError("id token expired")
    if claims.get("nonce") != nonce:
        raise IdTokenError("id token nonce does not match this sign-in")
    if not claims.get("sub"):
        raise IdTokenError("id token has no subject")
    return claims
