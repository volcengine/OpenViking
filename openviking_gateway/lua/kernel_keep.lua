-- KEYS: tombstone, replay and state keys. ARGV: ttl -> 1
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.error_reply('OVGW_DELETED') end
for i = 2, #KEYS do redis.call('PEXPIRE', KEYS[i], ARGV[1]) end
return 1
