-- KEYS: replays (own last), state. ARGV: count, anchors, inherited, with state -> {rows, state}
local count = tonumber(ARGV[1])
local anchors, inherited = {}, {}
for _, anchor in ipairs(cjson.decode(ARGV[2])) do anchors[anchor] = true end
for _, kind in ipairs(cjson.decode(ARGV[3])) do inherited[kind] = true end
local rows = {}
for i = 1, count do
    local fields = {}
    for _, field in ipairs(redis.call('HKEYS', KEYS[i])) do
        local colon = string.find(field, ':', 1, true)
        local kind, anchor = string.sub(field, 1, colon - 1), string.sub(field, colon + 1)
        if kind ~= 'reply' and anchors[anchor] and (i == count or inherited[kind]) then
            table.insert(fields, field)
        end
    end
    -- Bound unpack arguments for sessions with many replay records.
    for first = 1, #fields, 256 do
        local last = math.min(first + 255, #fields)
        local values = redis.call('HMGET', KEYS[i], unpack(fields, first, last))
        for offset, value in ipairs(values) do
            table.insert(rows, {fields[first + offset - 1], value})
        end
    end
end
local state = {}
if ARGV[4] == '1' then
    local value = redis.call('HMGET', KEYS[count + 1], 'v', 'ver')
    if value[1] then state = value end
end
return {rows, state}
