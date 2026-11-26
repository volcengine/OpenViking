// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

package openviking

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func testClient(t *testing.T, handler http.Handler) (*Client, func()) {
	t.Helper()
	server := httptest.NewServer(handler)
	client, err := NewClient(Config{
		BaseURL:      server.URL,
		APIKey:       "key",
		Account:      "acct",
		User:         "alice",
		ActorPeerID:  "peer-1",
		Profile:      true,
		UploadMode:   "shared",
		ExtraHeaders: map[string]string{"X-Tenant": "test-tenant"},
	})
	if err != nil {
		t.Fatal(err)
	}
	return client, server.Close
}

func writeOK(t *testing.T, w http.ResponseWriter, result any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"status": "ok",
		"result": result,
	}); err != nil {
		t.Fatal(err)
	}
}

func writeAPIError(t *testing.T, w http.ResponseWriter, status int, code string, details map[string]any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(map[string]any{
		"status": "error",
		"error": map[string]any{
			"code":    code,
			"message": "not found",
			"details": details,
		},
	}); err != nil {
		t.Fatal(err)
	}
}

func readJSONBody(t *testing.T, r *http.Request) map[string]any {
	t.Helper()
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	return body
}

func requireBodyKeysAbsent(t *testing.T, body map[string]any, keys ...string) {
	t.Helper()
	for _, key := range keys {
		if _, ok := body[key]; ok {
			t.Fatalf("unexpected %s in body: %#v", key, body)
		}
	}
}

func TestFindSendsHeadersQueryAndBody(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/search/find" || r.URL.Query().Get("profile") != "1" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		for name, want := range map[string]string{
			"X-API-Key": "key", "X-OpenViking-Account": "acct", "X-OpenViking-User": "alice",
			"X-OpenViking-Actor-Peer": "peer-1", "X-Tenant": "test-tenant",
		} {
			if got := r.Header.Get(name); got != want {
				t.Fatalf("header %s = %q, want %q", name, got, want)
			}
		}
		want := map[string]any{
			"query": "auth", "target_uri": "viking://resources/docs", "limit": float64(5),
			"since": "2026-06-01", "until": "2026-06-18", "time_field": "created_at",
			"events_time_decay_protection": "2d", "level": []any{float64(0), float64(2)},
			"tags": []any{"topic=docs", "kind=api"}, "context_type": []any{"resource"}, "read_content": true,
		}
		body := readJSONBody(t, r)
		for key, expected := range want {
			if !reflect.DeepEqual(body[key], expected) {
				t.Fatalf("%s = %#v, want %#v", key, body[key], expected)
			}
		}
		requireBodyKeysAbsent(t, body, "agent_id", "agent_uri")
		writeOK(t, w, map[string]any{
			"resources": []map[string]any{
				{"uri": "viking://resources/docs/api.md", "context_type": "resource", "score": 0.9, "tags": []string{"topic=docs", "kind=api"}, "content": "full visible content"},
			},
		})
	}))
	defer closeServer()

	result, err := client.Find(context.Background(), "auth", &FindOptions{
		TargetURI:                 "resources/docs",
		Limit:                     5,
		ContextType:               []string{"resource"},
		Since:                     "2026-06-01",
		Until:                     "2026-06-18",
		TimeField:                 "created_at",
		Level:                     []int{0, 2},
		Tags:                      []string{"topic=docs", "kind=api"},
		EventsTimeDecayProtection: "2d",
		ReadContent:               Bool(true),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Resources) != 1 || result.Resources[0].URI != "viking://resources/docs/api.md" || result.Resources[0].Content != "full visible content" {
		t.Fatalf("unexpected result: %#v", result)
	}
	if got := result.Resources[0].Tags; len(got) != 2 || got[0] != "topic=docs" || got[1] != "kind=api" {
		t.Fatalf("result tags = %#v", got)
	}
}

func TestSearchPreservesSessionAndDistinguishesOmittedAndEmptyFilters(t *testing.T) {
	var body map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/search/search" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		body = readJSONBody(t, r)
		writeOK(t, w, map[string]any{"resources": []any{}})
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.Search(ctx, "auth", nil); err != nil {
		t.Fatal(err)
	}
	requireBodyKeysAbsent(t, body, "session_id", "tags", "level")
	if body["limit"] != float64(10) {
		t.Fatalf("default limit = %#v", body["limit"])
	}

	if _, err := client.Search(ctx, "auth", &SearchOptions{
		SessionID: "session-1", SearchType: "keywords", NodeLimit: Int(3), Level: []int{0}, Tags: []string{},
	}); err != nil {
		t.Fatal(err)
	}
	if body["session_id"] != "session-1" || body["search_type"] != "keywords" ||
		body["limit"] != float64(3) || !reflect.DeepEqual(body["tags"], []any{}) ||
		!reflect.DeepEqual(body["level"], []any{float64(0)}) {
		t.Fatalf("search body = %#v", body)
	}
}

