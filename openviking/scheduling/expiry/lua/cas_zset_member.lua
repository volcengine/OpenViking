local action = ARGV[1]
local expected = integer_arg(ARGV[3], 0, 256 * BASE - 1)
local replacement = nil
if action == 'set' then
    replacement = integer_arg(ARGV[4], 1, 256 * BASE - 1)
elseif action ~= 'delete' or ARGV[4] ~= '' then
    return redis.error_reply('invalid_cas_action')
end

local current = redis.call('ZSCORE', KEYS[1], ARGV[2])
if not current or tonumber(current) ~= expected then return 0 end
if action == 'set' then
    redis.call('ZADD', KEYS[1], decimal(replacement), ARGV[2])
    return 1
end
return redis.call('ZREM', KEYS[1], ARGV[2])
