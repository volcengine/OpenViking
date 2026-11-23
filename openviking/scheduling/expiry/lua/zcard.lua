expect_type(KEYS[1], 'zset')
return redis.call('ZCARD', KEYS[1])
