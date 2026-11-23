local progress = integer_arg(ARGV[1], 0, MAX_INT)
return redis.call('SET', KEYS[1], decimal(progress), 'NX') and 1 or 0
