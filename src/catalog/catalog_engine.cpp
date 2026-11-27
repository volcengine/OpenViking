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
  std::optional<std::string> search_items(IndexEngine& index, KVStore& candidates,
                                          const BytesRow& row, SearchRequest query,
                                          const std::string& request_json);

 private:
  struct Candidate {
    std::string label;
    std::string uri;
    double score;
  };
  using CandidateConsumer = std::function<void(const Candidate&)>;
  using CandidateSource = std::function<void(const CandidateConsumer&)>;
  static std::optional<Candidate> project_candidate(const JsonValue& fields,
                                                      const std::string& primary_key,
                                                      double score);
  void group_candidates(const JsonValue& request, const std::string& scope,
                        JsonDoc& result, const CandidateSource& source) const;

  struct ResolvedItem {
    bool found = false;
    std::string item_uuid;
    std::string root_uri;
    std::vector<std::string> item_attrs;
  };

  std::shared_ptr<KVStore> store_;
  // Persisted collection settings and explicit Item declarations.
  std::map<std::string, std::string> records_;
  std::map<std::string, ResolvedItem> declarations_;
  std::map<std::string, std::string> uuid_roots_;
  std::map<std::string, std::set<std::string>> attribute_roots_;
  std::mutex mutex_;
  uint64_t revision_ = 0;
  uint64_t mode_revision_ = 0;
  std::map<std::string, uint64_t> scope_revisions_;
  bool collection_union_mode_ = true;

  ResolvedItem resolve_item(const std::string& scope,
                            const std::string& uri) const;
  std::vector<ResolvedItem> resolve_items(const std::string& scope,
                                          const std::string& uri) const;
  bool union_mode() const;
  std::string identity_revision(const std::string& scope) const;
  void unindex_declaration(const std::string& record_key);
  void index_declaration(const std::string& record_key, const JsonValue& record);
  JsonValue compile_filter(const std::string& scope, const JsonValue& filter,
                           JsonAllocator& allocator) const;
  JsonValue compile_item_filter(const std::string& scope, const JsonValue& filter,
                                JsonAllocator& allocator) const;
  void commit(const std::map<std::string, std::string>& updates,
              const std::vector<std::string>& deletes = {});
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

std::optional<std::string> CatalogEngine::search_items(
    IndexEngine& index, KVStore& candidates, const BytesRow& row,
    SearchRequest query, const std::string& request_json) {
  return impl_->search_items(index, candidates, row, std::move(query), request_json);
}

