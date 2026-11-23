expect_type(KEYS[1], 'hash')
if #ARGV == 0 or #ARGV % 2 ~= 0 then
    return redis.error_reply('invalid_hash_write')
end
return redis.call('HSET', KEYS[1], unpack(ARGV))
