package openviking

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testClient(t *testing.T, handler http.Handler) (*Client, func()) {
	t.Helper()
	server := httptest.NewServer(handler)
	client, err := NewClient(Config{
		BaseURL:     server.URL,
		APIKey:      "key",
		Account:     "acct",
		User:        "alice",
		ActorPeerID: "peer-1",
		Profile:     true,
		UploadMode:  "shared",
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
		if r.URL.Path != "/api/v1/search/find" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s", r.Method)
		}
		if got := r.URL.Query().Get("profile"); got != "1" {
			t.Fatalf("profile query = %q", got)
		}
		if got := r.Header.Get("X-API-Key"); got != "key" {
			t.Fatalf("X-API-Key = %q", got)
		}
		if got := r.Header.Get("X-OpenViking-Account"); got != "acct" {
			t.Fatalf("X-OpenViking-Account = %q", got)
		}
		if got := r.Header.Get("X-OpenViking-User"); got != "alice" {
			t.Fatalf("X-OpenViking-User = %q", got)
		}
		if got := r.Header.Get("X-OpenViking-Actor-Peer"); got != "peer-1" {
			t.Fatalf("X-OpenViking-Actor-Peer = %q", got)
		}
		body := readJSONBody(t, r)
		if got := body["query"]; got != "auth" {
			t.Fatalf("query = %#v", got)
		}
		if got := body["target_uri"]; got != "viking://resources/docs" {
			t.Fatalf("target_uri = %#v", got)
		}
		if got := body["limit"]; got != float64(5) {
			t.Fatalf("limit = %#v", got)
		}
		if got := body["since"]; got != "2026-06-01" {
			t.Fatalf("since = %#v", got)
		}
		if got := body["until"]; got != "2026-06-18" {
			t.Fatalf("until = %#v", got)
		}
		if got := body["time_field"]; got != "created_at" {
			t.Fatalf("time_field = %#v", got)
		}
		levels, ok := body["level"].([]any)
		if !ok || len(levels) != 2 || levels[0] != float64(0) || levels[1] != float64(2) {
			t.Fatalf("level = %#v", body["level"])
		}
		requireBodyKeysAbsent(t, body, "agent_id", "agent_uri")
		writeOK(t, w, map[string]any{
			"resources": []map[string]any{
				{"uri": "viking://resources/docs/api.md", "context_type": "resource", "score": 0.9},
			},
		})
	}))
	defer closeServer()

	result, err := client.Find(context.Background(), "auth", &FindOptions{
		TargetURI:   "resources/docs",
		Limit:       5,
		ContextType: []string{"resource"},
		Since:       "2026-06-01",
		Until:       "2026-06-18",
		TimeField:   "created_at",
		Level:       []int{0, 2},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Resources) != 1 || result.Resources[0].URI != "viking://resources/docs/api.md" {
		t.Fatalf("unexpected result: %#v", result)
	}
}

func TestFindOmitsSearchFiltersWhenUnset(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search/find" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		body := readJSONBody(t, r)
		requireBodyKeysAbsent(t, body, "since", "until", "time_field", "level", "agent_id", "agent_uri")
		writeOK(t, w, map[string]any{"resources": []any{}})
	}))
	defer closeServer()

	if _, err := client.Find(context.Background(), "auth", &FindOptions{}); err != nil {
		t.Fatal(err)
	}
}

