package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// List lists directory contents.
func (c *Client) List(ctx context.Context, uri string, opts *ListOptions) ([]any, error) {
	if opts == nil {
		opts = &ListOptions{Output: "original", AbsLimit: 256, NodeLimit: 1000}
	}
	output := opts.Output
	if output == "" {
		output = "original"
	}
	absLimit := opts.AbsLimit
	if absLimit == 0 {
		absLimit = 256
	}
	nodeLimit := opts.NodeLimit
	if nodeLimit == 0 {
		nodeLimit = 1000
	}
	query := url.Values{}
	query.Set("uri", NormalizeURI(uri))
	queryBool(query, "simple", opts.Simple)
	queryBool(query, "recursive", opts.Recursive)
	query.Set("output", output)
	queryInt(query, "abs_limit", absLimit)
	queryBool(query, "show_all_hidden", opts.ShowAllHidden)
	queryInt(query, "node_limit", nodeLimit)
	if opts.SortBy != "" {
		query.Set("sort_by", opts.SortBy)
	}
	if opts.SortOrder != "" {
		query.Set("sort_order", opts.SortOrder)
	}
	var result []any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/fs/ls", query, nil, &result)
	return result, err
}

// Tree returns a directory tree.
func (c *Client) Tree(ctx context.Context, uri string, opts *TreeOptions) ([]map[string]any, error) {
	if opts == nil {
		opts = &TreeOptions{Output: "original", AbsLimit: 128, NodeLimit: 1000}
	}
	output := opts.Output
	if output == "" {
		output = "original"
	}
	absLimit := opts.AbsLimit
	if absLimit == 0 {
		absLimit = 128
	}
	nodeLimit := opts.NodeLimit
	if nodeLimit == 0 {
		nodeLimit = 1000
	}
	query := url.Values{}
	query.Set("uri", NormalizeURI(uri))
	query.Set("output", output)
	queryInt(query, "abs_limit", absLimit)
	queryBool(query, "show_all_hidden", opts.ShowAllHidden)
	queryInt(query, "node_limit", nodeLimit)
	var result []map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/fs/tree", query, nil, &result)
	return result, err
}

// Stat returns metadata for a URI.
func (c *Client) Stat(ctx context.Context, uri string) (map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/fs/stat", query, nil, &result)
	return result, err
}

// Attrs returns logical extended attributes for a URI.
func (c *Client) Attrs(ctx context.Context, uri string) (map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/fs/attrs", query, nil, &result)
	return result, err
}

// Mkdir creates a directory.
func (c *Client) Mkdir(ctx context.Context, uri string, description string) error {
	payload := map[string]any{"uri": NormalizeURI(uri)}
	setString(payload, "description", description)
	return c.doJSON(ctx, http.MethodPost, "/api/v1/fs/mkdir", nil, payload, nil)
}

// Remove deletes a URI.
func (c *Client) Remove(ctx context.Context, uri string, opts *RemoveOptions) error {
	if opts == nil {
		opts = &RemoveOptions{}
	}
	query := url.Values{}
	query.Set("uri", NormalizeURI(uri))
	queryBool(query, "recursive", opts.Recursive)
	queryBool(query, "wait", opts.Wait)
	if opts.Timeout != nil {
		queryFloat(query, "timeout", *opts.Timeout)
	}
	return c.doJSON(ctx, http.MethodDelete, "/api/v1/fs", query, nil, nil)
}

// Move moves a URI to another URI.
func (c *Client) Move(ctx context.Context, fromURI, toURI string) error {
	return c.doJSON(ctx, http.MethodPost, "/api/v1/fs/mv", nil, map[string]any{
		"from_uri": NormalizeURI(fromURI),
		"to_uri":   NormalizeURI(toURI),
	}, nil)
}

// Read reads file content.
func (c *Client) Read(ctx context.Context, uri string, offset int, limit int) (string, error) {
	query := url.Values{}
	query.Set("uri", NormalizeURI(uri))
	queryInt(query, "offset", offset)
	queryInt(query, "limit", limit)
	var result string
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/content/read", query, nil, &result)
	return result, err
}

// Abstract reads L0 abstract content.
func (c *Client) Abstract(ctx context.Context, uri string) (string, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result string
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/content/abstract", query, nil, &result)
	return result, err
}

// Overview reads L1 overview content.
func (c *Client) Overview(ctx context.Context, uri string) (string, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result string
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/content/overview", query, nil, &result)
	return result, err
}

// Write writes text content and refreshes related semantics/vectors.
func (c *Client) Write(ctx context.Context, uri string, content string, opts *WriteOptions) (map[string]any, error) {
	if opts == nil {
		opts = &WriteOptions{Mode: "replace"}
	}
	mode := opts.Mode
	if mode == "" {
		mode = "replace"
	}
	payload := map[string]any{
		"uri":     NormalizeURI(uri),
		"content": content,
		"mode":    mode,
		"wait":    opts.Wait,
	}
	setFloatPtr(payload, "timeout", opts.Timeout)
	setAny(payload, "telemetry", opts.Telemetry)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/content/write", nil, payload, &result)
	return result, err
}

// SetTags sets explicit k=v retrieval tags metadata for a file or directory.
// Valid modes are "replace" (default) and "append"; Recursive applies the tags
// to every file under a directory URI.
func (c *Client) SetTags(ctx context.Context, uri string, tags []string, opts *SetTagsOptions) (map[string]any, error) {
	if opts == nil {
		opts = &SetTagsOptions{Mode: "replace"}
	}
	mode := opts.Mode
	if mode == "" {
		mode = "replace"
	}
	// The server contract is tags:list[str]; a nil slice would marshal to JSON
	// null and fail validation, so normalize to an empty list. With mode
	// "replace" an empty list clears all tags.
	if tags == nil {
		tags = []string{}
	}
	payload := map[string]any{
		"uri":       NormalizeURI(uri),
		"tags":      tags,
		"mode":      mode,
		"recursive": opts.Recursive,
	}
	setAny(payload, "telemetry", opts.Telemetry)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/fs/attrs/set_tags", nil, payload, &result)
	return result, err
}

// Reindex triggers reindexing for a URI.
func (c *Client) Reindex(ctx context.Context, uri string, opts *ReindexOptions) (map[string]any, error) {
	if opts == nil {
		opts = &ReindexOptions{Mode: "vectors_only", Wait: true}
	}
	mode := opts.Mode
	if mode == "" {
		mode = "vectors_only"
	}
	payload := map[string]any{
		"uri":     NormalizeURI(uri),
		"mode":    mode,
		"wait":    opts.Wait,
		"dry_run": opts.DryRun,
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/content/reindex", nil, payload, &result)
	return result, err
}
