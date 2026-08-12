package openviking

import (
	"context"
	"net/http"
)

// ResolveOpenVikingAssets parses and validates an OpenViking Assets manifest.
func (c *Client) ResolveOpenVikingAssets(
	ctx context.Context,
	manifestYAML string,
	opts *ResolveAssetsOptions,
) (map[string]any, error) {
	payload := map[string]any{"manifest_yaml": manifestYAML}
	if opts != nil {
		setString(payload, "catalog_yaml", opts.CatalogYAML)
		setString(payload, "manifest_label", opts.ManifestLabel)
		setString(payload, "catalog_label", opts.CatalogLabel)
		if err := mergeExtra(payload, opts.Extra); err != nil {
			return nil, err
		}
	}
	var result map[string]any
	err := c.doJSON(
		ctx,
		http.MethodPost,
		"/api/v1/openviking-assets/resolve",
		nil,
		payload,
		&result,
	)
	return result, err
}

// PreflightOpenVikingAsset verifies read access to one Git asset.
func (c *Client) PreflightOpenVikingAsset(
	ctx context.Context,
	name string,
	repoURL string,
	opts *PreflightAssetOptions,
) (map[string]any, error) {
	payload := map[string]any{
		"name":      name,
		"connector": "git",
		"repo_url":  repoURL,
	}
	if opts != nil {
		setString(payload, "branch", opts.Branch)
		setString(payload, "commit", opts.Commit)
		setAny(payload, "auth_config", opts.AuthConfig)
		if err := mergeExtra(payload, opts.Extra); err != nil {
			return nil, err
		}
	}
	var result map[string]any
	err := c.doJSON(
		ctx,
		http.MethodPost,
		"/api/v1/openviking-assets/preflight",
		nil,
		payload,
		&result,
	)
	return result, err
}
