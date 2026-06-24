#include "yuanrong_bridge.h"

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <vector>

#include "datasystem/datasystem.h"

using datasystem::ConnectOptions;
using datasystem::DsClient;
using datasystem::MSetParam;
using datasystem::Optional;
using datasystem::ReadOnlyBuffer;
using datasystem::Status;
using datasystem::StatusCode;
using datasystem::StringView;

struct YrClientHandle {
    std::shared_ptr<DsClient> client;
    // Serialize all SDK calls made through this native client handle. A single
    // native YuanrongProvider owns one handle today, so sdk_concurrency > 1 in
    // Rust does not create true backend concurrency for that provider.
    //
    // If the Rust layer creates multiple native clients to provide concurrent
    // channels, those channels must not be treated as a global ordering
    // guarantee for concurrent conflicting writes.
    std::mutex call_mutex;
    std::atomic<bool> shutdown { false };
};

namespace {
thread_local std::string last_error;

void set_error(const Status &status)
{
    last_error = status.ToString();
}

void set_error(const char *message)
{
    last_error = message;
}

int map_status(const Status &status)
{
    if (status.IsOk()) {
        last_error.clear();
        return YR_OK;
    }
    set_error(status);
    switch (status.GetCode()) {
        case StatusCode::K_NOT_FOUND:
        case StatusCode::K_NOT_FOUND_IN_L2CACHE:
            return YR_NOT_FOUND;
        case StatusCode::K_INVALID:
        case StatusCode::K_OUT_OF_RANGE:
        case StatusCode::K_FILE_NAME_TOO_LONG:
            return YR_INVALID_ARGUMENT;
        case StatusCode::K_MASTER_TIMEOUT:
        case StatusCode::K_RPC_DEADLINE_EXCEEDED:
        case StatusCode::K_FUTURE_TIMEOUT:
            return YR_TIMEOUT;
        case StatusCode::K_NOT_READY:
        case StatusCode::K_SHUTTING_DOWN:
        case StatusCode::K_WORKER_ABNORMAL:
        case StatusCode::K_CLIENT_WORKER_DISCONNECT:
        case StatusCode::K_RPC_UNAVAILABLE:
        case StatusCode::K_URMA_ERROR:
        case StatusCode::K_RDMA_ERROR:
            return YR_UNAVAILABLE;
        default:
            return YR_INTERNAL;
    }
}

bool valid_client(YrClientHandle *client)
{
    if (client == nullptr || client->client == nullptr) {
        set_error("Yuanrong client handle is null");
        return false;
    }
    if (client->shutdown.load()) {
        set_error("Yuanrong client is shut down");
        return false;
    }
    return true;
}

bool valid_bytes(const uint8_t *data, size_t len, const char *name)
{
    if (data == nullptr || len == 0) {
        last_error = std::string(name) + " must not be empty";
        return false;
    }
    return true;
}

std::string to_string(const uint8_t *data, size_t len)
{
    return std::string(reinterpret_cast<const char *>(data), len);
}

int copy_value(const void *source, size_t size, uint8_t **out)
{
    auto *copy = static_cast<uint8_t *>(std::malloc(size));
    if (copy == nullptr) {
        set_error("failed to allocate Yuanrong result buffer");
        return YR_INTERNAL;
    }
    std::memcpy(copy, source, size);
    *out = copy;
    return YR_OK;
}

template <typename Function>
int protect(Function &&function) noexcept
{
    try {
        return function();
    } catch (const std::exception &error) {
        last_error = std::string("Yuanrong bridge exception: ") + error.what();
        return YR_INTERNAL;
    } catch (...) {
        set_error("Yuanrong bridge caught an unknown exception");
        return YR_INTERNAL;
    }
}

template <typename Function>
void protect_void(Function &&function) noexcept
{
    try {
        function();
    } catch (const std::exception &error) {
        last_error = std::string("Yuanrong bridge exception: ") + error.what();
    } catch (...) {
        set_error("Yuanrong bridge caught an unknown exception");
    }
}
}  // namespace

