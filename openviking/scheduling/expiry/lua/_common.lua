local BASE = 1099511627776
local MAX_INT = 9007199254740991

local function decimal(n)
    return string.format('%.0f', n)
end

local function now_ms()
    local t = redis.call('TIME')
    return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

local function integer_arg(value, min_value, max_value)
    if type(value) ~= 'string' or not string.match(value, '^%-?%d+$') then
        error('invalid_integer')
    end
    local n = tonumber(value)
    if not n or n ~= math.floor(n) or n < min_value or n > max_value or decimal(n) ~= value then
        error('invalid_integer')
    end
    return n
end

local function expect_type(key, allowed)
    local actual = redis.call('TYPE', key)['ok']
    if actual ~= 'none' and actual ~= allowed then
        error('wrong_key_type')
    end
end

local function require_owner(key, token)
    if redis.call('GET', key) ~= token then
        error('owner_lost')
    end
end

local function tuple_after(score, member, lower, last_score, last_member)
    if score <= lower then return false end
    if last_score == nil then return true end
    return score > last_score or (score == last_score and member > last_member)
end

local function page_after(key, lower, upper, last_score, last_member, limit)
    local lo = nil
    if last_score ~= nil then
        local current = redis.call('ZSCORE', key, last_member)
        if current and tonumber(current) == last_score and last_score > lower then
            local rank = redis.call('ZRANK', key, last_member)
            if rank then lo = rank + 1 end
        end
    end
    if lo == nil then
        local size = redis.call('ZCARD', key)
        lo = 0
        local hi = size
        while lo < hi do
            local mid = math.floor((lo + hi) / 2)
            local entry = redis.call('ZRANGE', key, mid, mid, 'WITHSCORES')
            if #entry == 0 then
                hi = mid
            else
                local score = tonumber(entry[2])
                if tuple_after(score, entry[1], lower, last_score, last_member) then
                    hi = mid
                else
                    lo = mid + 1
                end
            end
        end
    end
    local raw = redis.call('ZRANGE', key, lo, lo + limit, 'WITHSCORES')
    local items = {}
    local count = 0
    for i = 1, #raw, 2 do
        local score = tonumber(raw[i + 1])
        if score > upper then break end
        count = count + 1
        items[count] = {raw[i], decimal(score)}
    end
    local next_value = false
    if count > limit then
        items[count] = nil
        count = count - 1
        next_value = {items[count][2], items[count][1]}
    end
    return items, next_value
end
