package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// AddSkill registers a skill from raw data, a local file, or a local directory.
func (c *Client) AddSkill(ctx context.Context, data any, opts *AddSkillOptions) (map[string]any, error) {
	if opts == nil {
		opts = &AddSkillOptions{}
	}
	payload := map[string]any{
		"wait": opts.Wait,
	}
	setFloatPtr(payload, "timeout", opts.Timeout)
	setAny(payload, "telemetry", opts.Telemetry)
	if err := c.attachSkillData(ctx, payload, data); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/skills", nil, payload, &result)
	return result, err
}

// ListSkills lists installed agent skills.
func (c *Client) ListSkills(ctx context.Context, opts *ListSkillsOptions) (map[string]any, error) {
	nodeLimit := 1000
	if opts != nil && opts.NodeLimit != 0 {
		nodeLimit = opts.NodeLimit
	}
	query := url.Values{}
	queryInt(query, "node_limit", nodeLimit)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/skills", query, nil, &result)
	return result, err
}

// FindSkills finds installed agent skills by semantic search.
func (c *Client) FindSkills(ctx context.Context, queryText string, opts *FindSkillsOptions) (map[string]any, error) {
	if opts == nil {
		opts = &FindSkillsOptions{}
	}
	limit := opts.Limit
	if limit == 0 {
		limit = 10
	}
	payload := map[string]any{
		"query": queryText,
		"limit": limit,
	}
	setAny(payload, "score_threshold", opts.ScoreThreshold)
	if len(opts.Level) > 0 {
		payload["level"] = opts.Level
	}
	setAny(payload, "telemetry", opts.Telemetry)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/skills/find", nil, payload, &result)
	return result, err
}

// ValidateSkill validates a skill payload without installing it.
func (c *Client) ValidateSkill(ctx context.Context, data any, opts *ValidateSkillOptions) (map[string]any, error) {
	payload := map[string]any{"data": data}
	if opts != nil {
		payload["strict"] = opts.Strict
		setString(payload, "source_path", opts.SourcePath)
		setString(payload, "skill_dir_name", opts.SkillDirName)
	} else {
		payload["strict"] = false
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/skills/validate", nil, payload, &result)
	return result, err
}

// GetSkill returns one installed agent skill.
func (c *Client) GetSkill(ctx context.Context, skillName string, opts *GetSkillOptions) (map[string]any, error) {
	query := url.Values{}
	includeFiles := true
	if opts != nil {
		if opts.IncludeFiles != nil {
			includeFiles = *opts.IncludeFiles
		}
		if opts.IncludeContent != nil {
			queryBool(query, "include_content", *opts.IncludeContent)
		}
		queryBool(query, "include_source", opts.IncludeSource)
		if opts.Level != nil {
			queryInt(query, "level", *opts.Level)
		}
	} else {
		queryBool(query, "include_source", false)
	}
	queryBool(query, "include_files", includeFiles)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/skills/"+url.PathEscape(skillName), query, nil, &result)
	return result, err
}

// UpdateSkill replaces an installed agent skill.
func (c *Client) UpdateSkill(ctx context.Context, skillName string, data any, opts *UpdateSkillOptions) (map[string]any, error) {
	if opts == nil {
		opts = &UpdateSkillOptions{}
	}
	payload := map[string]any{
		"wait": opts.Wait,
	}
	setFloatPtr(payload, "timeout", opts.Timeout)
	setAny(payload, "source_metadata", opts.SourceMetadata)
	setAny(payload, "telemetry", opts.Telemetry)
	if err := c.attachSkillData(ctx, payload, data); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPut, "/api/v1/skills/"+url.PathEscape(skillName), nil, payload, &result)
	return result, err
}

// DeleteSkill removes an installed agent skill.
func (c *Client) DeleteSkill(ctx context.Context, skillName string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/skills/"+url.PathEscape(skillName), nil, nil, &result)
	return result, err
}

func (c *Client) attachSkillData(ctx context.Context, payload map[string]any, data any) error {
	path, ok := data.(string)
	if !ok {
		payload["data"] = data
		return nil
	}
	if err := c.addLocalUpload(ctx, payload, path, false); err != nil {
		return err
	}
	if _, ok := payload["path"]; ok {
		delete(payload, "path")
		payload["data"] = path
	}
	return nil
}
