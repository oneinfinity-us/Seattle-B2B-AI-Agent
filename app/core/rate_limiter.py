"""
A Redis + Lua based token-bucket rate limiter.

Why not an in-memory counter / a simple INCR + EXPIRE:
1. Across multi-process/multi-instance deployments, an in-memory counter counts independently on each
   instance, so it can't enforce a global rate limit.
2. INCR+EXPIRE has a "boundary burst" problem under high concurrency (e.g., landing right at a window
   boundary can let through twice the intended quota).
3. A token bucket allows "bursty traffic + smooth refill," which better matches the real scenario:
   when a merchant receives 20 new Yelp reviews at once, it's fine to process them concurrently for a
   short burst, but the long-term rate is still controlled.

A Lua script is used because "read remaining tokens -> check -> deduct" must be an atomic operation,
otherwise there would be a race condition (TOCTOU) between concurrent requests.
"""
import time
from dataclasses import dataclass

from redis.asyncio import Redis

_TOKEN_BUCKET_LUA = """
-- KEYS[1] = the bucket's redis key
-- ARGV[1] = capacity
-- ARGV[2] = refill_rate, tokens refilled per second
-- ARGV[3] = now, current timestamp (seconds, float)
-- ARGV[4] = requested, tokens consumed by this request
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(bucket[1])
local last_ts = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_ts = now
end

-- Refill tokens based on elapsed time, but not beyond the capacity cap
local elapsed = math.max(0, now - last_ts)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call("HMSET", key, "tokens", tokens, "ts", now)
redis.call("EXPIRE", key, 3600)

return { allowed, tokens }
"""


@dataclass
class RateLimitResult:
    allowed: bool
    remaining_tokens: float


class TenantRateLimiter:
    """Rate-limits at tenant_id (merchant) granularity, so a traffic spike from one tenant doesn't exhaust another tenant's quota."""

    def __init__(self, redis: Redis, capacity: int, refill_per_sec: float):
        self._redis = redis
        self._capacity = capacity
        self._refill_per_sec = refill_per_sec
        self._script = redis.register_script(_TOKEN_BUCKET_LUA)

    async def acquire(self, tenant_id: str, cost: float = 1.0) -> RateLimitResult:
        key = f"ratelimit:tenant:{tenant_id}"
        now = time.time()
        allowed, remaining = await self._script(
            keys=[key],
            args=[self._capacity, self._refill_per_sec, now, cost],
        )
        return RateLimitResult(allowed=bool(allowed), remaining_tokens=float(remaining))