func TestAdminCreatePathsAcceptInitialUserConfig(t *testing.T) {
	var seen []map[string]any
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/admin/accounts" && r.URL.Path != "/api/v1/admin/accounts/acct/users" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s", r.Method)
		}
		body := readJSONBody(t, r)
		seen = append(seen, body)
		writeOK(t, w, body)
	}))
	defer closeServer()

	userConfig := map[string]any{
		"add_targets": map[string]any{"resource_uri": "viking://user/resources/project-a"},
	}
	if _, err := client.AdminCreateAccountWithOptions(context.Background(), "acct", "admin", &AdminCreateAccountOptions{
		UserConfig: userConfig,
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.AdminRegisterUserWithOptions(context.Background(), "acct", "alice", "admin", &AdminRegisterUserOptions{
		UserConfig: userConfig,
	}); err != nil {
		t.Fatal(err)
	}
	if got := seen[0]["user_config"].(map[string]any)["add_targets"].(map[string]any)["resource_uri"]; got != "viking://user/resources/project-a" {
		t.Fatalf("user_config resource_uri = %#v", got)
	}
	if got := seen[1]["user_config"].(map[string]any)["add_targets"].(map[string]any)["resource_uri"]; got != "viking://user/resources/project-a" {
		t.Fatalf("user_config resource_uri = %#v", got)
	}
}

func TestSearchSendsSessionAndSearchFilters(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search/search" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s", r.Method)
		}
		body := readJSONBody(t, r)
		if got := body["query"]; got != "auth" {
			t.Fatalf("query = %#v", got)
		}
		if got := body["session_id"]; got != "session-1" {
			t.Fatalf("session_id = %#v", got)
		}
		if got := body["target_uri"]; got != "viking://resources/docs" {
			t.Fatalf("target_uri = %#v", got)
		}
		if got := body["since"]; got != "1d" {
			t.Fatalf("since = %#v", got)
		}
		if got := body["until"]; got != "2026-06-18" {
			t.Fatalf("until = %#v", got)
		}
		if got := body["time_field"]; got != "updated_at" {
			t.Fatalf("time_field = %#v", got)
		}
		levels, ok := body["level"].([]any)
		if !ok || len(levels) != 1 || levels[0] != float64(2) {
			t.Fatalf("level = %#v", body["level"])
		}
		requireBodyKeysAbsent(t, body, "agent_id", "agent_uri")
		writeOK(t, w, map[string]any{"resources": []any{}})
	}))
	defer closeServer()

	if _, err := client.Search(context.Background(), "auth", &SearchOptions{
		TargetURI: "resources/docs",
		SessionID: "session-1",
		Since:     "1d",
		Until:     "2026-06-18",
		TimeField: "updated_at",
		Level:     []int{2},
	}); err != nil {
		t.Fatal(err)
	}
}

func TestSearchOmitsSearchFiltersWhenUnset(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search/search" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		body := readJSONBody(t, r)
		requireBodyKeysAbsent(t, body, "since", "until", "time_field", "level", "agent_id", "agent_uri")
		writeOK(t, w, map[string]any{"resources": []any{}})
	}))
	defer closeServer()

	if _, err := client.Search(context.Background(), "auth", &SearchOptions{}); err != nil {
		t.Fatal(err)
	}
}

func TestErrorEnvelopePreservesCodeDetailsAndStatus(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeAPIError(t, w, http.StatusNotFound, "NOT_FOUND", map[string]any{"resource": "viking://resources/missing"})
	}))
	defer closeServer()

	_, err := client.Read(context.Background(), "resources/missing", 0, -1)
	if err == nil {
		t.Fatal("expected error")
	}
	var apiErr *Error
	if !errors.As(err, &apiErr) {
		t.Fatalf("expected *Error, got %T", err)
	}
	if apiErr.Code != "NOT_FOUND" || apiErr.StatusCode != http.StatusNotFound {
		t.Fatalf("unexpected error: %#v", apiErr)
	}
	if apiErr.Details["resource"] != "viking://resources/missing" {
		t.Fatalf("details = %#v", apiErr.Details)
	}
}