func TestListAndTreePreserveOptionsAndPagination(t *testing.T) {
	entries := []any{map[string]any{"name": "docs"}}
	listCalls := 0
	treeCalls := 0
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var want map[string][]string
		var omitted []string
		switch r.URL.Path {
		case "/api/v1/fs/ls":
			if listCalls == 0 {
				want = map[string][]string{
					"node_limit": {"200"}, "offset": {"4"}, "limit": {"5"},
					"sort_by": {"mtime"}, "sort_order": {"desc"},
					"tags": {"env=prod", "team=search"}, "extra_fields": {"locked", "id"},
					"include_abstract": {"false"}, "include_overview": {"true"},
					"overview_limit": {"512"}, "abs_limit": {"128"}, "include_tags": {"true"},
				}
			} else {
				want = map[string][]string{"abs_limit": {"2000"}, "overview_limit": {"4000"}}
				omitted = []string{"include_abstract", "include_overview", "include_tags"}
			}
			listCalls++
		case "/api/v1/fs/tree":
			if got := r.URL.Query().Get("abs_limit"); got != "2000" {
				t.Fatalf("abs_limit = %q", got)
			}
			if treeCalls == 0 {
				want = map[string][]string{
					"level_limit": {"0"}, "offset": {"6"}, "limit": {"7"},
					"tags": {"env=prod"}, "extra_fields": {"count"},
					"include_abstract": {"false"}, "include_overview": {"true"},
					"overview_limit": {"512"}, "directories_only": {"true"}, "include_tags": {"true"},
				}
			} else {
				want = map[string][]string{"level_limit": {"3"}, "overview_limit": {"4000"}}
				omitted = []string{"offset", "limit", "include_abstract", "include_overview"}
			}
			treeCalls++
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		query := r.URL.Query()
		for key, expected := range want {
			if !reflect.DeepEqual(query[key], expected) {
				t.Fatalf("%s: %s = %v, want %v", r.URL.Path, key, query[key], expected)
			}
		}
		for _, key := range omitted {
			if _, ok := query[key]; ok {
				t.Fatalf("%s unexpectedly sent %s: %v", r.URL.Path, key, query[key])
			}
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "result": entries, "has_more": r.URL.Path == "/api/v1/fs/ls",
		}); err != nil {
			t.Fatal(err)
		}
	}))
	defer closeServer()

	listPage, err := client.ListPage(context.Background(), "viking://~/sessions", &ListOptions{
		NodeLimit:       200,
		Offset:          4,
		Limit:           5,
		SortBy:          "mtime",
		SortOrder:       "desc",
		ExtraFields:     []string{"locked", "id"},
		Tags:            []string{"env=prod", "team=search"},
		AbsLimit:        128,
		IncludeAbstract: Bool(false),
		IncludeOverview: Bool(true),
		IncludeTags:     true,
		OverviewLimit:   512,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !listPage.HasMore || !reflect.DeepEqual(listPage.Result, entries) {
		t.Fatalf("list page = %#v", listPage)
	}
	sessionList, err := client.List(context.Background(), "viking://~/sessions", nil)
	if err != nil || !reflect.DeepEqual(sessionList, entries) {
		t.Fatalf("list = %#v, err = %v", sessionList, err)
	}
	treePage, err := client.TreePage(context.Background(), "viking://resources/docs", &TreeOptions{
		NodeLimit:       200,
		LevelLimit:      Int(0),
		Offset:          6,
		Limit:           7,
		ExtraFields:     []string{"count"},
		Tags:            []string{"env=prod"},
		IncludeAbstract: Bool(false),
		IncludeOverview: Bool(true),
		IncludeTags:     true,
		OverviewLimit:   512,
		DirectoriesOnly: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if treePage.HasMore || !reflect.DeepEqual(treePage.Result, []map[string]any{{"name": "docs"}}) {
		t.Fatalf("tree page = %#v", treePage)
	}
	legacyTree, err := client.Tree(context.Background(), "viking://resources/docs", nil)
	if err != nil || !reflect.DeepEqual(legacyTree, treePage.Result) {
		t.Fatalf("tree = %#v, err = %v", legacyTree, err)
	}
}

func TestFindSendsImageQuery(t *testing.T) {
	imagePath := filepath.Join(t.TempDir(), "query.png")
	if err := os.WriteFile(imagePath, []byte("\x89PNG\r\n\x1a\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search/find" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		body := readJSONBody(t, r)
		if got := body["query"]; got != "" {
			t.Fatalf("query = %#v", got)
		}
		imageURL, ok := body["image_url"].(string)
		if !ok || !strings.HasPrefix(imageURL, "data:image/png;base64,") {
			t.Fatalf("image_url = %#v", body["image_url"])
		}
		writeOK(t, w, map[string]any{"resources": []any{}})
	}))
	defer closeServer()

	if _, err := client.Find(context.Background(), "", &FindOptions{
		TargetURI: "viking://resources/images",
		Image:     imagePath,
	}); err != nil {
		t.Fatal(err)
	}
}

func TestReindexPreservesExplicitOptions(t *testing.T) {
	var body map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/content/reindex" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		body = readJSONBody(t, r)
		writeOK(t, w, map[string]any{"status": "completed"})
	}))
	defer closeServer()

	ctx := context.Background()
	uri := "resources/demo"
	for _, test := range []struct {
		opts ReindexOptions
		want map[string]any
	}{
		{
			ReindexOptions{Wait: false, Force: true, Recursive: Bool(false), Tags: []string{}, TagMode: "replace",
				Extra: map[string]any{"future_flag": false}},
			map[string]any{"uri": "viking://resources/demo", "mode": "vectors_only", "wait": false,
				"force": true, "recursive": false, "tags": []any{}, "tag_mode": "replace", "future_flag": false},
		},
		{
			ReindexOptions{TagMode: "clear"},
			map[string]any{"uri": "viking://resources/demo", "mode": "vectors_only", "wait": false,
				"recursive": true, "tag_mode": "clear"},
		},
	} {
		if _, err := client.Reindex(ctx, uri, &test.opts); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(body, test.want) {
			t.Fatalf("body = %#v, want %#v", body, test.want)
		}
	}
	if _, err := client.Reindex(ctx, uri, &ReindexOptions{Extra: map[string]any{"tags": []string{"team=search"}}}); err == nil {
		t.Fatal("extra must not override tags")
	}
}

