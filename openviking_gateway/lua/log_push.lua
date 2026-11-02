-- KEYS: log. ARGV: row, limit, ttl -> 1
redis.call('LPUSH', KEYS[1], ARGV[1])
redis.call('LTRIM', KEYS[1], 0, tonumber(ARGV[2]) - 1)
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
