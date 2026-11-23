expect_type(KEYS[1], 'zset')
for index = 1, #ARGV do
    if #ARGV[index] == 0 then return redis.error_reply('invalid_identity') end
    redis.call('ZADD', KEYS[1], '0', ARGV[index])
end
return #ARGV
