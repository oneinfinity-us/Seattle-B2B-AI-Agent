"""
基于 Redis + Lua 的令牌桶限流器。

为什么不用内存计数器 / 简单 INCR + EXPIRE：
1. 多进程/多实例部署时，内存计数器各算各的，起不到全局限流作用。
2. INCR+EXPIRE 在高并发下会有"临界突刺"问题（比如刚好在窗口边界瞬间打满两倍配额）。
3. 令牌桶允许"突发流量 + 平滑补充"，更符合真实场景：
   商户一次性收到 20 条新 Yelp 评论时，允许短时间内并发处理，但长期速率仍受控。

用 Lua 脚本是因为"读取剩余令牌 -> 判断 -> 扣减"必须是原子操作，
否则并发请求之间会有 race condition（TOCTOU）。
"""
import time
from dataclasses import dataclass

from redis.asyncio import Redis

_TOKEN_BUCKET_LUA = """
-- KEYS[1] = 桶的 redis key
-- ARGV[1] = capacity 容量
-- ARGV[2] = refill_rate 每秒补充多少令牌
-- ARGV[3] = now 当前时间戳（秒，float）
-- ARGV[4] = requested 本次请求消耗的令牌数
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

-- 按经过的时间补充令牌，但不超过容量上限
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
    """按 tenant_id（商户）粒度限流，避免一个商户的流量突刺打垮其他商户的额度。"""

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
