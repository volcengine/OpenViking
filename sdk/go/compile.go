// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

package openviking

import (
	"context"
	"fmt"
	"net/http"
)

// CreateCompileTask saves a persistent configuration without starting a run.
// The returned map contains compile_id for subsequent Compile requests.
func (c *Client) CreateCompileTask(ctx context.Context, input CompileInput) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/compile/create", nil, input, &result)
	return result, err
}

// Compile queues an ID-only persistent execution or a full independent execution.
// Exactly one of request.CompileID and request.Input must be provided.
func (c *Client) Compile(ctx context.Context, request CompileRequest) (map[string]any, error) {
	if (request.CompileID != "") == (request.Input != nil) {
		return nil, fmt.Errorf("provide exactly one of CompileID and Input")
	}
	var payload any = request.Input
	if request.CompileID != "" {
		payload = map[string]string{"compile_id": request.CompileID}
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/compile", nil, payload, &result)
	return result, err
}
