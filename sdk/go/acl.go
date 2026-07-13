package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// ACLEntry grants one user a viewer, editor, or manager level.
type ACLEntry struct {
	UserID string `json:"user_id"`
	Level  string `json:"level"`
}

// ACL returns the direct, inherited, and effective ACL for a URI.
func (c *Client) ACL(ctx context.Context, uri string) (map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/acl", query, nil, &result)
	return result, err
}

// SetACL replaces the direct ACL on a URI.
func (c *Client) SetACL(ctx context.Context, uri string, entries []ACLEntry) (map[string]any, error) {
	if entries == nil {
		entries = []ACLEntry{}
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPut, "/api/v1/acl", nil, map[string]any{
		"uri":     NormalizeURI(uri),
		"entries": entries,
	}, &result)
	return result, err
}

// GrantACL sets one user's direct ACL level.
func (c *Client) GrantACL(ctx context.Context, uri, userID, level string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/acl/grant", nil, map[string]any{
		"uri": NormalizeURI(uri), "user_id": userID, "level": level,
	}, &result)
	return result, err
}

// RevokeACL removes one user's direct ACL entry.
func (c *Client) RevokeACL(ctx context.Context, uri, userID string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/acl/revoke", nil, map[string]any{
		"uri": NormalizeURI(uri), "user_id": userID,
	}, &result)
	return result, err
}

// DeleteACL clears the direct ACL on a URI.
func (c *Client) DeleteACL(ctx context.Context, uri string) (map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/acl", query, nil, &result)
	return result, err
}
