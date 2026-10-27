// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// AddMCP registers a Streamable HTTP MCP server and discovers its tools.
func (c *Client) AddMCP(ctx context.Context, name string, serverURL string, headers map[string]string, opts ...*WaitOptions) (map[string]any, error) {
	if headers == nil {
		headers = map[string]string{}
	}
	wait := false
	if len(opts) > 0 && opts[0] != nil {
		wait = opts[0].Wait
	}
	payload := map[string]any{
		"name":    name,
		"url":     serverURL,
		"headers": headers,
		"wait":    wait,
	}
	if len(opts) > 0 && opts[0] != nil {
		setString(payload, "target_uri", NormalizeURI(opts[0].TargetURI))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps", nil, payload, &result)
	return result, err
}

// RemoveMCP removes an MCP server and its tool catalog.
func (c *Client) RemoveMCP(ctx context.Context, name string, targetURI ...string) (map[string]any, error) {
	query := url.Values{}
	if len(targetURI) > 0 {
		setQueryString(query, "target_uri", NormalizeURI(targetURI[0]))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/mcps/"+url.PathEscape(name), query, nil, &result)
	return result, err
}

// ListMCPs lists registered MCP servers.
func (c *Client) ListMCPs(ctx context.Context, targetURI ...string) (map[string]any, error) {
	query := url.Values{}
	if len(targetURI) > 0 {
		setQueryString(query, "target_uri", NormalizeURI(targetURI[0]))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/mcps", query, nil, &result)
	return result, err
}

// FindMCPTools searches the MCP tool catalog semantically.
func (c *Client) FindMCPTools(ctx context.Context, queryText string, limit int, targetURI ...string) (map[string]any, error) {
	if limit == 0 {
		limit = 10
	}
	payload := map[string]any{"query": queryText, "limit": limit}
	if len(targetURI) > 0 {
		setString(payload, "target_uri", NormalizeURI(targetURI[0]))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps/find", nil, payload, &result)
	return result, err
}

// ValidateMCP validates and discovers an MCP server without registering it.
func (c *Client) ValidateMCP(ctx context.Context, name string, serverURL string, headers map[string]string) (map[string]any, error) {
	if headers == nil {
		headers = map[string]string{}
	}
	payload := map[string]any{
		"name":    name,
		"url":     serverURL,
		"headers": headers,
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps/validate", nil, payload, &result)
	return result, err
}

// GetMCP returns a registered MCP server and its current tool catalog.
func (c *Client) GetMCP(ctx context.Context, name string, targetURI ...string) (map[string]any, error) {
	query := url.Values{}
	if len(targetURI) > 0 {
		setQueryString(query, "target_uri", NormalizeURI(targetURI[0]))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/mcps/"+url.PathEscape(name), query, nil, &result)
	return result, err
}

// SyncMCP refreshes an MCP tool catalog and reports affected live context mounts.
func (c *Client) SyncMCP(ctx context.Context, name string, opts ...*WaitOptions) (map[string]any, error) {
	wait := false
	if len(opts) > 0 && opts[0] != nil {
		wait = opts[0].Wait
	}
	payload := map[string]any{"wait": wait}
	if len(opts) > 0 && opts[0] != nil {
		setString(payload, "target_uri", NormalizeURI(opts[0].TargetURI))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps/"+url.PathEscape(name)+"/sync", nil, payload, &result)
	return result, err
}

// GetMCPStatus checks whether an MCP server is reachable.
func (c *Client) GetMCPStatus(ctx context.Context, name string, targetURI ...string) (map[string]any, error) {
	query := url.Values{"name": []string{name}}
	if len(targetURI) > 0 {
		setQueryString(query, "target_uri", NormalizeURI(targetURI[0]))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/mcps/status", query, nil, &result)
	return result, err
}

// InvokeMCPTool invokes a registered MCP tool through OpenViking.
func (c *Client) InvokeMCPTool(ctx context.Context, toolURI string, arguments map[string]any) (map[string]any, error) {
	if arguments == nil {
		arguments = map[string]any{}
	}
	payload := map[string]any{
		"uri":       NormalizeURI(toolURI),
		"arguments": arguments,
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps/invoke", nil, payload, &result)
	return result, err
}

// MountMCPTool creates or replaces a live context node backed by an MCP tool.
func (c *Client) MountMCPTool(ctx context.Context, toolURI string, nodeURI string, title string, opts *MountMCPToolOptions) (map[string]any, error) {
	timeoutSeconds := 60
	arguments := map[string]any{}
	var tags []string
	var description *string
	wait := false
	if opts != nil {
		description = opts.Description
		if opts.TimeoutSeconds != 0 {
			timeoutSeconds = opts.TimeoutSeconds
		}
		if opts.Arguments != nil {
			arguments = opts.Arguments
		}
		tags = opts.Tags
		wait = opts.Wait
	}
	payload := map[string]any{
		"tool_uri":        NormalizeURI(toolURI),
		"node_uri":        NormalizeURI(nodeURI),
		"title":           title,
		"description":     description,
		"timeout_seconds": timeoutSeconds,
		"arguments":       arguments,
		"wait":            wait,
	}
	if tags != nil {
		payload["tags"] = tags
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps/mount", nil, payload, &result)
	return result, err
}

// BatchMountMCPTools creates or replaces multiple MCP-backed live context nodes.
func (c *Client) BatchMountMCPTools(ctx context.Context, mounts []MCPToolMount, opts ...*WaitOptions) (map[string]any, error) {
	items := make([]map[string]any, 0, len(mounts))
	for _, mount := range mounts {
		timeoutSeconds := mount.TimeoutSeconds
		if timeoutSeconds == 0 {
			timeoutSeconds = 60
		}
		arguments := mount.Arguments
		if arguments == nil {
			arguments = map[string]any{}
		}
		item := map[string]any{
			"tool_uri":        NormalizeURI(mount.ToolURI),
			"node_uri":        NormalizeURI(mount.NodeURI),
			"title":           mount.Title,
			"description":     mount.Description,
			"timeout_seconds": timeoutSeconds,
			"arguments":       arguments,
		}
		if mount.Tags != nil {
			item["tags"] = mount.Tags
		}
		items = append(items, item)
	}
	wait := false
	if len(opts) > 0 && opts[0] != nil {
		wait = opts[0].Wait
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/mcps/batch-mount", nil, map[string]any{
		"mounts": items,
		"wait":   wait,
	}, &result)
	return result, err
}

// UnmountMCPTool removes a live context mount and its node.
func (c *Client) UnmountMCPTool(ctx context.Context, nodeURI string, opts ...*WaitOptions) (map[string]any, error) {
	query := url.Values{"node_uri": []string{NormalizeURI(nodeURI)}}
	wait := false
	if len(opts) > 0 && opts[0] != nil {
		wait = opts[0].Wait
	}
	queryBool(query, "wait", wait)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/mcps/mount", query, nil, &result)
	return result, err
}

// BatchUnmountMCPTools removes multiple live context mounts and their nodes.
func (c *Client) BatchUnmountMCPTools(ctx context.Context, nodeURIs []string, opts ...*WaitOptions) (map[string]any, error) {
	normalized := make([]string, len(nodeURIs))
	for index, nodeURI := range nodeURIs {
		normalized[index] = NormalizeURI(nodeURI)
	}
	wait := false
	if len(opts) > 0 && opts[0] != nil {
		wait = opts[0].Wait
	}
	var result map[string]any
	err := c.doJSON(
		ctx,
		http.MethodPost,
		"/api/v1/mcps/batch-unmount",
		nil,
		map[string]any{"node_uris": normalized, "wait": wait},
		&result,
	)
	return result, err
}

// ListMCPMounts lists live context mounts for one MCP server and optional tool.
func (c *Client) ListMCPMounts(ctx context.Context, mcp string, tool string, targetURI ...string) (map[string]any, error) {
	query := url.Values{"mcp": []string{mcp}}
	if tool != "" {
		query.Set("tool", tool)
	}
	if len(targetURI) > 0 {
		setQueryString(query, "target_uri", NormalizeURI(targetURI[0]))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/mcps/mounts", query, nil, &result)
	return result, err
}
