package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// Relations returns the relations recorded for a resource URI.
// Each entry mirrors the server payload, e.g. {"uri": "...", "reason": "..."}.
func (c *Client) Relations(ctx context.Context, uri string) ([]map[string]any, error) {
	var result []map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/relations",
		url.Values{"uri": {NormalizeURI(uri)}}, nil, &result)
	return result, err
}

// Link creates relations from one resource to one or more target resources.
// The server accepts a single target or a list (Union[str, List[str]]); the Go
// client always sends a list, so pass []string{target} to link a single URI.
func (c *Client) Link(ctx context.Context, fromURI string, toURIs []string, reason string) error {
	normalized := make([]string, len(toURIs))
	for i, u := range toURIs {
		normalized[i] = NormalizeURI(u)
	}
	payload := map[string]any{
		"from_uri": NormalizeURI(fromURI),
		"to_uris":  normalized,
		"reason":   reason,
	}
	return c.doJSON(ctx, http.MethodPost, "/api/v1/relations/link", nil, payload, nil)
}

// Unlink removes the relation between two resources.
func (c *Client) Unlink(ctx context.Context, fromURI, toURI string) error {
	payload := map[string]any{
		"from_uri": NormalizeURI(fromURI),
		"to_uri":   NormalizeURI(toURI),
	}
	return c.doJSON(ctx, http.MethodDelete, "/api/v1/relations/link", nil, payload, nil)
}