namespace {

constexpr const char* kIdentityModeKey = "@item_uuids_mode";
constexpr size_t kItemCandidateBatchSize = 256;

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

std::string required_item_uuid(const JsonValue& record) {
  if (!record.HasMember("item_uuids") || !record["item_uuids"].IsArray() ||
      record["item_uuids"].Size() != 1 || !record["item_uuids"][0].IsString() ||
      record["item_uuids"][0].GetStringLength() == 0) {
    throw std::invalid_argument("An Item root must declare one item_uuids string");
  }
  const auto& id = record["item_uuids"][0];
  std::string uuid(id.GetString(), id.GetStringLength());
  if (uuid.find('\0') != std::string::npos) {
    throw std::invalid_argument("item_uuids cannot contain NUL");
  }
  return uuid;
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

JsonValue path_filter(const char* op, JsonValue paths,
                      JsonAllocator& allocator) {
  JsonValue filter(rapidjson::kObjectType);
  filter.AddMember("op", JsonValue(op, allocator), allocator);
  filter.AddMember("field", "uri", allocator);
  filter.AddMember("conds", paths, allocator);
  filter.AddMember("para", "-d=-1", allocator);
  return filter;
}

JsonValue path_filter(const char* op, const std::string& uri,
                      JsonAllocator& allocator) {
  JsonValue paths(rapidjson::kArrayType);
  paths.PushBack(JsonValue(uri.data(),
                           static_cast<rapidjson::SizeType>(uri.size()), allocator),
                 allocator);
  return path_filter(op, std::move(paths), allocator);
}

}  // namespace

CatalogEngine::Impl::Impl(const std::string& path) {
  store_ = path.empty() ? std::shared_ptr<KVStore>(new VolatileStore())
                        : std::shared_ptr<KVStore>(new PersistStore(path));
  for (const auto& entry :
       store_->seek_range("", std::string(1, static_cast<char>(0xff)))) {
    const auto record = parse(entry.second);
    if (entry.first == "@catalog" || entry.first == kIdentityModeKey ||
        record.HasMember("item_uuids")) {
      records_.insert(entry);
      index_declaration(entry.first, record);
    }
  }
  const auto read_mode = [](const std::string& value) {
    const auto mode = required_string(parse(value), "item_identity_mode");
    if (mode != "shadow" && mode != "union") {
      throw std::invalid_argument("item_identity_mode must be shadow or union");
    }
    return mode == "union";
  };
  const auto config = records_.find(kIdentityModeKey);
  if (config != records_.end()) {
    collection_union_mode_ = read_mode(config->second);
    return;
  }
}

void CatalogEngine::Impl::commit(
    const std::map<std::string, std::string>& updates,
    const std::vector<std::string>& deletes) {
  std::vector<StorageOp> operations;
  operations.reserve(updates.size() + deletes.size());
  for (const auto& entry : updates) {
    operations.push_back({StorageOp::PUT_OP, entry.first, entry.second});
  }
  for (const auto& record_key : deletes) {
    if (updates.count(record_key) == 0) {
      operations.push_back({StorageOp::DELETE_OP, record_key, ""});
    }
  }
  if (store_->exec_op(operations) != 0) {
    throw std::runtime_error("Catalog transaction failed");
  }
  for (const auto& record_key : deletes) {
    unindex_declaration(record_key);
    records_.erase(record_key);
  }
  for (const auto& entry : updates) unindex_declaration(entry.first);
  for (const auto& entry : updates) {
    records_[entry.first] = entry.second;
    index_declaration(entry.first, parse(entry.second));
  }
}

void CatalogEngine::Impl::unindex_declaration(const std::string& record_key) {
  const auto found = declarations_.find(record_key);
  if (found == declarations_.end()) return;
  const auto scope = record_key.substr(0, record_key.find('\0'));
  uuid_roots_.erase(key(scope, found->second.item_uuid));
  for (const auto& attr : found->second.item_attrs) {
    const auto posting = attribute_roots_.find(key(scope, attr));
    if (posting == attribute_roots_.end()) continue;
    posting->second.erase(record_key);
    if (posting->second.empty()) attribute_roots_.erase(posting);
  }
  declarations_.erase(found);
}

void CatalogEngine::Impl::index_declaration(const std::string& record_key,
                                            const JsonValue& record) {
  const auto separator = record_key.find('\0');
  if (separator == std::string::npos || record_key[separator + 1] != '/') return;
  ResolvedItem item;
  item.found = true;
  item.item_uuid = required_item_uuid(record);
  item.root_uri = record_key.substr(separator + 1);
  if (record.HasMember("item_attrs")) {
    validate_item_attrs(record["item_attrs"]);
    for (const auto& attr : record["item_attrs"].GetArray()) {
      item.item_attrs.emplace_back(attr.GetString(), attr.GetStringLength());
    }
  }
  const auto scope = record_key.substr(0, separator);
  uuid_roots_[key(scope, item.item_uuid)] = record_key;
  for (const auto& attr : item.item_attrs) {
    attribute_roots_[key(scope, attr)].insert(record_key);
  }
  declarations_[record_key] = std::move(item);
}

bool CatalogEngine::Impl::union_mode() const {
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

JsonValue CatalogEngine::Impl::compile_item_filter(
    const std::string& scope, const JsonValue& filter,
    JsonAllocator& allocator) const {
  auto compiled = compile_filter(scope, filter, allocator);
  JsonValue roots(rapidjson::kArrayType);
  const auto prefix = key(scope, "/");
  for (auto it = declarations_.lower_bound(prefix);
       it != declarations_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
    const auto& uri = it->second.root_uri;
    // An ancestor's path already covers this subtree in either inheritance mode.
    auto ancestor = parent(uri);
    while (!ancestor.empty() && !declarations_.count(key(scope, ancestor))) {
      ancestor = parent(ancestor);
    }
    if (ancestor.empty()) {
      // Direct collection writes can keep the URI scheme in the path index.
      const auto qualified_uri = "viking:/" + uri;
      for (const auto* path : {&uri, &qualified_uri}) {
        roots.PushBack(JsonValue(path->data(),
                                 static_cast<rapidjson::SizeType>(path->size()),
                                 allocator),
                       allocator);
      }
    }
  }
  auto membership = roots.Empty() ? impossible_filter(allocator)
                                  : path_filter("must", std::move(roots), allocator);
  if (compiled.HasMember("sorter") && !compiled.HasMember("filter")) {
    compiled.AddMember("filter", membership, allocator);
    return compiled;
  }
  auto& predicate = compiled.HasMember("filter") ? compiled["filter"] : compiled;
  if (!predicate.ObjectEmpty()) {
    JsonValue clauses(rapidjson::kArrayType);
    clauses.PushBack(predicate, allocator).PushBack(membership, allocator);
    membership.SetObject();
    membership.AddMember("op", "and", allocator);
    membership.AddMember("conds", clauses, allocator);
  }
  predicate = std::move(membership);
  return compiled;
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
  // Union mode credits every declared ancestor of the candidate. Shadow mode
  // credits its nearest declaration.
  source([&](const Candidate& candidate) {
    for (const auto& item : resolve_items(scope, candidate.uri)) {
      const auto entry = grouped.emplace(
          item.item_uuid,
          Group{item.item_uuid,
                aggregation == "max" ? -std::numeric_limits<double>::infinity()
                                     : 0.0,
                0,
                {}});
      auto& group = entry.first->second;
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
  // Apply the threshold after all members have contributed to the Item score.
  std::vector<Group> groups;
  for (auto& pair : grouped) {
    auto& group = pair.second;
    if (!has_threshold || group.score >= threshold) {
      std::sort(group.members.begin(), group.members.end(), better_member);
      groups.push_back(std::move(group));
    }
  }
  // Break ties by UUID so filter-only Items with zero scores have stable order.
  std::sort(groups.begin(), groups.end(),
            [](const Group& left, const Group& right) {
              if (left.score != right.score) return left.score > right.score;
              return left.uuid < right.uuid;
            });
  // Report the number of eligible Items before enforcing the response limit.
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
    output.AddMember("candidate_members", labels, allocator);
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

std::optional<CatalogEngine::Impl::Candidate> CatalogEngine::Impl::project_candidate(
    const JsonValue& fields, const std::string& primary_key, double score) {
  if (!fields.HasMember("uri") || !fields["uri"].IsString() ||
      fields["uri"].GetStringLength() == 0) return std::nullopt;
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
  if (!std::isfinite(score)) {
    throw std::invalid_argument("candidate score must be finite");
  }
  return Candidate{std::move(label), canonical(required_string(fields, "uri")), score};
}

std::optional<std::string> CatalogEngine::Impl::search_items(
    IndexEngine& index, KVStore& candidates, const BytesRow& row,
    SearchRequest query, const std::string& request_json) {
  const auto req = parse(request_json);
  const auto scope = required_string(req, "scope");
  const auto table = required_string(req, "candidate_table");
  const auto primary_key = required_string(req, "primary_key");
  const auto filter = parse(query.dsl);

  JsonDoc compiled(rapidjson::kObjectType);
  std::string revision;
  {
    std::lock_guard<std::mutex> guard(mutex_);
    auto effective = compile_item_filter(scope, filter, compiled.GetAllocator());
    compiled.CopyFrom(effective, compiled.GetAllocator());
    revision = identity_revision(scope);
  }

  auto& allocator = compiled.GetAllocator();
  JsonValue count_filter(rapidjson::kObjectType);
  count_filter.AddMember("filter", JsonValue(
      compiled.HasMember("filter") ? compiled["filter"] : compiled, allocator),
      allocator);
  JsonValue sorter(rapidjson::kObjectType);
  sorter.AddMember("op", "count", allocator);
  count_filter.AddMember("sorter", sorter, allocator);
  SearchRequest count_query;
  count_query.topk = 1;
  count_query.dsl = encode(count_filter);
  JsonDoc counts;
  const auto count_response = index.search(count_query);
  counts.Parse(count_response.extra_json.c_str(), count_response.extra_json.size());
  if (!counts.IsObject() || !counts.HasMember("__total_count__") ||
      !counts["__total_count__"].IsUint()) {
    throw std::invalid_argument("Native index could not count eligible Item members");
  }

  SearchResult recalled;
  query.topk = counts["__total_count__"].GetUint();
  if (query.topk != 0) {
    query.dsl = encode(compiled);
    recalled = index.search(query);
  }
  if (recalled.labels.size() != recalled.scores.size()) {
    throw std::runtime_error("Native Item candidate labels and scores differ in size");
  }

  const auto limit = std::min(recalled.labels.size(), static_cast<size_t>(query.topk));
  std::vector<Candidate> projected;
  projected.reserve(limit);
  std::vector<std::string> keys;
  keys.reserve(kItemCandidateBatchSize);
  for (size_t start = 0; start < limit; start += kItemCandidateBatchSize) {
    const auto end = std::min(limit, start + kItemCandidateBatchSize);
    keys.clear();
    for (size_t i = start; i < end; ++i) {
      keys.push_back(table + std::to_string(recalled.labels[i]));
    }
    const auto records = candidates.get_data(keys);
    if (records.size() != keys.size()) {
      throw std::runtime_error("Incomplete Item candidate batch");
    }
    for (size_t i = 0; i < records.size(); ++i) {
      if (records[i].empty()) continue;
      const auto fields_value = row.deserialize_field(records[i], "fields");
      const auto* encoded = std::get_if<std::string>(&fields_value);
      if (!encoded) continue;
      JsonDoc fields;
      fields.Parse(encoded->data(), encoded->size());
      if (fields.HasParseError() || !fields.IsObject()) continue;
      if (auto candidate = project_candidate(fields, primary_key, recalled.scores[start + i])) {
        projected.push_back(std::move(*candidate));
      }
    }
  }

  JsonDoc result(rapidjson::kObjectType);
  {
    // Index and Store reads above do not hold the Catalog lock.
    std::lock_guard<std::mutex> guard(mutex_);
    if (identity_revision(scope) != revision) return std::nullopt;
    group_candidates(req, scope, result, [&](const CandidateConsumer& consume) {
      for (const auto& candidate : projected) consume(candidate);
    });
  }
  put_string(result, "revision", revision, result.GetAllocator());
  return encode(result);
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
  JsonDoc result(rapidjson::kObjectType);
  auto& allocator = result.GetAllocator();
  JsonDoc dsl(rapidjson::kObjectType);
  auto compiled = compile_item_filter(scope, req["filter"], dsl.GetAllocator());
  if (compiled.HasMember("filter")) {
    dsl.CopyFrom(compiled, dsl.GetAllocator());
  } else {
    dsl.AddMember("filter", compiled, dsl.GetAllocator());
  }
  const auto filter_dsl = encode(dsl);
  group_candidates(req, scope, result, [&](const CandidateConsumer& consume) {
    // Only one bounded batch of stored records is decoded at a time. Dense
    // vectors remain encoded, and no per-member data crosses the Python ABI.
    std::vector<std::string> keys;
    keys.reserve(kItemCandidateBatchSize);
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
        if (auto candidate = project_candidate(fields, primary_key, 0.0)) {
          consume(*candidate);
        }
      }
      keys.clear();
    };
    index.visit_filtered_labels(filter_dsl, [&](uint64_t label) {
      keys.push_back(table + std::to_string(label));
      if (keys.size() == kItemCandidateBatchSize) flush();
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
  // Hold the lock across persistence and index updates so concurrent calls
  // see a consistent Catalog state and revision.
  std::lock_guard<std::mutex> guard(mutex_);
  JsonDoc result(rapidjson::kObjectType);
  auto& allocator = result.GetAllocator();

  if (op == "capabilities") {
    result.AddMember("enabled", records_.count("@catalog") != 0, allocator);
    result.AddMember("version", 1, allocator);
    put_string(result, "item_identity_mode", union_mode() ? "union" : "shadow", allocator);
    JsonValue modes(rapidjson::kArrayType);
    modes.PushBack("shadow", allocator).PushBack("union", allocator);
    result.AddMember("item_identity_modes", modes, allocator);
    result.AddMember("item_attrs_inherit", false, allocator);
  } else if (op == "configure") {
    std::map<std::string, std::string> updates;
    if (records_.count("@catalog") == 0) updates["@catalog"] = "{}";
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
    if (records_.count(kIdentityModeKey) == 0 || records_.at(kIdentityModeKey) != encoded) {
      updates[kIdentityModeKey] = encoded;
    }
    const bool changed = accumulate != collection_union_mode_;
    if (!updates.empty()) commit(updates);
    collection_union_mode_ = accumulate;
    if (changed) {
      // The same field configuration affects every account in the collection.
      mode_revision_ = ++revision_;
    }
  } else if (op == "validate_resource_update") {
    const auto uri = canonical(required_string(req, "uri"));
    const auto own = declarations_.find(key(scope, uri));
    if (own != declarations_.end() && req.HasMember("node_type") &&
        required_string(req, "node_type") != "dir") {
      throw std::invalid_argument("Cannot change an Item root node type");
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
    if (req.HasMember("uris")) {
      if (!req["uris"].IsArray()) {
        throw std::invalid_argument("Catalog resource update expects a list of removed URIs");
      }
      for (const auto& value : req["uris"].GetArray()) {
        if (!value.IsString()) throw std::invalid_argument("URI must be a string");
        const auto removed_uri = canonical(
            std::string(value.GetString(), value.GetStringLength()));
        if (removed_uri != uri && declarations_.count(key(scope, removed_uri))) {
          throw std::invalid_argument(
              "Cannot replace a resource root containing independent child Items");
        }
      }
    }
    result.AddMember("valid", true, allocator);
    result.AddMember("has_item", resolve_item(scope, uri).found, allocator);
  } else if (op == "declare_item") {
    const auto uri = canonical(required_string(req, "uri"));
    auto generated_uuid = required_item_uuid(req);
    const bool attrs_supplied = req.HasMember("item_attrs");
    if (attrs_supplied) validate_item_attrs(req["item_attrs"]);
    const auto item_key = key(scope, uri);
    const auto existing_item = declarations_.find(item_key);
    JsonDoc declaration(rapidjson::kObjectType);
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
      declaration = parse(records_.at(item_key));
    } else {
      if (uuid_roots_.count(key(scope, generated_uuid))) {
        throw std::invalid_argument("item_uuid already belongs to another root");
      }
      auto& declaration_allocator = declaration.GetAllocator();
      put_string(declaration, "uri", uri, declaration_allocator);
      JsonValue ids(rapidjson::kArrayType);
      ids.CopyFrom(req["item_uuids"], declaration_allocator);
      declaration.AddMember("item_uuids", ids, declaration_allocator);
      JsonValue attrs(rapidjson::kArrayType);
      if (attrs_supplied) attrs.CopyFrom(req["item_attrs"], declaration_allocator);
      declaration.AddMember("item_attrs", attrs, declaration_allocator);
      commit({{"@catalog", "{}"}, {item_key, encode(declaration)}});
      scope_revisions_[scope] = ++revision_;
    }
    put_string(result, "uri", uri, allocator);
    put_string(result, "item_uuid", generated_uuid, allocator);
    JsonValue ids(rapidjson::kArrayType);
    ids.PushBack(JsonValue(generated_uuid.c_str(), allocator), allocator);
    result.AddMember("item_uuids", ids, allocator);
    JsonValue attrs(rapidjson::kArrayType);
    if (declaration.HasMember("item_attrs")) {
      attrs.CopyFrom(declaration["item_attrs"], allocator);
    }
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
      JsonValue row(rapidjson::kObjectType);
      put_string(row, "uri", uri, allocator);
      if (!items.empty()) {
        const auto& item = items.front();
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
  } else if (op == "compile_filter" || op == "compile_item_filter") {
    if (!req.HasMember("filter")) {
      throw std::invalid_argument("Catalog requires filter");
    }
    auto filter = op == "compile_item_filter"
                      ? compile_item_filter(scope, req["filter"], allocator)
                      : compile_filter(scope, req["filter"], allocator);
    result.AddMember("filter", filter, allocator);
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

    // Snapshot changed Item declarations for rollback if the transfer fails.
    const auto prefix = key(scope, "/");
    std::vector<std::string> source_keys;
    std::map<std::string, std::string> updates;
    JsonValue source_nodes(rapidjson::kArrayType);
    JsonValue target_nodes(rapidjson::kArrayType);
    JsonValue created_target_uris(rapidjson::kArrayType);
    for (auto it = records_.lower_bound(prefix);
         it != records_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
      const auto uri = it->first.substr(scope.size() + 1);
      if (!in_tree(uri, from_uri)) continue;
      const auto suffix = uri.substr(from_uri.size());
      const auto moved_uri = to_uri + suffix;
      const auto moved_key = key(scope, moved_uri);
      auto declaration = parse(it->second);
      source_nodes.PushBack(JsonValue(declaration, allocator), allocator);
      const auto existing = records_.find(moved_key);
      if (existing != records_.end()) {
        const auto target = parse(existing->second);
        target_nodes.PushBack(JsonValue(target, allocator), allocator);
      } else {
        created_target_uris.PushBack(
            JsonValue(moved_uri.c_str(),
                      static_cast<rapidjson::SizeType>(moved_uri.size()),
                      allocator),
            allocator);
      }
      put_string(declaration, "uri", moved_uri, declaration.GetAllocator());
      source_keys.push_back(it->first);
      updates[moved_key] = encode(declaration);
    }
    if (!source_keys.empty()) {
      commit(updates, source_keys);
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
    const auto restore_declarations = [&](const JsonValue& snapshots,
                                          const std::string& root) {
      for (const auto& snapshot : snapshots.GetArray()) {
        const auto uri = canonical(required_string(snapshot, "uri"));
        if (!in_tree(uri, root)) {
          throw std::invalid_argument(
              "Catalog restore declaration is outside its subtree");
        }
        required_item_uuid(snapshot);
        if (snapshot.HasMember("item_attrs")) {
          validate_item_attrs(snapshot["item_attrs"]);
        }
        updates[key(scope, uri)] = encode(snapshot);
      }
    };
    restore_declarations(undo["source_nodes"], from_uri);
    restore_declarations(undo["target_nodes"], to_uri);

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
    // Restore saved declarations and remove move-created targets in one store
    // transaction before advancing the account revision.
    commit(updates, deletes);
    if (!updates.empty() || !deletes.empty()) {
      scope_revisions_[scope] = ++revision_;
    }
    result.AddMember("restored_nodes", static_cast<uint64_t>(updates.size()),
                     allocator);
  } else if (op == "delete_subtree") {
    const auto uri = canonical(required_string(req, "uri"));
    const auto prefix = key(scope, "/");
    std::vector<std::string> keys;
    for (auto it = declarations_.lower_bound(prefix);
         it != declarations_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
      if (in_tree(it->second.root_uri, uri)) keys.push_back(it->first);
    }
    if (!keys.empty()) {
      commit({}, keys);
      scope_revisions_[scope] = ++revision_;
    }
  } else {
    throw std::invalid_argument("Unknown Catalog operation: " + op);
  }

  // Report the newer of the account's identity revision and the collection's
  // inheritance-mode revision. A mode change invalidates every account's
  // Item snapshot because it changes how membership is resolved.
  put_string(result, "revision", identity_revision(scope),
             allocator);
  return encode(result);
}

}  // namespace vectordb
