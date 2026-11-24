-- KEYS: object, index (indexed kinds). ARGV: id -> 1
redis.call('UNLINK', KEYS[1])
if KEYS[2] then
    redis.call('ZREM', KEYS[2], ARGV[1])
    local last = redis.call('ZREVRANGE', KEYS[2], 0, 0, 'WITHSCORES')
    if last[2] == 'inf' then
        redis.call('PERSIST', KEYS[2])
    elseif last[2] then
        redis.call('PEXPIREAT', KEYS[2], math.ceil(tonumber(last[2])))
    end
end
return 1
