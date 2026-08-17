"""
Shared rate limiter instance (slowapi) - protects sensitive endpoints
like login from brute-force attacks. Limits are applied per IP address
by default, EXCEPT where explicitly keyed per-user instead (see
user_or_ip_key below).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

limiter = Limiter(key_func=get_remote_address)


def user_or_ip_key(request: Request) -> str:
    """Rate-limit key that prefers the authenticated user's ID over their
    IP address, falling back to IP when there's no valid token yet
    (signup/login - there's no user identity to key on before auth
    succeeds, so IP is the only option there).

    This matters specifically for ALOS: many teachers/students at the
    same school share one IP behind their network's NAT/proxy. Found via
    QA testing - IP-only limiting on chapter-upload meant one teacher
    exhausting their hourly quota would block every OTHER teacher at
    that school too, even ones who'd never uploaded anything. Keying
    authenticated endpoints per-user instead means each account gets its
    own independent quota, regardless of how many other accounts share
    the same network.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        try:
            from .auth import decode_access_token
            payload = decode_access_token(token)
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass  # invalid/expired token - fall through to IP-based limiting
    return f"ip:{get_remote_address(request)}"
