package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// ACLEntry grants one user or group a read, write, or manage level.
type ACLEntry struct {
	Principal string `json:"principal"`
	Level     string `json:"level"`
}

// SetACLOptions controls optional ACL properties updated together with direct entries.
type SetACLOptions struct {
	ACLMode string
}

// ACL returns the direct, inherited, and effective ACL for a URI.
func (c *Client) ACL(ctx context.Context, uri string) (map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/acl", query, nil, &result)
	return result, err
}

// SetACL replaces the direct ACL on a URI and can update restricted mode atomically.
func (c *Client) SetACL(ctx context.Context, uri string, entries []ACLEntry, options ...SetACLOptions) (map[string]any, error) {
	if entries == nil {
		entries = []ACLEntry{}
	}
	body := map[string]any{
		"uri":     NormalizeURI(uri),
		"entries": entries,
	}
	if len(options) > 0 && options[0].ACLMode != "" {
		body["acl_mode"] = options[0].ACLMode
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPut, "/api/v1/acl", nil, body, &result)
	return result, err
}

// SetACLMode switches between inherit and restricted without changing direct grants.
func (c *Client) SetACLMode(ctx context.Context, uri, aclMode string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPut, "/api/v1/acl", nil, map[string]any{
		"uri": NormalizeURI(uri), "acl_mode": aclMode,
	}, &result)
	return result, err
}

// GrantACL sets one principal's direct ACL level.
func (c *Client) GrantACL(ctx context.Context, uri, principal, level string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/acl/grant", nil, map[string]any{
		"uri": NormalizeURI(uri), "principal": principal, "level": level,
	}, &result)
	return result, err
}

// RevokeACL removes one principal's direct ACL entry.
func (c *Client) RevokeACL(ctx context.Context, uri, principal string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/acl/revoke", nil, map[string]any{
		"uri": NormalizeURI(uri), "principal": principal,
	}, &result)
	return result, err
}

// DeleteACL clears the direct ACL and restricted mode on a URI.
func (c *Client) DeleteACL(ctx context.Context, uri string) (map[string]any, error) {
	query := url.Values{"uri": []string{NormalizeURI(uri)}}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/acl", query, nil, &result)
	return result, err
}