int client_create_impl(const char *host, uint16_t port, int32_t connect_timeout_ms,
                       int32_t request_timeout_ms, YrClientHandle **out)
{
    if (host == nullptr || host[0] == '\0' || port == 0 || connect_timeout_ms <= 0
        || request_timeout_ms <= 0 || out == nullptr) {
        set_error("invalid Yuanrong connection options");
        return YR_INVALID_ARGUMENT;
    }
    *out = nullptr;
    ConnectOptions options;
    options.host = host;
    options.port = port;
    options.connectTimeoutMs = connect_timeout_ms;
    options.requestTimeoutMs = request_timeout_ms;
    auto handle = std::make_unique<YrClientHandle>();
    handle->client = std::make_shared<DsClient>(options);
    int code = map_status(handle->client->Init());
    if (code != YR_OK) {
        return code;
    }
    *out = handle.release();
    return YR_OK;
}

int client_health_check_impl(YrClientHandle *client)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    return map_status(client->client->KV()->HealthCheck());
}

int client_get_impl(YrClientHandle *client, const uint8_t *key, size_t key_len,
                    uint8_t **data, size_t *size)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (!valid_bytes(key, key_len, "key") || data == nullptr || size == nullptr) {
        return YR_INVALID_ARGUMENT;
    }
    *data = nullptr;
    *size = 0;
    std::lock_guard<std::mutex> guard(client->call_mutex);
    std::string value;
    int code = map_status(client->client->KV()->Get(to_string(key, key_len), value));
    if (code != YR_OK) {
        return code;
    }
    if (value.empty()) {
        set_error("Yuanrong returned an empty cache value");
        return YR_INTERNAL;
    }
    code = copy_value(value.data(), value.size(), data);
    if (code == YR_OK) {
        *size = value.size();
    }
    return code;
}

int client_set_impl(YrClientHandle *client, const uint8_t *key, size_t key_len,
                    const uint8_t *data, size_t size)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (!valid_bytes(key, key_len, "key") || !valid_bytes(data, size, "value")) {
        return YR_INVALID_ARGUMENT;
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    StringView value(reinterpret_cast<const char *>(data), size);
    return map_status(client->client->KV()->Set(to_string(key, key_len), value));
}

int client_delete_impl(YrClientHandle *client, const uint8_t *key, size_t key_len)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (!valid_bytes(key, key_len, "key")) {
        return YR_INVALID_ARGUMENT;
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    int code = map_status(client->client->KV()->Del(to_string(key, key_len)));
    return code == YR_NOT_FOUND ? YR_OK : code;
}

int client_exists_impl(YrClientHandle *client, const uint8_t *key, size_t key_len,
                       uint8_t *exists)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (!valid_bytes(key, key_len, "key") || exists == nullptr) {
        return YR_INVALID_ARGUMENT;
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    std::vector<bool> results;
    int code = map_status(client->client->KV()->Exist({ to_string(key, key_len) }, results));
    if (code == YR_NOT_FOUND) {
        *exists = 0;
        return YR_OK;
    }
    if (code != YR_OK) {
        return code;
    }
    if (results.size() != 1) {
        set_error("Yuanrong Exist returned an unexpected result count");
        return YR_INTERNAL;
    }
    *exists = results[0] ? 1 : 0;
    return YR_OK;
}

