-- KEYS: hashes. ARGV: fields -> {position, values...} per hash holding the first field
local rows = {}
for i, key in ipairs(KEYS) do
    local values = redis.call('HMGET', key, unpack(ARGV))
    if values[1] then table.insert(rows, {i, unpack(values)}) end
end
return rows