func TestAdminRegenerateKeyDistinguishesEmptySeedFromMissingSeed(t *testing.T) {
	var request map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/admin/accounts/acct/users/alice/key" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		request = readJSONBody(t, r)
		if request["seed"] == "" {
			writeAPIError(t, w, http.StatusBadRequest, "INVALID_ARGUMENT", nil)
			return
		}
		writeOK(t, w, map[string]any{"api_key": "rotated-key"})
	}))
	defer closeServer()

	ctx := context.Background()
	result, err := client.AdminRegenerateKey(ctx, "acct", "alice")
	if err != nil || result["api_key"] != "rotated-key" {
		t.Fatalf("rotate result = %#v, err = %v", result, err)
	}
	requireBodyKeysAbsent(t, request, "seed")
	if _, err := client.AdminRegenerateKeyWithOptions(ctx, "acct", "alice", &AdminRegenerateKeyOptions{
		Seed: String(""),
	}); !IsCode(err, "INVALID_ARGUMENT") {
		t.Fatalf("empty seed must be rejected, got %v", err)
	}
}

func TestAdminGroupMembersUseBatchEndpoint(t *testing.T) {
	for _, tc := range []struct {
		name   string
		method string
		result map[string]any
		call   func(*Client, context.Context, string, string, string) (map[string]any, error)
	}{
		{"add", http.MethodPut, map[string]any{"added": true}, (*Client).AdminAddGroupMember},
		{"remove", http.MethodDelete, map[string]any{"removed": true}, (*Client).AdminRemoveGroupMember},
		{"remove non-member", http.MethodDelete, map[string]any{"removed": false}, (*Client).AdminRemoveGroupMember},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != tc.method || r.URL.Path != "/api/v1/admin/accounts/acct/groups/engineering/members" {
					t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
				}
				if got := r.Header.Get("Content-Type"); got != "application/json" {
					t.Errorf("Content-Type = %q", got)
				}
				if body := readJSONBody(t, r); !reflect.DeepEqual(body, map[string]any{"user_ids": []any{"alice"}}) {
					t.Errorf("body = %#v", body)
				}
				writeOK(t, w, tc.result)
			}))
			defer closeServer()

			result, err := tc.call(client, context.Background(), "acct", "engineering", "alice")
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(result, tc.result) {
				t.Fatalf("result = %#v, want %#v", result, tc.result)
			}
		})
	}
}

func TestSearchContextSendsContextOptionsAndRejectsModeOverride(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/search/search" {
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		body := readJSONBody(t, r)
		if body["mode"] != "context" || body["query"] != "continue refactor" {
			t.Fatalf("body = %#v", body)
		}
		if body["session_id"] != "session-1" || body["purpose"] != "coding" {
			t.Fatalf("context fields = %#v", body)
		}
		if body["search_type"] != "keywords" {
			t.Fatalf("search_type = %#v", body["search_type"])
		}
		if body["max_tokens"] != float64(3000) || body["dedup_turns"] != float64(5) {
			t.Fatalf("budget fields = %#v", body)
		}
		if body["events_time_decay_protection"] != "2d" {
			t.Fatalf("events_time_decay_protection = %#v", body["events_time_decay_protection"])
		}
		writeOK(t, w, map[string]any{
			"rendered": "<memory />",
			"entries":  []any{},
			"stats":    map[string]any{"returned": 0},
		})
	}))
	defer closeServer()

	result, err := client.SearchContext(context.Background(), "continue refactor", &SearchContextOptions{
		SessionID:                 "session-1",
		SearchType:                "keywords",
		Purpose:                   "coding",
		MaxTokens:                 Int(3000),
		DedupTurns:                Int(5),
		EventsTimeDecayProtection: "2d",
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Rendered != "<memory />" {
		t.Fatalf("result = %#v", result)
	}

	if _, err := client.SearchContext(context.Background(), "query", &SearchContextOptions{
		Extra: map[string]any{"mode": "list"},
	}); err == nil || !strings.Contains(err.Error(), "mode") {
		t.Fatalf("expected mode conflict, got %v", err)
	}
}

func TestWriteSendsProcessingModeAndExtra(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body := readJSONBody(t, r)
		if body["processing_mode"] != "vectors_only" || body["future_flag"] != float64(0) || body["wait"] != true || !reflect.DeepEqual(body["tags"], []any{}) || body["tag_mode"] != "replace" || body["use_snapshot"] != true || body["resolve_conflicts"] != true {
			t.Fatalf("body = %#v", body)
		}
		writeOK(t, w, map[string]any{"uri": "viking://resources/a.md"})
	}))
	defer closeServer()

	if _, err := client.Write(context.Background(), "resources/a.md", "", &WriteOptions{
		ProcessingMode:   "vectors_only",
		Tags:             []string{},
		TagMode:          "replace",
		Wait:             true,
		UseSnapshot:      true,
		ResolveConflicts: true,
		Extra:            map[string]any{"future_flag": 0},
	}); err != nil {
		t.Fatal(err)
	}
}

