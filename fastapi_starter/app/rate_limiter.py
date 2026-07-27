"""
Shared rate limiter instance (slowapi) - login jaise sensitive endpoints
ko brute-force se bachane ke liye. IP address ke hisaab se limit lagta hai.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
