package openviking

import (
	"context"
	"encoding/json"
	"net/http"
)

// WaitProcessed waits until all queued processing completes.
func (c *Client) WaitProcessed(ctx context.Context, opts *WaitProcessedOptions) (map[string]any, error) {
	payload := map[string]any{}
	if opts != nil && opts.Timeout != nil {
		payload["timeout"] = *opts.Timeout
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/system/wait", nil, payload, &result)
	return result, err
}

// CheckConsistency checks filesystem/vector-index consistency for a URI subtree.
func (c *Client) CheckConsistency(ctx context.Context, uri string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/system/consistency", nil, map[string]any{"uri": NormalizeURI(uri)}, &result)
	return result, err
}

// Health checks whether the server health endpoint reports ok.
func (c *Client) Health(ctx context.Context) (bool, error) {
	req, err := c.newRequest(ctx, http.MethodGet, "/health", nil, nil)
	if err != nil {
		return false, err
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return false, nil
	}
	var payload struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return false, err
	}
	return payload.Status == "ok", nil
}

// QueueStatus returns queue observer status.
func (c *Client) QueueStatus(ctx context.Context) (map[string]any, error) {
	return c.observerStatus(ctx, "/api/v1/observer/queue")
}

// VikingDBStatus returns vector DB observer status.
func (c *Client) VikingDBStatus(ctx context.Context) (map[string]any, error) {
	return c.observerStatus(ctx, "/api/v1/observer/vikingdb")
}

// ModelsStatus returns model observer status.
func (c *Client) ModelsStatus(ctx context.Context) (map[string]any, error) {
	return c.observerStatus(ctx, "/api/v1/observer/models")
}

// GetStatus returns overall observer status.
func (c *Client) GetStatus(ctx context.Context) (map[string]any, error) {
	return c.observerStatus(ctx, "/api/v1/observer/system")
}

func (c *Client) observerStatus(ctx context.Context, path string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, path, nil, nil, &result)
	return result, err
}

// IsHealthy checks the observer system status.
func (c *Client) IsHealthy(ctx context.Context) (bool, error) {
	status, err := c.GetStatus(ctx)
	if err != nil {
		return false, err
	}
	healthy, _ := status["is_healthy"].(bool)
	return healthy, nil
}