int client_mget_impl(YrClientHandle *client, const uint8_t *const *keys,
                     const size_t *key_lens, size_t count, YrBuffer **values)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (keys == nullptr || key_lens == nullptr || count == 0 || values == nullptr) {
        set_error("invalid Yuanrong mget arguments");
        return YR_INVALID_ARGUMENT;
    }
    *values = nullptr;
    std::vector<std::string> native_keys;
    native_keys.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        if (!valid_bytes(keys[i], key_lens[i], "key")) {
            return YR_INVALID_ARGUMENT;
        }
        native_keys.push_back(to_string(keys[i], key_lens[i]));
    }

    std::lock_guard<std::mutex> guard(client->call_mutex);
    std::vector<Optional<ReadOnlyBuffer>> buffers;
    int code = map_status(client->client->KV()->Get(native_keys, buffers));
    if (code == YR_NOT_FOUND) {
        buffers.resize(count);
        code = YR_OK;
    }
    if (code != YR_OK) {
        return code;
    }
    if (buffers.size() != count) {
        set_error("Yuanrong MGet returned an unexpected result count");
        return YR_INTERNAL;
    }

    auto *results = static_cast<YrBuffer *>(std::calloc(count, sizeof(YrBuffer)));
    if (results == nullptr) {
        set_error("failed to allocate Yuanrong mget result array");
        return YR_INTERNAL;
    }
    for (size_t i = 0; i < count; ++i) {
        if (!buffers[i]) {
            continue;
        }
        Status latch = buffers[i]->RLatch();
        if (latch.IsError()) {
            set_error(latch);
            yr_buffers_free(results, count);
            return map_status(latch);
        }
        const auto size = static_cast<size_t>(buffers[i]->GetSize());
        code = size == 0 ? YR_INTERNAL
                         : copy_value(buffers[i]->ImmutableData(), size, &results[i].data);
        Status unlatch = buffers[i]->UnRLatch();
        if (code != YR_OK) {
            yr_buffers_free(results, count);
            return code;
        }
        if (unlatch.IsError()) {
            set_error(unlatch);
            yr_buffers_free(results, count);
            return map_status(unlatch);
        }
        results[i].len = size;
        results[i].found = 1;
    }
    *values = results;
    last_error.clear();
    return YR_OK;
}

int client_mset_impl(YrClientHandle *client, const uint8_t *const *keys,
                     const size_t *key_lens, const uint8_t *const *values,
                     const size_t *value_lens, size_t count)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (keys == nullptr || key_lens == nullptr || values == nullptr || value_lens == nullptr
        || count == 0) {
        set_error("invalid Yuanrong mset arguments");
        return YR_INVALID_ARGUMENT;
    }
    std::vector<std::string> native_keys;
    std::vector<StringView> native_values;
    native_keys.reserve(count);
    native_values.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        if (!valid_bytes(keys[i], key_lens[i], "key")
            || !valid_bytes(values[i], value_lens[i], "value")) {
            return YR_INVALID_ARGUMENT;
        }
        native_keys.push_back(to_string(keys[i], key_lens[i]));
        native_values.emplace_back(reinterpret_cast<const char *>(values[i]), value_lens[i]);
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    std::vector<std::string> failed_keys;
    int code = map_status(client->client->KV()->MSet(native_keys, native_values, failed_keys,
                                                     MSetParam {}));
    if (code != YR_OK) {
        return code;
    }
    for (const auto &failed_key : failed_keys) {
        auto position = std::find(native_keys.begin(), native_keys.end(), failed_key);
        if (position == native_keys.end()) {
            set_error("Yuanrong MSet returned an unknown failed key");
            return YR_INTERNAL;
        }
        const auto index = static_cast<size_t>(std::distance(native_keys.begin(), position));
        code = map_status(client->client->KV()->Set(failed_key, native_values[index]));
        if (code != YR_OK) {
            return code;
        }
    }
    return YR_OK;
}

int client_mdelete_impl(YrClientHandle *client, const uint8_t *const *keys,
                        const size_t *key_lens, size_t count)
{
    if (!valid_client(client)) {
        return YR_UNAVAILABLE;
    }
    if (keys == nullptr || key_lens == nullptr || count == 0) {
        set_error("invalid Yuanrong mdelete arguments");
        return YR_INVALID_ARGUMENT;
    }
    std::vector<std::string> native_keys;
    native_keys.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        if (!valid_bytes(keys[i], key_lens[i], "key")) {
            return YR_INVALID_ARGUMENT;
        }
        native_keys.push_back(to_string(keys[i], key_lens[i]));
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    std::vector<std::string> failed_keys;
    int code = map_status(client->client->KV()->Del(native_keys, failed_keys));
    if (code == YR_NOT_FOUND) {
        return YR_OK;
    }
    if (code != YR_OK) {
        return code;
    }
    for (const auto &failed_key : failed_keys) {
        code = map_status(client->client->KV()->Del(failed_key));
        if (code != YR_OK && code != YR_NOT_FOUND) {
            return code;
        }
    }
    return YR_OK;
}

