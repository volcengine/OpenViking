// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

package openviking

import (
	"context"
	"fmt"
	"net/http"
)

// CreateCompileTask saves a persistent configuration without starting a run.
// compileName is required and returned unchanged as compile_name for subsequent runs.
func (c *Client) CreateCompileTask(ctx context.Context, compileName string, input CompileInput) (map[string]any, error) {
	if compileName == "" {
		return nil, fmt.Errorf("compile_name is required")
	}
	payload := struct {
		CompileName string `json:"compile_name"`
		CompileInput
	}{compileName, input}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/compile/create", nil, payload, &result)
	return result, err
}

// Compile queues a named persistent execution or a full independent execution.
// Exactly one of request.CompileName and request.Input must be provided.
func (c *Client) Compile(ctx context.Context, request CompileRequest) (map[string]any, error) {
	if (request.CompileName != "") == (request.Input != nil) {
		return nil, fmt.Errorf("provide exactly one of CompileName and Input")
	}
	var payload any = request.Input
	if request.CompileName != "" {
		payload = map[string]string{"compile_name": request.CompileName}
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/compile", nil, payload, &result)
	return result, err
}