func TestBatchWriteAndDownloadBytes(t *testing.T) {
	var wait *bool
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "POST /api/v1/content/batch-write":
			body := readJSONBody(t, r)
			if wait == nil {
				requireBodyKeysAbsent(t, body, "wait")
			} else if body["wait"] != *wait {
				t.Errorf("wait = %#v, want %v", body["wait"], *wait)
			}
			if body["root_uri"] != "viking://resources/project" || body["future_flag"] != float64(0) || body["processing_mode"] != "vectors_only" || body["defer_processing"] != false || body["skip_conflicts"] != true {
				t.Fatalf("body = %#v", body)
			}
			operations, ok := body["operations"].([]any)
			if !ok || len(operations) != 1 {
				t.Fatalf("operations = %#v", body["operations"])
			}
			operation, ok := operations[0].(map[string]any)
			if !ok || !reflect.DeepEqual(operation, map[string]any{
				"uri":             "viking://resources/project/a.txt",
				"content":         "hello",
				"mode":            "upsert",
				"expected_sha256": strings.Repeat("a", 64),
				"tags":            []any{"team=search"},
				"tag_mode":        "append",
				"acl": map[string]any{
					"acl_mode": "restricted",
					"entries":  []any{map[string]any{"principal": "group:engineering", "level": "write"}},
				},
			}) {
				t.Fatalf("operation = %#v", operations[0])
			}
			writeOK(t, w, map[string]any{"updated": 1})
		case "GET /api/v1/content/download":
			if r.URL.Query().Get("uri") != "viking://resources/project/a.txt" {
				t.Fatalf("query = %s", r.URL.RawQuery)
			}
			_, _ = w.Write([]byte{1, 2, 3})
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.Path)
		}
	}))
	defer closeServer()

	operations := []BatchWriteOperation{
		{
			URI:            "resources/project/a.txt",
			Content:        String("hello"),
			Mode:           "upsert",
			ExpectedSHA256: String(strings.Repeat("a", 64)),
			Tags:           []string{"team=search"},
			TagMode:        "append",
			ACL:            &ACLSpec{ACLMode: "restricted", Entries: []ACLEntry{{Principal: "group:engineering", Level: "write"}}},
		},
	}
	for _, wait = range []*bool{nil, Bool(false), Bool(true)} {
		opts := &BatchWriteOptions{Wait: wait, ProcessingMode: "vectors_only", SkipConflicts: true, Extra: map[string]any{"future_flag": 0}}
		if _, err := client.BatchWrite(context.Background(), "resources/project", operations, opts); err != nil {
			t.Fatal(err)
		}
		opts.Extra = map[string]any{"wait": true}
		if _, err := client.BatchWrite(context.Background(), "resources/project", operations, opts); err == nil || !strings.Contains(err.Error(), `extra cannot override "wait"`) {
			t.Fatalf("expected wait conflict, got %v", err)
		}
	}
	data, err := client.DownloadBytes(context.Background(), "resources/project/a.txt")
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != string([]byte{1, 2, 3}) {
		t.Fatalf("data = %v", data)
	}
}

