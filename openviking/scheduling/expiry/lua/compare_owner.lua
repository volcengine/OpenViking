local action = ARGV[1]
local ttl = nil
if action == 'renew' then
    ttl = integer_arg(ARGV[3], 1, MAX_INT)
elseif action ~= 'release' or ARGV[3] ~= '' then
    return redis.error_reply('invalid_owner_action')
end

if redis.call('GET', KEYS[1]) ~= ARGV[2] then return 0 end
if action == 'renew' then
    return redis.call('PEXPIRE', KEYS[1], ttl)
end
return redis.call('DEL', KEYS[1])
