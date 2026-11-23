if #ARGV[1] == 0 then return redis.error_reply('invalid_identity') end
local requested = integer_arg(ARGV[2], 0, MAX_INT)
local accepted = math.max(requested, now_ms() + 1)
local max_supported_due = math.floor(MAX_INT / 60000) * 60000
if accepted > max_supported_due then return redis.error_reply('invalid_report') end
redis.call('ZADD', KEYS[1], decimal(accepted), ARGV[1])
return decimal(accepted)
