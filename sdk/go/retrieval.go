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
	if opts.NodeLimit != nil {
		limit = *opts.NodeLimit
	}
	imageURL, err := normalizeImageInput(opts.Image)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{"query": queryText}
	if opts.TargetURI != nil {
		payload["target_uri"] = normalizeTarget(opts.TargetURI)
	}
	payload["limit"] = limit
	setString(payload, "image_url", imageURL)
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	setAny(payload, "filter", opts.Filter)
	setAny(payload, "context_type", opts.ContextType)
	setAny(payload, "include_provenance", opts.IncludeProvenance)
	setAny(payload, "read_content", opts.ReadContent)
	setString(payload, "since", opts.Since)
	setString(payload, "until", opts.Until)
	setString(payload, "time_field", opts.TimeField)
	if opts.Level != nil {
		payload["level"] = opts.Level
	}
	if opts.Tags != nil {
		payload["tags"] = opts.Tags
	}
	setAny(payload, "telemetry", opts.Telemetry)
	if err := mergeExtra(payload, opts.Extra); err != nil {
		return nil, err
	}
	var result FindResult
	err = c.doJSON(ctx, http.MethodPost, "/api/v1/search/find", nil, payload, &result)
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
	if opts.NodeLimit != nil {
		limit = *opts.NodeLimit
	}
	imageURL, err := normalizeImageInput(opts.Image)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{"query": queryText}
	if opts.TargetURI != nil {
		payload["target_uri"] = normalizeTarget(opts.TargetURI)
	}
	payload["limit"] = limit
	setString(payload, "image_url", imageURL)
	setString(payload, "session_id", opts.SessionID)
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	setAny(payload, "filter", opts.Filter)
	setAny(payload, "context_type", opts.ContextType)
	setAny(payload, "include_provenance", opts.IncludeProvenance)
	setAny(payload, "read_content", opts.ReadContent)
	setString(payload, "since", opts.Since)
	setString(payload, "until", opts.Until)
	setString(payload, "time_field", opts.TimeField)
	if opts.Level != nil {
		payload["level"] = opts.Level
	}
	if opts.Tags != nil {
		payload["tags"] = opts.Tags
	}
	setAny(payload, "telemetry", opts.Telemetry)
	if err := mergeExtra(payload, opts.Extra); err != nil {
		return nil, err
	}
	var result FindResult
	err = c.doJSON(ctx, http.MethodPost, "/api/v1/search/search", nil, payload, &result)
	return &result, err
}

// SearchContext assembles injection-ready context on the server.
func (c *Client) SearchContext(ctx context.Context, query string, opts *SearchContextOptions) (*SearchContextResult, error) {
	if opts == nil {
		opts = &SearchContextOptions{}
	}
	payload := map[string]any{
		"query": query,
		"mode":  "context",
	}
	imageURL, err := normalizeImageInput(opts.Image)
	if err != nil {
		return nil, err
	}
	setString(payload, "image_url", imageURL)
	setString(payload, "session_id", opts.SessionID)
	setAny(payload, "limit", opts.Limit)
	setAny(payload, "node_limit", opts.NodeLimit)
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	setAny(payload, "filter", opts.Filter)
	setAny(payload, "context_type", opts.ContextType)
	setAny(payload, "include_provenance", opts.IncludeProvenance)
	if opts.Tags != nil {
		payload["tags"] = opts.Tags
	}
	setString(payload, "since", opts.Since)
	setString(payload, "until", opts.Until)
	setString(payload, "time_field", opts.TimeField)
	setString(payload, "query_expansion", opts.QueryExpansion)
	setAny(payload, "max_tokens", opts.MaxTokens)
	if opts.Quotas != nil {
		payload["quotas"] = opts.Quotas
	}
	setString(payload, "purpose", opts.Purpose)
	setAny(payload, "detail", opts.Detail)
	setAny(payload, "dedup_turns", opts.DedupTurns)
	if opts.ExcludeURIs != nil {
		payload["exclude_uris"] = opts.ExcludeURIs
	}
	setString(payload, "peer_scope", opts.PeerScope)
	setAny(payload, "other_peer_penalty", opts.OtherPeerPenalty)
	setAny(payload, "rewrite", opts.Rewrite)
	setAny(payload, "rewrite_max_bullets", opts.RewriteMaxBullets)
	setAny(payload, "telemetry", opts.Telemetry)
	if err := mergeExtra(payload, opts.Extra); err != nil {
		return nil, err
	}
	var result SearchContextResult
	err = c.doJSON(ctx, http.MethodPost, "/api/v1/search/search", nil, payload, &result)
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
	if opts.LevelLimit != nil {
		payload["level_limit"] = *opts.LevelLimit
	}
	if opts.ExcludeURI != "" {
		payload["exclude_uri"] = NormalizeURI(opts.ExcludeURI)
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/grep", nil, payload, &result)
	return result, err
}

// Glob finds files by glob pattern.
func (c *Client) Glob(ctx context.Context, pattern string, uri string, opts *GlobOptions) (map[string]any, error) {
	if opts == nil {
		opts = &GlobOptions{}
	}
	if uri == "" {
		uri = "viking://"
	}
	payload := map[string]any{
		"pattern": pattern,
		"uri":     NormalizeURI(uri),
	}
	if opts.NodeLimit != nil {
		payload["node_limit"] = *opts.NodeLimit
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/glob", nil, payload, &result)
	return result, err
}
