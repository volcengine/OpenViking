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
	imageURL, err := normalizeImageInput(opts.Image)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{
		"query":      queryText,
		"target_uri": normalizeTarget(opts.TargetURI),
		"limit":      actualLimit,
	}
	setString(payload, "image_url", imageURL)
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	setAny(payload, "filter", opts.Filter)
	setAny(payload, "context_type", opts.ContextType)
	setString(payload, "since", opts.Since)
	setString(payload, "until", opts.Until)
	setString(payload, "time_field", opts.TimeField)
	if len(opts.Level) > 0 {
		payload["level"] = opts.Level
	}
	if len(opts.Tags) > 0 {
		payload["tags"] = opts.Tags
	}
	payload["include_provenance"] = opts.IncludeProvenance
	setAny(payload, "telemetry", opts.Telemetry)
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
	actualLimit := limit
	if opts.NodeLimit != nil {
		actualLimit = *opts.NodeLimit
	}
	imageURL, err := normalizeImageInput(opts.Image)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{
		"query":      queryText,
		"target_uri": normalizeTarget(opts.TargetURI),
		"limit":      actualLimit,
	}
	setString(payload, "image_url", imageURL)
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
	if len(opts.Tags) > 0 {
		payload["tags"] = opts.Tags
	}
	payload["include_provenance"] = opts.IncludeProvenance
	setAny(payload, "telemetry", opts.Telemetry)
	var result FindResult
	err = c.doJSON(ctx, http.MethodPost, "/api/v1/search/search", nil, payload, &result)
	return &result, err
}

// Recall performs type-quota memory recall with bounded rendering.
func (c *Client) Recall(ctx context.Context, queryText string, opts *RecallOptions) (map[string]any, error) {
	if opts == nil {
		opts = &RecallOptions{}
	}
	payload := map[string]any{"query": queryText}
	if opts.Quotas != nil {
		payload["quotas"] = opts.Quotas
	}
	if opts.MaxChars > 0 {
		payload["max_chars"] = opts.MaxChars
	}
	if opts.MinScore != nil {
		payload["min_score"] = *opts.MinScore
	}
	setString(payload, "peer_scope", opts.PeerScope)
	setAny(payload, "other_peer_penalty", opts.OtherPeerPenalty)
	if opts.Render != nil {
		payload["render"] = *opts.Render
	}
	setAny(payload, "telemetry", opts.Telemetry)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/search/recall", nil, payload, &result)
	return result, err
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