func TestAddResourcePreservesRemoteSourcesAndOptions(t *testing.T) {
	root := "viking://resources/a"
	var body map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/resources" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		body = readJSONBody(t, r)
		writeOK(t, w, map[string]any{"root_uri": root})
	}))
	defer closeServer()

	ctx := context.Background()
	for _, source := range []string{
		"http://example.com/a.md",
		"https://example.com/a.md",
		"git@example.com:team/docs.git",
		"ssh://git@example.com/team/docs.git",
		"git://example.com/team/docs.git",
	} {
		result, err := client.AddResource(ctx, source, nil)
		if err != nil || result["root_uri"] != root || body["path"] != source {
			t.Fatalf("add %s: result = %#v, body = %#v, err = %v", source, result, body, err)
		}
		requireBodyKeysAbsent(t, body, "temp_file_id", "create_parent", "args", "tag_mode", "tags")
	}

	const source = "https://example.com/manual.pdf"
	for _, test := range []struct {
		source string
		opts   AddResourceOptions
		want   map[string]any
		absent []string
	}{
		{
			source,
			AddResourceOptions{Extra: map[string]any{"future_flag": false}},
			map[string]any{"future_flag": false},
			[]string{"create_parent", "args", "tags", "tag_mode"},
		},
		{
			source,
			AddResourceOptions{CreateParent: Bool(false), ProcessingMode: "vectors_only",
				Args: map[string]any{"parse_mode": "no_split"}, Tags: []string{"team=search"}, TagMode: "append"},
			map[string]any{"create_parent": false, "processing_mode": "vectors_only",
				"args": map[string]any{"parse_mode": "no_split"}, "tags": []any{"team=search"}, "tag_mode": "append"},
			[]string{"parse_mode"},
		},
		{
			source,
			AddResourceOptions{CreateParent: Bool(true), TagMode: "clear"},
			map[string]any{"create_parent": true, "tag_mode": "clear"},
			[]string{"tags"},
		},
		{
			"https://example.com/team/docs.git",
			AddResourceOptions{AddType: "git"},
			map[string]any{"add_type": "git"},
			[]string{"temp_file_id"},
		},
	} {
		if _, err := client.AddResource(ctx, test.source, &test.opts); err != nil {
			t.Fatal(err)
		}
		if body["path"] != test.source {
			t.Fatalf("body = %#v", body)
		}
		for key, want := range test.want {
			if !reflect.DeepEqual(body[key], want) {
				t.Fatalf("%s = %#v, want %#v", key, body[key], want)
			}
		}
		requireBodyKeysAbsent(t, body, test.absent...)
	}
	if _, err := client.AddResource(ctx, source, &AddResourceOptions{
		Extra: map[string]any{"create_parent": true},
	}); err == nil || !strings.Contains(err.Error(), `extra cannot override "create_parent"`) {
		t.Fatalf("expected create_parent conflict, got %v", err)
	}
}

func TestSessionAutoCommitPolicyCanBeDisabled(t *testing.T) {
	var body map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body = readJSONBody(t, r)
		writeOK(t, w, map[string]any{"session_id": "session-1"})
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.CreateSession(ctx, nil); err != nil {
		t.Fatal(err)
	}
	requireBodyKeysAbsent(t, body, "auto_commit_policy")

	if _, err := client.CreateSession(ctx, &CreateSessionOptions{
		DisableAutoCommit: true,
		AutoCommitPolicy:  map[string]any{"message_count_threshold": 25},
	}); err != nil {
		t.Fatal(err)
	}
	if value, ok := body["auto_commit_policy"]; !ok || value != nil {
		t.Fatalf("disabled auto-commit policy = %#v, present = %t", value, ok)
	}

	if _, err := client.CreateSession(ctx, &CreateSessionOptions{
		Extra: map[string]any{"auto_commit_policy": nil},
	}); err == nil {
		t.Fatal("extra must not override the auto-commit policy")
	}
}

func TestErrorEnvelopePreservesCodeDetailsAndStatus(t *testing.T) {
	for _, tc := range []struct {
		name, body, code, message string
		status                    int
		details                   map[string]any
	}{
		{
			name:    "missing resource",
			body:    `{"status":"error","error":{"code":"NOT_FOUND","message":"missing","details":{"resource":"viking://resources/missing"}}}`,
			code:    "NOT_FOUND",
			message: "missing",
			status:  http.StatusNotFound,
			details: map[string]any{"resource": "viking://resources/missing"},
		},
		{
			name:    "domain error with HTTP success",
			body:    `{"status":"error","error":{"code":"PROCESSING_ERROR","message":"operation failed"}}`,
			code:    "PROCESSING_ERROR",
			message: "operation failed",
			status:  http.StatusOK,
		},
		{
			name:    "proxy response",
			body:    "gateway unavailable",
			code:    "UNKNOWN",
			message: "HTTP 502: gateway unavailable",
			status:  http.StatusBadGateway,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer closeServer()

			_, err := client.Read(context.Background(), "resources/missing", 0, -1)
			var apiErr *Error
			if !errors.As(err, &apiErr) || !IsCode(err, tc.code) || apiErr.StatusCode != tc.status ||
				apiErr.Message != tc.message || !reflect.DeepEqual(apiErr.Details, tc.details) {
				t.Fatalf("Read error = %#v (%v), want %s HTTP %d: %s", apiErr, err, tc.code, tc.status, tc.message)
			}
		})
	}
}

