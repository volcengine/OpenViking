use super::*;

macro_rules! token_script {
    ($body:literal) => {
        concat!(
            r#"
local u128_max = "340282366920938463463374607431768211455"
local function normalize_decimal(value)
    if not value or not string.match(value, "^%d+$") then
        return nil
    end
    -- Rust writes canonical decimals; skip the pattern scan in the common case.
    local normalized = value
    if string.byte(value, 1) == 48 then
        normalized = string.gsub(value, "^0+", "")
        if normalized == "" then
            return "0"
        end
    end
    if string.len(normalized) > string.len(u128_max)
        or (string.len(normalized) == string.len(u128_max)
            and normalized > u128_max) then
        return nil
    end
    return normalized
end

local function decimal_less(left, right)
    left = normalize_decimal(left)
    right = normalize_decimal(right)
    if not left or not right then
        return nil
    end
    if string.len(left) ~= string.len(right) then
        return string.len(left) < string.len(right)
    end
    return left < right
end

-- Subtract decimal strings without converting nanosecond epochs to doubles.
-- Match the manager's previous saturating subtraction near the epoch.
local function decimal_subtract(left, right)
    left = normalize_decimal(left)
    right = normalize_decimal(right)
    if not left or not right then
        return nil
    end
    if decimal_less(left, right) then
        return "0"
    end
    local digits = {}
    local borrow = 0
    local j = string.len(right)
    for i = string.len(left), 1, -1 do
        local digit = tonumber(string.sub(left, i, i)) - borrow
        if j > 0 then
            digit = digit - tonumber(string.sub(right, j, j))
        end
        borrow = 0
        if digit < 0 then
            digit = digit + 10
            borrow = 1
        end
        table.insert(digits, 1, tostring(digit))
        j = j - 1
    end
    return normalize_decimal(table.concat(digits))
end

local function lease_values(raw)
    local normalized = normalize_decimal(raw)
    local lease_ms = normalized and tonumber(normalized)
    if not lease_ms or lease_ms < 1000 or lease_ms > 4503599627370495 then
        return nil
    end
    -- String arguments avoid Redis/Lua's scientific notation for large values.
    return normalized .. "000000", string.format("%.0f", 2 * lease_ms)
end

local function server_now_ns()
    local time = redis.call("TIME")
    return normalize_decimal(time[1] .. string.rep("0", 6 - string.len(time[2]))
        .. time[2] .. "000")
end

local function valid_owner(owner)
    return owner and owner ~= "" and not string.find(owner, ":", 1, true)
end
local function decode_token(raw, stale_before)
    if string.sub(raw, 1, 3) == "v2|" then
        local owner, body = string.match(raw, "^v2|([^|:,]+)|(.+)$")
        if not owner then return nil end
        local token = { owner = owner, time_ns = "0", kind = "E", holders = {} }
        local seen = {}
        local count = 0
        for item in string.gmatch(body .. ",", "(.-),") do
            local id, kind, time = string.match(item, "^([^|:,]+):([ET]):(%d+)$")
            if not id or seen[id] or not normalize_decimal(time) then return nil end
            seen[id] = true
            count = count + 1
            if not stale_before or decimal_less(time, stale_before) ~= true then
                table.insert(token.holders, { id = id, kind = kind, time_ns = time })
                if kind == "T" then token.kind = "T" end
                if decimal_less(token.time_ns, time) then token.time_ns = time end
            end
        end
        if count == 0 then return nil end
        return token
    end
    local p1 = string.find(raw, ":", 1, true)
    local p2 = p1 and string.find(raw, ":", p1 + 1, true)
    local p3 = p2 and string.find(raw, ":", p2 + 1, true)
    if not p1 or not p2 or p3 then
        return nil
    end
    local token_owner = string.sub(raw, 1, p1 - 1)
    local time_ns = string.sub(raw, p1 + 1, p2 - 1)
    local kind = string.sub(raw, p2 + 1)
    if token_owner == "" or not normalize_decimal(time_ns)
        or (kind ~= "E" and kind ~= "T") then
        return nil
    end
    return { owner = token_owner, time_ns = time_ns, kind = kind }
end
local function holder_less(a, b)
    return a.id < b.id
end
local function is_normalized_path(path)
    if not path or string.sub(path, 1, 1) ~= "/"
        or (path ~= "/" and string.sub(path, -1) == "/")
        or string.find(path, "//", 1, true)
        or string.find(path, string.char(0), 1, true) then
        return false
    end
    for part in string.gmatch(path, "[^/]+") do
        if part == "." or part == ".." then
            return false
        end
    end
    return true
end

local function ancestors(path)
    local result = {}
    if path == "/" then
        return result
    end
    table.insert(result, "/")
    local start = 2
    while true do
        local slash = string.find(path, "/", start, true)
        if not slash then
            break
        end
        table.insert(result, string.sub(path, 1, slash - 1))
        start = slash + 1
    end
    return result
end
"#,
            $body
        )
    };
}

