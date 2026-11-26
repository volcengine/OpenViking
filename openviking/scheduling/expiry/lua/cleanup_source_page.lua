local through = integer_arg(ARGV[1], 0, MAX_INT)
local limit = integer_arg(ARGV[2], 1, 4096)
local due = redis.call('ZCOUNT', KEYS[1], '-inf', decimal(through))
local removed = math.min(due, limit)
if removed > 0 then
    redis.call('ZREMRANGEBYRANK', KEYS[1], 0, removed - 1)
end
return removed
