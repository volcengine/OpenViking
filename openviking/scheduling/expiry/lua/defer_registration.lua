require_owner(KEYS[2], ARGV[1])
local next_poll_at_ms = integer_arg(ARGV[3], 0, MAX_INT)
return redis.call('ZADD', KEYS[1], 'XX', decimal(next_poll_at_ms), ARGV[2])
