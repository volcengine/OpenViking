-- KEYS: capture tombstone, capture, waiting. ARGV: version, value, ttl, due, member -> 0|1
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.error_reply('OVGW_DELETED') end
local version = tonumber(redis.call('HGET', KEYS[2], 'ver') or '0')
if version ~= tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[2], 'v', ARGV[2], 'ver', version + 1)
redis.call('PEXPIRE', KEYS[2], ARGV[3])
if ARGV[4] == '' then
    redis.call('ZREM', KEYS[3], ARGV[5])
else
    redis.call('ZADD', KEYS[3], ARGV[4], ARGV[5])
end
return 1
