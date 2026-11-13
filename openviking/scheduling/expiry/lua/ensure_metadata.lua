expect_type(KEYS[1], 'hash')
if #ARGV[1] == 0 or #ARGV[2] == 0 or #ARGV[3] == 0 or #ARGV[4] == 0 then
    return redis.error_reply('invalid_identity')
end
local ttl = integer_arg(ARGV[5], 1, MAX_INT)
local fields = {'item_type', 'local_id', 'account_id', 'user_id'}
local exists = redis.call('EXISTS', KEYS[1]) == 1
if exists then
    for i = 1, 4 do
        local current = redis.call('HGET', KEYS[1], fields[i])
        if current ~= ARGV[i] then return redis.error_reply('metadata_conflict') end
    end
else
    redis.call('HSET', KEYS[1],
        fields[1], ARGV[1], fields[2], ARGV[2],
        fields[3], ARGV[3], fields[4], ARGV[4])
end
redis.call('PEXPIRE', KEYS[1], ttl)
return exists and 'refreshed' or 'created'
