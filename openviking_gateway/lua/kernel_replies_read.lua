-- KEYS: reply indexes. ARGV: none -> members per index
local rows = {}
for i, key in ipairs(KEYS) do rows[i] = redis.call('SMEMBERS', key) end
return rows
