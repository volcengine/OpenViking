use super::backend::StoredMessage;
use crate::core::errors::{Error, Result};
use std::time::{SystemTime, UNIX_EPOCH};

pub(super) const HEARTBEAT_TTL_SECS: u64 = 30;
pub(super) const HEARTBEAT_INTERVAL_SECS: u64 = 10;
pub(super) const RECOVERY_INTERVAL_SECS: u64 = 10;
pub(super) const RECOVERY_LEADER_TTL_SECS: u64 = 30;

// Every queue script receives the same keys (see QueueKeys::script_keys).
// Dynamic group/node keys retain the queue's hash tag. Hex-encoded group keys
// cannot introduce separators or alias another queue's state keys.
macro_rules! queue_script {
    ($body:literal) => {
        concat!(
            r#"
local meta, pending, processing = KEYS[1], KEYS[2], KEYS[3]
local message_group, active, owner, inflight = KEYS[4], KEYS[5], KEYS[6], KEYS[7]
local delivery_owner, ready_free, names = KEYS[8], KEYS[9], KEYS[10]
local prefix = string.sub(meta, 1, -6)
local function pending_count() return redis.call('ZCARD', pending) end
local function pending_ids() return redis.call('ZRANGE', pending, 0, -1) end
local function group_list(group, p)
    if group == 'p' then return (p or prefix) .. ':plain_pending' end
    return (p or prefix) .. ':group_pending:' .. group
end
local function node_ready(instance) return prefix .. ':ready_node:' .. instance end
local function mark_ready(group, list, queued_id)
    list = list or group_list(group)
    -- A just-pushed message proves the list is nonempty. Its ID also avoids
    -- reading the head when determining serial eligibility.
    if not queued_id and redis.call('LLEN', list) == 0 then return end
    if string.sub(group, 1, 2) == 's:' then
        local id = redis.call('HGET', active, group)
        if not id or id == (queued_id or redis.call('LINDEX', list, 0)) then
            redis.call('SADD', ready_free, group)
        end
    elseif group == 'p' then
        redis.call('SADD', ready_free, group)
    else
        local instance = redis.call('HGET', owner, group)
        redis.call('SADD', instance and node_ready(instance) or ready_free, group)
    end
end
local function push_pending(id, group, recovering)
    group = group or 'p'
    redis.call('ZADD', pending, redis.call('HINCRBY', meta, 'pending_sequence', 1), id)
    local list = group_list(group)
    if recovering and string.sub(group, 1, 2) == 's:' then
        redis.call('LPUSH', list, id)
    else
        redis.call('RPUSH', list, id)
    end
    mark_ready(group, list, id)
end
local function clear_queue(p)
    local ids = redis.call('ZRANGE', p .. ':pending', 0, -1)
    local deliveries = redis.call('ZRANGE', p .. ':processing', 0, -1)
    for _, id in ipairs(ids) do redis.call('DEL', p .. ':msg:' .. id) end
    for _, id in ipairs(deliveries) do redis.call('DEL', p .. ':msg:' .. id) end
    for _, group in ipairs(redis.call('HVALS', p .. ':message_group')) do
        redis.call('DEL', group_list(group, p))
    end
    for _, instance in ipairs(redis.call('HVALS', p .. ':group_owner')) do
        redis.call('DEL', p .. ':ready_node:' .. instance)
    end
    redis.call('DEL', p .. ':pending', p .. ':processing', p .. ':message_group',
        p .. ':group_active', p .. ':group_owner', p .. ':group_inflight',
        p .. ':delivery_owner', p .. ':ready_free', p .. ':plain_pending')
    redis.call('HDEL', p .. ':meta', 'pending_sequence', 'schedule_turn')
    return #ids + #deliveries
end
"#,
            $body
        )
    };
}

pub(super) const CREATE_QUEUE_SCRIPT: &str = queue_script!(
    r#"
if redis.call('SADD', names, ARGV[1]) == 0 then
    return 0
end
redis.call('HSET', meta, 'created_at', ARGV[2], 'last_updated', ARGV[2])
return 1
"#
);

