// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "catalog/catalog_engine.h"

#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

#include "common/json_utils.h"
#include "index/index_engine.h"
#include "store/bytes_row.h"
#include "store/kv_store.h"
#include "store/persist_store.h"
#include "store/volatile_store.h"

namespace vectordb {

class CatalogEngine::Impl {
 public:
  explicit Impl(const std::string& path);
  std::string execute(const std::string& request_json);
  std::string aggregate_filter(IndexEngine& index, KVStore& candidates,
                               const BytesRow& row,
                               const std::string& request_json);

 private:
  struct Candidate {
    std::string label;
    std::string uri;
    double score;
  };
  using CandidateConsumer = std::function<void(const Candidate&)>;
  using CandidateSource = std::function<void(const CandidateConsumer&)>;
  void group_candidates(const JsonValue& request, const std::string& scope,
                        JsonDoc& result, const CandidateSource& source) const;

  struct ResolvedItem {
    bool found = false;
    std::string item_uuid;
    std::string root_uri;
    std::vector<std::string> item_attrs;
  };

  std::shared_ptr<KVStore> store_;
  std::map<std::string, std::string> nodes_;
  // Sparse declaration indexes: ordinary member registration never needs to
  // parse all nodes, and UUID lookup does not scan the collection.
  std::map<std::string, ResolvedItem> declarations_;
  std::map<std::string, std::string> uuid_roots_;
  std::map<std::string, std::set<std::string>> attribute_roots_;
  std::mutex mutex_;
  uint64_t revision_ = 0;
  uint64_t mode_revision_ = 0;
  std::map<std::string, uint64_t> scope_revisions_;
  bool collection_union_mode_ = false;
  bool legacy_mode_conflict_ = false;

