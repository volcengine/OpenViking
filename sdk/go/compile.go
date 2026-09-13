package openviking

import (
	"context"
	"net/http"
)

// Compile starts an asynchronous Compile task.
func (c *Client) Compile(
	ctx context.Context,
	fromURIs []string,
	to string,
	skill string,
	opts *CompileOptions,
) (map[string]any, error) {
	if opts == nil {
		opts = &CompileOptions{}
	}
	payload := map[string]any{
		"from":  fromURIs,
		"to":    to,
		"skill": skill,
	}
	setString(payload, "instruction", opts.Instruction)
	if len(opts.Args) > 0 {
		payload["args"] = opts.Args
	}
	if err := mergeExtraProtected(payload, opts.Extra, "instruction", "args"); err != nil {
		return nil, err
	}
	var result map[string]any
	err := c.doJSON(ctx, http.MethodPost, "/api/v1/compile", nil, payload, &result)
	return result, err
}
