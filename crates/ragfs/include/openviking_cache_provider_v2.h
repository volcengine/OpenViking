#ifndef OPENVIKING_CACHE_PROVIDER_V2_H
#define OPENVIKING_CACHE_PROVIDER_V2_H

#include "openviking_cache_provider_v1.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OV_CACHE_PROVIDER_ABI_V2 2u
typedef struct {
    OvCacheByteSliceV1 field;
    OvCacheByteSliceV1 value;
} OvCacheFieldValueV2;
typedef struct {
    OvCacheOwnedBufferV1 field;
    OvCacheOwnedBufferV1 value;
} OvCacheOwnedFieldValueV2;
typedef struct {
    OvCacheOwnedFieldValueV2 *items;
    size_t len;
} OvCacheOwnedFieldValueArrayV2;
typedef struct {
    double score;
    OvCacheByteSliceV1 member;
} OvCacheScoredMemberV2;
typedef struct {
    double score;
    OvCacheOwnedBufferV1 member;
} OvCacheOwnedScoredMemberV2;
typedef struct {
    OvCacheOwnedScoredMemberV2 *items;
    size_t len;
} OvCacheOwnedScoredMemberArrayV2;
typedef struct {
    double min;
    uint8_t min_exclusive;
    double max;
    uint8_t max_exclusive;
    uint64_t offset;
    uint32_t limit;
} OvCacheScoreRangeV2;
typedef int32_t (*OvCacheProviderHgetallV2)(void *provider, OvCacheByteSliceV1 key, OvCacheOwnedFieldValueArrayV2 *output, OvCacheOwnedBufferV1 *error);
typedef int32_t (*OvCacheProviderHsetV2)(void *provider, OvCacheByteSliceV1 key, const OvCacheFieldValueV2 *entries, size_t entries_len, uint64_t *added, OvCacheOwnedBufferV1 *error);
typedef int32_t (*OvCacheProviderZaddV2)(void *provider, OvCacheByteSliceV1 key, const OvCacheScoredMemberV2 *entries, size_t entries_len, uint64_t *added, OvCacheOwnedBufferV1 *error);
typedef int32_t (*OvCacheProviderZrangeByScoreV2)(void *provider, OvCacheByteSliceV1 key, OvCacheScoreRangeV2 range, OvCacheOwnedScoredMemberArrayV2 *output, OvCacheOwnedBufferV1 *error);
typedef int32_t (*OvCacheProviderZcardV2)(void *provider, OvCacheByteSliceV1 key, uint64_t *count, OvCacheOwnedBufferV1 *error);
typedef int32_t (*OvCacheProviderServerTimeForKeyV2)(void *provider, OvCacheByteSliceV1 key, uint64_t *time_ms, OvCacheOwnedBufferV1 *error);
/*
 * V2 starts with the complete V1 table. The embedded prefix must report
 * abi_version = OV_CACHE_PROVIDER_ABI_V2 and struct_size =
 * sizeof(OvCacheProviderApiV2). V1 callbacks retain their V1 semantics.
 *
 * The V1 output allocation, initialization, ownership, and error rules apply
 * to every V2 output. Field names and members are UTF-8, and returned scores
 * must not be NaN.
 */
typedef struct {
    OvCacheProviderApiV1 v1;
    OvCacheProviderHgetallV2 hgetall;
    OvCacheProviderHsetV2 hset;
    OvCacheProviderZaddV2 zadd;
    OvCacheProviderZrangeByScoreV2 zrangebyscore;
    OvCacheProviderZcardV2 zcard;
    OvCacheProviderServerTimeForKeyV2 server_time_for_key;
} OvCacheProviderApiV2;
/* Preferred entry point resolved before the compatible V1 entry point. */
OV_CACHE_PROVIDER_EXPORT
const OvCacheProviderApiV2 *openviking_cache_provider_v2(void);

#ifdef __cplusplus
}
#endif

#endif
