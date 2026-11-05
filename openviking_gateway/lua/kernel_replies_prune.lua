-- KEYS: reply indexes, then replays. ARGV: ttl, n, then per index anchor, count, members -> live
local count, key, arg, result = tonumber(ARGV[2]), tonumber(ARGV[2]), 3, {}
for i = 1, count do
    local field, members, live = 'reply:' .. ARGV[arg], tonumber(ARGV[arg + 1]), {}
    arg = arg + 2
    for _ = 1, members do
        key = key + 1
        if redis.call('HEXISTS', KEYS[key], field) == 1 then
            table.insert(live, ARGV[arg])
        else
            redis.call('SREM', KEYS[i], ARGV[arg])
        end
        arg = arg + 1
    end
    if #live > 0 then redis.call('PEXPIRE', KEYS[i], ARGV[1]) end
    result[i] = live
end
return result
