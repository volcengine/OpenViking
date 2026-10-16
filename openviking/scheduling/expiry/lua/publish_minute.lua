require_owner(KEYS[3], ARGV[1])
local expected = integer_arg(ARGV[2], 0, MAX_INT)
local minute = integer_arg(ARGV[3], 0, MAX_INT)
local count = integer_arg(ARGV[4], 1, 1024)
if minute % 60000 ~= 0 or #ARGV ~= 4 + count then return redis.error_reply('invalid_publish') end
local raw_current = redis.call('GET', KEYS[2])
if not raw_current then return redis.error_reply('namespace_corrupt') end
local current = tonumber(raw_current)
if current >= minute then return {'already_published', decimal(current)} end
if current ~= expected or minute <= current then return redis.error_reply('progress_conflict') end
for i = 1, count do
    redis.call('ZADD', KEYS[1], decimal(minute), ARGV[4 + i])
end
redis.call('SET', KEYS[2], decimal(minute))
return {'published', decimal(minute)}
