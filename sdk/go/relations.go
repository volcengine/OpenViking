package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// Relations returns the relations associated with a resource.
func (c *Client) Relations(ctx context.Context, uri string) ([]map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result []map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/relations", query, nil, &result)
	return result, err
}

// Link creates relations from one resource to a string or []string target.
func (c *Client) Link(ctx context.Context, fromURI string, toURIs any, reason string) error {
	payload := map[string]any{
		"from_uri": NormalizeURI(fromURI),
		"to_uris":  normalizeTarget(toURIs),
		"reason":   reason,
	}
	return c.doJSON(ctx, http.MethodPost, "/api/v1/relations/link", nil, payload, nil)
}

// Unlink removes a resource relation.
func (c *Client) Unlink(ctx context.Context, fromURI, toURI string) error {
	payload := map[string]any{
		"from_uri": NormalizeURI(fromURI),
		"to_uri":   NormalizeURI(toURI),
	}
	return c.doJSON(ctx, http.MethodDelete, "/api/v1/relations/link", nil, payload, nil)
}