pub(super) const ACQUIRE_BATCH_SCRIPT: &str = token_script!(
    r#"
local key = KEYS[1]
local owner = ARGV[1]
local lease_ns, ttl_ms = lease_values(ARGV[2])
-- The holder entrypoint supplies a versioned identity envelope. The legacy
-- entrypoint remains available for reading/testing drained v1 deployments.
local holder, source
if string.sub(owner, 1, 3) == "v2|" then
    owner, holder, source = string.match(owner, "^v2|([^|:,]+)|([^|:,]+)|([^|:,]*)$")
    if not owner then return { "invalid_request", "invalid holder identity" } end
end
local function is_descendant(path, root)
    if root == "/" then
        return path ~= "/"
    end
    return string.sub(path, 1, string.len(root) + 1) == root .. "/"
end

if not valid_owner(owner) or not lease_ns
    or (#ARGV - 2) % 2 ~= 0 or #ARGV == 2 then
    return { "invalid_request", "invalid acquire arguments" }
end

local requests = {}
local includes_tree = false
for index = 3, #ARGV, 2 do
    local path = ARGV[index]
    local kind = ARGV[index + 1]
    if not is_normalized_path(path) or (kind ~= "E" and kind ~= "T") then
        return { "invalid_request", "invalid path or lock kind" }
    end
    table.insert(requests, { path = path, kind = kind })
    if kind == "T" then
        includes_tree = true
    end
end

local now_ns = server_now_ns()
local stale_before_ns = decimal_subtract(now_ns, lease_ns)
if not stale_before_ns then
    return { "invalid_request", "invalid Redis time" }
end

local records = {}
if includes_tree then
    -- Decode every record that can conflict, plus a bounded sample of unrelated
    -- records for opportunistic stale cleanup.
    local unrelated_budget = 16
    local related = {}
    for _, request in ipairs(requests) do
        related[request.path] = true
        for _, ancestor in ipairs(ancestors(request.path)) do
            related[ancestor] = true
        end
    end
    local values = redis.call("HGETALL", key)
    for index = 1, #values, 2 do
        local path = values[index]
        local relevant = related[path]
        if not relevant then
            for _, request in ipairs(requests) do
                if request.kind == "T" and is_descendant(path, request.path) then
                    relevant = true
                    break
                end
            end
        end
        if not relevant and unrelated_budget > 0 then
            unrelated_budget = unrelated_budget - 1
            relevant = true
        end
        if relevant then
            local raw = values[index + 1]
            local token = decode_token(raw, stale_before_ns)
            if not token then
                return { "invalid_token", path, raw }
            end
            records[path] = { raw = raw, token = token }
        end
    end
else
    local fields = {}
    local seen = {}
    local function add_field(path)
        if not seen[path] then
            seen[path] = true
            table.insert(fields, path)
        end
    end
    for _, request in ipairs(requests) do
        add_field(request.path)
        for _, ancestor in ipairs(ancestors(request.path)) do
            add_field(ancestor)
        end
    end
    local values = redis.call("HMGET", key, unpack(fields))
    for index, raw in ipairs(values) do
        if raw then
            local token = decode_token(raw, stale_before_ns)
            if not token then
                return { "invalid_token", fields[index], raw }
            end
            records[fields[index]] = { raw = raw, token = token }
        end
    end
end

local stale = {}
local stale_seen = {}
local function is_other_stale(record)
    if not record or record.token.owner == owner then
        return false
    end
    if record.token.holders and #record.token.holders == 0 then return true end
    return decimal_less(record.token.time_ns, stale_before_ns) == true
end
local function mark_stale(path, record)
    if is_other_stale(record) and not stale_seen[path] then
        stale_seen[path] = true
        table.insert(stale, path)
    end
end

for path, record in pairs(records) do
    mark_stale(path, record)
end

-- Validate the whole source batch before conflict checking or storage writes.
-- decode_token already removed expired holders.
if source and source ~= "" then
    for _, request in ipairs(requests) do
        local target = records[request.path]
        local found
        if target and target.token.owner == owner and target.token.holders then
            for _, h in ipairs(target.token.holders) do
                if h.id == source then found = h; break end
            end
        end
        if not found then return { "recovery_required", "token_unavailable" } end
        if found.kind ~= request.kind then
            return { "invalid_request", "handoff kind does not match holder" }
        end
    end
end

for _, request in ipairs(requests) do
    local target = records[request.path]
    if not holder and target and target.token.owner == owner and target.token.holders then
        return { "invalid_request", "holder token requires holder acquisition" }
    end
    if target and target.token.owner ~= owner and not is_other_stale(target) then
        return { "conflict", request.path, target.raw }
    end
    for _, ancestor in ipairs(ancestors(request.path)) do
        local record = records[ancestor]
        if record and record.token.owner ~= owner and record.token.kind == "T"
            and not is_other_stale(record) then
            return { "conflict", ancestor, record.raw }
        end
    end
    if request.kind == "T" then
        for path, record in pairs(records) do
            if is_descendant(path, request.path)
                and record.token.owner ~= owner and not is_other_stale(record) then
                return { "conflict", path, record.raw }
            end
        end
    end
end

local changes = {}
for _, request in ipairs(requests) do
    local existing = records[request.path]
    local replacement = owner .. ":" .. now_ns .. ":" .. request.kind
    if holder then
        local refs = {}
        if existing and existing.token.owner == owner then
            local old = existing.token
            if old.holders then
                for _, h in ipairs(old.holders) do
                    if h.id ~= source then table.insert(refs, h) end
                end
            elseif decimal_less(old.time_ns, stale_before_ns) ~= true then
                table.insert(refs, { id = "legacy", kind = old.kind, time_ns = old.time_ns })
            end
        end
        table.insert(refs, { id = holder, kind = request.kind, time_ns = now_ns })
        table.sort(refs, holder_less)
        local parts = {}
        for _, h in ipairs(refs) do
            table.insert(parts, h.id .. ":" .. h.kind .. ":" .. h.time_ns)
        end
        replacement = "v2|" .. owner .. "|" .. table.concat(parts, ",")
        table.insert(changes, {
            path = request.path,
            change = existing and existing.token.owner == owner and "upgraded" or "created",
            previous = existing and existing.raw or "",
            replacement = replacement,
        })
    elseif existing and existing.token.owner == owner then
        if request.kind == "T" and existing.token.kind == "E" then
            table.insert(changes, {
                path = request.path,
                change = "upgraded",
                previous = existing.raw,
                replacement = replacement,
            })
        else
            table.insert(changes, {
                path = request.path,
                change = "reentrant",
                previous = "",
                replacement = "",
            })
        end
    else
        table.insert(changes, {
            path = request.path,
            change = "created",
            previous = "",
            replacement = replacement,
        })
    end
end

local stale_batch_size = 1000
for start_index = 1, #stale, stale_batch_size do
    local end_index = math.min(start_index + stale_batch_size - 1, #stale)
    redis.call("HDEL", key, unpack(stale, start_index, end_index))
end
for _, change in ipairs(changes) do
    if change.change ~= "reentrant" then
        redis.call("HSET", key, change.path, change.replacement)
    end
end
if redis.call("HLEN", key) > 0 then
    redis.call("PEXPIRE", key, ttl_ms)
end

local result = { "ok", tostring(#stale) }
for _, change in ipairs(changes) do
    table.insert(result, change.path)
    table.insert(result, change.change)
    table.insert(result, change.previous)
    table.insert(result, change.replacement)
end
return result
"#
);

pub(super) const ROLLBACK_BATCH_SCRIPT: &str = token_script!(
    r#"
local key = KEYS[1]
local owner = ARGV[1]
local lease_ns, ttl_ms = lease_values(ARGV[2])
if not valid_owner(owner) or not lease_ns
    or (#ARGV - 2) % 4 ~= 0 then
    return { "invalid_request", "invalid rollback arguments" }
end

local changes = {}
for index = 3, #ARGV, 4 do
    local path = ARGV[index]
    local change = ARGV[index + 1]
    local previous = ARGV[index + 2]
    local replacement = ARGV[index + 3]
    if change ~= "created" and change ~= "reentrant" and change ~= "upgraded" then
        return { "invalid_request", "invalid acquisition change" }
    end
    if change ~= "reentrant" then
        local current = redis.call("HGET", key, path)
        if current ~= replacement then
            return { "changed", path }
        end
        local token = decode_token(replacement)
        if not token or token.owner ~= owner then
            return { "invalid_request", "replacement owner mismatch" }
        end
    end
    table.insert(changes, {
        path = path,
        change = change,
        previous = previous,
    })
end

for index = #changes, 1, -1 do
    local change = changes[index]
    if change.change == "created" then
        redis.call("HDEL", key, change.path)
    elseif change.change == "upgraded" then
        redis.call("HSET", key, change.path, change.previous)
    end
end
if redis.call("HLEN", key) > 0 then
    redis.call("PEXPIRE", key, ttl_ms)
end
return { "ok" }
"#
);

pub(super) const COMPARE_WRITE_SCRIPT: &str = token_script!(
    r#"
local lease_ns, ttl_ms = lease_values(ARGV[6])
if not lease_ns or not valid_owner(ARGV[3])
    or (ARGV[4] ~= "E" and ARGV[4] ~= "T")
    or (ARGV[5] ~= "preserve" and ARGV[5] ~= "confirm" and ARGV[5] ~= "holders") then
    return redis.error_reply("invalid compare write arguments")
end
local current = redis.call("HGET", KEYS[1], ARGV[1])
if current ~= ARGV[2] then
    return 0
end
local token = decode_token(current)
if not token then
    return redis.error_reply("invalid compare write token")
end
-- Low-level holder CAS preserves supplied snapshots. Ordinary holder renewal,
-- release and failed handoff rollback use the server-clock mutation scripts.
if ARGV[5] == "holders" then
    local replacement = decode_token(ARGV[7])
    if not replacement or not replacement.holders or replacement.owner ~= ARGV[3] then
        return redis.error_reply("invalid holder replacement")
    end
    local raw = ARGV[7]
    if replacement.owner ~= token.owner then
        local now_ns = server_now_ns()
        if not now_ns then return redis.error_reply("invalid Redis time") end
        table.sort(replacement.holders, holder_less)
        local items = {}
        for _, holder in ipairs(replacement.holders) do
            table.insert(items, holder.id .. ":" .. holder.kind .. ":" .. now_ns)
        end
        raw = "v2|" .. replacement.owner .. "|" .. table.concat(items, ",")
    end
    redis.call("HSET", KEYS[1], ARGV[1], raw)
    redis.call("PEXPIRE", KEYS[1], ttl_ms)
    return 1
end
local time_ns
if ARGV[5] == "preserve" then
    if token.owner ~= ARGV[3] then
        return redis.error_reply("cannot preserve time across owners")
    end
    time_ns = token.time_ns
else
    time_ns = server_now_ns()
end
if not time_ns then
    return redis.error_reply("invalid Redis time")
end
redis.call("HSET", KEYS[1], ARGV[1], ARGV[3] .. ":" .. time_ns .. ":" .. ARGV[4])
redis.call("PEXPIRE", KEYS[1], ttl_ms)
return 1
"#
);

pub(super) const COMPARE_REMOVE_SCRIPT: &str = token_script!(
    r#"
local lease_ns, ttl_ms = lease_values(ARGV[3])
if not lease_ns then
    return redis.error_reply("invalid lease_ms")
end
local current = redis.call("HGET", KEYS[1], ARGV[1])
if current ~= ARGV[2] then
    return 0
end
redis.call("HDEL", KEYS[1], ARGV[1])
if redis.call("HLEN", KEYS[1]) > 0 then
    redis.call("PEXPIRE", KEYS[1], ttl_ms)
end
return 1
"#
);

pub(super) const REFRESH_SCRIPT: &str = token_script!(
    r#"
local lease_ns, ttl_ms = lease_values(ARGV[3])
if not lease_ns or not valid_owner(ARGV[2]) then
    return { "invalid_request", "invalid refresh arguments" }
end
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if not raw then
    return { "missing" }
end
local token = decode_token(raw)
if not token then
    return { "invalid_token", raw }
end
if token.owner ~= ARGV[2] or token.holders then
    return { "owner_mismatch" }
end
local time_ns = server_now_ns()
if not time_ns then
    return { "invalid_request", "invalid Redis time" }
end
redis.call("HSET", KEYS[1], ARGV[1], token.owner .. ":" .. time_ns .. ":" .. token.kind)
redis.call("PEXPIRE", KEYS[1], ttl_ms)
return { "ok" }
"#
);

pub(super) const REFRESH_BATCH_SCRIPT: &str = token_script!(
    r#"
local owner = ARGV[1]
local lease_ns, ttl_ms = lease_values(ARGV[2])
local holder
if string.sub(owner, 1, 3) == "v2|" then
    owner, holder = string.match(owner, "^v2|([^|:,]+)|([^|:,]+)|%d+$")
    if not owner then
        return { "invalid_request", "invalid holder refresh" }
    end
end
if not lease_ns or not valid_owner(owner) then
    return { "invalid_request", "invalid refresh arguments" }
end
local time_ns = server_now_ns()
local stale_before = decimal_subtract(time_ns, lease_ns)
if not stale_before then
    return { "invalid_request", "invalid Redis time" }
end
local results = {}
local refreshed = false
for index = 3, #ARGV do
    local path = ARGV[index]
    local raw = redis.call("HGET", KEYS[1], path)
    if not raw then
        table.insert(results, { "missing" })
    else
        local token = decode_token(raw)
        if not token then
            table.insert(results, { "invalid_token", raw })
        elseif token.owner ~= owner then
            table.insert(results, { "owner_mismatch" })
        elseif holder then
            local found = false
            local refs = {}
            for _, h in ipairs(token.holders or {}) do
                if decimal_less(h.time_ns, stale_before) ~= true then
                    if h.id == holder then
                        found = true
                        h.time_ns = time_ns
                    end
                    table.insert(refs, h)
                end
            end
            if found then
                table.sort(refs, holder_less)
                local parts = {}
                for _, h in ipairs(refs) do
                    table.insert(parts, h.id .. ":" .. h.kind .. ":" .. h.time_ns)
                end
                redis.call("HSET", KEYS[1], path, "v2|" .. owner .. "|" .. table.concat(parts, ","))
                table.insert(results, { "ok" })
                refreshed = true
            else
                table.insert(results, { "missing" })
            end
        elseif token.holders then
            table.insert(results, { "owner_mismatch" })
        else
            redis.call("HSET", KEYS[1], path, token.owner .. ":" .. time_ns .. ":" .. token.kind)
            table.insert(results, { "ok" })
            refreshed = true
        end
    end
end
if refreshed then
    redis.call("PEXPIRE", KEYS[1], ttl_ms)
end
return results
"#
);

pub(super) const MUTATE_HOLDER_SCRIPT: &str = token_script!(
    r#"
local lease_ns, ttl_ms = lease_values(ARGV[4])
if not lease_ns or not valid_owner(ARGV[2])
    or not valid_owner(ARGV[3]) or (ARGV[5] ~= "0" and ARGV[5] ~= "1") then
    return { "invalid_request", "invalid holder mutation" }
end
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if not raw then return { "missing" } end
local token = decode_token(raw)
if not token then return { "invalid_token", raw } end
if token.owner ~= ARGV[2] then return { "owner_mismatch" } end
local time_ns = server_now_ns()
local stale_before = decimal_subtract(time_ns, lease_ns)
local release = ARGV[5] == "1"
local found = false
local refs = {}
for _, h in ipairs(token.holders or {}) do
    if h.id == ARGV[3] then
        if release or decimal_less(h.time_ns, stale_before) ~= true then
            found = true
            if not release then
                h.time_ns = time_ns
                table.insert(refs, h)
            end
        end
    elseif decimal_less(h.time_ns, stale_before) ~= true then
        table.insert(refs, h)
    end
end
if not found then return { "missing" } end
if #refs == 0 then
    redis.call("HDEL", KEYS[1], ARGV[1])
else
    table.sort(refs, holder_less)
    local parts = {}
    for _, h in ipairs(refs) do
        table.insert(parts, h.id .. ":" .. h.kind .. ":" .. h.time_ns)
    end
    redis.call("HSET", KEYS[1], ARGV[1], "v2|" .. token.owner .. "|" .. table.concat(parts, ","))
end
if redis.call("HLEN", KEYS[1]) > 0 then
    redis.call("PEXPIRE", KEYS[1], ttl_ms)
end
return { "ok" }
"#
);

// Failed adoption must not filter Redis timestamps using the client's clock or
// restore a complete snapshot over a concurrently renewed sibling.
pub(super) const REPLACE_HOLDER_SCRIPT: &str = token_script!(
    r#"
local lease_ns, ttl_ms = lease_values(ARGV[7])
local restored_time = normalize_decimal(ARGV[6])
if not lease_ns or not valid_owner(ARGV[2]) or not valid_owner(ARGV[3])
    or not valid_owner(ARGV[4]) or ARGV[3] == ARGV[4] or not restored_time
    or (ARGV[5] ~= "E" and ARGV[5] ~= "T") then
    return { "invalid_request", "invalid holder rollback" }
end
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if not raw then return { "missing" } end
local token = decode_token(raw)
if not token then return { "invalid_token", raw } end
if token.owner ~= ARGV[2] then return { "owner_mismatch" } end
local stale_before = decimal_subtract(server_now_ns(), lease_ns)
local found = false
local source_found = false
local refs = {}
for _, h in ipairs(token.holders or {}) do
    if h.id == ARGV[3] then
        found = true
    elseif decimal_less(h.time_ns, stale_before) ~= true then
        table.insert(refs, h)
        if h.id == ARGV[4] then source_found = true end
    end
end
if not found then return { "missing" } end
if not source_found and decimal_less(restored_time, stale_before) ~= true then
    table.insert(refs, { id = ARGV[4], kind = ARGV[5], time_ns = restored_time })
end
if #refs == 0 then
    redis.call("HDEL", KEYS[1], ARGV[1])
else
    table.sort(refs, holder_less)
    local parts = {}
    for _, h in ipairs(refs) do
        table.insert(parts, h.id .. ":" .. h.kind .. ":" .. h.time_ns)
    end
    redis.call("HSET", KEYS[1], ARGV[1], "v2|" .. token.owner .. "|" .. table.concat(parts, ","))
end
if redis.call("HLEN", KEYS[1]) > 0 then redis.call("PEXPIRE", KEYS[1], ttl_ms) end
return { "ok" }
"#
);

pub(super) const IS_LOCKED_SCRIPT: &str = token_script!(
    r#"
local path = ARGV[1]
local lease_ns = lease_values(ARGV[2])
local ignore_stale = ARGV[3] == "1"
if not lease_ns then
    return { "invalid_request", "invalid lease_ms" }
end
local stale_before_ns = decimal_subtract(server_now_ns(), lease_ns)
if not stale_before_ns then
    return { "invalid_request", "invalid Redis time" }
end

local fields = { path }
for _, ancestor in ipairs(ancestors(path)) do
    table.insert(fields, ancestor)
end
local values = redis.call("HMGET", KEYS[1], unpack(fields))
for index, raw in ipairs(values) do
    if raw then
        local token = decode_token(raw, ignore_stale and stale_before_ns or nil)
        if not token then
            return { "invalid_token", fields[index], raw }
        end
        local live = not ignore_stale
            or decimal_less(token.time_ns, stale_before_ns) ~= true
        if live and (index == 1 or token.kind == "T") then
            return { "ok", 1 }
        end
    end
end
return { "ok", 0 }
"#
);

pub(super) const SCAN_DESCENDANTS_SCRIPT: &str = r#"
local root = ARGV[1]
local values = redis.call("HGETALL", KEYS[1])
local result = { "ok" }
for index = 1, #values, 2 do
    local path = values[index]
    local matches = root == "/" or path == root
        or string.sub(path, 1, string.len(root) + 1) == root .. "/"
    if matches then
        table.insert(result, path)
        table.insert(result, values[index + 1])
    end
end
return result
"#;

pub(super) const READ_MANY_SCRIPT: &str = r#"
if #ARGV == 0 then
    return {}
end
return redis.call("HMGET", KEYS[1], unpack(ARGV))
"#;

pub(super) const CREATE_SCRIPT: &str = token_script!(
    r#"
local lease_ns, ttl_ms = lease_values(ARGV[4])
local supplied = ARGV[5] and decode_token(ARGV[5])
if not lease_ns or not valid_owner(ARGV[2])
    or (ARGV[3] ~= "E" and ARGV[3] ~= "T") then
    return { "invalid_request", "invalid create arguments" }
end
local current = redis.call("HGET", KEYS[1], ARGV[1])
if current then
    return { "exists", current }
end
local time_ns = server_now_ns()
if not time_ns then
    return { "invalid_request", "invalid Redis time" }
end
local replacement = ARGV[2] .. ":" .. time_ns .. ":" .. ARGV[3]
if supplied and supplied.holders then
    if supplied.owner ~= ARGV[2] then
        return { "invalid_request", "invalid holder owner" }
    end
    local parts = {}
    for _, h in ipairs(supplied.holders) do
        table.insert(parts, h.id .. ":" .. h.kind .. ":" .. time_ns)
    end
    replacement = "v2|" .. supplied.owner .. "|" .. table.concat(parts, ",")
end
redis.call("HSET", KEYS[1], ARGV[1], replacement)
redis.call("PEXPIRE", KEYS[1], ttl_ms)
return { "created" }
"#
);

macro_rules! script {
    ($id:expr, $lua:expr) => {
        ScriptDefinition {
            id: $id,
            redis_lua: $lua,
        }
    };
}

pub(super) const SCRIPT_DEFINITIONS: &[ScriptDefinition] = &[
    script!(ACQUIRE_BATCH_ID, ACQUIRE_BATCH_SCRIPT),
    script!(ROLLBACK_BATCH_ID, ROLLBACK_BATCH_SCRIPT),
    script!(COMPARE_WRITE_ID, COMPARE_WRITE_SCRIPT),
    script!(COMPARE_REMOVE_ID, COMPARE_REMOVE_SCRIPT),
    script!(REFRESH_ID, REFRESH_SCRIPT),
    script!(REFRESH_BATCH_ID, REFRESH_BATCH_SCRIPT),
    script!(MUTATE_HOLDER_ID, MUTATE_HOLDER_SCRIPT),
    script!(REPLACE_HOLDER_ID, REPLACE_HOLDER_SCRIPT),
    script!(IS_LOCKED_ID, IS_LOCKED_SCRIPT),
    script!(SCAN_DESCENDANTS_ID, SCAN_DESCENDANTS_SCRIPT),
    script!(READ_MANY_ID, READ_MANY_SCRIPT),
    script!(CREATE_ID, CREATE_SCRIPT),
];
