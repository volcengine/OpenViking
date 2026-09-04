#ifndef OPENVIKING_CACHE_PROVIDER_V1_H
#define OPENVIKING_CACHE_PROVIDER_V1_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define OV_CACHE_PROVIDER_EXPORT __declspec(dllexport)
#elif defined(__GNUC__) || defined(__clang__)
#define OV_CACHE_PROVIDER_EXPORT __attribute__((visibility("default")))
#else
#define OV_CACHE_PROVIDER_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#define OV_CACHE_ALIGNOF(type) alignof(type)
#else
#define OV_CACHE_ALIGNOF(type) _Alignof(type)
#endif

#define OV_CACHE_PROVIDER_ABI_V1 1u

#define OV_CACHE_STATUS_OK 0
#define OV_CACHE_STATUS_TIMEOUT 2
#define OV_CACHE_STATUS_UNAVAILABLE 3
#define OV_CACHE_STATUS_AUTHENTICATION 4
#define OV_CACHE_STATUS_PERMISSION_DENIED 5
#define OV_CACHE_STATUS_INVALID_ARGUMENT 6
#define OV_CACHE_STATUS_INVALID_DATA 7
#define OV_CACHE_STATUS_CROSS_SLOT 8
#define OV_CACHE_STATUS_READ_ONLY 9
#define OV_CACHE_STATUS_UNSUPPORTED_OPERATION 10
#define OV_CACHE_STATUS_INTERNAL 11

#define OV_CACHE_SET_CONDITION_NONE 0u
#define OV_CACHE_SET_CONDITION_NX 1u
#define OV_CACHE_SET_CONDITION_XX 2u

#define OV_CACHE_SET_APPLIED 0u
#define OV_CACHE_SET_CONDITION_NOT_MET 1u

#define OV_CACHE_LIST_BEFORE 0u
#define OV_CACHE_LIST_AFTER 1u
#define OV_CACHE_LIST_LEFT 0u
#define OV_CACHE_LIST_RIGHT 1u

#define OV_CACHE_SCRIPT_NULL 0u
#define OV_CACHE_SCRIPT_INTEGER 1u
#define OV_CACHE_SCRIPT_BYTES 2u
#define OV_CACHE_SCRIPT_ARRAY 3u
#define OV_CACHE_SCRIPT_BOOLEAN 4u

typedef struct {
    const uint8_t *data;
    size_t len;
} OvCacheByteSliceV1;

typedef struct {
    uint8_t *data;
    size_t len;
} OvCacheOwnedBufferV1;

typedef struct {
    OvCacheOwnedBufferV1 value;
    uint8_t present;
} OvCacheOptionalOwnedBufferV1;

typedef struct {
    OvCacheOwnedBufferV1 *items;
    size_t len;
} OvCacheOwnedBufferArrayV1;

typedef struct {
    OvCacheOptionalOwnedBufferV1 *items;
    size_t len;
} OvCacheOptionalOwnedBufferArrayV1;

typedef struct {
    OvCacheByteSliceV1 key;
    OvCacheByteSliceV1 value;
} OvCacheKeyValueV1;

typedef struct {
    /* -1 means no expiration; otherwise milliseconds. */
    int64_t expiration_ms;
    uint32_t condition;
    uint8_t keep_ttl;
} OvCacheSetOptionsV1;

typedef struct OvCacheScriptValueV1 OvCacheScriptValueV1;

struct OvCacheScriptValueV1 {
    OvCacheOwnedBufferV1 bytes;
    OvCacheScriptValueV1 *items;
    size_t items_len;
    int64_t integer;
    uint32_t kind;
    uint8_t boolean;
};

typedef uint8_t *(*OvCacheHostAllocV1)(size_t size, size_t alignment);
typedef void (*OvCacheHostDeallocV1)(uint8_t *data, size_t size, size_t alignment);

typedef struct {
    uint32_t abi_version;
    size_t struct_size;
    OvCacheHostAllocV1 alloc;
    OvCacheHostDeallocV1 dealloc;
} OvCacheHostApiV1;

/*
 * Provider contract:
 *
 * - OpenViking consumes cache.params.library and passes the remaining fields
 *   to create as opaque JSON.
 * - Input slices and arrays are borrowed only for the duration of the callback.
 *   The provider must not retain their pointers after the callback returns.
 * - All callbacks use the C ABI. Exceptions and language-runtime panics must
 *   be caught inside the provider library.
 * - A successfully created provider handle must support concurrent calls.
 * - Every returned buffer, array, and nested item must be a separate,
 *   non-aliasing allocation created with host->alloc using its exact C size and
 *   alignment. Extra capacity, interior pointers, shared child buffers, and
 *   splitting one allocation across multiple returned objects are forbidden.
 * - Empty buffers and arrays must use the canonical NULL pointer plus zero
 *   length representation. Unused fields in optional and tagged outputs must
 *   also use their zero representation.
 * - On OV_CACHE_STATUS_OK, the error buffer must be empty and every output must
 *   be fully initialized. On any error status, normal outputs must remain in
 *   their zero representation and only the error buffer may be returned.
 * - The provider owns and must release all partially constructed output before
 *   returning an error. Once a successful output is returned, OpenViking owns
 *   every allocation and releases it through the matching host allocator.
 * - host->alloc may reject unreasonable sizes or invalid alignments by
 *   returning NULL. The provider must handle allocation failure.
 * - create, ping and close are required. Other callbacks are optional; NULL
 *   means that operation is unsupported.
 * - close is terminal and consumes the provider handle even when it returns an
 *   error status. It must stop and join provider-owned background work before
 *   returning. OpenViking never retries close and unloads the library only
 *   after close returns.
 */

