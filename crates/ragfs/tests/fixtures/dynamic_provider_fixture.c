#include "openviking_cache_provider_v1.h"

#include <stdatomic.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    const OvCacheHostApiV1 *host;
} FixtureProvider;

static const OvCacheHostApiV1 *fixture_host = NULL;
static _Atomic uint32_t fixture_close_calls = 0;

static int32_t fixture_copy(
    const uint8_t *data,
    size_t len,
    OvCacheOwnedBufferV1 *output) {
    output->data = fixture_host->alloc(len, OV_CACHE_ALIGNOF(uint8_t));
    if (len > 0 && output->data == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    if (len > 0) {
        memcpy(output->data, data, len);
    }
    output->len = len;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_create(
    const OvCacheHostApiV1 *host,
    OvCacheByteSliceV1 params_json,
    void **provider,
    OvCacheOwnedBufferV1 *error) {
    (void)params_json;
    (void)error;
    FixtureProvider *state = (FixtureProvider *)malloc(sizeof(FixtureProvider));
    if (state == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    state->host = host;
    fixture_host = host;
    *provider = state;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_ping(void *provider, OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)error;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_close(void *provider, OvCacheOwnedBufferV1 *error) {
    (void)error;
    atomic_fetch_add(&fixture_close_calls, 1);
    free(provider);
#ifdef OV_FIXTURE_CLOSE_ERROR
    return OV_CACHE_STATUS_INTERNAL;
#else
    return OV_CACHE_STATUS_OK;
#endif
}

static int32_t fixture_get(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheOptionalOwnedBufferV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    static const uint8_t value[] = "fixture-value";
    output->present = 1;
    int32_t status = fixture_copy(value, sizeof(value) - 1, &output->value);
#ifdef OV_FIXTURE_BAD_LENGTH
    if (status == OV_CACHE_STATUS_OK) {
        output->value.len += 1;
    }
#endif
    return status;
}

static int32_t fixture_expire(
    void *provider,
    OvCacheByteSliceV1 key,
    uint64_t duration_ms,
    uint8_t *applied,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)duration_ms;
    (void)error;
    *applied = 1;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_hget(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheByteSliceV1 field,
    OvCacheOptionalOwnedBufferV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)field;
    (void)error;
    static const uint8_t value[] = "fixture-hash-value";
    output->present = 1;
    return fixture_copy(value, sizeof(value) - 1, &output->value);
}

static int32_t fixture_zscan(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheByteSliceV1 pattern,
    uint32_t count,
    OvCacheOwnedBufferArrayV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)pattern;
    (void)count;
    (void)error;
    output->items = (OvCacheOwnedBufferV1 *)fixture_host->alloc(
        sizeof(OvCacheOwnedBufferV1),
        OV_CACHE_ALIGNOF(OvCacheOwnedBufferV1));
    if (output->items == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    output->len = 1;
    static const uint8_t value[] = "bucket";
    return fixture_copy(value, sizeof(value) - 1, &output->items[0]);
}

static int32_t fixture_zrem(
    void *provider,
    OvCacheByteSliceV1 key,
    const OvCacheByteSliceV1 *members,
    size_t members_len,
    uint64_t *removed,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)members;
    (void)members_len;
    (void)error;
    *removed = 1;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_del(
    void *provider,
    const OvCacheByteSliceV1 *keys,
    size_t keys_len,
    uint64_t *removed,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)keys;
    (void)keys_len;
    (void)error;
    *removed = 77;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_unlink(
    void *provider,
    const OvCacheByteSliceV1 *keys,
    size_t keys_len,
    uint64_t *removed,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)keys;
    (void)error;
    *removed = (uint64_t)keys_len;
    return OV_CACHE_STATUS_OK;
}

/* Return a valid scalar result to advertise atomic script execution. */
static int32_t fixture_execute_script(
    void *provider,
    OvCacheByteSliceV1 script_id,
    OvCacheByteSliceV1 script_source,
    const OvCacheByteSliceV1 *keys,
    size_t keys_len,
    const OvCacheByteSliceV1 *args,
    size_t args_len,
    OvCacheScriptValueV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)script_id;
    (void)script_source;
    (void)keys;
    (void)keys_len;
    (void)args;
    (void)args_len;
    (void)error;
    output->kind = OV_CACHE_SCRIPT_INTEGER;
    output->integer = 1;
    return OV_CACHE_STATUS_OK;
}

/* Compare a borrowed ABI byte slice with one literal ASCII value. */
static int fixture_slice_equals(OvCacheByteSliceV1 value, const char *expected) {
    size_t length = strlen(expected);
    return value.len == length && memcmp(value.data, expected, length) == 0;
}

/* Release a host-owned buffer during fixture error cleanup. */
static void fixture_release(OvCacheOwnedBufferV1 *buffer) {
    if (buffer->data != NULL) {
        fixture_host->dealloc(
            buffer->data, buffer->len, OV_CACHE_ALIGNOF(uint8_t));
    }
    buffer->data = NULL;
    buffer->len = 0;
}

/* Check that a V1 hash write contains one literal field/value pair. */
static int fixture_has_field(
    const OvCacheFieldValueV1 *entries,
    size_t entries_len,
    const char *field,
    const char *value) {
    for (size_t index = 0; index < entries_len; ++index) {
        if (fixture_slice_equals(entries[index].field, field) &&
            fixture_slice_equals(entries[index].value, value)) {
            return 1;
        }
    }
    return 0;
}

/* Check that a V1 sorted-set write contains one literal score/member pair. */
static int fixture_has_scored_member(
    const OvCacheScoredMemberV1 *entries,
    size_t entries_len,
    double score,
    const char *member) {
    for (size_t index = 0; index < entries_len; ++index) {
        if (entries[index].score == score &&
            fixture_slice_equals(entries[index].member, member)) {
            return 1;
        }
    }
    return 0;
}

/* Return one V1 hash field/value pair through host-owned buffers. */
static int32_t fixture_hgetall(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheOwnedFieldValueArrayV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    static const uint8_t field[] = "state";
    static const uint8_t value[] = "ready";
    output->items = (OvCacheOwnedFieldValueV1 *)fixture_host->alloc(
        sizeof(OvCacheOwnedFieldValueV1),
        OV_CACHE_ALIGNOF(OvCacheOwnedFieldValueV1));
    if (output->items == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    output->len = 1;
    memset(output->items, 0, sizeof(OvCacheOwnedFieldValueV1));
    int32_t status = fixture_copy(
        field, sizeof(field) - 1, &output->items[0].field);
    if (status == OV_CACHE_STATUS_OK) {
        status = fixture_copy(
            value, sizeof(value) - 1, &output->items[0].value);
    }
    if (status != OV_CACHE_STATUS_OK) {
        fixture_release(&output->items[0].field);
        fixture_release(&output->items[0].value);
        fixture_host->dealloc(
            (uint8_t *)output->items,
            sizeof(OvCacheOwnedFieldValueV1),
            OV_CACHE_ALIGNOF(OvCacheOwnedFieldValueV1));
        output->items = NULL;
        output->len = 0;
    }
    return status;
}

/* Verify a V1 HSET batch and return its inserted field count. */
static int32_t fixture_hset(
    void *provider,
    OvCacheByteSliceV1 key,
    const OvCacheFieldValueV1 *entries,
    size_t entries_len,
    uint64_t *added,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    if (entries_len != 2 ||
        !fixture_has_field(entries, entries_len, "state", "ready") ||
        !fixture_has_field(entries, entries_len, "version", "2")) {
        return OV_CACHE_STATUS_INVALID_ARGUMENT;
    }
    *added = 2;
    return OV_CACHE_STATUS_OK;
}

/* Verify a V1 HSETNX batch and return its inserted field count. */
static int32_t fixture_hsetnx(
    void *provider,
    OvCacheByteSliceV1 key,
    const OvCacheFieldValueV1 *entries,
    size_t entries_len,
    uint64_t *added,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    if (entries_len != 1 ||
        !fixture_has_field(entries, entries_len, "checkpoint", "first")) {
        return OV_CACHE_STATUS_INVALID_ARGUMENT;
    }
    *added = 1;
    return OV_CACHE_STATUS_OK;
}

/* Verify a V1 ZADD batch and return its inserted member count. */
static int32_t fixture_zadd(
    void *provider,
    OvCacheByteSliceV1 key,
    const OvCacheScoredMemberV1 *entries,
    size_t entries_len,
    uint64_t *added,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    if (entries_len != 2 ||
        !fixture_has_scored_member(entries, entries_len, 3.5, "three") ||
        !fixture_has_scored_member(entries, entries_len, 7.0, "seven")) {
        return OV_CACHE_STATUS_INVALID_ARGUMENT;
    }
    *added = 2;
    return OV_CACHE_STATUS_OK;
}

/* Verify a conditional V1 ZADD batch and return its inserted member count. */
static int32_t fixture_zadd_conditional(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheZaddOptionsV1 options,
    const OvCacheScoredMemberV1 *entries,
    size_t entries_len,
    uint64_t *added,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    if (options.condition != OV_CACHE_ZADD_CONDITION_NX ||
        entries_len != 1 ||
        !fixture_has_scored_member(entries, entries_len, 8.0, "new")) {
        return OV_CACHE_STATUS_INVALID_ARGUMENT;
    }
    *added = 1;
    return OV_CACHE_STATUS_OK;
}

/* Return ordered V1 ZMSCORE values, including one missing member. */
static int32_t fixture_zmscore(
    void *provider,
    OvCacheByteSliceV1 key,
    const OvCacheByteSliceV1 *members,
    size_t members_len,
    OvCacheOwnedOptionalScoreArrayV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    if (members_len != 2 ||
        !fixture_slice_equals(members[0], "three") ||
        !fixture_slice_equals(members[1], "missing")) {
        return OV_CACHE_STATUS_INVALID_ARGUMENT;
    }
    output->items = (OvCacheOptionalScoreV1 *)fixture_host->alloc(
        2 * sizeof(OvCacheOptionalScoreV1),
        OV_CACHE_ALIGNOF(OvCacheOptionalScoreV1));
    if (output->items == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    output->len = 2;
    output->items[0].score = 3.5;
    output->items[0].present = 1;
    output->items[1].score = 0.0;
    output->items[1].present = 0;
#ifdef OV_FIXTURE_BAD_ZMSCORE_LENGTH
    output->len = 1;
#endif
#ifdef OV_FIXTURE_BAD_ZMSCORE_PRESENT
    output->items[1].present = 2;
#endif
#ifdef OV_FIXTURE_BAD_ZMSCORE_NAN
    output->items[0].score = NAN;
#endif
    return OV_CACHE_STATUS_OK;
}

/* Return two scored V1 members after checking range and pagination inputs. */
static int32_t fixture_zrangebyscore(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheScoreRangeV1 range,
    OvCacheOwnedScoredMemberArrayV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    static const uint8_t first[] = "three";
    static const uint8_t second[] = "seven";
    if (range.min != 1.0 || range.min_exclusive != 1 ||
        range.max != 9.0 || range.max_exclusive != 0 ||
        range.offset != 1 || range.limit != 2) {
        return OV_CACHE_STATUS_INVALID_ARGUMENT;
    }
    output->items = (OvCacheOwnedScoredMemberV1 *)fixture_host->alloc(
        2 * sizeof(OvCacheOwnedScoredMemberV1),
        OV_CACHE_ALIGNOF(OvCacheOwnedScoredMemberV1));
    if (output->items == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    output->len = 2;
    memset(
        output->items, 0, 2 * sizeof(OvCacheOwnedScoredMemberV1));
    output->items[0].score = 3.5;
    output->items[1].score = 7.0;
    int32_t status = fixture_copy(
        first, sizeof(first) - 1, &output->items[0].member);
    if (status == OV_CACHE_STATUS_OK) {
        status = fixture_copy(
            second, sizeof(second) - 1, &output->items[1].member);
    }
    if (status != OV_CACHE_STATUS_OK) {
        fixture_release(&output->items[0].member);
        fixture_release(&output->items[1].member);
        fixture_host->dealloc(
            (uint8_t *)output->items,
            2 * sizeof(OvCacheOwnedScoredMemberV1),
            OV_CACHE_ALIGNOF(OvCacheOwnedScoredMemberV1));
        output->items = NULL;
        output->len = 0;
    }
    return status;
}

/* Return the literal cardinality expected by the V1 forwarding test. */
static int32_t fixture_zcard(
    void *provider,
    OvCacheByteSliceV1 key,
    uint64_t *count,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    *count = 2;
    return OV_CACHE_STATUS_OK;
}

/* Return the literal millisecond clock expected by the V1 forwarding test. */
static int32_t fixture_server_time_for_key(
    void *provider,
    OvCacheByteSliceV1 key,
    uint64_t *time_ms,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    *time_ms = 1700000000123ULL;
    return OV_CACHE_STATUS_OK;
}

OV_CACHE_PROVIDER_EXPORT uint32_t ov_fixture_close_count(void) {
    return atomic_load(&fixture_close_calls);
}

static const OvCacheProviderApiV1 FIXTURE_API = {
#ifdef OV_FIXTURE_BAD_ABI
    .abi_version = OV_CACHE_PROVIDER_ABI_V1 + 1,
#else
    .abi_version = OV_CACHE_PROVIDER_ABI_V1,
#endif
    .struct_size = sizeof(OvCacheProviderApiV1),
    .create = fixture_create,
    .ping = fixture_ping,
    .close = fixture_close,
    .get = fixture_get,
    .del = fixture_del,
    .expire = fixture_expire,
    .hget = fixture_hget,
    .hset = fixture_hset,
    .hsetnx = fixture_hsetnx,
    .zadd = fixture_zadd,
    .zadd_conditional = fixture_zadd_conditional,
    .zmscore = fixture_zmscore,
    .zscan = fixture_zscan,
    .zrem = fixture_zrem,
    .execute_script = fixture_execute_script,
#ifndef OV_FIXTURE_NO_UNLINK
    .unlink = fixture_unlink,
#endif
#ifndef OV_FIXTURE_NO_ZRANGEBYSCORE
    .zrangebyscore = fixture_zrangebyscore,
#endif
    .hgetall = fixture_hgetall,
    .zcard = fixture_zcard,
    .server_time_for_key = fixture_server_time_for_key,
};

OV_CACHE_PROVIDER_EXPORT
const OvCacheProviderApiV1 *openviking_cache_provider_v1(void) {
    return &FIXTURE_API;
}
