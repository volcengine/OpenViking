package openviking

import (
	"context"
	"net/http"
)

// ACLEntry grants one user or group a read, write, or manage level.
type ACLEntry struct {
	Principal string `json:"principal"`
	Level     string `json:"level"`
}

// ACLSpec updates only the supplied ACL fields. Nil Entries preserves direct grants;
// an empty non-nil slice clears them.
type ACLSpec struct {
	ACLMode string     `json:"acl_mode,omitempty"`
	Entries []ACLEntry `json:"entries"`
}

type ResourceAttrs struct {
	ACL *ACLSpec `json:"acl,omitempty"`
}

// AttrsSetACL updates the ACL attribute.
func (c *Client) AttrsSetACL(ctx context.Context, uri string, acl ACLSpec) (map[string]any, error) {
	body := map[string]any{"uri": NormalizeURI(uri)}
	if acl.Entries != nil {
		body["entries"] = acl.Entries
	}
	if acl.ACLMode != "" {
		body["acl_mode"] = acl.ACLMode
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/fs/attrs/set_acl", nil, body, &result)
	return result, err
}

// AttrsGrantACL sets one principal's direct ACL level.
func (c *Client) AttrsGrantACL(ctx context.Context, uri, principal, level string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/fs/attrs/grant_acl", nil, map[string]any{
		"uri": NormalizeURI(uri), "principal": principal, "level": level,
	}, &result)
	return result, err
}

// AttrsRevokeACL removes one principal's direct ACL entry.
func (c *Client) AttrsRevokeACL(ctx context.Context, uri, principal string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/fs/attrs/revoke_acl", nil, map[string]any{
		"uri": NormalizeURI(uri), "principal": principal,
	}, &result)
	return result, err
}

// AttrsResetACL clears the direct ACL and restricted mode on a URI.
func (c *Client) AttrsResetACL(ctx context.Context, uri string) (map[string]any, error) {
	body := map[string]any{"uri": NormalizeURI(uri)}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/fs/attrs/reset_acl", nil, body, &result)
	return result, err
}
