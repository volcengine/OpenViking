-- KEYS: object, index (indexed kinds). ARGV: id, value, h(user_id), ttl or '' -> {rev, PTTL}
local now = redis.call('TIME')
now = now[1] * 1000 + math.floor(now[2] / 1000)
local ttl = redis.call('PTTL', KEYS[1])
local rev = redis.call('HINCRBY', KEYS[1], 'rev', 1)
redis.call('HSET', KEYS[1], 'v', ARGV[2], 'u', ARGV[3])
if ARGV[4] ~= '' then
    ttl = tonumber(ARGV[4])
    redis.call('PEXPIRE', KEYS[1], ttl)
end
if KEYS[2] then
    local expires = '+inf'
    if ttl >= 0 then expires = now + ttl end
    redis.call('ZADD', KEYS[2], expires, ARGV[1])
    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
    local last = redis.call('ZREVRANGE', KEYS[2], 0, 0, 'WITHSCORES')
    if last[2] == 'inf' then
        redis.call('PERSIST', KEYS[2])
    elseif last[2] then
        redis.call('PEXPIREAT', KEYS[2], math.ceil(tonumber(last[2])))
    end
end
return {rev, redis.call('PTTL', KEYS[1])}