typedef int32_t (*OvCacheProviderCreateV1)(
    const OvCacheHostApiV1 *host,
    OvCacheByteSliceV1 params_json,
    void **provider,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderPingV1)(
    void *provider,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderCloseV1)(
    void *provider,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderGetV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheOptionalOwnedBufferV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderSetV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheByteSliceV1 value,
    OvCacheSetOptionsV1 options,
    uint8_t *result,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderDelV1)(
    void *provider,
    const OvCacheByteSliceV1 *keys,
    size_t keys_len,
    uint64_t *removed,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderMgetV1)(
    void *provider,
    const OvCacheByteSliceV1 *keys,
    size_t keys_len,
    OvCacheOptionalOwnedBufferArrayV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderMsetV1)(
    void *provider,
    const OvCacheKeyValueV1 *entries,
    size_t entries_len,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderIncrbyV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    int64_t delta,
    int64_t *value,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderSismemberV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheByteSliceV1 member,
    uint8_t *present,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderSmembersV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheOwnedBufferArrayV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderScardV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    uint64_t *count,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderListPushV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    const OvCacheByteSliceV1 *values,
    size_t values_len,
    uint64_t *length,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderListPopV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    uint8_t has_count,
    uint64_t count,
    OvCacheOwnedBufferArrayV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLlenV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    uint64_t *length,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLrangeV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    int64_t start,
    int64_t stop,
    OvCacheOwnedBufferArrayV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLindexV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    int64_t index,
    OvCacheOptionalOwnedBufferV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLsetV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    int64_t index,
    OvCacheByteSliceV1 value,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLtrimV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    int64_t start,
    int64_t stop,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLremV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    int64_t count,
    OvCacheByteSliceV1 value,
    uint64_t *removed,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLinsertV1)(
    void *provider,
    OvCacheByteSliceV1 key,
    uint32_t position,
    OvCacheByteSliceV1 pivot,
    OvCacheByteSliceV1 value,
    int64_t *length,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderLmoveV1)(
    void *provider,
    OvCacheByteSliceV1 source,
    OvCacheByteSliceV1 destination,
    uint32_t source_direction,
    uint32_t destination_direction,
    OvCacheOptionalOwnedBufferV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef int32_t (*OvCacheProviderExecuteScriptV1)(
    void *provider,
    OvCacheByteSliceV1 script_id,
    OvCacheByteSliceV1 script_source,
    const OvCacheByteSliceV1 *keys,
    size_t keys_len,
    const OvCacheByteSliceV1 *args,
    size_t args_len,
    OvCacheScriptValueV1 *output,
    OvCacheOwnedBufferV1 *error);

typedef struct {
    uint32_t abi_version;
    size_t struct_size;
    OvCacheProviderCreateV1 create;
    OvCacheProviderPingV1 ping;
    OvCacheProviderCloseV1 close;
    OvCacheProviderGetV1 get;
    OvCacheProviderSetV1 set;
    OvCacheProviderDelV1 del;
    OvCacheProviderMgetV1 mget;
    OvCacheProviderMsetV1 mset;
    OvCacheProviderIncrbyV1 incrby;
    OvCacheProviderSismemberV1 sismember;
    OvCacheProviderSmembersV1 smembers;
    OvCacheProviderScardV1 scard;
    OvCacheProviderListPushV1 lpush;
    OvCacheProviderListPushV1 rpush;
    OvCacheProviderListPopV1 lpop;
    OvCacheProviderListPopV1 rpop;
    OvCacheProviderLlenV1 llen;
    OvCacheProviderLrangeV1 lrange;
    OvCacheProviderLindexV1 lindex;
    OvCacheProviderLsetV1 lset;
    OvCacheProviderLtrimV1 ltrim;
    OvCacheProviderLremV1 lrem;
    OvCacheProviderLinsertV1 linsert;
    OvCacheProviderLmoveV1 lmove;
    OvCacheProviderExecuteScriptV1 execute_script;
} OvCacheProviderApiV1;

/* The only symbol resolved by DynamicProvider. */
OV_CACHE_PROVIDER_EXPORT
const OvCacheProviderApiV1 *openviking_cache_provider_v1(void);

#ifdef __cplusplus
}
#endif

#endif