func TestAddResourceUploadsLocalFile(t *testing.T) {
	dir := t.TempDir()
	filePath := filepath.Join(dir, "note.md")
	if err := os.WriteFile(filePath, []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

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
			writeOK(t, w, map[string]any{"uri": "viking://resources/note.md"})
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer closeServer()

	result, err := client.AddResource(context.Background(), filePath, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result["uri"] != "viking://resources/note.md" {
		t.Fatalf("result = %#v", result)
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
			if !contains(names, "SKILL.md") || !contains(names, "references/a.md") {
				t.Fatalf("zip names = %#v", names)
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

func TestSkillManagementRequests(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "GET /api/v1/skills":
			if got := r.URL.Query().Get("node_limit"); got != "77" {
				t.Fatalf("node_limit = %q", got)
			}
			writeOK(t, w, map[string]any{"total": 1})
		case "POST /api/v1/skills/find":
			body := readJSONBody(t, r)
			if body["query"] != "browser automation" || body["limit"] != float64(3) {
				t.Fatalf("find body = %#v", body)
			}
			levels, ok := body["level"].([]any)
			if !ok || len(levels) != 2 || levels[0] != float64(0) || levels[1] != float64(1) {
				t.Fatalf("level = %#v", body["level"])
			}
			writeOK(t, w, map[string]any{"skills": []any{}})
		case "POST /api/v1/skills/validate":
			body := readJSONBody(t, r)
			if body["strict"] != true || body["source_path"] != "SKILL.md" {
				t.Fatalf("validate body = %#v", body)
			}
			writeOK(t, w, map[string]any{"valid": true})
		case "GET /api/v1/skills/demo":
			query := r.URL.Query()
			if query.Get("include_content") != "true" ||
				query.Get("include_files") != "false" ||
				query.Get("include_source") != "true" ||
				query.Get("level") != "1" {
				t.Fatalf("get skill query = %s", r.URL.RawQuery)
			}
			writeOK(t, w, map[string]any{"name": "demo"})
		case "PUT /api/v1/skills/demo":
			body := readJSONBody(t, r)
			if body["wait"] != true {
				t.Fatalf("wait = %#v", body["wait"])
			}
			if _, ok := body["data"].(map[string]any); !ok {
				t.Fatalf("data = %#v", body["data"])
			}
			if _, ok := body["source_metadata"].(map[string]any); !ok {
				t.Fatalf("source_metadata = %#v", body["source_metadata"])
			}
			writeOK(t, w, map[string]any{"updated": true})
		case "DELETE /api/v1/skills/demo":
			writeOK(t, w, map[string]any{"deleted": true})
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.String())
		}
	}))
	defer closeServer()

	if _, err := client.ListSkills(context.Background(), &ListSkillsOptions{NodeLimit: 77}); err != nil {
		t.Fatal(err)
	}
	threshold := 0.4
	if _, err := client.FindSkills(context.Background(), "browser automation", &FindSkillsOptions{
		Limit:          3,
		ScoreThreshold: &threshold,
		Level:          []int{0, 1},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.ValidateSkill(context.Background(), map[string]any{"name": "demo"}, &ValidateSkillOptions{
		Strict:     true,
		SourcePath: "SKILL.md",
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.GetSkill(context.Background(), "demo", &GetSkillOptions{
		IncludeContent: Bool(true),
		IncludeFiles:   Bool(false),
		IncludeSource:  true,
		Level:          Int(1),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.UpdateSkill(context.Background(), "demo", map[string]any{"name": "demo"}, &UpdateSkillOptions{
		Wait:           true,
		SourceMetadata: map[string]any{"source": "test"},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.DeleteSkill(context.Background(), "demo"); err != nil {
		t.Fatal(err)
	}
}

func TestSkillRequestsScopeTargetURI(t *testing.T) {
	const target = "viking://agent/skills"
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "POST /api/v1/skills":
			body := readJSONBody(t, r)
			if body["target_uri"] != target {
				t.Fatalf("add target_uri = %#v", body["target_uri"])
			}
			writeOK(t, w, map[string]any{"added": true})
		case "GET /api/v1/skills":
			if got := r.URL.Query().Get("target_uri"); got != target {
				t.Fatalf("list target_uri = %q", got)
			}
			writeOK(t, w, map[string]any{"total": 0})
		case "POST /api/v1/skills/find":
			body := readJSONBody(t, r)
			if body["target_uri"] != target {
				t.Fatalf("find target_uri = %#v", body["target_uri"])
			}
			writeOK(t, w, map[string]any{"skills": []any{}})
		case "POST /api/v1/skills/validate":
			body := readJSONBody(t, r)
			if body["target_uri"] != target {
				t.Fatalf("validate target_uri = %#v", body["target_uri"])
			}
			writeOK(t, w, map[string]any{"valid": true})
		case "GET /api/v1/skills/demo":
			if got := r.URL.Query().Get("target_uri"); got != target {
				t.Fatalf("get target_uri = %q", got)
			}
			writeOK(t, w, map[string]any{"name": "demo"})
		case "PUT /api/v1/skills/demo":
			body := readJSONBody(t, r)
			if body["target_uri"] != target {
				t.Fatalf("update target_uri = %#v", body["target_uri"])
			}
			writeOK(t, w, map[string]any{"updated": true})
		case "DELETE /api/v1/skills/demo":
			if got := r.URL.Query().Get("target_uri"); got != target {
				t.Fatalf("delete target_uri = %q", got)
			}
			writeOK(t, w, map[string]any{"deleted": true})
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.String())
		}
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.AddSkill(ctx, map[string]any{"name": "demo"}, &AddSkillOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.ListSkills(ctx, &ListSkillsOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.FindSkills(ctx, "demo", &FindSkillsOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.ValidateSkill(ctx, map[string]any{"name": "demo"}, &ValidateSkillOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.GetSkill(ctx, "demo", &GetSkillOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.UpdateSkill(ctx, "demo", map[string]any{"name": "demo"}, &UpdateSkillOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.DeleteSkill(ctx, "demo", &DeleteSkillOptions{TargetURI: target}); err != nil {
		t.Fatal(err)
	}
}

func TestSkillRequestsOmitTargetURIWhenUnset(t *testing.T) {
	assertNoTargetURIQuery := func(t *testing.T, r *http.Request) {
		if r.URL.Query().Has("target_uri") {
			t.Fatalf("unexpected target_uri query on %s %s: %s", r.Method, r.URL.Path, r.URL.RawQuery)
		}
	}
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "POST /api/v1/skills":
			requireBodyKeysAbsent(t, readJSONBody(t, r), "target_uri")
			writeOK(t, w, map[string]any{"added": true})
		case "POST /api/v1/skills/find":
			requireBodyKeysAbsent(t, readJSONBody(t, r), "target_uri")
			writeOK(t, w, map[string]any{"skills": []any{}})
		case "POST /api/v1/skills/validate":
			requireBodyKeysAbsent(t, readJSONBody(t, r), "target_uri")
			writeOK(t, w, map[string]any{"valid": true})
		case "PUT /api/v1/skills/demo":
			requireBodyKeysAbsent(t, readJSONBody(t, r), "target_uri")
			writeOK(t, w, map[string]any{"updated": true})
		case "GET /api/v1/skills":
			assertNoTargetURIQuery(t, r)
			writeOK(t, w, map[string]any{"total": 0})
		case "GET /api/v1/skills/demo":
			assertNoTargetURIQuery(t, r)
			writeOK(t, w, map[string]any{"name": "demo"})
		case "DELETE /api/v1/skills/demo":
			assertNoTargetURIQuery(t, r)
			writeOK(t, w, map[string]any{"deleted": true})
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.String())
		}
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.AddSkill(ctx, map[string]any{"name": "demo"}, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := client.FindSkills(ctx, "demo", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := client.ValidateSkill(ctx, map[string]any{"name": "demo"}, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := client.UpdateSkill(ctx, "demo", map[string]any{"name": "demo"}, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := client.ListSkills(ctx, &ListSkillsOptions{NodeLimit: 5}); err != nil {
		t.Fatal(err)
	}
	// Non-nil opts with a nil TargetURI: exercises GetSkill's opts != nil branch
	// so setQueryAny is actually reached and must still omit target_uri.
	if _, err := client.GetSkill(ctx, "demo", &GetSkillOptions{IncludeSource: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.DeleteSkill(ctx, "demo"); err != nil {
		t.Fatal(err)
	}
}

func TestWatchManagementRequests(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "GET /api/v1/watches":
			query := r.URL.Query()
			if query.Get("active_only") != "true" || query.Get("to_uri") != "viking://resources/guide.md" {
				t.Fatalf("list query = %s", r.URL.RawQuery)
			}
			writeOK(t, w, map[string]any{"total": 1})
		case "GET /api/v1/watches/task-1":
			if got := r.URL.Query().Get("to_uri"); got != "viking://resources/guide.md" {
				t.Fatalf("get to_uri = %q", got)
			}
			writeOK(t, w, map[string]any{"task_id": "task-1"})
		case "PATCH /api/v1/watches/task-1":
			if got := r.URL.Query().Get("to_uri"); got != "viking://resources/guide.md" {
				t.Fatalf("patch to_uri = %q", got)
			}
			body := readJSONBody(t, r)
			if body["watch_interval"] != float64(30) || body["is_active"] != false {
				t.Fatalf("patch body = %#v", body)
			}
			if body["reason"] != "" || body["instruction"] != "refresh docs" {
				t.Fatalf("patch text fields = %#v", body)
			}
			writeOK(t, w, map[string]any{"updated": true})
		case "POST /api/v1/watches/task-1/trigger":
			writeOK(t, w, map[string]any{"triggered": true})
		case "DELETE /api/v1/watches":
			if got := r.URL.Query().Get("to_uri"); got != "viking://resources/guide.md" {
				t.Fatalf("delete to_uri = %q", got)
			}
			writeOK(t, w, map[string]any{"deleted": true})
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.String())
		}
	}))
	defer closeServer()

	ctx := context.Background()
	if _, err := client.ListWatches(ctx, &ListWatchesOptions{
		ActiveOnly: true,
		ToURI:      "resources/guide.md",
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.GetWatch(ctx, "task-1", "resources/guide.md"); err != nil {
		t.Fatal(err)
	}
	if _, err := client.UpdateWatch(ctx, UpdateWatchOptions{
		TaskID:        "task-1",
		ToURI:         "resources/guide.md",
		WatchInterval: Float64(30),
		IsActive:      Bool(false),
		Reason:        String(""),
		Instruction:   String("refresh docs"),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.TriggerWatch(ctx, WatchRef{TaskID: "task-1"}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.DeleteWatch(ctx, WatchRef{ToURI: "resources/guide.md"}); err != nil {
		t.Fatal(err)
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

	outPath, err := client.ExportOVPack(context.Background(), "resources/docs", t.TempDir(), nil)
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

func TestListTasksRequest(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Fatalf("method = %s", r.Method)
		}
		if r.URL.Path != "/api/v1/tasks" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		query := r.URL.Query()
		if query.Get("task_type") != "session_commit" ||
			query.Get("status") != "running" ||
			query.Get("resource_id") != "session-1" ||
			query.Get("limit") != "20" {
			t.Fatalf("query = %s", r.URL.RawQuery)
		}
		writeOK(t, w, []map[string]any{
			{"task_id": "task-1", "status": "running"},
		})
	}))
	defer closeServer()

	tasks, err := client.ListTasks(context.Background(), &ListTasksOptions{
		TaskType:   "session_commit",
		Status:     "running",
		ResourceID: "session-1",
		Limit:      20,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(tasks) != 1 {
		t.Fatalf("tasks = %#v", tasks)
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
}

func TestSetTagsSendsBody(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/content/set_tags" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s", r.Method)
		}
		body := readJSONBody(t, r)
		if got := body["uri"]; got != "viking://resources/docs" {
			t.Fatalf("uri = %#v", got)
		}
		tags, ok := body["tags"].([]any)
		if !ok || len(tags) != 2 || tags[0] != "team=infra" || tags[1] != "tier=gold" {
			t.Fatalf("tags = %#v", body["tags"])
		}
		if got := body["mode"]; got != "append" {
			t.Fatalf("mode = %#v", got)
		}
		if got := body["recursive"]; got != true {
			t.Fatalf("recursive = %#v", got)
		}
		if got := body["telemetry"]; got != true {
			t.Fatalf("telemetry = %#v", got)
		}
		writeOK(t, w, map[string]any{"updated": 3})
	}))
	defer closeServer()

	result, err := client.SetTags(context.Background(), "resources/docs", []string{"team=infra", "tier=gold"}, &SetTagsOptions{
		Mode:      "append",
		Recursive: true,
		Telemetry: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := result["updated"]; got != float64(3) {
		t.Fatalf("updated = %#v", got)
	}
}

func TestSetTagsDefaultsModeAndOmitsTelemetry(t *testing.T) {
	client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/content/set_tags" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		body := readJSONBody(t, r)
		if got := body["mode"]; got != "replace" {
			t.Fatalf("default mode = %#v", got)
		}
		if got := body["recursive"]; got != false {
			t.Fatalf("recursive = %#v", got)
		}
		// nil tags must serialize as an empty JSON array, not null, to satisfy
		// the server's tags:list[str] contract.
		tags, ok := body["tags"].([]any)
		if !ok || len(tags) != 0 {
			t.Fatalf("tags = %#v (want empty array)", body["tags"])
		}
		requireBodyKeysAbsent(t, body, "telemetry")
		writeOK(t, w, map[string]any{"updated": 1})
	}))
	defer closeServer()

	if _, err := client.SetTags(context.Background(), "resources/docs/readme.md", nil, nil); err != nil {
		t.Fatal(err)
	}
}
