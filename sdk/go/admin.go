package openviking

import (
	"context"
	"net/http"
	"net/url"
)

// AdminCreateAccount creates an account with its first admin user.
func (c *Client) AdminCreateAccount(ctx context.Context, accountID, adminUserID string) (map[string]any, error) {
	return c.AdminCreateAccountWithOptions(ctx, accountID, adminUserID, nil)
}

// AdminCreateAccountWithOptions creates an account with its first admin user.
func (c *Client) AdminCreateAccountWithOptions(ctx context.Context, accountID, adminUserID string, opts *AdminCreateAccountOptions) (map[string]any, error) {
	payload := map[string]any{
		"account_id":    accountID,
		"admin_user_id": adminUserID,
	}
	if opts != nil && opts.UserConfig != nil {
		payload["user_config"] = opts.UserConfig
	}
	if opts != nil && opts.Seed != nil {
		payload["seed"] = *opts.Seed
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/admin/accounts", nil, payload, &result)
	return result, err
}

// AdminListAccounts lists accounts.
func (c *Client) AdminListAccounts(ctx context.Context) ([]any, error) {
	var result []any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/admin/accounts", nil, nil, &result)
	return result, err
}

// AdminDeleteAccount deletes an account and all associated users.
func (c *Client) AdminDeleteAccount(ctx context.Context, accountID string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/admin/accounts/"+url.PathEscape(accountID), nil, nil, &result)
	return result, err
}

// AdminRegisterUser registers a user in an account.
func (c *Client) AdminRegisterUser(ctx context.Context, accountID, userID, role string) (map[string]any, error) {
	return c.AdminRegisterUserWithOptions(ctx, accountID, userID, role, nil)
}

// AdminRegisterUserWithOptions registers a user in an account.
func (c *Client) AdminRegisterUserWithOptions(ctx context.Context, accountID, userID, role string, opts *AdminRegisterUserOptions) (map[string]any, error) {
	if role == "" {
		role = "user"
	}
	payload := map[string]any{
		"user_id": userID,
		"role":    role,
	}
	if opts != nil && opts.UserConfig != nil {
		payload["user_config"] = opts.UserConfig
	}
	if opts != nil && opts.Seed != nil {
		payload["seed"] = *opts.Seed
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/admin/accounts/"+url.PathEscape(accountID)+"/users", nil, payload, &result)
	return result, err
}

// AdminListUsers lists users in an account.
func (c *Client) AdminListUsers(ctx context.Context, accountID string) ([]any, error) {
	var result []any
	err := c.doJSON(ctx, http.MethodGet, "/api/v1/admin/accounts/"+url.PathEscape(accountID)+"/users", nil, nil, &result)
	return result, err
}

// AdminRemoveUser removes a user from an account.
func (c *Client) AdminRemoveUser(ctx context.Context, accountID, userID string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodDelete, "/api/v1/admin/accounts/"+url.PathEscape(accountID)+"/users/"+url.PathEscape(userID), nil, nil, &result)
	return result, err
}

// AdminSetRole changes a user's role.
func (c *Client) AdminSetRole(ctx context.Context, accountID, userID, role string) (map[string]any, error) {
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPut, "/api/v1/admin/accounts/"+url.PathEscape(accountID)+"/users/"+url.PathEscape(userID)+"/role", nil, map[string]any{
		"role": role,
	}, &result)
	return result, err
}

// AdminRegenerateKey regenerates a user's API key.
func (c *Client) AdminRegenerateKey(ctx context.Context, accountID, userID string) (map[string]any, error) {
	return c.AdminRegenerateKeyWithOptions(ctx, accountID, userID, nil)
}

// AdminRegenerateKeyWithOptions regenerates a user's API key.
func (c *Client) AdminRegenerateKeyWithOptions(ctx context.Context, accountID, userID string, opts *AdminRegenerateKeyOptions) (map[string]any, error) {
	var payload map[string]any
	if opts != nil && opts.Seed != nil {
		payload = map[string]any{"seed": *opts.Seed}
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/admin/accounts/"+url.PathEscape(accountID)+"/users/"+url.PathEscape(userID)+"/key", nil, payload, &result)
	return result, err
}

// AdminMigrate starts legacy data migration or cleanup.
func (c *Client) AdminMigrate(ctx context.Context, opts *AdminMigrateOptions) (map[string]any, error) {
	action := "migrate"
	if opts != nil && opts.Cleanup {
		action = "cleanup"
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/admin/migrate", nil, map[string]any{"action": action}, &result)
	return result, err
}
