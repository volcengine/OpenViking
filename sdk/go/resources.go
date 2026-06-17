package openviking

import (
	"context"
	"fmt"
	"net/http"
)

// AddResource imports a local path or remote URL into OpenViking resources.
func (c *Client) AddResource(ctx context.Context, path string, opts *AddResourceOptions) (map[string]any, error) {
	if opts == nil {
		opts = &AddResourceOptions{}
	}
	if opts.To != "" && opts.Parent != "" {
		return nil, fmt.Errorf("openviking: cannot specify both To and Parent")
	}
	payload := map[string]any{
		"reason":                opts.Reason,
		"instruction":           opts.Instruction,
		"wait":                  opts.Wait,
		"strict":                opts.Strict,
		"directly_upload_media": boolValue(opts.DirectlyUploadMedia, true),
		"watch_interval":        opts.WatchInterval,
		"args":                  map[string]any{},
	}
	setString(payload, "to", opts.To)
	setString(payload, "parent", opts.Parent)
	setString(payload, "ignore_dirs", opts.IgnoreDirs)
	setString(payload, "include", opts.Include)
	setString(payload, "exclude", opts.Exclude)
	setFloatPtr(payload, "timeout", opts.Timeout)
	if opts.PreserveStructure != nil {
		payload["preserve_structure"] = *opts.PreserveStructure
	}
	setAny(payload, "telemetry", opts.Telemetry)
	if opts.Args != nil {
		payload["args"] = opts.Args
	}
	if err := c.addLocalUpload(ctx, payload, path, true); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/resources", nil, payload, &result)
	return result, err
}
