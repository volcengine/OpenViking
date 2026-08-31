use super::backend::StoredMessage;
use crate::core::errors::{Error, Result};
use std::time::{SystemTime, UNIX_EPOCH};

pub(super) const HEARTBEAT_TTL_SECS: u64 = 30;
pub(super) const HEARTBEAT_INTERVAL_SECS: u64 = 10;
pub(super) const STARTUP_RECOVERY_SWEEPS: usize = 3;

pub(super) const CREATE_QUEUE_SCRIPT: &str = r#"
if redis.call('SADD', KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[2], 'created_at', ARGV[2], 'last_updated', ARGV[2])
return 1
"#;

pub(super) const REMOVE_QUEUE_SCRIPT: &str = r#"
local removed = 0
local queues = redis.call('SMEMBERS', KEYS[1])
for _, queue in ipairs(queues) do
    if queue == ARGV[1] or string.sub(queue, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '/' then
        local prefix = ARGV[2] .. queue
        local pending_key = prefix .. ':pending'
        local processing_key = prefix .. ':processing'
        local message_prefix = prefix .. ':msg:'
        local pending = redis.call('LRANGE', pending_key, 0, -1)
        for _, id in ipairs(pending) do
            redis.call('DEL', message_prefix .. id)
        end
        local processing = redis.call('ZRANGE', processing_key, 0, -1)
        for _, member in ipairs(processing) do
            local separator = string.find(member, '|', 1, true)
            if separator then
                redis.call('DEL', message_prefix .. string.sub(member, 1, separator - 1))
            end
        end
        redis.call('DEL', prefix .. ':meta', pending_key, processing_key)
        redis.call('SREM', KEYS[1], queue)
        removed = removed + 1
    end
end
return removed
"#;

pub(super) const ENQUEUE_SCRIPT: &str = r#"
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call('SET', KEYS[2], ARGV[3])
redis.call('RPUSH', KEYS[3], ARGV[2])
redis.call('HSET', KEYS[4], 'last_updated', ARGV[4])
return 1
"#;

pub(super) const DEQUEUE_SCRIPT: &str = r#"
local id = redis.call('LPOP', KEYS[1])
if not id then
    return nil
end
local payload = redis.call('GET', ARGV[1] .. id)
if not payload then
    redis.call('LPUSH', KEYS[1], id)
    return redis.error_reply('queuefs payload missing for message ' .. id)
end
redis.call('ZADD', KEYS[2], ARGV[3], id .. '|' .. ARGV[2])
return {id, payload}
"#;

pub(super) const PEEK_SCRIPT: &str = r#"
local id = redis.call('LINDEX', KEYS[1], 0)
if not id then
    return nil
end
local payload = redis.call('GET', ARGV[1] .. id)
if not payload then
    return redis.error_reply('queuefs payload missing for message ' .. id)
end
return payload
"#;

pub(super) const LIST_UNACKED_SCRIPT: &str = r#"
local result = {}
local pending = redis.call('LRANGE', KEYS[1], 0, -1)
for _, id in ipairs(pending) do
    local payload = redis.call('GET', ARGV[1] .. id)
    if not payload then
        return redis.error_reply('queuefs payload missing for message ' .. id)
    end
    table.insert(result, payload)
end
local processing = redis.call('ZRANGE', KEYS[2], 0, -1)
for _, member in ipairs(processing) do
    local separator = string.find(member, '|', 1, true)
    if separator then
        local id = string.sub(member, 1, separator - 1)
        local payload = redis.call('GET', ARGV[1] .. id)
        if not payload then
            return redis.error_reply('queuefs payload missing for message ' .. id)
        end
        table.insert(result, payload)
    end
end
return result
"#;

pub(super) const ACK_SCRIPT: &str = r#"
local members = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, member in ipairs(members) do
    if string.sub(member, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '|' then
        redis.call('ZREM', KEYS[1], member)
        redis.call('DEL', KEYS[2])
        return 1
    end
end
return 0
"#;

pub(super) const CLEAR_SCRIPT: &str = r#"
local pending = redis.call('LRANGE', KEYS[1], 0, -1)
for _, id in ipairs(pending) do
    redis.call('DEL', ARGV[1] .. id)
end
local processing = redis.call('ZRANGE', KEYS[2], 0, -1)
for _, member in ipairs(processing) do
    local separator = string.find(member, '|', 1, true)
    if separator then
        redis.call('DEL', ARGV[1] .. string.sub(member, 1, separator - 1))
    end
end
redis.call('DEL', KEYS[1], KEYS[2])
return #pending + #processing
"#;

pub(super) const RECOVER_STALE_SCRIPT: &str = r#"
local recovered = 0
local members = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, member in ipairs(members) do
    local separator = string.find(member, '|', 1, true)
    if separator then
        local id = string.sub(member, 1, separator - 1)
        local instance = string.sub(member, separator + 1)
        if redis.call('EXISTS', ARGV[1] .. instance .. ':alive') == 0 then
            redis.call('ZREM', KEYS[1], member)
            redis.call('RPUSH', KEYS[2], id)
            recovered = recovered + 1
        end
    end
end
return recovered
"#;

pub(super) struct QueueKeys {
    pub(super) meta: String,
    pub(super) pending: String,
    pub(super) processing: String,
    pub(super) message_prefix: String,
}

impl QueueKeys {
    pub(super) fn new(key_prefix: &str, queue: &str) -> Self {
        let prefix = format!("{}{queue}", queue_key_prefix(key_prefix));
        Self {
            meta: format!("{prefix}:meta"),
            pending: format!("{prefix}:pending"),
            processing: format!("{prefix}:processing"),
            message_prefix: format!("{prefix}:msg:"),
        }
    }

    pub(super) fn message(&self, message_id: &str) -> String {
        format!("{}{message_id}", self.message_prefix)
    }
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
