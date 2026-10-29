-- KEYS: log, rebuild scratch. ARGV: h(user_id) -> rows kept
local ttl = redis.call('PTTL', KEYS[1])
local rows = redis.call('LRANGE', KEYS[1], 0, -1)
redis.call('UNLINK', KEYS[2])
local kept = 0
for _, row in ipairs(rows) do
    local user = string.match(row, '^[^%z]*%z([^%z]*)%z')
    if user ~= ARGV[1] then
        redis.call('RPUSH', KEYS[2], row)
        kept = kept + 1
    end
end
if kept == 0 then
    redis.call('UNLINK', KEYS[1])
else
    if ttl >= 0 then redis.call('PEXPIRE', KEYS[2], ttl) end
    redis.call('RENAME', KEYS[2], KEYS[1])
end
return kept
