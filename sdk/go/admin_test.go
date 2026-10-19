// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

package openviking

import (
	"context"
	"net/http"
	"testing"
)

func TestAdminDeleteGroupForceIsOptIn(t *testing.T) {
	for _, tc := range []struct {
		name    string
		options *AdminDeleteGroupOptions
		force   string
	}{
		{name: "existing method"},
		{name: "nil options"},
		{name: "force false", options: &AdminDeleteGroupOptions{}},
		{name: "force true", options: &AdminDeleteGroupOptions{Force: true}, force: "true"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client, closeServer := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodDelete {
					t.Errorf("method = %s", r.Method)
				}
				if r.URL.Path != "/api/v1/admin/accounts/acme/groups/engineering" {
					t.Errorf("path = %s", r.URL.Path)
				}
				if got := r.URL.Query().Get("force"); got != tc.force {
					t.Errorf("force query = %q, want %q", got, tc.force)
				}
				if tc.force == "" && r.URL.Query().Has("force") {
					t.Error("force should be omitted by default")
				}
				writeOK(t, w, map[string]any{"deleted": true})
			}))
			defer closeServer()

			var result map[string]any
			var err error
			if tc.name == "existing method" {
				result, err = client.AdminDeleteGroup(context.Background(), "acme", "engineering")
			} else {
				result, err = client.AdminDeleteGroupWithOptions(context.Background(), "acme", "engineering", tc.options)
			}
			if err != nil {
				t.Fatal(err)
			}
			if result["deleted"] != true || len(result) != 1 {
				t.Fatalf("result = %#v", result)
			}
		})
	}
}
