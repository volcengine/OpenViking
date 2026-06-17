package openviking

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// ExportOVPack exports a URI into a local .ovpack file.
func (c *Client) ExportOVPack(ctx context.Context, uri, to string, opts *PackOptions) (string, error) {
	includeVectors := opts != nil && opts.IncludeVectors
	outPath := packOutputPath(to, uri, "export")
	err := c.downloadPack(ctx, "/api/v1/pack/export", map[string]any{
		"uri":             NormalizeURI(uri),
		"include_vectors": includeVectors,
	}, outPath)
	return outPath, err
}

// BackupOVPack backs up public scopes into a local restore-only .ovpack file.
func (c *Client) BackupOVPack(ctx context.Context, to string, opts *PackOptions) (string, error) {
	includeVectors := opts != nil && opts.IncludeVectors
	outPath := packOutputPath(to, "", "openviking-backup")
	err := c.downloadPack(ctx, "/api/v1/pack/backup", map[string]any{
		"include_vectors": includeVectors,
	}, outPath)
	return outPath, err
}

func packOutputPath(to, uri, fallback string) string {
	if to == "" {
		to = "."
	}
	out := to
	if info, err := os.Stat(to); err == nil && info.IsDir() {
		name := fallback
		if uri != "" {
			trimmed := strings.TrimRight(strings.TrimSpace(uri), "/")
			if last := filepath.Base(trimmed); last != "." && last != "/" && last != "" {
				name = last
			}
		}
		out = filepath.Join(to, name+".ovpack")
	} else if !strings.HasSuffix(out, ".ovpack") {
		out += ".ovpack"
	}
	return out
}

func (c *Client) downloadPack(ctx context.Context, path string, payload map[string]any, outPath string) error {
	if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
		return err
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := c.newRequest(ctx, http.MethodPost, path, nil, strings.NewReader(string(body)))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		data, readErr := io.ReadAll(resp.Body)
		if readErr != nil {
			return readErr
		}
		env, err := decodeEnvelope(resp.StatusCode, data)
		if err != nil {
			return err
		}
		if env.Error != nil {
			return apiError(resp.StatusCode, env.Error)
		}
		return &Error{Code: "UNKNOWN", Message: envelopeDetail(env, resp.StatusCode, data), StatusCode: resp.StatusCode}
	}
	file, err := os.Create(outPath)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = io.Copy(file, resp.Body)
	return err
}

// ImportOVPack imports a local .ovpack under parent.
func (c *Client) ImportOVPack(ctx context.Context, filePath, parent string, opts *ImportPackOptions) (string, error) {
	tempID, err := c.uploadPackFile(ctx, filePath)
	if err != nil {
		return "", err
	}
	payload := map[string]any{
		"parent":       NormalizeURI(parent),
		"temp_file_id": tempID,
	}
	if opts != nil {
		setString(payload, "on_conflict", opts.OnConflict)
		setString(payload, "vector_mode", opts.VectorMode)
	}
	var result struct {
		URI string `json:"uri"`
	}
	err = c.doJSON(ctx, http.MethodPost, "/api/v1/pack/import", nil, payload, &result)
	return result.URI, err
}

// RestoreOVPack restores a local backup .ovpack.
func (c *Client) RestoreOVPack(ctx context.Context, filePath string, opts *ImportPackOptions) (string, error) {
	tempID, err := c.uploadPackFile(ctx, filePath)
	if err != nil {
		return "", err
	}
	payload := map[string]any{"temp_file_id": tempID}
	if opts != nil {
		setString(payload, "on_conflict", opts.OnConflict)
		setString(payload, "vector_mode", opts.VectorMode)
	}
	var result struct {
		URI string `json:"uri"`
	}
	err = c.doJSON(ctx, http.MethodPost, "/api/v1/pack/restore", nil, payload, &result)
	return result.URI, err
}

func (c *Client) uploadPackFile(ctx context.Context, filePath string) (string, error) {
	info, err := os.Stat(filePath)
	if err != nil {
		return "", err
	}
	if info.IsDir() {
		return "", fmt.Errorf("openviking: %s is a directory, expected .ovpack file", filePath)
	}
	return c.uploadTempFile(ctx, filePath)
}
