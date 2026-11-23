expect_type(KEYS[1], 'hash')
local values = redis.call('HGETALL', KEYS[1])
local result = {}
for index = 1, #values, 2 do
    result[values[index]] = values[index + 1]
end
return cjson.encode(result)
