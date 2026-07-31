package openviking

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

func zipDirectory(dir string) (string, error) {
	root, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	tmp, err := os.CreateTemp("", "openviking-upload-*.zip")
	if err != nil {
		return "", err
	}
	zipPath := tmp.Name()
	defer tmp.Close()

	zw := zip.NewWriter(tmp)
	defer zw.Close()

	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == root {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return nil
		}
		abs, err := filepath.Abs(path)
		if err != nil {
			return err
		}
		if relToRoot, err := filepath.Rel(root, abs); err != nil || strings.HasPrefix(relToRoot, "..") {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		w, err := zw.Create(rel)
		if err != nil {
			return err
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		defer f.Close()
		_, err = io.Copy(w, f)
		return err
	})
	if err != nil {
		_ = os.Remove(zipPath)
		return "", err
	}
	return zipPath, nil
}

func (c *Client) uploadTempFile(ctx context.Context, filePath string) (string, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return "", err
	}
	defer file.Close()

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", filepath.Base(filePath))
	if err != nil {
		return "", err
	}
	if _, err := io.Copy(part, file); err != nil {
		return "", err
	}
	if c.uploadMode != "" {
		if err := writer.WriteField("upload_mode", c.uploadMode); err != nil {
			return "", err
		}
	}
	if err := writer.Close(); err != nil {
		return "", err
	}

	req, err := c.newRequest(ctx, http.MethodPost, "/api/v1/resources/temp_upload", nil, &body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	env, err := decodeEnvelope(resp.StatusCode, data)
	if err != nil {
		return "", err
	}
	if env.Error != nil {
		return "", apiError(resp.StatusCode, env.Error)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", &Error{Code: "UNKNOWN", Message: envelopeDetail(env, resp.StatusCode, data), StatusCode: resp.StatusCode}
	}
	var result struct {
		TempFileID string `json:"temp_file_id"`
	}
	if len(env.Result) > 0 && string(env.Result) != "null" {
		if err := json.Unmarshal(env.Result, &result); err != nil {
			return "", err
		}
	}
	return result.TempFileID, nil
}

func (c *Client) addLocalUpload(ctx context.Context, payload map[string]any, path string, includeSourceName bool) error {
	info, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			payload["path"] = path
			return nil
		}
		return err
	}
	if info.IsDir() {
		if includeSourceName {
			payload["source_name"] = filepath.Base(path)
		}
		zipPath, err := zipDirectory(path)
		if err != nil {
			return err
		}
		defer os.Remove(zipPath)
		tempID, err := c.uploadTempFile(ctx, zipPath)
		if err != nil {
			return err
		}
		payload["temp_file_id"] = tempID
		return nil
	}
	if info.Mode().IsRegular() {
		if includeSourceName {
			payload["source_name"] = filepath.Base(path)
		}
		tempID, err := c.uploadTempFile(ctx, path)
		if err != nil {
			return err
		}
		payload["temp_file_id"] = tempID
		return nil
	}
	payload["path"] = path
	return nil
}
