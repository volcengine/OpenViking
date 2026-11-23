local lower = integer_arg(ARGV[1], -1, MAX_INT)
local upper = nil
if ARGV[2] == '' then
    upper = now_ms()
else
    upper = integer_arg(ARGV[2], 0, MAX_INT)
end
local after_score = integer_arg(ARGV[3], 0, MAX_INT)
local limit = integer_arg(ARGV[5], 1, 4096)
local items, next_value = page_after(
    KEYS[1], lower, upper, after_score, ARGV[4], limit
)
return cjson.encode({items=items, next=next_value})
