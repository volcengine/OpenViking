// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "catalog/catalog_engine.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

#include "common/json_utils.h"
#include "store/kv_store.h"
#include "store/persist_store.h"
#include "store/volatile_store.h"

namespace vectordb {

class CatalogEngine::Impl {
 public:
  explicit Impl(const std::string& path);
  std::string execute(const std::string& request_json);

 private:
  struct ResolvedItem {
    bool found = false;
    std::string item_uuid;
    std::string root_uri;
    std::vector<std::string> item_attrs;
  };

  std::shared_ptr<KVStore> store_;
  std::map<std::string, std::string> nodes_;
  std::mutex mutex_;
  uint64_t revision_ = 0;
  std::map<std::string, uint64_t> scope_revisions_;

  ResolvedItem resolve_item(const std::string& scope,
                            const std::string& uri) const;
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

namespace {

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
  }
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
  for (const auto& entry : updates) nodes_[entry.first] = entry.second;
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
  for (const auto& node_key : deletes) nodes_.erase(node_key);
  for (const auto& entry : updates) nodes_[entry.first] = entry.second;
}

CatalogEngine::Impl::ResolvedItem CatalogEngine::Impl::resolve_item(
    const std::string& scope, const std::string& uri) const {
  for (std::string current = uri; !current.empty(); current = parent(current)) {
    const auto found = nodes_.find(key(scope, current));
    if (found == nodes_.end()) continue;
    const auto node = parse(found->second);
    if (!node.HasMember("item_uuid")) continue;
    if (!node["item_uuid"].IsString() || node["item_uuid"].GetStringLength() == 0) {
      throw std::runtime_error("Catalog contains an invalid item_uuid");
    }
    ResolvedItem result;
    result.found = true;
    result.item_uuid.assign(node["item_uuid"].GetString(),
                            node["item_uuid"].GetStringLength());
    result.root_uri = current;
    if (node.HasMember("item_attrs")) {
      validate_item_attrs(node["item_attrs"]);
      for (const auto& attr : node["item_attrs"].GetArray()) {
        result.item_attrs.emplace_back(attr.GetString(), attr.GetStringLength());
      }
    }
    return result;
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

  struct DeclaredItem {
    std::string uri;
    std::vector<std::string> attrs;
    bool matches = false;
  };
  std::vector<DeclaredItem> declarations;
  const std::string prefix = scope + '\0' + '/';
  for (auto it = nodes_.lower_bound(prefix);
       it != nodes_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
    const auto uri = it->first.substr(scope.size() + 1);
    const auto node = parse(it->second);
    if (!node.HasMember("item_uuid")) continue;
    if (!node["item_uuid"].IsString() ||
        node["item_uuid"].GetStringLength() == 0) {
      throw std::runtime_error("Catalog contains an invalid item_uuid");
    }
    std::vector<std::string> attrs;
    if (node.HasMember("item_attrs")) {
      validate_item_attrs(node["item_attrs"]);
      for (const auto& attr : node["item_attrs"].GetArray()) {
        attrs.emplace_back(attr.GetString(), attr.GetStringLength());
      }
    }
    declarations.push_back(
        {uri, attrs, matches_item_attrs(attrs, filter)});
  }

  JsonValue branches(rapidjson::kArrayType);
  for (const auto& declaration : declarations) {
    if (!declaration.matches) continue;
    JsonValue branch_conds(rapidjson::kArrayType);
    branch_conds.PushBack(path_filter("must", declaration.uri, allocator),
                          allocator);
    // A nested declaration starts a new shadow scope. Exclude it from the
    // parent branch regardless of whether it independently matches; matching
    // nested Items are reintroduced through their own branch below.
    for (const auto& nested : declarations) {
      if (nested.uri != declaration.uri &&
          in_tree(nested.uri, declaration.uri)) {
        branch_conds.PushBack(path_filter("must_not", nested.uri, allocator),
                              allocator);
      }
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

std::string CatalogEngine::Impl::execute(const std::string& request_json) {
  const auto req = parse(request_json);
  const auto op = required_string(req, "op");
  const auto scope = required_string(req, "scope");
  std::lock_guard<std::mutex> guard(mutex_);
  JsonDoc result(rapidjson::kObjectType);
  auto& allocator = result.GetAllocator();

  if (op == "capabilities") {
    result.AddMember("enabled", nodes_.count("@catalog") != 0, allocator);
    result.AddMember("version", 1, allocator);
    result.AddMember("item_identity_mode", "shadow", allocator);
    result.AddMember("item_attrs_inherit", false, allocator);
  } else if (op == "configure") {
    commit({{"@catalog", "{}"}});
    scope_revisions_[scope] = ++revision_;
  } else if (op == "upsert_nodes") {
    if (!req.HasMember("nodes") || !req["nodes"].IsArray()) {
      throw std::invalid_argument("Catalog requires nodes");
    }
    std::map<std::string, std::string> updates{{"@catalog", "{}"}};
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
    commit(updates);
    scope_revisions_[scope] = ++revision_;
  } else if (op == "declare_item") {
    const auto uri = canonical(required_string(req, "uri"));
    const auto generated_uuid = required_string(req, "item_uuid");
    const auto found = nodes_.find(key(scope, uri));
    if (found == nodes_.end()) {
      throw std::invalid_argument("Catalog node does not exist");
    }
    const bool attrs_supplied = req.HasMember("item_attrs");
    if (attrs_supplied) validate_item_attrs(req["item_attrs"]);
    auto node = parse(found->second);
    auto& node_allocator = node.GetAllocator();
    if (node.HasMember("item_uuid")) {
      if (!node["item_uuid"].IsString()) {
        throw std::runtime_error("Catalog contains an invalid item_uuid");
      }
      if (attrs_supplied) {
        if (!node.HasMember("item_attrs") || node["item_attrs"] != req["item_attrs"]) {
          throw std::invalid_argument(
              "item_attrs cannot change after an Item is declared");
        }
      }
    } else {
      const std::string prefix = scope + '\0' + '/';
      for (auto it = nodes_.lower_bound(prefix);
           it != nodes_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
        const auto other = parse(it->second);
        if (other.HasMember("item_uuid") && other["item_uuid"].IsString() &&
            std::string(other["item_uuid"].GetString(),
                        other["item_uuid"].GetStringLength()) == generated_uuid) {
          throw std::invalid_argument("item_uuid already belongs to another root");
        }
      }
      put_string(node, "item_uuid", generated_uuid, node_allocator);
      JsonValue attrs(rapidjson::kArrayType);
      if (attrs_supplied) attrs.CopyFrom(req["item_attrs"], node_allocator);
      node.AddMember("item_attrs", attrs, node_allocator);
      commit({{"@catalog", "{}"}, {key(scope, uri), encode(node)}});
      scope_revisions_[scope] = ++revision_;
    }
    put_string(result, "uri", uri, allocator);
    put_string(result, "item_uuid",
               std::string(node["item_uuid"].GetString(),
                           node["item_uuid"].GetStringLength()),
               allocator);
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
      const auto item = resolve_item(scope, uri);
      JsonValue row(rapidjson::kObjectType);
      put_string(row, "uri", uri, allocator);
      if (item.found) {
        put_string(row, "item_uuid", item.item_uuid, allocator);
        put_string(row, "item_root_uri", item.root_uri, allocator);
      }
      data.PushBack(row, allocator);
    }
    result.AddMember("data", data, allocator);
  } else if (op == "query_items") {
    if (!req.HasMember("item_uuids") || !req["item_uuids"].IsArray()) {
      throw std::invalid_argument("Catalog query_items requires item_uuids");
    }
    std::map<std::string, std::string> declarations;
    const std::string prefix = scope + '\0' + '/';
    for (auto it = nodes_.lower_bound(prefix);
         it != nodes_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
      const auto node = parse(it->second);
      if (!node.HasMember("item_uuid") || !node["item_uuid"].IsString()) continue;
      declarations.emplace(
          std::string(node["item_uuid"].GetString(),
                      node["item_uuid"].GetStringLength()),
          it->second);
    }
    JsonValue data(rapidjson::kArrayType);
    for (const auto& requested : req["item_uuids"].GetArray()) {
      if (!requested.IsString()) {
        throw std::invalid_argument("item_uuids must contain strings");
      }
      const std::string uuid(requested.GetString(), requested.GetStringLength());
      const auto found = declarations.find(uuid);
      if (found == declarations.end()) continue;
      const auto node = parse(found->second);
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
    const auto aggregation = required_string(req, "aggregation");
    if (aggregation != "max" && aggregation != "sum") {
      throw std::invalid_argument("aggregation must be max or sum");
    }
    if (!req.HasMember("max_groups") || !req["max_groups"].IsInt() ||
        req["max_groups"].GetInt() <= 0) {
      throw std::invalid_argument("max_groups must be a positive integer");
    }
    const int max_groups = req["max_groups"].GetInt();
    const bool has_threshold = req.HasMember("score_threshold");
    const double threshold = has_threshold && req["score_threshold"].IsNumber()
                                 ? req["score_threshold"].GetDouble()
                                 : 0.0;
    if (has_threshold &&
        (!req["score_threshold"].IsNumber() || !std::isfinite(threshold))) {
      throw std::invalid_argument("score_threshold must be finite");
    }

    struct Candidate {
      std::string label;
      std::string uri;
      double score;
    };
    std::map<std::string, Candidate> unique_candidates;
    for (const auto& input : req["candidates"].GetArray()) {
      const auto label = required_string(input, "label");
      const auto uri = canonical(required_string(input, "uri"));
      if (!input.HasMember("score") || !input["score"].IsNumber() ||
          !std::isfinite(input["score"].GetDouble())) {
        throw std::invalid_argument("candidate score must be finite");
      }
      const double score = input["score"].GetDouble();
      const auto existing = unique_candidates.find(label);
      if (existing == unique_candidates.end() || score > existing->second.score) {
        unique_candidates[label] = {label, uri, score};
      }
    }

    struct Group {
      std::string uuid;
      double score;
      std::vector<Candidate> members;
    };
    std::map<std::string, Group> grouped;
    for (const auto& pair : unique_candidates) {
      const auto& candidate = pair.second;
      const auto item = resolve_item(scope, candidate.uri);
      if (!item.found) continue;
      auto [it, inserted] = grouped.emplace(
          item.item_uuid,
          Group{item.item_uuid,
                aggregation == "max" ? -std::numeric_limits<double>::infinity()
                                     : 0.0,
                {}});
      auto& group = it->second;
      group.score = aggregation == "max" ? std::max(group.score, candidate.score)
                                         : group.score + candidate.score;
      group.members.push_back(candidate);
    }
    std::vector<Group> groups;
    for (auto& pair : grouped) {
      auto& group = pair.second;
      if (!has_threshold || group.score >= threshold) {
        std::sort(group.members.begin(), group.members.end(),
                  [](const Candidate& left, const Candidate& right) {
                    if (left.score != right.score) return left.score > right.score;
                    return left.label < right.label;
                  });
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
      output.AddMember("count", static_cast<uint64_t>(group.members.size()), allocator);
      JsonValue labels(rapidjson::kArrayType);
      JsonValue uris(rapidjson::kArrayType);
      for (const auto& member : group.members) {
        labels.PushBack(JsonValue(member.label.c_str(),
                                  static_cast<rapidjson::SizeType>(member.label.size()),
                                  allocator),
                        allocator);
        uris.PushBack(JsonValue(member.uri.c_str(),
                                static_cast<rapidjson::SizeType>(member.uri.size()),
                                allocator),
                      allocator);
      }
      output.AddMember("labels", labels, allocator);
      output.AddMember("labels_truncated", false, allocator);
      output.AddMember("candidate_members_uri", uris, allocator);
      output_groups.PushBack(output, allocator);
    }
    result.AddMember("group_field", "item_uuid", allocator);
    result.AddMember("aggregation",
                     JsonValue(aggregation.c_str(),
                               static_cast<rapidjson::SizeType>(aggregation.size()),
                               allocator),
                     allocator);
    result.AddMember("group_count", static_cast<uint64_t>(group_count), allocator);
    result.AddMember("groups_truncated",
                     group_count > static_cast<size_t>(max_groups), allocator);
    result.AddMember("groups", output_groups, allocator);
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
        if (snapshot.HasMember("item_uuid")) {
          required_string(snapshot, "item_uuid");
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
    const std::string prefix = scope + '\0' + '/';
    std::vector<std::string> keys;
    for (auto it = nodes_.lower_bound(prefix);
         it != nodes_.end() && it->first.rfind(prefix, 0) == 0; ++it) {
      if (in_tree(it->first.substr(scope.size() + 1), uri)) keys.push_back(it->first);
    }
    if (store_->delete_data(keys) != 0) {
      throw std::runtime_error("Catalog deletion failed");
    }
    for (const auto& node_key : keys) nodes_.erase(node_key);
    if (!keys.empty()) scope_revisions_[scope] = ++revision_;
  } else {
    throw std::invalid_argument("Unknown Catalog operation: " + op);
  }

  put_string(result, "revision", std::to_string(scope_revisions_[scope]),
             allocator);
  return encode(result);
}

}  // namespace vectordb