  ResolvedItem resolve_item(const std::string& scope,
                            const std::string& uri) const;
  std::vector<ResolvedItem> resolve_items(const std::string& scope,
                                          const std::string& uri) const;
  bool union_mode() const;
  std::string identity_revision(const std::string& scope) const;
  void unindex_node(const std::string& node_key);
  void index_node(const std::string& node_key, const std::string& value);
  JsonValue compile_filter(const std::string& scope, const JsonValue& filter,
                           JsonAllocator& allocator) const;
  void commit(const std::map<std::string, std::string>& updates);
  void commit_changes(const std::map<std::string, std::string>& updates,
                      const std::vector<std::string>& deletes);
};

CatalogEngine::CatalogEngine(const std::string& path)
    : impl_(std::make_unique<Impl>(path)) {}
CatalogEngine::~CatalogEngine() = default;
std::string CatalogEngine::execute(const std::string& request_json) {
  return impl_->execute(request_json);
}

std::string CatalogEngine::aggregate_filter(IndexEngine& index, KVStore& candidates,
                                            const BytesRow& row,
                                            const std::string& request_json) {
  return impl_->aggregate_filter(index, candidates, row, request_json);
}

namespace {

constexpr const char* kIdentityModeKey = "@item_uuids_mode";

JsonDoc parse(const std::string& value) {
  JsonDoc doc;
  doc.Parse(value.c_str(), value.size());
  if (doc.HasParseError() || !doc.IsObject()) {
    throw std::invalid_argument("Catalog expects a JSON object");
  }
  return doc;
}

std::string encode(const JsonValue& value) {
  rapidjson::StringBuffer buffer;
  rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
  value.Accept(writer);
  return std::string(buffer.GetString(), buffer.GetSize());
}

std::string required_string(const JsonValue& obj, const char* name) {
  if (!obj.IsObject() || !obj.HasMember(name) || !obj[name].IsString() ||
      obj[name].GetStringLength() == 0) {
    throw std::invalid_argument(std::string("Catalog requires ") + name);
  }
  std::string value(obj[name].GetString(), obj[name].GetStringLength());
  if (value.find('\0') != std::string::npos) {
    throw std::invalid_argument("Catalog strings cannot contain NUL");
  }
  return value;
}

std::string canonical(std::string uri) {
  if (uri.rfind("viking://", 0) == 0) uri = "/" + uri.substr(9);
  if (uri.empty() || uri[0] != '/' || uri.find('\0') != std::string::npos) {
    throw std::invalid_argument("Catalog requires an absolute URI");
  }
  while (uri.size() > 1 && uri.back() == '/') uri.pop_back();
  size_t start = 1;
  while (start < uri.size()) {
    const auto end = uri.find('/', start);
    const auto part = uri.substr(start, end - start);
    if (part.empty() || part == "." || part == "..") {
      throw std::invalid_argument("Catalog URI must be canonical");
    }
    if (end == std::string::npos) break;
    start = end + 1;
  }
  return uri;
}

std::string parent(const std::string& uri) {
  if (uri == "/") return "";
  const auto slash = uri.rfind('/');
  return slash == 0 ? "/" : uri.substr(0, slash);
}

std::string key(const std::string& scope, const std::string& uri) {
  return scope + '\0' + uri;
}

bool in_tree(const std::string& uri, const std::string& root) {
  return uri == root || root == "/" ||
         (uri.size() > root.size() && uri.compare(0, root.size(), root) == 0 &&
          uri[root.size()] == '/');
}

void put_string(JsonValue& obj, const char* name, const std::string& value,
                JsonAllocator& allocator) {
  JsonValue k(name, allocator);
  JsonValue v(value.c_str(), static_cast<rapidjson::SizeType>(value.size()),
              allocator);
  if (obj.HasMember(name)) obj.RemoveMember(name);
  obj.AddMember(k, v, allocator);
}

void validate_item_attrs(const JsonValue& attrs) {
  if (!attrs.IsArray()) {
    throw std::invalid_argument("item_attrs must be a string list in the engine");
  }
  std::set<std::string> keys;
  for (const auto& entry : attrs.GetArray()) {
    if (!entry.IsString()) {
      throw std::invalid_argument("item_attrs must contain strings");
    }
    const std::string value(entry.GetString(), entry.GetStringLength());
    const auto separator = value.find('=');
    if (separator == std::string::npos || separator == 0 ||
        value.find('\0') != std::string::npos) {
      throw std::invalid_argument("item_attrs entries must use key=value");
    }
    if (!keys.insert(value.substr(0, separator)).second) {
      throw std::invalid_argument("item_attrs keys must be unique");
    }
  }
}

bool matches_item_attrs(const std::vector<std::string>& attrs,
                        const JsonValue& filter) {
  const auto op = required_string(filter, "op");
  if (op != "must" && op != "must_not") {
    throw std::invalid_argument(
        "item_attrs filters support must and must_not only");
  }
  if (!filter.HasMember("conds") || !filter["conds"].IsArray()) {
    throw std::invalid_argument("item_attrs filter requires conds");
  }
  bool found = false;
  for (const auto& cond : filter["conds"].GetArray()) {
    if (!cond.IsString()) {
      throw std::invalid_argument("item_attrs filter conds must be strings");
    }
    const std::string wanted(cond.GetString(), cond.GetStringLength());
    found = found || std::find(attrs.begin(), attrs.end(), wanted) != attrs.end();
  }
  return op == "must" ? found : !found;
}

JsonValue impossible_filter(JsonAllocator& allocator) {
  const auto empty = parse(
      "{\"op\":\"and\",\"conds\":[{\"op\":\"must\",\"field\":\"uri\","
      "\"conds\":[\"/\"],\"para\":\"-d=0\"},{\"op\":\"must_not\","
      "\"field\":\"uri\",\"conds\":[\"/\"],\"para\":\"-d=0\"}]}");
  return JsonValue(empty, allocator);
}

JsonValue path_filter(const char* op, const std::string& uri,
                      JsonAllocator& allocator) {
  JsonValue filter(rapidjson::kObjectType);
  filter.AddMember("op", JsonValue(op, allocator), allocator);
  filter.AddMember("field", "uri", allocator);
  JsonValue conds(rapidjson::kArrayType);
  conds.PushBack(JsonValue(uri.c_str(),
                           static_cast<rapidjson::SizeType>(uri.size()),
                           allocator),
                 allocator);
  filter.AddMember("conds", conds, allocator);
  filter.AddMember("para", "-d=-1", allocator);
  return filter;
}

}  // namespace

CatalogEngine::Impl::Impl(const std::string& path) {
  store_ = path.empty() ? std::shared_ptr<KVStore>(new VolatileStore())
                        : std::shared_ptr<KVStore>(new PersistStore(path));
  for (const auto& entry :
       store_->seek_range("", std::string(1, static_cast<char>(0xff)))) {
    parse(entry.second);  // Fail closed on corrupt persisted metadata.
    nodes_.insert(entry);
    index_node(entry.first, entry.second);
  }
  const auto read_mode = [](const std::string& value) {
    const auto mode = required_string(parse(value), "item_identity_mode");
    if (mode != "shadow" && mode != "union") {
      throw std::invalid_argument("item_identity_mode must be shadow or union");
    }
    return mode == "union";
  };
  const auto config = nodes_.find(kIdentityModeKey);
  if (config != nodes_.end()) {
    collection_union_mode_ = read_mode(config->second);
    return;
  }
  // Read draft per-account configuration without silently changing semantics.
  // Existing accounts with no explicit configuration used shadow.
  const auto legacy_prefix = key("@mode", "");
  std::map<std::string, bool> legacy_modes;
  std::set<std::string> scopes;
  for (const auto& entry : nodes_) {
    if (entry.first.rfind(legacy_prefix, 0) == 0) {
      legacy_modes[entry.first.substr(legacy_prefix.size())] = read_mode(entry.second);
    } else {
      const auto separator = entry.first.find('\0');
      if (separator != std::string::npos && separator + 1 < entry.first.size() &&
          entry.first[separator + 1] == '/') {
        scopes.insert(entry.first.substr(0, separator));
      }
    }
  }
  std::set<bool> modes;
  for (const auto& entry : legacy_modes) modes.insert(entry.second);
  for (const auto& scope : scopes) {
    if (legacy_modes.count(scope) == 0) modes.insert(false);
  }
  legacy_mode_conflict_ = modes.size() > 1;
  collection_union_mode_ = modes.count(true) != 0;
}

void CatalogEngine::Impl::commit(
    const std::map<std::string, std::string>& updates) {
  std::vector<std::string> keys;
  std::vector<std::string> values;
  for (const auto& entry : updates) {
    keys.push_back(entry.first);
    values.push_back(entry.second);
  }
  if (store_->put_data(keys, values) != 0) {
    throw std::runtime_error("Catalog persistence failed");
  }
  for (const auto& entry : updates) unindex_node(entry.first);
  for (const auto& entry : updates) {
    nodes_[entry.first] = entry.second;
    index_node(entry.first, entry.second);
  }
}

void CatalogEngine::Impl::commit_changes(
    const std::map<std::string, std::string>& updates,
    const std::vector<std::string>& deletes) {
  std::vector<StorageOp> operations;
  operations.reserve(updates.size() + deletes.size());
  for (const auto& entry : updates) {
    operations.push_back({StorageOp::PUT_OP, entry.first, entry.second});
  }
  for (const auto& node_key : deletes) {
    if (updates.count(node_key) == 0) {
      operations.push_back({StorageOp::DELETE_OP, node_key, ""});
    }
  }
  if (store_->exec_op(operations) != 0) {
    throw std::runtime_error("Catalog transaction failed");
  }
  for (const auto& node_key : deletes) {
    unindex_node(node_key);
    nodes_.erase(node_key);
  }
  for (const auto& entry : updates) unindex_node(entry.first);
  for (const auto& entry : updates) {
    nodes_[entry.first] = entry.second;
    index_node(entry.first, entry.second);
  }
}

void CatalogEngine::Impl::unindex_node(const std::string& node_key) {
  const auto found = declarations_.find(node_key);
  if (found == declarations_.end()) return;
  const auto scope = node_key.substr(0, node_key.find('\0'));
  uuid_roots_.erase(key(scope, found->second.item_uuid));
  for (const auto& attr : found->second.item_attrs) {
    const auto posting = attribute_roots_.find(key(scope, attr));
    if (posting == attribute_roots_.end()) continue;
    posting->second.erase(node_key);
    if (posting->second.empty()) attribute_roots_.erase(posting);
  }
  declarations_.erase(found);
}

void CatalogEngine::Impl::index_node(const std::string& node_key,
                                      const std::string& value) {
  const auto separator = node_key.find('\0');
  if (separator == std::string::npos || node_key[separator + 1] != '/') return;
  const auto node = parse(value);
  std::string uuid;
  if (node.HasMember("item_uuids")) {
    const auto& ids = node["item_uuids"];
    if (!ids.IsArray() || ids.Size() != 1 || !ids[0].IsString() ||
        ids[0].GetStringLength() == 0) {
      throw std::invalid_argument("An Item root must declare one item_uuids string");
    }
    uuid.assign(ids[0].GetString(), ids[0].GetStringLength());
  } else if (node.HasMember("item_uuid")) {
    // Read collections created by the first implementation without a rebuild.
    uuid = required_string(node, "item_uuid");
  } else {
    return;
  }
  ResolvedItem item;
  item.found = true;
  item.item_uuid = uuid;
  item.root_uri = node_key.substr(separator + 1);
  if (node.HasMember("item_attrs")) {
    validate_item_attrs(node["item_attrs"]);
    for (const auto& attr : node["item_attrs"].GetArray()) {
      item.item_attrs.emplace_back(attr.GetString(), attr.GetStringLength());
    }
  }
  const auto scope = node_key.substr(0, separator);
  uuid_roots_[key(scope, uuid)] = node_key;
  for (const auto& attr : item.item_attrs) {
    attribute_roots_[key(scope, attr)].insert(node_key);
  }
  declarations_[node_key] = std::move(item);
}

bool CatalogEngine::Impl::union_mode() const {
  if (legacy_mode_conflict_) {
    throw std::invalid_argument(
        "Conflicting legacy Item modes require explicit collection-wide configure");
  }
  return collection_union_mode_;
}

std::string CatalogEngine::Impl::identity_revision(const std::string& scope) const {
  const auto found = scope_revisions_.find(scope);
  const auto scope_revision = found == scope_revisions_.end() ? 0 : found->second;
  return std::to_string(std::max(mode_revision_, scope_revision));
}

std::vector<CatalogEngine::Impl::ResolvedItem> CatalogEngine::Impl::resolve_items(
    const std::string& scope, const std::string& uri) const {
  std::vector<ResolvedItem> items;
  const bool accumulate = union_mode();
  for (std::string current = uri; !current.empty(); current = parent(current)) {
    const auto found = declarations_.find(key(scope, current));
    if (found == declarations_.end()) continue;
    items.push_back(found->second);
    if (!accumulate) break;
  }
  return items;
}

CatalogEngine::Impl::ResolvedItem CatalogEngine::Impl::resolve_item(
    const std::string& scope, const std::string& uri) const {
  for (std::string current = uri; !current.empty(); current = parent(current)) {
    const auto found = declarations_.find(key(scope, current));
    if (found != declarations_.end()) return found->second;
  }
  return {};
}

JsonValue CatalogEngine::Impl::compile_filter(const std::string& scope,
                                               const JsonValue& filter,
                                               JsonAllocator& allocator) const {
  if (!filter.IsObject()) {
    throw std::invalid_argument("Catalog filter must be an object");
  }
  JsonValue out(filter, allocator);
  if (filter.HasMember("filter")) {
    out["filter"] = compile_filter(scope, filter["filter"], allocator);
    return out;
  }
  if (filter.HasMember("op") && filter["op"].IsString()) {
    const std::string op(filter["op"].GetString(),
                         filter["op"].GetStringLength());
    if (op == "and" || op == "or") {
      if (!filter.HasMember("conds") || !filter["conds"].IsArray()) {
        throw std::invalid_argument("Catalog logical filter requires conds");
      }
      for (rapidjson::SizeType i = 0; i < filter["conds"].Size(); ++i) {
        out["conds"][i] = compile_filter(scope, filter["conds"][i], allocator);
      }
      return out;
    }
  }
  if (!filter.HasMember("field") || !filter["field"].IsString() ||
      std::string(filter["field"].GetString(),
                  filter["field"].GetStringLength()) != "item_attrs") {
    return out;
  }

  // Posting lookup selects matching roots. The ordered sparse frontier only
  // excludes immediate declared children; deeper descendants are already hidden
  // by that child. This keeps wide and nested filters linear in declarations.
  std::set<std::string> matched;
  const auto op = required_string(filter, "op");
  matches_item_attrs({}, filter);  // Validate the predicate even for an empty catalog.
  for (const auto& cond : filter["conds"].GetArray()) {
    const auto posting = attribute_roots_.find(
        key(scope, std::string(cond.GetString(), cond.GetStringLength())));
    if (posting != attribute_roots_.end()) {
      matched.insert(posting->second.begin(), posting->second.end());
    }
  }
  const bool accumulate = union_mode();
  std::map<std::string, std::vector<std::string>> children;
  const std::string prefix = scope + '\0' + '/';
  for (auto it = declarations_.lower_bound(prefix);
       it != declarations_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
    const auto& uri = it->second.root_uri;
    if (accumulate) continue;
    for (auto ancestor = parent(uri); !ancestor.empty(); ancestor = parent(ancestor)) {
      if (declarations_.count(key(scope, ancestor))) {
        children[ancestor].push_back(uri);
        break;
      }
    }
  }

  JsonValue branches(rapidjson::kArrayType);
  for (auto it = declarations_.lower_bound(prefix);
       it != declarations_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
    if ((matched.count(it->first) != 0) != (op == "must")) continue;
    const auto& uri = it->second.root_uri;
    JsonValue branch_conds(rapidjson::kArrayType);
    branch_conds.PushBack(path_filter("must", uri, allocator), allocator);
    for (const auto& nested : children[uri]) {
      branch_conds.PushBack(path_filter("must_not", nested, allocator), allocator);
    }
    if (branch_conds.Size() == 1) {
      branches.PushBack(branch_conds[0], allocator);
    } else {
      JsonValue branch(rapidjson::kObjectType);
      branch.AddMember("op", "and", allocator);
      branch.AddMember("conds", branch_conds, allocator);
      branches.PushBack(branch, allocator);
    }
  }
  if (branches.Empty()) return impossible_filter(allocator);
  if (branches.Size() == 1) return JsonValue(branches[0], allocator);

  out.SetObject();
  out.AddMember("op", "or", allocator);
  out.AddMember("conds", branches, allocator);
  return out;
}

void CatalogEngine::Impl::group_candidates(
    const JsonValue& req, const std::string& scope, JsonDoc& result,
    const CandidateSource& source) const {
  auto& allocator = result.GetAllocator();
  const auto aggregation = required_string(req, "aggregation");
  if (aggregation != "max" && aggregation != "sum") {
    throw std::invalid_argument("aggregation must be max or sum");
  }
  if (!req.HasMember("max_groups") || !req["max_groups"].IsInt() ||
      req["max_groups"].GetInt() <= 0) {
    throw std::invalid_argument("max_groups must be a positive integer");
  }
  const int max_groups = req["max_groups"].GetInt();
  int max_members = 1000;
  if (req.HasMember("max_members_per_group")) {
    if (!req["max_members_per_group"].IsInt() ||
        req["max_members_per_group"].GetInt() < 1 ||
        req["max_members_per_group"].GetInt() > 10000) {
      throw std::invalid_argument("max_members_per_group must be in [1, 10000]");
    }
    max_members = req["max_members_per_group"].GetInt();
  }
  const bool has_threshold = req.HasMember("score_threshold");
  const double threshold = has_threshold && req["score_threshold"].IsNumber()
                               ? req["score_threshold"].GetDouble()
                               : 0.0;
  if (has_threshold &&
      (!req["score_threshold"].IsNumber() || !std::isfinite(threshold))) {
    throw std::invalid_argument("score_threshold must be finite");
  }

  struct Group {
    std::string uuid;
    double score;
    uint64_t count;
    std::vector<Candidate> members;
  };
  const auto better_member = [](const Candidate& left, const Candidate& right) {
    if (left.score != right.score) return left.score > right.score;
    return left.label < right.label;
  };
  std::map<std::string, Group> grouped;
  source([&](const Candidate& candidate) {
    for (const auto& item : resolve_items(scope, candidate.uri)) {
      auto [it, inserted] = grouped.emplace(
          item.item_uuid,
          Group{item.item_uuid,
                aggregation == "max" ? -std::numeric_limits<double>::infinity()
                                     : 0.0,
                0,
                {}});
      auto& group = it->second;
      group.score = aggregation == "max" ? std::max(group.score, candidate.score)
                                         : group.score + candidate.score;
      ++group.count;
      // Keep only the best evidence, while scoring/counting every member.
      // A heap with this comparator puts the worst retained member first.
      if (group.members.size() < static_cast<size_t>(max_members)) {
        group.members.push_back(candidate);
        std::push_heap(group.members.begin(), group.members.end(), better_member);
      } else if (better_member(candidate, group.members.front())) {
        std::pop_heap(group.members.begin(), group.members.end(), better_member);
        group.members.back() = candidate;
        std::push_heap(group.members.begin(), group.members.end(), better_member);
      }
    }
  });
  std::vector<Group> groups;
  for (auto& pair : grouped) {
    auto& group = pair.second;
    if (!has_threshold || group.score >= threshold) {
      std::sort(group.members.begin(), group.members.end(), better_member);
      groups.push_back(std::move(group));
    }
  }
  std::sort(groups.begin(), groups.end(),
            [](const Group& left, const Group& right) {
              if (left.score != right.score) return left.score > right.score;
              return left.uuid < right.uuid;
            });
  const auto group_count = groups.size();
  if (groups.size() > static_cast<size_t>(max_groups)) groups.resize(max_groups);

  JsonValue output_groups(rapidjson::kArrayType);
  for (const auto& group : groups) {
    JsonValue output(rapidjson::kObjectType);
    put_string(output, "entity_id", group.uuid, allocator);
    output.AddMember("entity_score", group.score, allocator);
    const auto& item = declarations_.at(uuid_roots_.at(key(scope, group.uuid)));
    put_string(output, "entity_uri", item.root_uri, allocator);
    JsonValue attrs(rapidjson::kArrayType);
    for (const auto& attr : item.item_attrs) {
      attrs.PushBack(JsonValue(attr.c_str(), allocator), allocator);
    }
    output.AddMember("entity_attrs", attrs, allocator);
    const auto count = group.count;
    const bool truncated = count > group.members.size();
    output.AddMember("candidate_members_count", count, allocator);
    output.AddMember("candidate_members_truncated", truncated, allocator);
    output.AddMember("count", count, allocator);  // Legacy local adapter alias.
    JsonValue labels(rapidjson::kArrayType);
    JsonValue uris(rapidjson::kArrayType);
    for (size_t i = 0; i < group.members.size() && i < static_cast<size_t>(max_members); ++i) {
      const auto& member = group.members[i];
      labels.PushBack(JsonValue(member.label.c_str(),
                                static_cast<rapidjson::SizeType>(member.label.size()),
                                allocator),
                      allocator);
      uris.PushBack(JsonValue(member.uri.c_str(),
                              static_cast<rapidjson::SizeType>(member.uri.size()),
                              allocator),
                    allocator);
    }
    output.AddMember("candidate_members", JsonValue(labels, allocator), allocator);
    output.AddMember("labels", labels, allocator);
    output.AddMember("labels_truncated", truncated, allocator);
    output.AddMember("candidate_members_uri", uris, allocator);
    output_groups.PushBack(output, allocator);
  }
  result.AddMember("group_field", "item_uuids", allocator);
  result.AddMember("aggregation",
                   JsonValue(aggregation.c_str(),
                             static_cast<rapidjson::SizeType>(aggregation.size()),
                             allocator),
                   allocator);
  result.AddMember("group_count", static_cast<uint64_t>(group_count), allocator);
  result.AddMember("groups_truncated",
                   group_count > static_cast<size_t>(max_groups), allocator);
  result.AddMember("groups", output_groups, allocator);
}

std::string CatalogEngine::Impl::aggregate_filter(
    IndexEngine& index, KVStore& candidates, const BytesRow& row,
    const std::string& request_json) {
  const auto req = parse(request_json);
  const auto scope = required_string(req, "scope");
  const auto table = required_string(req, "candidate_table");
  const auto primary_key = required_string(req, "primary_key");
  if (!req.HasMember("filter")) {
    throw std::invalid_argument("Catalog requires filter");
  }
  std::lock_guard<std::mutex> guard(mutex_);
  union_mode();  // Reject ambiguous legacy configuration even for empty scans.
  JsonDoc result(rapidjson::kObjectType);
  auto& allocator = result.GetAllocator();
  JsonDoc dsl(rapidjson::kObjectType);
  auto compiled = compile_filter(scope, req["filter"], dsl.GetAllocator());
  if (compiled.HasMember("filter")) {
    dsl.CopyFrom(compiled, dsl.GetAllocator());
  } else {
    dsl.AddMember("filter", compiled, dsl.GetAllocator());
  }
  const auto filter_dsl = encode(dsl);
  group_candidates(req, scope, result, [&](const CandidateConsumer& consume) {
    // Only one bounded batch of stored records is decoded at a time. Dense
    // vectors remain encoded, and no per-member data crosses the Python ABI.
    constexpr size_t kBatchSize = 256;
    std::vector<std::string> keys;
    keys.reserve(kBatchSize);
    const auto flush = [&]() {
      if (keys.empty()) return;
      const auto records = candidates.get_data(keys);
      if (records.size() != keys.size()) {
        throw std::runtime_error("Incomplete Item candidate batch");
      }
      for (const auto& record : records) {
        if (record.empty()) {
          throw std::runtime_error("Item candidate missing from Store snapshot");
        }
        const auto fields_value = row.deserialize_field(record, "fields");
        if (!std::holds_alternative<std::string>(fields_value)) {
          throw std::runtime_error("Item candidate fields must be JSON text");
        }
        const auto fields = parse(std::get<std::string>(fields_value));
        if (!fields.HasMember("uri") || !fields["uri"].IsString() ||
            fields["uri"].GetStringLength() == 0) continue;
        const auto pk = fields.FindMember(primary_key.c_str());
        if (pk == fields.MemberEnd()) {
          throw std::runtime_error("Item candidate is missing its primary key");
        }
        std::string label;
        if (pk->value.IsString()) {
          label.assign(pk->value.GetString(), pk->value.GetStringLength());
        } else if (pk->value.IsUint64()) {
          label = std::to_string(pk->value.GetUint64());
        } else if (pk->value.IsInt64()) {
          label = std::to_string(pk->value.GetInt64());
        } else {
          throw std::runtime_error("Item candidate primary key must be string or integer");
        }
        if (label.empty() || label.find('\0') != std::string::npos) {
          throw std::invalid_argument("Item candidate label must be non-empty and without NUL");
        }
        consume({label, canonical(required_string(fields, "uri")), 0.0});
      }
      keys.clear();
    };
    index.visit_filtered_labels(filter_dsl, [&](uint64_t label) {
      keys.push_back(table + std::to_string(label));
      if (keys.size() == kBatchSize) flush();
    });
    flush();
  });
  put_string(result, "revision", identity_revision(scope), allocator);
  return encode(result);
}

std::string CatalogEngine::Impl::execute(const std::string& request_json) {
  const auto req = parse(request_json);
  const auto op = required_string(req, "op");
  const auto scope = required_string(req, "scope");
  std::lock_guard<std::mutex> guard(mutex_);
  if (op != "configure" && op != "capabilities") union_mode();
  JsonDoc result(rapidjson::kObjectType);
  auto& allocator = result.GetAllocator();

  if (op == "capabilities") {
    result.AddMember("enabled", nodes_.count("@catalog") != 0, allocator);
    result.AddMember("version", 1, allocator);
    if (legacy_mode_conflict_) {
      result.AddMember("configuration_required", true, allocator);
    } else {
      put_string(result, "item_identity_mode", union_mode() ? "union" : "shadow", allocator);
    }
    JsonValue modes(rapidjson::kArrayType);
    modes.PushBack("shadow", allocator).PushBack("union", allocator);
    result.AddMember("item_identity_modes", modes, allocator);
    result.AddMember("item_attrs_inherit", false, allocator);
    result.AddMember("preserve_item_root", true, allocator);
  } else if (op == "configure") {
    std::map<std::string, std::string> updates;
    if (nodes_.count("@catalog") == 0) updates["@catalog"] = "{}";
    bool accumulate;
    if (req.HasMember("item_identity_mode")) {
      const auto mode = required_string(req, "item_identity_mode");
      if (mode != "shadow" && mode != "union") {
        throw std::invalid_argument("item_identity_mode must be shadow or union");
      }
      accumulate = mode == "union";
    } else {
      accumulate = union_mode();
    }
    JsonDoc config(rapidjson::kObjectType);
    put_string(config, "item_identity_mode", accumulate ? "union" : "shadow",
               config.GetAllocator());
    const auto encoded = encode(config);
    if (nodes_.count(kIdentityModeKey) == 0 || nodes_.at(kIdentityModeKey) != encoded) {
      updates[kIdentityModeKey] = encoded;
    }
    const auto legacy_prefix = key("@mode", "");
    std::vector<std::string> deletes;
    for (auto it = nodes_.lower_bound(legacy_prefix);
         it != nodes_.end() && it->first.rfind(legacy_prefix, 0) == 0; ++it) {
      deletes.push_back(it->first);
    }
    const bool changed = legacy_mode_conflict_ || accumulate != collection_union_mode_;
    if (!updates.empty() || !deletes.empty()) commit_changes(updates, deletes);
    collection_union_mode_ = accumulate;
    legacy_mode_conflict_ = false;
    if (changed) {
      // The same field configuration affects every account in the collection.
      mode_revision_ = ++revision_;
    }
  } else if (op == "upsert_nodes") {
    if (!req.HasMember("nodes") || !req["nodes"].IsArray()) {
      throw std::invalid_argument("Catalog requires nodes");
    }
    std::map<std::string, std::string> updates;
    if (nodes_.count("@catalog") == 0) updates["@catalog"] = "{}";
    for (const auto& input : req["nodes"].GetArray()) {
      const auto uri = canonical(required_string(input, "uri"));
      const auto node_type = required_string(input, "node_type");
      if (node_type != "file" && node_type != "dir") {
        throw std::invalid_argument("Invalid Catalog node_type");
      }
      for (std::string current = uri; !current.empty(); current = parent(current)) {
        const auto node_key = key(scope, current);
        const std::string current_type = current == uri ? node_type : "dir";
        const auto pending = updates.find(node_key);
        const auto existing = nodes_.find(node_key);
        if (pending != updates.end() || existing != nodes_.end()) {
          const auto stored = parse(pending != updates.end() ? pending->second
                                                             : existing->second);
          if (!stored.HasMember("node_type") || !stored["node_type"].IsString() ||
              std::string(stored["node_type"].GetString(),
                          stored["node_type"].GetStringLength()) != current_type) {
            throw std::invalid_argument(
                "Catalog node type conflicts with existing node");
          }
          continue;
        }
        JsonDoc node(rapidjson::kObjectType);
        auto& node_allocator = node.GetAllocator();
        put_string(node, "uri", current, node_allocator);
        put_string(node, "node_type", current_type, node_allocator);
        updates[node_key] = encode(node);
      }
    }
    if (!updates.empty()) commit(updates);
    // Member nodes do not change effective Item identity or attribute filters.
  } else if (op == "validate_resource_update") {
    const auto uri = canonical(required_string(req, "uri"));
    const auto own = declarations_.find(key(scope, uri));
    if (own != declarations_.end() && req.HasMember("node_type")) {
      const auto stored = parse(nodes_.at(key(scope, uri)));
      if (required_string(req, "node_type") != required_string(stored, "node_type")) {
        throw std::invalid_argument("Cannot change an Item root node type");
      }
    }
    if (req.HasMember("item_attrs")) {
      validate_item_attrs(req["item_attrs"]);
      if (own != declarations_.end()) {
        std::vector<std::string> attrs;
        for (const auto& attr : req["item_attrs"].GetArray()) {
          attrs.emplace_back(attr.GetString(), attr.GetStringLength());
        }
        auto stored = own->second.item_attrs;
        std::sort(attrs.begin(), attrs.end());
        std::sort(stored.begin(), stored.end());
        if (attrs != stored) {
          throw std::invalid_argument("item_attrs cannot change after an Item is declared");
        }
      }
    }
    const auto prefix = key(scope, uri == "/" ? "/" : uri + "/");
    const auto nested = declarations_.lower_bound(prefix);
    if (nested != declarations_.end() && nested->first.rfind(prefix, 0) == 0 &&
        nested->second.root_uri != uri) {
      throw std::invalid_argument(
          "Cannot replace a resource root containing independent child Items");
    }
    result.AddMember("valid", true, allocator);
    result.AddMember("has_item", resolve_item(scope, uri).found, allocator);
  } else if (op == "declare_item") {
    const auto uri = canonical(required_string(req, "uri"));
    std::string generated_uuid;
    if (req.HasMember("item_uuids")) {
      const auto& ids = req["item_uuids"];
      if (!ids.IsArray() || ids.Size() != 1 || !ids[0].IsString() ||
          ids[0].GetStringLength() == 0) {
        throw std::invalid_argument("An Item root must declare one item_uuids string");
      }
      generated_uuid.assign(ids[0].GetString(), ids[0].GetStringLength());
      if (generated_uuid.find('\0') != std::string::npos) {
        throw std::invalid_argument("item_uuids cannot contain NUL");
      }
    } else {
      generated_uuid = required_string(req, "item_uuid");
    }
    const auto found = nodes_.find(key(scope, uri));
    if (found == nodes_.end()) {
      throw std::invalid_argument("Catalog node does not exist");
    }
    const bool attrs_supplied = req.HasMember("item_attrs");
    if (attrs_supplied) validate_item_attrs(req["item_attrs"]);
    auto node = parse(found->second);
    auto& node_allocator = node.GetAllocator();
    const auto existing_item = declarations_.find(key(scope, uri));
    if (existing_item != declarations_.end()) {
      if (attrs_supplied) {
        std::vector<std::string> attrs;
        for (const auto& attr : req["item_attrs"].GetArray()) {
          attrs.emplace_back(attr.GetString(), attr.GetStringLength());
        }
        auto stored = existing_item->second.item_attrs;
        std::sort(attrs.begin(), attrs.end());
        std::sort(stored.begin(), stored.end());
        if (attrs != stored) {
          throw std::invalid_argument("item_attrs cannot change after an Item is declared");
        }
      }
      generated_uuid = existing_item->second.item_uuid;
    } else {
      if (uuid_roots_.count(key(scope, generated_uuid))) {
        throw std::invalid_argument("item_uuid already belongs to another root");
      }
      JsonValue ids(rapidjson::kArrayType);
      ids.PushBack(JsonValue(generated_uuid.c_str(), node_allocator), node_allocator);
      node.AddMember("item_uuids", ids, node_allocator);
      JsonValue attrs(rapidjson::kArrayType);
      if (attrs_supplied) attrs.CopyFrom(req["item_attrs"], node_allocator);
      node.AddMember("item_attrs", attrs, node_allocator);
      commit({{"@catalog", "{}"}, {key(scope, uri), encode(node)}});
      scope_revisions_[scope] = ++revision_;
    }
    put_string(result, "uri", uri, allocator);
    put_string(result, "item_uuid", generated_uuid, allocator);
    JsonValue ids(rapidjson::kArrayType);
    ids.PushBack(JsonValue(generated_uuid.c_str(), allocator), allocator);
    result.AddMember("item_uuids", ids, allocator);
    JsonValue attrs(rapidjson::kArrayType);
    if (node.HasMember("item_attrs")) attrs.CopyFrom(node["item_attrs"], allocator);
    result.AddMember("item_attrs", attrs, allocator);
  } else if (op == "resolve") {
    if (!req.HasMember("uris") || !req["uris"].IsArray()) {
      throw std::invalid_argument("Catalog resolve requires uris");
    }
    JsonValue data(rapidjson::kArrayType);
    for (const auto& value : req["uris"].GetArray()) {
      if (!value.IsString()) throw std::invalid_argument("URI must be a string");
      const auto uri = canonical(
          std::string(value.GetString(), value.GetStringLength()));
      const auto items = resolve_items(scope, uri);
      const auto item = resolve_item(scope, uri);
      JsonValue row(rapidjson::kObjectType);
      put_string(row, "uri", uri, allocator);
      if (item.found) {
        put_string(row, "item_uuid", item.item_uuid, allocator);
        put_string(row, "item_root_uri", item.root_uri, allocator);
      }
      JsonValue ids(rapidjson::kArrayType);
      for (const auto& effective : items) {
        ids.PushBack(JsonValue(effective.item_uuid.c_str(), allocator), allocator);
      }
      row.AddMember("item_uuids", ids, allocator);
      data.PushBack(row, allocator);
    }
    result.AddMember("data", data, allocator);
  } else if (op == "query_items") {
    if (!req.HasMember("item_uuids") || !req["item_uuids"].IsArray()) {
      throw std::invalid_argument("Catalog query_items requires item_uuids");
    }
    JsonValue data(rapidjson::kArrayType);
    for (const auto& requested : req["item_uuids"].GetArray()) {
      if (!requested.IsString()) {
        throw std::invalid_argument("item_uuids must contain strings");
      }
      const std::string uuid(requested.GetString(), requested.GetStringLength());
      const auto found = uuid_roots_.find(key(scope, uuid));
      if (found == uuid_roots_.end()) continue;
      const auto node = parse(nodes_.at(found->second));
      JsonValue row(rapidjson::kObjectType);
      put_string(row, "item_uuid", uuid, allocator);
      put_string(row, "uri",
                 std::string(node["uri"].GetString(), node["uri"].GetStringLength()),
                 allocator);
      JsonValue attrs(rapidjson::kArrayType);
      if (node.HasMember("item_attrs")) attrs.CopyFrom(node["item_attrs"], allocator);
      row.AddMember("item_attrs", attrs, allocator);
      data.PushBack(row, allocator);
    }
    result.AddMember("data", data, allocator);
  } else if (op == "compile_filter") {
    if (!req.HasMember("filter")) {
      throw std::invalid_argument("Catalog requires filter");
    }
    result.AddMember("filter", compile_filter(scope, req["filter"], allocator),
                     allocator);
  } else if (op == "group_candidates") {
    if (!req.HasMember("candidates") || !req["candidates"].IsArray()) {
      throw std::invalid_argument("Catalog grouping requires candidates");
    }
    group_candidates(req, scope, result, [&](const CandidateConsumer& consume) {
      for (const auto& input : req["candidates"].GetArray()) {
        const auto label = required_string(input, "label");
        const auto uri = canonical(required_string(input, "uri"));
        if (!input.HasMember("score") || !input["score"].IsNumber() ||
            !std::isfinite(input["score"].GetDouble())) {
          throw std::invalid_argument("candidate score must be finite");
        }
        consume({label, uri, input["score"].GetDouble()});
      }
    });
  } else if (op == "move_subtree") {
    const auto from_uri = canonical(required_string(req, "from_uri"));
    const auto to_uri = canonical(required_string(req, "to_uri"));
    if (from_uri == to_uri || in_tree(to_uri, from_uri) ||
        in_tree(from_uri, to_uri)) {
      throw std::invalid_argument(
          "Catalog move source and target subtrees must not overlap");
    }

    const std::string prefix = scope + '\0' + '/';
    std::vector<std::string> source_keys;
    std::map<std::string, std::string> updates;
    JsonValue source_nodes(rapidjson::kArrayType);
    JsonValue target_nodes(rapidjson::kArrayType);
    JsonValue created_target_uris(rapidjson::kArrayType);
    for (auto it = nodes_.lower_bound(prefix);
         it != nodes_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
      const auto uri = it->first.substr(scope.size() + 1);
      if (!in_tree(uri, from_uri)) continue;
      const auto suffix = uri.substr(from_uri.size());
      const auto moved_uri = to_uri + suffix;
      const auto moved_key = key(scope, moved_uri);
      const auto source_node = parse(it->second);
      source_nodes.PushBack(JsonValue(source_node, allocator), allocator);
      const auto existing = nodes_.find(moved_key);
      if (existing != nodes_.end()) {
        const auto target_node = parse(existing->second);
        target_nodes.PushBack(JsonValue(target_node, allocator), allocator);
      } else {
        created_target_uris.PushBack(
            JsonValue(moved_uri.c_str(),
                      static_cast<rapidjson::SizeType>(moved_uri.size()),
                      allocator),
            allocator);
      }
      auto node = parse(it->second);
      put_string(node, "uri", moved_uri, node.GetAllocator());
      source_keys.push_back(it->first);
      updates[moved_key] = encode(node);
    }
    if (!source_keys.empty()) {
      commit_changes(updates, source_keys);
      scope_revisions_[scope] = ++revision_;
    }
    result.AddMember("moved_nodes", static_cast<uint64_t>(source_keys.size()), allocator);
    if (!source_keys.empty()) {
      JsonValue undo(rapidjson::kObjectType);
      put_string(undo, "from_uri", from_uri, allocator);
      put_string(undo, "to_uri", to_uri, allocator);
      undo.AddMember("source_nodes", source_nodes, allocator);
      undo.AddMember("target_nodes", target_nodes, allocator);
      undo.AddMember("created_target_uris", created_target_uris, allocator);
      result.AddMember("undo", undo, allocator);
    }
  } else if (op == "restore_move") {
    if (!req.HasMember("undo") || !req["undo"].IsObject()) {
      throw std::invalid_argument("Catalog restore_move requires undo");
    }
    const auto& undo = req["undo"];
    const auto from_uri = canonical(required_string(undo, "from_uri"));
    const auto to_uri = canonical(required_string(undo, "to_uri"));
    if (from_uri == to_uri || in_tree(to_uri, from_uri) ||
        in_tree(from_uri, to_uri)) {
      throw std::invalid_argument(
          "Catalog restore source and target subtrees must not overlap");
    }
    if (!undo.HasMember("source_nodes") || !undo["source_nodes"].IsArray() ||
        !undo.HasMember("target_nodes") || !undo["target_nodes"].IsArray() ||
        !undo.HasMember("created_target_uris") ||
        !undo["created_target_uris"].IsArray()) {
      throw std::invalid_argument("Catalog restore_move undo is invalid");
    }

    std::map<std::string, std::string> updates;
    const auto restore_nodes = [&](const JsonValue& snapshots,
                                   const std::string& root) {
      for (const auto& snapshot : snapshots.GetArray()) {
        const auto uri = canonical(required_string(snapshot, "uri"));
        if (!in_tree(uri, root)) {
          throw std::invalid_argument(
              "Catalog restore node is outside its subtree");
        }
        const auto node_type = required_string(snapshot, "node_type");
        if (node_type != "file" && node_type != "dir") {
          throw std::invalid_argument("Invalid Catalog node_type");
        }
        if (snapshot.HasMember("item_uuid")) required_string(snapshot, "item_uuid");
        if (snapshot.HasMember("item_uuids")) {
          const auto& ids = snapshot["item_uuids"];
          if (!ids.IsArray() || ids.Size() != 1 || !ids[0].IsString() ||
              ids[0].GetStringLength() == 0) {
            throw std::invalid_argument("Invalid restored item_uuids");
          }
        }
        if (snapshot.HasMember("item_attrs")) {
          validate_item_attrs(snapshot["item_attrs"]);
        }
        updates[key(scope, uri)] = encode(snapshot);
      }
    };
    restore_nodes(undo["source_nodes"], from_uri);
    restore_nodes(undo["target_nodes"], to_uri);

    std::vector<std::string> deletes;
    for (const auto& value : undo["created_target_uris"].GetArray()) {
      if (!value.IsString()) {
        throw std::invalid_argument(
            "Catalog restore target URIs must be strings");
      }
      const auto uri = canonical(
          std::string(value.GetString(), value.GetStringLength()));
      if (!in_tree(uri, to_uri)) {
        throw std::invalid_argument(
            "Catalog restore target is outside its subtree");
      }
      deletes.push_back(key(scope, uri));
    }
    commit_changes(updates, deletes);
    if (!updates.empty() || !deletes.empty()) {
      scope_revisions_[scope] = ++revision_;
    }
    result.AddMember("restored_nodes", static_cast<uint64_t>(updates.size()),
                     allocator);
  } else if (op == "delete_subtree") {
    const auto uri = canonical(required_string(req, "uri"));
    if (req.HasMember("preserve_item_root") && !req["preserve_item_root"].IsBool()) {
      throw std::invalid_argument("preserve_item_root must be a boolean");
    }
    const bool preserve_root = req.HasMember("preserve_item_root") &&
                               req["preserve_item_root"].GetBool();
    const std::string prefix = scope + '\0' + '/';
    std::vector<std::string> keys;
    for (auto it = nodes_.lower_bound(prefix);
         it != nodes_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
      const auto node_uri = it->first.substr(scope.size() + 1);
      if (!in_tree(node_uri, uri)) continue;
      if (preserve_root && declarations_.count(it->first)) {
        if (node_uri == uri) continue;
        throw std::invalid_argument("Cannot reset a resource containing independent child Items");
      }
      keys.push_back(it->first);
    }
    if (store_->delete_data(keys) != 0) {
      throw std::runtime_error("Catalog deletion failed");
    }
    for (const auto& node_key : keys) {
      unindex_node(node_key);
      nodes_.erase(node_key);
    }
    if (!keys.empty() && !preserve_root) scope_revisions_[scope] = ++revision_;
  } else {
    throw std::invalid_argument("Unknown Catalog operation: " + op);
  }

  put_string(result, "revision", identity_revision(scope),
             allocator);
  return encode(result);
}

}  // namespace vectordb