pub(super) const REMOVE_QUEUE_SCRIPT: &str = queue_script!(
    r#"
local removed = 0
local queues = redis.call('SMEMBERS', names)
for _, queue in ipairs(queues) do
    if queue == ARGV[1] or string.sub(queue, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '/' then
        clear_queue(ARGV[2] .. queue)
        redis.call('DEL', ARGV[2] .. queue .. ':meta')
        redis.call('SREM', names, queue)
        removed = removed + 1
    end
end
return removed
"#
);

pub(super) const ENQUEUE_SCRIPT: &str = queue_script!(
    r#"
if redis.call('SISMEMBER', names, ARGV[1]) == 0 then
    return 0
end
if not redis.call('SET', prefix .. ':msg:' .. ARGV[2], ARGV[3], 'NX') then
    return -2
end
if ARGV[5] ~= 'p' then
    redis.call('HSET', message_group, ARGV[2], ARGV[5])
end
push_pending(ARGV[2], ARGV[5], false)
redis.call('HSET', meta, 'last_updated', ARGV[4])
return 1
"#
);

pub(super) const DEQUEUE_SCRIPT: &str = queue_script!(
    r#"
local free = redis.call('SRANDMEMBER', ready_free)
local own = redis.call('SRANDMEMBER', node_ready(ARGV[2]))
local group
if free and own then
    -- Alternate the two sources; SET selection does not promise strict fairness.
    local turn = redis.call('HGET', meta, 'schedule_turn') or '0'
    group = turn == '0' and free or own
    redis.call('HSET', meta, 'schedule_turn', turn == '0' and '1' or '0')
else
    group = free or own
end
if not group then return nil end
local list = group_list(group)
local id = redis.call('LINDEX', list, 0)
if not id then
    return redis.error_reply('queuefs ready group has no pending message')
end
local payload = redis.call('GET', ARGV[1] .. id)
if not payload then
    return redis.error_reply('queuefs payload missing for message ' .. id)
end
redis.call('LPOP', list)
redis.call('ZREM', pending, id)
redis.call('SREM', ready_free, group)
if string.sub(group, 1, 2) == 's:' then
    redis.call('HSET', active, group, id)
elseif group == 'p' then
    mark_ready(group, list)
else
    redis.call('HSET', owner, group, ARGV[2])
    redis.call('HINCRBY', inflight, group, 1)
    if redis.call('LLEN', list) > 0 then
        redis.call('SADD', node_ready(ARGV[2]), group)
    else
        redis.call('SREM', node_ready(ARGV[2]), group)
    end
end
redis.call('ZADD', processing, ARGV[3], id)
redis.call('HSET', delivery_owner, id, ARGV[2])
return {id, payload}
"#
);

pub(super) const PEEK_SCRIPT: &str = queue_script!(
    r#"
local id = redis.call('ZRANGE', pending, 0, 0)[1]
if not id then
    return nil
end
local payload = redis.call('GET', ARGV[1] .. id)
if not payload then
    return redis.error_reply('queuefs payload missing for message ' .. id)
end
return payload
"#
);

pub(super) const STATUS_SCRIPT: &str = queue_script!(
    r#"
if redis.call('SISMEMBER', names, ARGV[1]) == 0 then
    return nil
end
return {pending_count(), redis.call('ZCARD', processing)}
"#
);

pub(super) const PENDING_IDS_SCRIPT: &str = queue_script!(
    r#"
return pending_ids()
"#
);

pub(super) const LIST_UNACKED_SCRIPT: &str = queue_script!(
    r#"
local result = {}
for _, id in ipairs(pending_ids()) do
    local payload = redis.call('GET', ARGV[1] .. id)
    if not payload then
        return redis.error_reply('queuefs payload missing for message ' .. id)
    end
    table.insert(result, payload)
end
for _, id in ipairs(redis.call('ZRANGE', processing, 0, -1)) do
    local payload = redis.call('GET', ARGV[1] .. id)
    if not payload then
        return redis.error_reply('queuefs payload missing for message ' .. id)
    end
    table.insert(result, payload)
end
return result
"#
);

pub(super) const ACK_SCRIPT: &str = queue_script!(
    r#"
if redis.call('ZREM', processing, ARGV[1]) == 0 then return 0 end
redis.call('HDEL', delivery_owner, ARGV[1])
local group = redis.call('HGET', message_group, ARGV[1])
if group then
    redis.call('HDEL', message_group, ARGV[1])
    if string.sub(group, 1, 2) == 's:' then
        if redis.call('HGET', active, group) == ARGV[1] then
            redis.call('HDEL', active, group)
            mark_ready(group)
        end
    elseif redis.call('HINCRBY', inflight, group, -1) == 0 then
        local instance = redis.call('HGET', owner, group)
        redis.call('SREM', node_ready(instance), group)
        redis.call('HDEL', inflight, group)
        redis.call('HDEL', owner, group)
        mark_ready(group)
    end
end
redis.call('DEL', prefix .. ':msg:' .. ARGV[1])
return 1
"#
);

pub(super) const CLEAR_SCRIPT: &str = queue_script!(
    r#"
return clear_queue(prefix)
"#
);

pub(super) const RETRY_SCRIPT: &str = queue_script!(
    r#"
if redis.call('ZREM', processing, ARGV[1]) == 0 then return 0 end
redis.call('HDEL', delivery_owner, ARGV[1])
local group = redis.call('HGET', message_group, ARGV[1])
-- A serial delivery keeps its active ID and goes ahead of its followers.
-- Node affinity is released only when the final inflight delivery returns.
if group and string.sub(group, 1, 2) ~= 's:' then
    if redis.call('HINCRBY', inflight, group, -1) == 0 then
        local instance = redis.call('HGET', owner, group)
        redis.call('SREM', node_ready(instance), group)
        redis.call('HDEL', inflight, group)
        redis.call('HDEL', owner, group)
    end
end
push_pending(ARGV[1], group, true)
return 1
"#
);

pub(super) const RECOVER_STALE_SCRIPT: &str = queue_script!(
    r#"
local recovered = 0
local alive = {}
for _, id in ipairs(redis.call('ZRANGE', processing, 0, -1)) do
    local instance = redis.call('HGET', delivery_owner, id)
    if not instance then return redis.error_reply('queuefs delivery owner missing') end
    if alive[instance] == nil then
        alive[instance] = redis.call('EXISTS', ARGV[1] .. instance .. ':alive')
    end
    if alive[instance] == 0 then
        local group = redis.call('HGET', message_group, id)
        redis.call('ZREM', processing, id)
        redis.call('HDEL', delivery_owner, id)
        push_pending(id, group, true)
        recovered = recovered + 1
    end
end
-- Release node ownership only after all of the dead instance's deliveries
-- have been requeued. Serial active IDs remain until their eventual ACK.
local owners = redis.call('HGETALL', owner)
for i = 1, #owners, 2 do
    local group, instance = owners[i], owners[i + 1]
    if alive[instance] == nil then
        alive[instance] = redis.call('EXISTS', ARGV[1] .. instance .. ':alive')
    end
    if alive[instance] == 0 then
        redis.call('SREM', node_ready(instance), group)
        redis.call('HDEL', owner, group)
        redis.call('HDEL', inflight, group)
        mark_ready(group)
    end
end
return recovered
"#
);

pub(super) const ACQUIRE_RECOVERY_LEADER_SCRIPT: &str = r#"
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) then
    return 1
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
    return 1
