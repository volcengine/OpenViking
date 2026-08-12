package openviking

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
)

// CreateSession creates a session.
func (c *Client) CreateSession(ctx context.Context, opts *CreateSessionOptions) (map[string]any, error) {
	if opts == nil {
		opts = &CreateSessionOptions{}
	}
	payload := map[string]any{}
	setString(payload, "session_id", opts.SessionID)
	setAny(payload, "memory_policy", opts.MemoryPolicy)
	if opts.DisableAutoCommit {
		payload["auto_commit_policy"] = nil
	} else {
		setAny(payload, "auto_commit_policy", opts.AutoCommitPolicy)
	}
	setAny(payload, "memory_extraction_config", opts.MemoryExtractionConfig)
	setAny(payload, "telemetry", opts.Telemetry)
	if err := mergeExtra(payload, opts.Extra); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/sessions", nil, payload, &result)
	return result, err
}

// ListSessions lists sessions.
func (c *Client) ListSessions(ctx context.Context) ([]any, error) {
	var result []any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/sessions", nil, nil, &result)
	return result, err
}

// GetSession returns session details.
func (c *Client) GetSession(ctx context.Context, sessionID string, opts *GetSessionOptions) (map[string]any, error) {
	query := url.Values{}
	if opts != nil && opts.AutoCreate {
		query.Set("auto_create", "true")
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/sessions/"+url.PathEscape(sessionID), query, nil, &result)
	return result, err
}

// UpdateSessionConfig updates mutable session memory extraction settings.
func (c *Client) UpdateSessionConfig(ctx context.Context, sessionID string, opts *UpdateSessionConfigOptions) (map[string]any, error) {
	if opts == nil {
		opts = &UpdateSessionConfigOptions{}
	}
	payload := map[string]any{}
	setAny(payload, "memory_extraction_config", opts.MemoryExtractionConfig)
	if opts.AutoCommitPolicy != nil {
		payload["auto_commit_policy"] = *opts.AutoCommitPolicy
	}
	setAny(payload, "telemetry", opts.Telemetry)
	if err := mergeExtra(payload, opts.Extra); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPatch, "/api/v1/sessions/"+url.PathEscape(sessionID)+"/config", nil, payload, &result)
	return result, err
}

// SessionExists reports whether a session exists.
func (c *Client) SessionExists(ctx context.Context, sessionID string) (bool, error) {
	_, err := c.GetSession(ctx, sessionID, nil)
	if err == nil {
		return true, nil
	}
	if IsCode(err, "NOT_FOUND") {
		return false, nil
	}
	return false, err
}

// GetSessionContext returns assembled session context.
func (c *Client) GetSessionContext(ctx context.Context, sessionID string, tokenBudget int) (map[string]any, error) {
	if tokenBudget == 0 {
		tokenBudget = 128000
	}
	query := url.Values{}
	queryInt(query, "token_budget", tokenBudget)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/sessions/"+url.PathEscape(sessionID)+"/context", query, nil, &result)
	return result, err
}

// GetSessionArchive returns one completed archive.
func (c *Client) GetSessionArchive(ctx context.Context, sessionID, archiveID string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/sessions/"+url.PathEscape(sessionID)+"/archives/"+url.PathEscape(archiveID), nil, nil, &result)
	return result, err
}

// DeleteSession deletes a session.
func (c *Client) DeleteSession(ctx context.Context, sessionID string) error {
	return c.doJSON(ctx, http.MethodDelete, "/api/v1/sessions/"+url.PathEscape(sessionID), nil, nil, nil)
}

// AddMessage appends a message to a session.
func (c *Client) AddMessage(ctx context.Context, sessionID string, message Message) (map[string]any, error) {
	payload := map[string]any{"role": message.Role}
	if len(message.Parts) > 0 {
		payload["parts"] = message.Parts
	} else if message.Content != nil {
		payload["content"] = *message.Content
	} else {
		return nil, fmt.Errorf("openviking: AddMessage requires Content or Parts")
	}
	setString(payload, "created_at", message.CreatedAt)
	setString(payload, "peer_id", message.PeerID)
	setString(payload, "turn_id", message.TurnID)
	setString(payload, "message_kind", message.MessageKind)
	if message.SourceMessageIDs != nil {
		payload["source_message_ids"] = message.SourceMessageIDs
	}
	setAny(payload, "telemetry", message.Telemetry)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/sessions/"+url.PathEscape(sessionID)+"/messages", nil, payload, &result)
	return result, err
}

// BatchAddMessages appends multiple messages to a session.
func (c *Client) BatchAddMessages(ctx context.Context, sessionID string, messages []Message, opts *BatchAddMessagesOptions) (map[string]any, error) {
	payload := map[string]any{"messages": messages}
	if opts != nil {
		setAny(payload, "telemetry", opts.Telemetry)
		if err := mergeExtra(payload, opts.Extra); err != nil {
			return nil, err
		}
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/sessions/"+url.PathEscape(sessionID)+"/messages/batch", nil, payload, &result)
	return result, err
}

// CommitSession archives and extracts memories for a session.
func (c *Client) CommitSession(ctx context.Context, sessionID string, opts *CommitSessionOptions) (map[string]any, error) {
	if opts == nil {
		opts = &CommitSessionOptions{}
	}
	payload := map[string]any{}
	if opts.KeepRecentCount != nil {
		payload["keep_recent_count"] = *opts.KeepRecentCount
	}
	setString(payload, "retention_mode", opts.RetentionMode)
	setAny(payload, "keep_recent_turn_count", opts.KeepRecentTurnCount)
	setAny(payload, "retained_message_token_budget", opts.RetainedMessageTokenBudget)
	setAny(payload, "min_raw_tail_steps", opts.MinRawTailSteps)
	setAny(payload, "telemetry", opts.Telemetry)
	if opts.EventTags != nil {
		payload["extraction_metadata"] = map[string]any{
			"event": map[string]any{"tags": opts.EventTags},
		}
	}
	if err := mergeExtra(payload, opts.Extra); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/sessions/"+url.PathEscape(sessionID)+"/commit", nil, payload, &result)
	return result, err
}

// GetTask returns a task or nil when it does not exist.
func (c *Client) GetTask(ctx context.Context, taskID string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/tasks/"+url.PathEscape(taskID), nil, nil, &result)
	if err != nil {
		if IsCode(err, "NOT_FOUND") {
			return nil, nil
		}
		var apiErr *Error
		if errors.As(err, &apiErr) && apiErr.StatusCode == http.StatusNotFound {
			return nil, nil
		}
		return nil, err
	}
	return result, nil
}

// CancelTask requests cooperative cancellation of a background task.
func (c *Client) CancelTask(
	ctx context.Context,
	taskID string,
) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(
		ctx,
		http.MethodPost,
		"/api/v1/tasks/"+url.PathEscape(taskID)+"/cancel",
		nil,
		nil,
		&result,
	)
	return result, err
}

// ListTasks lists background tasks visible to the caller.
func (c *Client) ListTasks(ctx context.Context, opts *ListTasksOptions) ([]any, error) {
	query := url.Values{}
	if opts != nil {
		setQueryString(query, "task_type", opts.TaskType)
		setQueryString(query, "status", opts.Status)
		setQueryString(query, "resource_id", opts.ResourceID)
		if opts.Limit > 0 {
			queryInt(query, "limit", opts.Limit)
		}
	}
	var result []any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/tasks", query, nil, &result)
	return result, err
}
