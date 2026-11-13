-- KEYS: capture tombstone, capture, ready queue. ARGV: owner, member -> 0|1
if redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('ZREM', KEYS[3], ARGV[2])
    return 0
end
if redis.call('HGET', KEYS[2], 'owner') ~= ARGV[1] then return 0 end
redis.call('HDEL', KEYS[2], 'owner')
redis.call('HSET', KEYS[2], 'lease', 0)
local ready = redis.call('HGET', KEYS[2], 'ready')
if ready then
    redis.call('ZADD', KEYS[3], tonumber(ready) * 1000, ARGV[2])
else
    redis.call('ZREM', KEYS[3], ARGV[2])
end
return 1