end
return 0
"#;

pub(super) const RELEASE_RECOVERY_LEADER_SCRIPT: &str = r#"
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"#;

pub(super) struct QueueKeys {
    pub(super) meta: String,
    pub(super) pending: String,
    pub(super) processing: String,
    pub(super) message_prefix: String,
    pub(super) message_group: String,
    pub(super) group_active: String,
    pub(super) group_owner: String,
    pub(super) group_inflight: String,
    pub(super) delivery_owner: String,
    pub(super) ready_free: String,
}

impl QueueKeys {
    pub(super) fn new(key_prefix: &str, queue: &str) -> Self {
        let prefix = format!("{}{queue}", queue_key_prefix(key_prefix));
        Self {
            meta: format!("{prefix}:meta"),
            pending: format!("{prefix}:pending"),
            processing: format!("{prefix}:processing"),
            message_prefix: format!("{prefix}:msg:"),
            message_group: format!("{prefix}:message_group"),
            group_active: format!("{prefix}:group_active"),
            group_owner: format!("{prefix}:group_owner"),
            group_inflight: format!("{prefix}:group_inflight"),
            delivery_owner: format!("{prefix}:delivery_owner"),
            ready_free: format!("{prefix}:ready_free"),
        }
    }

    pub(super) fn script_keys(self, key_prefix: &str) -> Vec<String> {
        vec![
            self.meta,
            self.pending,
            self.processing,
            self.message_group,
            self.group_active,
            self.group_owner,
            self.group_inflight,
            self.delivery_owner,
            self.ready_free,
            queue_names_key(key_prefix),
        ]
    }
}

pub(super) fn encoded_group_key(key: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(key.len() * 2);
    for byte in key.bytes() {
        encoded.push(HEX[(byte >> 4) as usize] as char);
        encoded.push(HEX[(byte & 15) as usize] as char);
    }
    encoded
}

pub(super) fn queue_names_key(key_prefix: &str) -> String {
    format!("{}names", queue_key_prefix(key_prefix))
}

pub(super) fn queue_key_prefix(key_prefix: &str) -> String {
    format!("{{{key_prefix}}}:ov:queue:")
}

pub(super) fn instance_key_prefix(key_prefix: &str) -> String {
    format!("{}instance:", queue_key_prefix(key_prefix))
}

pub(super) fn heartbeat_key(key_prefix: &str, instance_id: &str) -> String {
    format!("{}{instance_id}:alive", instance_key_prefix(key_prefix))
}

pub(super) fn recovery_leader_key(key_prefix: &str) -> String {
    format!("{}recovery:leader", queue_key_prefix(key_prefix))
}

pub(super) fn unix_secs(time: SystemTime) -> u64 {
    time.duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub(super) fn last_enqueue_time_from_pending_payloads(payloads: &[String]) -> Result<SystemTime> {
    payloads.iter().try_fold(UNIX_EPOCH, |latest, payload| {
        let timestamp = serde_json::from_str::<StoredMessage>(payload)
            .map(StoredMessage::into_message)
            .map(|message| message.timestamp)
            .map_err(|error| Error::Serialization(format!("invalid queue payload: {error}")))?;
        Ok(if timestamp > latest {
            timestamp
        } else {
            latest
        })
    })
}
