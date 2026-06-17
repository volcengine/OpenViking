package openviking

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
)

// ListWatches lists watch tasks, or returns one by target URI.
func (c *Client) ListWatches(ctx context.Context, opts *ListWatchesOptions) (map[string]any, error) {
	query := url.Values{}
	activeOnly := false
	if opts != nil {
		activeOnly = opts.ActiveOnly
		if opts.ToURI != "" {
			query.Set("to_uri", NormalizeURI(opts.ToURI))
		}
	}
	queryBool(query, "active_only", activeOnly)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/watches", query, nil, &result)
	return result, err
}

// GetWatch gets one watch task by task ID, optionally cross-checking the target URI.
func (c *Client) GetWatch(ctx context.Context, taskID string, toURI string) (map[string]any, error) {
	query := url.Values{}
	if toURI != "" {
		query.Set("to_uri", NormalizeURI(toURI))
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/watches/"+url.PathEscape(taskID), query, nil, &result)
	return result, err
}

// UpdateWatch partially updates a watch task by task ID or target URI.
func (c *Client) UpdateWatch(ctx context.Context, opts UpdateWatchOptions) (map[string]any, error) {
	if opts.TaskID == "" && opts.ToURI == "" {
		return nil, fmt.Errorf("openviking: UpdateWatch requires TaskID or ToURI")
	}
	payload := map[string]any{}
	setFloatPtr(payload, "watch_interval", opts.WatchInterval)
	if opts.IsActive != nil {
		payload["is_active"] = *opts.IsActive
	}
	if opts.Reason != nil {
		payload["reason"] = *opts.Reason
	}
	if opts.Instruction != nil {
		payload["instruction"] = *opts.Instruction
	}
	if len(payload) == 0 {
		return nil, fmt.Errorf("openviking: UpdateWatch requires at least one field to update")
	}
	path, query := watchPathAndQuery(opts.TaskID, opts.ToURI)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPatch, path, query, payload, &result)
	return result, err
}

// DeleteWatch deletes a watch task by task ID or target URI.
func (c *Client) DeleteWatch(ctx context.Context, ref WatchRef) (map[string]any, error) {
	if ref.TaskID == "" && ref.ToURI == "" {
		return nil, fmt.Errorf("openviking: DeleteWatch requires TaskID or ToURI")
	}
	path, query := watchPathAndQuery(ref.TaskID, ref.ToURI)
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, path, query, nil, &result)
	return result, err
}

// TriggerWatch schedules a watch task for immediate background execution.
func (c *Client) TriggerWatch(ctx context.Context, ref WatchRef) (map[string]any, error) {
	if ref.TaskID == "" && ref.ToURI == "" {
		return nil, fmt.Errorf("openviking: TriggerWatch requires TaskID or ToURI")
	}
	path, query := watchPathAndQuery(ref.TaskID, ref.ToURI)
	if ref.TaskID != "" {
		path += "/trigger"
	} else {
		path = "/api/v1/watches/trigger"
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, path, query, nil, &result)
	return result, err
}

func watchPathAndQuery(taskID string, toURI string) (string, url.Values) {
	query := url.Values{}
	if toURI != "" {
		query.Set("to_uri", NormalizeURI(toURI))
	}
	if taskID != "" {
		return "/api/v1/watches/" + url.PathEscape(taskID), query
	}
	return "/api/v1/watches", query
}