func TestAddResourceUploadsLocalFile(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "note.md")
	if err := os.WriteFile(filePath, []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	for _, tt := range []struct {
		name string
		opts *AddResourceOptions
	}{
		{name: "default"},
		{name: "explicit empty args", opts: &AddResourceOptions{Args: map[string]any{}}},
		{name: "explicit args", opts: &AddResourceOptions{Args: map[string]any{"parse_mode": "no_split"}}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/v1/resources/temp_upload":
					if err := r.ParseMultipartForm(1 << 20); err != nil {
						t.Fatal(err)
					}
					if got := r.FormValue("upload_mode"); got != "shared" {
						t.Fatalf("upload_mode = %q", got)
					}
					file, header, err := r.FormFile("file")
					if err != nil {
						t.Fatal(err)
					}
					defer file.Close()
					if header.Filename != "note.md" {
						t.Fatalf("filename = %q", header.Filename)
					}
					content, err := io.ReadAll(file)
					if err != nil {
						t.Fatal(err)
					}
					if string(content) != "hello" {
						t.Fatalf("content = %q", string(content))
					}
					writeOK(t, w, map[string]any{"temp_file_id": "tmp-file"})
				case "/api/v1/resources":
					body := readJSONBody(t, r)
					if body["temp_file_id"] != "tmp-file" {
						t.Fatalf("temp_file_id = %#v", body["temp_file_id"])
					}
					if body["source_name"] != "note.md" {
						t.Fatalf("source_name = %#v", body["source_name"])
					}
					if body["directly_upload_media"] != true {
						t.Fatalf("directly_upload_media = %#v", body["directly_upload_media"])
					}
					// args must be omitted when the caller does not pass any, so the
					// request is accepted by pre-#2549 instances whose resources route
					// uses model_config=ConfigDict(extra="forbid").
					requireBodyKeysAbsent(t, body, "parse_mode", "path")
					if tt.opts == nil || len(tt.opts.Args) == 0 {
						requireBodyKeysAbsent(t, body, "args")
					} else if !reflect.DeepEqual(body["args"], tt.opts.Args) {
						t.Fatalf("args = %#v, want %#v", body["args"], tt.opts.Args)
					}
					writeOK(t, w, map[string]any{"uri": "viking://resources/note.md"})
				default:
					t.Fatalf("unexpected path %s", r.URL.Path)
				}
			}))
			defer closeServer()

			result, err := client.AddResource(context.Background(), filePath, tt.opts)
			if err != nil {
				t.Fatal(err)
			}
			if result["uri"] != "viking://resources/note.md" {
				t.Fatalf("result = %#v", result)
			}
		})
	}
}

func TestAddSkillUploadsDirectoryZip(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte("# Skill"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(dir, "references"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "references", "a.md"), []byte("ref"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"..notes.md", "..references/a.md"} {
		path := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("double-dot reference"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/resources/temp_upload":
			reader, err := r.MultipartReader()
			if err != nil {
				t.Fatal(err)
			}
			var zipBytes []byte
			for {
				part, err := reader.NextPart()
				if errors.Is(err, io.EOF) {
					break
				}
				if err != nil {
					t.Fatal(err)
				}
				if part.FormName() == "file" {
					zipBytes, err = io.ReadAll(part)
					if err != nil {
						t.Fatal(err)
					}
				}
			}
			names := zipEntryNames(t, zipBytes)
			for _, name := range []string{"SKILL.md", "references/a.md", "..notes.md", "..references/a.md"} {
				if !contains(names, name) {
					t.Fatalf("zip names = %#v, missing %q", names, name)
				}
			}
			writeOK(t, w, map[string]any{"temp_file_id": "skill-upload"})
		case "/api/v1/skills":
			body := readJSONBody(t, r)
			if body["temp_file_id"] != "skill-upload" {
				t.Fatalf("temp_file_id = %#v", body["temp_file_id"])
			}
			if _, ok := body["data"]; ok {
				t.Fatalf("unexpected data field: %#v", body)
			}
			writeOK(t, w, map[string]any{"uri": "viking://user/skills/demo"})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer closeServer()

	if _, err := client.AddSkill(context.Background(), dir, nil); err != nil {
		t.Fatal(err)
	}
}

func TestSkillInputsPreserveInlineDataAndLocalUploads(t *testing.T) {
	content := "---\nname: demo\ndescription: Inline skill\n---\n" + strings.Repeat("Instructions.\n", 400)
	skillDir := t.TempDir()
	skillFile := filepath.Join(skillDir, "SKILL.md")
	if err := os.WriteFile(skillFile, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, method := range []string{http.MethodPost, http.MethodPut} {
		for kind, source := range map[string]string{"inline": content, "file": skillFile, "directory": skillDir} {
			t.Run(method+"/"+kind, func(t *testing.T) {
				requests := 0
				client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					if r.URL.Path == "/api/v1/resources/temp_upload" {
						writeOK(t, w, map[string]any{"temp_file_id": "skill-upload"})
						return
					}
					path := "/api/v1/skills"
					if method == http.MethodPut {
						path += "/demo"
					}
					if r.Method != method || r.URL.Path != path {
						t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
					}
					body := readJSONBody(t, r)
					if kind == "inline" {
						if body["data"] != content {
							t.Fatal("inline skill content changed")
						}
						requireBodyKeysAbsent(t, body, "temp_file_id")
					} else {
						if body["temp_file_id"] != "skill-upload" {
							t.Fatalf("missing upload ID: %#v", body)
						}
						requireBodyKeysAbsent(t, body, "data")
					}
					writeOK(t, w, map[string]any{"name": "demo"})
				}))
				defer closeServer()
				var result map[string]any
				var err error
				if method == http.MethodPost {
					result, err = client.AddSkill(context.Background(), source, nil)
				} else {
					result, err = client.UpdateSkill(context.Background(), "demo", source, nil)
				}
				if err != nil || result["name"] != "demo" {
					t.Fatalf("result = %#v, error = %v", result, err)
				}
				wantRequests := 2
				if kind == "inline" {
					wantRequests = 1
				}
				if requests != wantRequests {
					t.Fatalf("requests = %d, want %d", requests, wantRequests)
				}
			})
		}
	}
}

func TestDeleteSkillSupportsScopedAndLegacyCalls(t *testing.T) {
	var uri string
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "DELETE /api/v1/skills/demo" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		uri = r.URL.Query().Get("target_uri")
		writeOK(t, w, map[string]any{"deleted": true})
	}))
	defer closeServer()

	ctx := context.Background()
	// Existing callers omit the variadic scope. An explicit scope selects the
	// intended skill when the same name exists under more than one root.
	if _, err := client.DeleteSkill(ctx, "demo"); err != nil || uri != "" {
		t.Fatalf("default delete scope = %q, err = %v", uri, err)
	}
	const root = "viking://agent/skills"
	if _, err := client.DeleteSkill(ctx, "demo", &DeleteSkillOptions{TargetURI: root}); err != nil || uri != root {
		t.Fatalf("explicit delete scope = %q, err = %v", uri, err)
	}
}

