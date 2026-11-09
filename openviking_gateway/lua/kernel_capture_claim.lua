-- KEYS: capture tombstone, capture, ready queue. ARGV: owner, lease ms, member -> {v, ver} or {}
local ready = redis.call('HGET', KEYS[2], 'ready')
if redis.call('EXISTS', KEYS[1]) == 1 or not ready then
    redis.call('ZREM', KEYS[3], ARGV[3])
    return {}
end
local clock = redis.call('TIME')
local now = clock[1] * 1000 + clock[2] / 1000
local lease = tonumber(redis.call('HGET', KEYS[2], 'lease') or '0')
ready = tonumber(ready) * 1000
if ready > now or lease >= now then
    redis.call('ZADD', KEYS[3], math.max(ready, lease), ARGV[3])
    return {}
end
lease = now + tonumber(ARGV[2])
redis.call('HSET', KEYS[2], 'lease', lease, 'owner', ARGV[1])
redis.call('ZADD', KEYS[3], math.max(ready, lease), ARGV[3])
return redis.call('HMGET', KEYS[2], 'v', 'ver')
