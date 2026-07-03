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
	// Only attach args when arguments were actually provided. Instances that
	// predate #2549 (which added the args field to the resources route under
	// model_config=ConfigDict(extra="forbid")) reject an empty args object with
	// "body.args: Extra inputs are not permitted". Mirrors the Python SDK
	// _compact_request_body (#2834) and the Rust CLI compact_request_body (#2799).
	if len(opts.Args) > 0 {
		payload["args"] = opts.Args
	}
	if err := c.addLocalUpload(ctx, payload, path, true); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/resources", nil, payload, &result)
	return result, err
}
