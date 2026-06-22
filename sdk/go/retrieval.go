package openviking

import (
	"context"
	"net/http"
)

// Find performs semantic search without session context.
func (c *Client) Find(ctx context.Context, queryText string, opts *FindOptions) (*FindResult, error) {
	if opts == nil {
		opts = &FindOptions{}
	}
	limit := opts.Limit
	if limit == 0 {
		limit = 10
	}
	actualLimit := limit
	if opts.NodeLimit != nil {
		actualLimit = *opts.NodeLimit
	}
	payload := map[string]any{
		"query":      queryText,
		"target_uri": normalizeTarget(opts.TargetURI),
		"limit":      actualLimit,
	}
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	setAny(payload, "filter", opts.Filter)
	setAny(payload, "context_type", opts.ContextType)
	setString(payload, "since", opts.Since)
	setString(payload, "until", opts.Until)
	setString(payload, "time_field", opts.TimeField)
	if len(opts.Level) > 0 {
		payload["level"] = opts.Level
	}
	setAny(payload, "telemetry", opts.Telemetry)
	var result FindResult
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/find", nil, payload, &result)
	return &result, err
}

// Search performs semantic search with optional session context.
func (c *Client) Search(ctx context.Context, queryText string, opts *SearchOptions) (*FindResult, error) {
	if opts == nil {
		opts = &SearchOptions{}
	}
	limit := opts.Limit
	if limit == 0 {
		limit = 10
	}
	actualLimit := limit
	if opts.NodeLimit != nil {
		actualLimit = *opts.NodeLimit
	}
	payload := map[string]any{
		"query":      queryText,
		"target_uri": normalizeTarget(opts.TargetURI),
		"limit":      actualLimit,
	}
	setString(payload, "session_id", opts.SessionID)
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	setAny(payload, "filter", opts.Filter)
	setAny(payload, "context_type", opts.ContextType)
	setString(payload, "since", opts.Since)
	setString(payload, "until", opts.Until)
	setString(payload, "time_field", opts.TimeField)
	if len(opts.Level) > 0 {
		payload["level"] = opts.Level
	}
	setAny(payload, "telemetry", opts.Telemetry)
	var result FindResult
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/search", nil, payload, &result)
	return &result, err
}

// Grep searches file content by pattern.
func (c *Client) Grep(ctx context.Context, uri, pattern string, opts *GrepOptions) (map[string]any, error) {
	if opts == nil {
		opts = &GrepOptions{}
	}
	payload := map[string]any{
		"uri":              NormalizeURI(uri),
		"pattern":          pattern,
		"case_insensitive": opts.CaseInsensitive,
	}
	if opts.NodeLimit != nil {
		payload["node_limit"] = *opts.NodeLimit
	}
	if opts.ExcludeURI != "" {
		payload["exclude_uri"] = NormalizeURI(opts.ExcludeURI)
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/grep", nil, payload, &result)
	return result, err
}

// Glob finds files by glob pattern.
func (c *Client) Glob(ctx context.Context, pattern string, uri string) (map[string]any, error) {
	if uri == "" {
		uri = "viking://"
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/glob", nil, map[string]any{
		"pattern": pattern,
		"uri":     NormalizeURI(uri),
	}, &result)
	return result, err
}
