-- KEYS: capture, waiting. ARGV: version, member -> 0|1. A document that is gone has version 0.
local version = tonumber(redis.call('HGET', KEYS[1], 'ver') or '0')
if version ~= tonumber(ARGV[1]) then return 0 end
return redis.call('ZREM', KEYS[2], ARGV[2])
