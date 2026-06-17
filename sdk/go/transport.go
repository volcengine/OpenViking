package openviking

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

type responseEnvelope struct {
	Status    string          `json:"status"`
	Result    json.RawMessage `json:"result,omitempty"`
	Error     *ErrorInfo      `json:"error,omitempty"`
	Telemetry json.RawMessage `json:"telemetry,omitempty"`
	Profile   []string        `json:"profile,omitempty"`
}

func (c *Client) newRequest(ctx context.Context, method, path string, query url.Values, body io.Reader) (*http.Request, error) {
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	u, err := url.Parse(c.baseURL + path)
	if err != nil {
		return nil, err
	}
	values := u.Query()
	for k, vs := range query {
		for _, v := range vs {
			values.Add(k, v)
		}
	}
	if c.profile {
		values.Set("profile", "1")
	}
	u.RawQuery = values.Encode()

	req, err := http.NewRequestWithContext(ctx, method, u.String(), body)
	if err != nil {
		return nil, err
	}
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}
	if c.account != "" {
		req.Header.Set("X-OpenViking-Account", c.account)
	}
	if c.user != "" {
		req.Header.Set("X-OpenViking-User", c.user)
	}
	if c.actorPeerID != "" {
		req.Header.Set("X-OpenViking-Actor-Peer", c.actorPeerID)
	}
	for k, v := range c.extraHeaders {
		req.Header.Set(k, v)
	}
	return req, nil
}

func (c *Client) doJSON(ctx context.Context, method, path string, query url.Values, payload any, out any) error {
	var body io.Reader
	if payload != nil {
		buf, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(buf)
	}
	req, err := c.newRequest(ctx, method, path, query, body)
	if err != nil {
		return err
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	env, err := decodeEnvelope(resp.StatusCode, data)
	if err != nil {
		return err
	}
	if env.Error != nil {
		return apiError(resp.StatusCode, env.Error)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &Error{
			Code:       "UNKNOWN",
			Message:    envelopeDetail(env, resp.StatusCode, data),
			StatusCode: resp.StatusCode,
		}
	}
	if out == nil || len(env.Result) == 0 || string(env.Result) == "null" {
		return nil
	}
	return json.Unmarshal(env.Result, out)
}

func decodeEnvelope(statusCode int, data []byte) (*responseEnvelope, error) {
	if len(bytes.TrimSpace(data)) == 0 {
		return &responseEnvelope{Status: "ok"}, nil
	}
	var env responseEnvelope
	if err := json.Unmarshal(data, &env); err != nil {
		if statusCode >= 200 && statusCode < 300 {
			return nil, err
		}
		return nil, &Error{
			Code:       "UNKNOWN",
			Message:    fmt.Sprintf("HTTP %d: %s", statusCode, strings.TrimSpace(string(data))),
			StatusCode: statusCode,
		}
	}
	if env.Status == "error" && env.Error == nil {
		env.Error = &ErrorInfo{Code: "UNKNOWN", Message: envelopeDetail(&env, statusCode, data)}
	}
	return &env, nil
}

func envelopeDetail(env *responseEnvelope, statusCode int, data []byte) string {
	if env != nil {
		if env.Error != nil && env.Error.Message != "" {
			return env.Error.Message
		}
		var detail struct {
			Detail any `json:"detail"`
		}
		if err := json.Unmarshal(data, &detail); err == nil && detail.Detail != nil {
			return fmt.Sprint(detail.Detail)
		}
	}
	text := strings.TrimSpace(string(data))
	if text == "" {
		return fmt.Sprintf("HTTP %d", statusCode)
	}
	return fmt.Sprintf("HTTP %d: %s", statusCode, text)
}

func apiError(statusCode int, info *ErrorInfo) *Error {
	code := info.Code
	if code == "" {
		code = "UNKNOWN"
	}
	msg := info.Message
	if msg == "" {
		msg = "Unknown error"
	}
	return &Error{
		Code:       code,
		Message:    msg,
		Details:    info.Details,
		StatusCode: statusCode,
	}
}
