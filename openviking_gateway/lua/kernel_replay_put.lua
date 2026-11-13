-- KEYS: tombstone, replay, reply index (replies). ARGV: field, value, ttl, session -> stored
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.error_reply('OVGW_DELETED') end
redis.call('HSETNX', KEYS[2], ARGV[1], ARGV[2])
redis.call('PEXPIRE', KEYS[2], ARGV[3])
if #KEYS == 3 then
    redis.call('SADD', KEYS[3], ARGV[4])
    redis.call('PEXPIRE', KEYS[3], ARGV[3])
end
return redis.call('HGET', KEYS[2], ARGV[1])