int client_shutdown_impl(YrClientHandle *client)
{
    if (client == nullptr || client->client == nullptr) {
        set_error("Yuanrong client handle is null");
        return YR_INVALID_ARGUMENT;
    }
    std::lock_guard<std::mutex> guard(client->call_mutex);
    if (client->shutdown.load()) {
        return YR_OK;
    }
    int code = map_status(client->client->ShutDown());
    if (code == YR_OK) {
        client->shutdown.store(true);
    }
    return code;
}

void client_destroy_impl(YrClientHandle *client)
{
    if (client == nullptr) {
        return;
    }
    if (!client->shutdown.load() && client->client != nullptr) {
        std::lock_guard<std::mutex> guard(client->call_mutex);
        (void)client->client->ShutDown();
        client->shutdown.store(true);
    }
    delete client;
}

extern "C" int yr_client_create(const char *host, uint16_t port, int32_t connect_timeout_ms,
                                int32_t request_timeout_ms, YrClientHandle **out)
{
    return protect(
        [&] { return client_create_impl(host, port, connect_timeout_ms, request_timeout_ms, out); });
}

extern "C" int yr_client_health_check(YrClientHandle *client)
{
    return protect([&] { return client_health_check_impl(client); });
}

extern "C" int yr_client_get(YrClientHandle *client, const uint8_t *key, size_t key_len,
                             uint8_t **data, size_t *size)
{
    return protect([&] { return client_get_impl(client, key, key_len, data, size); });
}

extern "C" int yr_client_set(YrClientHandle *client, const uint8_t *key, size_t key_len,
                             const uint8_t *data, size_t size)
{
    return protect([&] { return client_set_impl(client, key, key_len, data, size); });
}

extern "C" int yr_client_delete(YrClientHandle *client, const uint8_t *key, size_t key_len)
{
    return protect([&] { return client_delete_impl(client, key, key_len); });
}

extern "C" int yr_client_exists(YrClientHandle *client, const uint8_t *key, size_t key_len,
                                uint8_t *exists)
{
    return protect([&] { return client_exists_impl(client, key, key_len, exists); });
}

extern "C" int yr_client_mget(YrClientHandle *client, const uint8_t *const *keys,
                              const size_t *key_lens, size_t count, YrBuffer **values)
{
    return protect([&] { return client_mget_impl(client, keys, key_lens, count, values); });
}

extern "C" int yr_client_mset(YrClientHandle *client, const uint8_t *const *keys,
                              const size_t *key_lens, const uint8_t *const *values,
                              const size_t *value_lens, size_t count)
{
    return protect(
        [&] { return client_mset_impl(client, keys, key_lens, values, value_lens, count); });
}

extern "C" int yr_client_mdelete(YrClientHandle *client, const uint8_t *const *keys,
                                 const size_t *key_lens, size_t count)
{
    return protect([&] { return client_mdelete_impl(client, keys, key_lens, count); });
}

extern "C" int yr_client_shutdown(YrClientHandle *client)
{
    return protect([&] { return client_shutdown_impl(client); });
}

extern "C" void yr_client_destroy(YrClientHandle *client)
{
    protect_void([&] { client_destroy_impl(client); });
}

extern "C" void yr_buffer_free(void *data)
{
    std::free(data);
}

extern "C" void yr_buffers_free(YrBuffer *values, size_t count)
{
    if (values == nullptr) {
        return;
    }
    for (size_t i = 0; i < count; ++i) {
        std::free(values[i].data);
    }
    std::free(values);
}

extern "C" const char *yr_last_error(YrClientHandle *)
{
    return last_error.c_str();
}