func TestUpdateWatchSelectsTargetAndRejectsAnEmptyPatch(t *testing.T) {
	requests := 0
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Method+" "+r.URL.Path != "PATCH /api/v1/watches" ||
			r.URL.Query().Get("to_uri") != "viking://resources/demo" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		if body := readJSONBody(t, r); !reflect.DeepEqual(body, map[string]any{"is_active": false, "reason": ""}) {
			t.Fatalf("unexpected patch: %#v", body)
		}
		writeOK(t, w, map[string]any{"is_active": false})
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.UpdateWatch(ctx, UpdateWatchOptions{ToURI: "resources/demo"}); err == nil {
		t.Fatal("expected empty patch to be rejected")
	}
	result, err := client.UpdateWatch(ctx, UpdateWatchOptions{
		ToURI: "resources/demo", IsActive: Bool(false), Reason: String(""),
	})
	if err != nil || result["is_active"] != false || requests != 1 {
		t.Fatalf("update = %#v, requests = %d, err = %v", result, requests, err)
	}
}

func zipEntryNames(t *testing.T, content []byte) []string {
	t.Helper()
	reader, err := zip.NewReader(strings.NewReader(string(content)), int64(len(content)))
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(reader.File))
	for _, f := range reader.File {
		names = append(names, f.Name)
	}
	return names
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func TestExportOVPackWritesFile(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/pack/export" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		body := readJSONBody(t, r)
		if body["uri"] != "viking://resources/docs" {
			t.Fatalf("uri = %#v", body["uri"])
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		if _, err := w.Write([]byte("OVPACK")); err != nil {
			t.Fatal(err)
		}
	}))
	defer closeServer()

	directory := t.TempDir()
	existingPath := filepath.Join(directory, "docs.ovpack")
	if err := os.WriteFile(existingPath, []byte("old-backup"), 0o600); err != nil {
		t.Fatal(err)
	}
	outPath, err := client.ExportOVPack(context.Background(), "resources/docs", directory, nil)
	if err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(outPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != "OVPACK" {
		t.Fatalf("content = %q", string(content))
	}
	if matches, err := filepath.Glob(filepath.Join(directory, ".docs.ovpack-*.tmp")); err != nil || len(matches) != 0 {
		t.Fatalf("temporary files = %v, err = %v", matches, err)
	}
}

func TestBackupOVPackDoesNotPublishInterruptedDownload(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Length", "20")
		if _, err := w.Write([]byte("partial")); err != nil {
			t.Fatal(err)
		}
	}))
	defer closeServer()

	for _, existingOutput := range []bool{true, false} {
		t.Run(fmt.Sprintf("existing=%t", existingOutput), func(t *testing.T) {
			directory := t.TempDir()
			outPath := filepath.Join(directory, "backup.ovpack")
			if existingOutput {
				if err := os.WriteFile(outPath, []byte("known-good-backup"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := client.BackupOVPack(context.Background(), outPath, nil); err == nil {
				t.Fatal("expected interrupted download to fail")
			}
			content, err := os.ReadFile(outPath)
			if existingOutput {
				if err != nil {
					t.Fatal(err)
				}
				if string(content) != "known-good-backup" {
					t.Fatalf("content = %q", string(content))
				}
			} else if !os.IsNotExist(err) {
				t.Fatalf("expected no final file, err = %v", err)
			}
			if matches, err := filepath.Glob(filepath.Join(directory, ".backup.ovpack-*.tmp")); err != nil || len(matches) != 0 {
				t.Fatalf("temporary files = %v, err = %v", matches, err)
			}
		})
	}
}

func TestSessionExistsHandlesNotFound(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeAPIError(t, w, http.StatusNotFound, "NOT_FOUND", map[string]any{"type": "session"})
	}))
	defer closeServer()

	exists, err := client.SessionExists(context.Background(), "missing")
	if err != nil {
		t.Fatal(err)
	}
	if exists {
		t.Fatal("expected missing session")
	}
}

