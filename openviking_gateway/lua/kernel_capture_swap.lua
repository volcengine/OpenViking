-- KEYS: capture tombstone, capture, ready. ARGV: version, value, ready, owner, ttl, member -> 0|1
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.error_reply('OVGW_DELETED') end
local lease = tonumber(redis.call('HGET', KEYS[2], 'lease') or '0')
if ARGV[4] ~= '' then
    local clock = redis.call('TIME')
    local now = clock[1] * 1000 + clock[2] / 1000
    if redis.call('HGET', KEYS[2], 'owner') ~= ARGV[4] or lease <= now then
        return redis.error_reply('OVGW_LEASE_LOST')
    end
end
local version = tonumber(redis.call('HGET', KEYS[2], 'ver') or '0')
if version ~= tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[2], 'v', ARGV[2], 'ver', version + 1)
redis.call('PEXPIRE', KEYS[2], ARGV[5])
if ARGV[3] == '' then
    redis.call('HDEL', KEYS[2], 'ready')
    redis.call('ZREM', KEYS[3], ARGV[6])
else
    redis.call('HSET', KEYS[2], 'ready', ARGV[3])
    redis.call('ZADD', KEYS[3], math.max(tonumber(ARGV[3]) * 1000, lease), ARGV[6])
end
return 1