func TestHealth(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		if _, err := w.Write([]byte(`{"status":"ok"}`)); err != nil {
			t.Fatal(err)
		}
	}))
	defer closeServer()

	ok, err := client.Health(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("expected healthy")
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	ok, err = client.Health(ctx)
	if ok || !errors.Is(err, context.Canceled) {
		t.Fatalf("Health on a cancelled context = %t, %v", ok, err)
	}

}

func TestSetTagsPreservesDefaultsAndExplicitUpdates(t *testing.T) {
	var body map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/fs/attrs/set_tags" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		body = readJSONBody(t, r)
		writeOK(t, w, map[string]any{"updated": 3})
	}))
	defer closeServer()

	ctx := context.Background()
	const uri = "resources/docs"
	for _, test := range []struct {
		tags []string
		opts *SetTagsOptions
		want map[string]any
	}{
		{nil, nil, map[string]any{
			"uri": "viking://resources/docs", "tags": []any{}, "mode": "replace", "recursive": false,
		}},
		{[]string{"team=infra", "tier=gold"}, &SetTagsOptions{
			Mode: "append", Recursive: true, Telemetry: true, Extra: map[string]any{"future_flag": false},
		}, map[string]any{
			"uri": "viking://resources/docs", "tags": []any{"team=infra", "tier=gold"},
			"mode": "append", "recursive": true, "telemetry": true, "future_flag": false,
		}},
	} {
		result, err := client.SetTags(ctx, uri, test.tags, test.opts)
		if err != nil || result["updated"] != float64(3) {
			t.Fatalf("result = %#v, err = %v", result, err)
		}
		if !reflect.DeepEqual(body, test.want) {
			t.Fatalf("body = %#v, want %#v", body, test.want)
		}
	}
	if _, err := client.SetTags(ctx, uri, nil, &SetTagsOptions{
		Extra: map[string]any{"uri": "viking://other"},
	}); err == nil {
		t.Fatal("extra must not override uri")
	}
}

func TestCommitSessionCanClearEventTagsForThisCommit(t *testing.T) {
	var body map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method+" "+r.URL.Path != "POST /api/v1/sessions/tagged/commit" {
			t.Fatalf("request = %s %s", r.Method, r.URL)
		}
		body = readJSONBody(t, r)
		writeOK(t, w, map[string]any{})
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.CommitSession(ctx, "tagged", nil); err != nil {
		t.Fatal(err)
	}
	requireBodyKeysAbsent(t, body, "extraction_metadata")
	if _, err := client.CommitSession(ctx, "tagged", &CommitSessionOptions{EventTags: []string{}}); err != nil {
		t.Fatal(err)
	}
	clear := map[string]any{"event": map[string]any{"tags": []any{}}}
	if !reflect.DeepEqual(body["extraction_metadata"], clear) {
		t.Fatalf("commit body = %#v", body)
	}
}

func TestPersistentCompileRequests(t *testing.T) {
	compileName := "research-summary"
	args := map[string]any{"owner_user_id": "alice", "model_name": "ignored"}
	namedRequests := 0
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body := readJSONBody(t, r)
		switch r.URL.Path {
		case "/api/v1/compile/create":
			if body["skill"] != "viking://agent/skills/wiki" || body["compile_name"] != compileName {
				t.Fatalf("body = %#v", body)
			}
			writeOK(t, w, map[string]any{"compile_name": compileName})
		case "/api/v1/compile":
			namedRequests++
			expected := map[string]any{"compile_name": compileName}
			if namedRequests == 2 {
				expected["args"] = args
			}
			if !reflect.DeepEqual(body, expected) {
				t.Fatalf("body = %#v", body)
			}
			writeOK(t, w, map[string]any{"task_id": "cmp_1"})
		default:
			t.Fatalf("path = %s", r.URL.Path)
		}
	}))
	defer closeServer()
	input := CompileInput{FromURIs: []string{"viking://resources/source"}, To: "viking://resources/output", Skill: "viking://agent/skills/wiki"}
	if _, err := client.CreateCompileTask(context.Background(), "", input); err == nil {
		t.Fatal("expected required compile_name error")
	}
	saved, err := client.CreateCompileTask(context.Background(), compileName, input)
	if err != nil || saved["compile_name"] != compileName {
		t.Fatalf("saved = %#v, err = %v", saved, err)
	}
	if _, err := client.Compile(context.Background(), CompileRequest{CompileName: compileName}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Compile(context.Background(), CompileRequest{CompileName: compileName, Args: args}); err != nil {
		t.Fatal(err)
	}
	for _, request := range []CompileRequest{{}, {CompileName: compileName, Input: &input}, {Input: &input, Args: args}} {
		if _, err := client.Compile(context.Background(), request); err == nil {
			t.Fatal("expected invalid request error")
		}
	}
}
