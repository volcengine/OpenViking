package agfs

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Common errors
var (
	// ErrNotSupported is returned when the server or endpoint does not support the requested operation (HTTP 501)
	ErrNotSupported = fmt.Errorf("operation not supported")
)

// Client is a Go client for AGFS HTTP API
type Client struct {
	baseURL    string
	httpClient *http.Client
}

// NewClient creates a new AGFS client
// baseURL can be either full URL with "/api/v1" or just the base.
// If "/api/v1" is not present, it will be automatically appended.
// e.g., "http://localhost:8080" or "http://localhost:8080/api/v1"
func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: normalizeBaseURL(baseURL),
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

// NewClientWithHTTPClient creates a new AGFS client with custom HTTP client
func NewClientWithHTTPClient(baseURL string, httpClient *http.Client) *Client {
	return &Client{
		baseURL:    normalizeBaseURL(baseURL),
		httpClient: httpClient,
	}
}

// normalizeBaseURL ensures the base URL ends with /api/v1
func normalizeBaseURL(baseURL string) string {
	// Remove trailing slash
	if len(baseURL) > 0 && baseURL[len(baseURL)-1] == '/' {
		baseURL = baseURL[:len(baseURL)-1]
	}

	// Validate that we have a proper URL with a host
	// A valid URL should at least have "protocol://host" format
	// Check for "://" to ensure we have both protocol and host
	if !strings.Contains(baseURL, "://") {
		// If there's no "://", this is likely a malformed URL
		// Don't try to fix it, just return as-is and let HTTP client fail with proper error
		return baseURL
	}

	// Auto-append /api/v1 if not present
	if len(baseURL) < 7 || baseURL[len(baseURL)-7:] != "/api/v1" {
		baseURL = baseURL + "/api/v1"
	}
	return baseURL
}

// ErrorResponse represents an error response from the API
type ErrorResponse struct {
	Error string `json:"error"`
}

// SuccessResponse represents a success response from the API
type SuccessResponse struct {
	Message string `json:"message"`
}

// FileInfoResponse represents file info response from the API
type FileInfoResponse struct {
	Name    string   `json:"name"`
	Size    int64    `json:"size"`
	Mode    uint32   `json:"mode"`
	ModTime string   `json:"modTime"`
	IsDir   bool     `json:"isDir"`
	Meta    MetaData `json:"meta,omitempty"`
}

// ListResponse represents directory listing response from the API
type ListResponse struct {
	Files []FileInfoResponse `json:"files"`
}

// RenameRequest represents a rename request
type RenameRequest struct {
	NewPath string `json:"newPath"`
}

// ChmodRequest represents a chmod request
type ChmodRequest struct {
	Mode uint32 `json:"mode"`
}

func (c *Client) doRequest(method, endpoint string, query url.Values, body io.Reader) (*http.Response, error) {
	u := c.baseURL + endpoint
	if len(query) > 0 {
		u += "?" + query.Encode()
	}

	req, err := http.NewRequest(method, u, body)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute request: %w", err)
	}

	return resp, nil
}

func (c *Client) handleErrorResponse(resp *http.Response) error {
	defer resp.Body.Close()

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}

	if resp.StatusCode == http.StatusNotImplemented {
		return ErrNotSupported
	}

	var errResp ErrorResponse
	if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
		return fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
	}

	return fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
}

// Create creates a new file
func (c *Client) Create(path string) error {
	query := url.Values{}
	query.Set("path", path)

	resp, err := c.doRequest(http.MethodPost, "/files", query, nil)
	if err != nil {
		return err
	}

	return c.handleErrorResponse(resp)
}

// Mkdir creates a new directory
func (c *Client) Mkdir(path string, perm uint32) error {
	query := url.Values{}
	query.Set("path", path)
	query.Set("mode", fmt.Sprintf("%o", perm))

	resp, err := c.doRequest(http.MethodPost, "/directories", query, nil)
	if err != nil {
		return err
	}

	return c.handleErrorResponse(resp)
}

// Remove removes a file or empty directory
func (c *Client) Remove(path string) error {
	query := url.Values{}
	query.Set("path", path)
	query.Set("recursive", "false")

	resp, err := c.doRequest(http.MethodDelete, "/files", query, nil)
	if err != nil {
		return err
	}

	return c.handleErrorResponse(resp)
}

// RemoveAll removes a path and any children it contains
func (c *Client) RemoveAll(path string) error {
	query := url.Values{}
	query.Set("path", path)
	query.Set("recursive", "true")

	resp, err := c.doRequest(http.MethodDelete, "/files", query, nil)
	if err != nil {
		return err
	}

	return c.handleErrorResponse(resp)
}

// Read reads file content with optional offset and size
// offset: starting position (0 means from beginning)
// size: number of bytes to read (-1 means read all)
// Returns io.EOF if offset+size >= file size (reached end of file)
func (c *Client) Read(path string, offset int64, size int64) ([]byte, error) {
	query := url.Values{}
	query.Set("path", path)
	if offset > 0 {
		query.Set("offset", fmt.Sprintf("%d", offset))
	}
	if size >= 0 {
		query.Set("size", fmt.Sprintf("%d", size))
	}

	resp, err := c.doRequest(http.MethodGet, "/files", query, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	return data, nil
}

// Write writes data to a file, creating it if necessary
// Automatically retries on network errors and timeouts (max 3 retries with exponential backoff)
func (c *Client) Write(path string, data []byte) ([]byte, error) {
	return c.WriteWithRetry(path, data, 3)
}

// WriteWithRetry writes data to a file with configurable retry attempts
func (c *Client) WriteWithRetry(path string, data []byte, maxRetries int) ([]byte, error) {
	query := url.Values{}
	query.Set("path", path)

	var lastErr error

	for attempt := 0; attempt <= maxRetries; attempt++ {
		resp, err := c.doRequest(http.MethodPut, "/files", query, bytes.NewReader(data))
		if err != nil {
			lastErr = err

			// Check if error is retryable (network/timeout errors)
			if isRetryableError(err) && attempt < maxRetries {
				waitTime := time.Duration(1<<uint(attempt)) * time.Second // 1s, 2s, 4s
				fmt.Printf("⚠ Upload failed (attempt %d/%d): %v\n", attempt+1, maxRetries+1, err)
				fmt.Printf("  Retrying in %v...\n", waitTime)
				time.Sleep(waitTime)
				continue
			}

			if attempt >= maxRetries {
				fmt.Printf("✗ Upload failed after %d attempts\n", maxRetries+1)
			}
			return nil, err
		}

		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			var errResp ErrorResponse
			if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
				return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
			}

			lastErr = fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)

			// Retry on server errors (5xx)
			if resp.StatusCode >= 500 && resp.StatusCode < 600 && attempt < maxRetries {
				waitTime := time.Duration(1<<uint(attempt)) * time.Second
				fmt.Printf("⚠ Server error %d (attempt %d/%d)\n", resp.StatusCode, attempt+1, maxRetries+1)
				fmt.Printf("  Retrying in %v...\n", waitTime)
				time.Sleep(waitTime)
				continue
			}

			if attempt >= maxRetries {
				fmt.Printf("✗ Upload failed after %d attempts\n", maxRetries+1)
			}
			return nil, lastErr
		}

		var successResp SuccessResponse
		if err := json.NewDecoder(resp.Body).Decode(&successResp); err != nil {
			return nil, fmt.Errorf("failed to decode success response: %w", err)
		}

		// If we succeeded after retrying, let user know
		if attempt > 0 {
			fmt.Printf("✓ Upload succeeded after %d retry(ies)\n", attempt)
		}

		return []byte(successResp.Message), nil
	}

	return nil, lastErr
}

// isRetryableError checks if an error is retryable (network/timeout errors)
func isRetryableError(err error) bool {
	if err == nil {
		return false
	}

	// Check for timeout errors
	if netErr, ok := err.(interface{ Timeout() bool }); ok && netErr.Timeout() {
		return true
	}

	// Check for temporary network errors
	if netErr, ok := err.(interface{ Temporary() bool }); ok && netErr.Temporary() {
		return true
	}

	// Check for connection errors
	errStr := err.Error()
	return strings.Contains(errStr, "connection refused") ||
		strings.Contains(errStr, "connection reset") ||
		strings.Contains(errStr, "broken pipe") ||
		strings.Contains(errStr, "timeout")
}

// ReadDir lists the contents of a directory
func (c *Client) ReadDir(path string) ([]FileInfo, error) {
	query := url.Values{}
	query.Set("path", path)

	resp, err := c.doRequest(http.MethodGet, "/directories", query, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var listResp ListResponse
	if err := json.NewDecoder(resp.Body).Decode(&listResp); err != nil {
		return nil, fmt.Errorf("failed to decode list response: %w", err)
	}

	files := make([]FileInfo, 0, len(listResp.Files))
	for _, f := range listResp.Files {
		modTime, _ := time.Parse(time.RFC3339Nano, f.ModTime)
		files = append(files, FileInfo{
			Name:    f.Name,
			Size:    f.Size,
			Mode:    f.Mode,
			ModTime: modTime,
			IsDir:   f.IsDir,
			Meta:    f.Meta,
		})
	}

	return files, nil
}

// Stat returns file information
func (c *Client) Stat(path string) (*FileInfo, error) {
	query := url.Values{}
	query.Set("path", path)

	resp, err := c.doRequest(http.MethodGet, "/stat", query, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var fileInfo FileInfoResponse
	if err := json.NewDecoder(resp.Body).Decode(&fileInfo); err != nil {
		return nil, fmt.Errorf("failed to decode file info response: %w", err)
	}

	modTime, _ := time.Parse(time.RFC3339Nano, fileInfo.ModTime)

	return &FileInfo{
		Name:    fileInfo.Name,
		Size:    fileInfo.Size,
		Mode:    fileInfo.Mode,
		ModTime: modTime,
		IsDir:   fileInfo.IsDir,
		Meta:    fileInfo.Meta,
	}, nil
}

// Rename renames/moves a file or directory
func (c *Client) Rename(oldPath, newPath string) error {
	query := url.Values{}
	query.Set("path", oldPath)

	reqBody := RenameRequest{NewPath: newPath}
	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		return fmt.Errorf("failed to marshal rename request: %w", err)
	}

	resp, err := c.doRequest(http.MethodPost, "/rename", query, bytes.NewReader(jsonData))
	if err != nil {
		return err
	}

	return c.handleErrorResponse(resp)
}

// Chmod changes file permissions
func (c *Client) Chmod(path string, mode uint32) error {
	query := url.Values{}
	query.Set("path", path)

	reqBody := ChmodRequest{Mode: mode}
	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		return fmt.Errorf("failed to marshal chmod request: %w", err)
	}

	resp, err := c.doRequest(http.MethodPost, "/chmod", query, bytes.NewReader(jsonData))
	if err != nil {
		return err
	}

	return c.handleErrorResponse(resp)
}

// Health checks the health of the AGFS server
func (c *Client) Health() error {
	resp, err := c.doRequest(http.MethodGet, "/health", nil, nil)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("health check failed with status: %d", resp.StatusCode)
	}

	return nil
}

// CapabilitiesResponse represents the server capabilities
type CapabilitiesResponse struct {
	Version  string   `json:"version"`
	Features []string `json:"features"`
}

// GetCapabilities retrieves the server capabilities
func (c *Client) GetCapabilities() (*CapabilitiesResponse, error) {
	resp, err := c.doRequest(http.MethodGet, "/capabilities", nil, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// Fallback for older servers that don't have this endpoint
		if resp.StatusCode == http.StatusNotFound {
			return &CapabilitiesResponse{
				Version:  "unknown",
				Features: []string{},
			}, nil
		}
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var caps CapabilitiesResponse
	if err := json.NewDecoder(resp.Body).Decode(&caps); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &caps, nil
}

// ReadStream opens a streaming connection to read from a file
// Returns an io.ReadCloser that streams data from the server
// The caller is responsible for closing the reader
func (c *Client) ReadStream(path string) (io.ReadCloser, error) {
	query := url.Values{}
	query.Set("path", path)
	query.Set("stream", "true") // Enable streaming mode

	// Create request with no timeout for streaming
	streamClient := &http.Client{
		Timeout: 0, // No timeout for streaming
	}

	reqURL := fmt.Sprintf("%s/files?%s", c.baseURL, query.Encode())
	req, err := http.NewRequest(http.MethodGet, reqURL, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := streamClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute request: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		defer resp.Body.Close()
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	// Return the response body as a ReadCloser
	// Caller must close it when done
	return resp.Body, nil
}

// GrepRequest represents a grep search request
type GrepRequest struct {
	Path            string `json:"path"`
	Pattern         string `json:"pattern"`
	Recursive       bool   `json:"recursive"`
	CaseInsensitive bool   `json:"case_insensitive"`
	NodeLimit       int    `json:"node_limit"` // Maximum number of results to return (0 means no limit)
}

// GrepMatch represents a single match result
type GrepMatch struct {
	File    string `json:"file"`
	Line    int    `json:"line"`
	Content string `json:"content"`
}

// GrepResponse represents the grep search results
type GrepResponse struct {
	Matches []GrepMatch `json:"matches"`
	Count   int         `json:"count"`
}

// DigestRequest represents a digest request
type DigestRequest struct {
	Algorithm string `json:"algorithm"` // "xxh3" or "md5"
	Path      string `json:"path"`      // Path to the file
}

// DigestResponse represents the digest result
type DigestResponse struct {
	Algorithm string `json:"algorithm"` // Algorithm used
	Path      string `json:"path"`      // File path
	Digest    string `json:"digest"`    // Hex-encoded digest
}

// Grep searches for a pattern in files using regular expressions
func (c *Client) Grep(path, pattern string, recursive, caseInsensitive bool, nodeLimit int) (*GrepResponse, error) {
	nl := 0
	if nodeLimit > 0 {
		nl = nodeLimit
	}
	reqBody := GrepRequest{
		Path:            path,
		Pattern:         pattern,
		Recursive:       recursive,
		CaseInsensitive: caseInsensitive,
		NodeLimit:       nl,
	}

	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	reqURL := fmt.Sprintf("%s/grep", c.baseURL)
	req, err := http.NewRequest(http.MethodPost, reqURL, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var grepResp GrepResponse
	if err := json.NewDecoder(resp.Body).Decode(&grepResp); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &grepResp, nil
}

// Digest calculates the digest of a file using specified algorithm
func (c *Client) Digest(path, algorithm string) (*DigestResponse, error) {
	reqBody := DigestRequest{
		Algorithm: algorithm,
		Path:      path,
	}

	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	reqURL := fmt.Sprintf("%s/digest", c.baseURL)
	req, err := http.NewRequest(http.MethodPost, reqURL, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var digestResp DigestResponse
	if err := json.NewDecoder(resp.Body).Decode(&digestResp); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &digestResp, nil
}

// OpenHandle opens a file and returns a handle ID
func (c *Client) OpenHandle(path string, flags OpenFlag, mode uint32) (int64, error) {
	query := url.Values{}
	query.Set("path", path)
	query.Set("flags", fmt.Sprintf("%d", flags))
	query.Set("mode", fmt.Sprintf("%o", mode))

	resp, err := c.doRequest(http.MethodPost, "/handles/open", query, nil)
	if err != nil {
		return 0, fmt.Errorf("open handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		if resp.StatusCode == http.StatusNotImplemented {
			return 0, ErrNotSupported
		}
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return 0, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return 0, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var handleResp HandleResponse
	if err := json.NewDecoder(resp.Body).Decode(&handleResp); err != nil {
		return 0, fmt.Errorf("failed to decode handle response: %w", err)
	}

	return handleResp.HandleID, nil
}

// CloseHandle closes a file handle
func (c *Client) CloseHandle(handleID int64) error {
	endpoint := fmt.Sprintf("/handles/%d", handleID)

	resp, err := c.doRequest(http.MethodDelete, endpoint, nil, nil)
	if err != nil {
		return fmt.Errorf("close handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	return nil
}

// ReadHandle reads data from a file handle
func (c *Client) ReadHandle(handleID int64, offset int64, size int) ([]byte, error) {
	endpoint := fmt.Sprintf("/handles/%d/read", handleID)
	query := url.Values{}
	query.Set("offset", fmt.Sprintf("%d", offset))
	query.Set("size", fmt.Sprintf("%d", size))

	resp, err := c.doRequest(http.MethodGet, endpoint, query, nil)
	if err != nil {
		return nil, fmt.Errorf("read handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	return data, nil
}

// ReadHandleStream opens a streaming connection to read from a file handle
// Returns an io.ReadCloser that streams data from the server
// The caller is responsible for closing the reader
func (c *Client) ReadHandleStream(handleID int64) (io.ReadCloser, error) {
	endpoint := fmt.Sprintf("/handles/%d/stream", handleID)

	// Create request with no timeout for streaming
	streamClient := &http.Client{
		Timeout: 0, // No timeout for streaming
	}

	reqURL := fmt.Sprintf("%s%s", c.baseURL, endpoint)
	req, err := http.NewRequest(http.MethodGet, reqURL, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := streamClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to execute request: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		defer resp.Body.Close()
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	return resp.Body, nil
}

// WriteHandle writes data to a file handle
func (c *Client) WriteHandle(handleID int64, data []byte, offset int64) (int, error) {
	endpoint := fmt.Sprintf("/handles/%d/write", handleID)
	query := url.Values{}
	query.Set("offset", fmt.Sprintf("%d", offset))

	// Note: For binary data, we don't use JSON
	req, err := http.NewRequest(http.MethodPut, c.baseURL+endpoint+"?"+query.Encode(), bytes.NewReader(data))
	if err != nil {
		return 0, fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/octet-stream")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return 0, fmt.Errorf("write handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return 0, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return 0, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	// Parse bytes written from response
	var result struct {
		BytesWritten int `json:"bytes_written"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		// If parsing fails, assume all bytes were written
		return len(data), nil
	}

	return result.BytesWritten, nil
}

// SyncHandle syncs a file handle
func (c *Client) SyncHandle(handleID int64) error {
	endpoint := fmt.Sprintf("/handles/%d/sync", handleID)

	resp, err := c.doRequest(http.MethodPost, endpoint, nil, nil)
	if err != nil {
		return fmt.Errorf("sync handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	return nil
}

// SeekHandle seeks to a position in a file handle
func (c *Client) SeekHandle(handleID int64, offset int64, whence int) (int64, error) {
	endpoint := fmt.Sprintf("/handles/%d/seek", handleID)
	query := url.Values{}
	query.Set("offset", fmt.Sprintf("%d", offset))
	query.Set("whence", fmt.Sprintf("%d", whence))

	resp, err := c.doRequest(http.MethodPost, endpoint, query, nil)
	if err != nil {
		return 0, fmt.Errorf("seek handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return 0, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return 0, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var result struct {
		Offset int64 `json:"offset"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return 0, fmt.Errorf("failed to decode response: %w", err)
	}

	return result.Offset, nil
}

// GetHandle retrieves information about an open handle
func (c *Client) GetHandle(handleID int64) (*HandleInfo, error) {
	endpoint := fmt.Sprintf("/handles/%d", handleID)

	resp, err := c.doRequest(http.MethodGet, endpoint, nil, nil)
	if err != nil {
		return nil, fmt.Errorf("get handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var handleInfo HandleInfo
	if err := json.NewDecoder(resp.Body).Decode(&handleInfo); err != nil {
		return nil, fmt.Errorf("failed to decode handle info: %w", err)
	}

	return &handleInfo, nil
}

// StatHandle gets file info via a handle
func (c *Client) StatHandle(handleID int64) (*FileInfo, error) {
	endpoint := fmt.Sprintf("/handles/%d/stat", handleID)

	resp, err := c.doRequest(http.MethodGet, endpoint, nil, nil)
	if err != nil {
		return nil, fmt.Errorf("stat handle request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		var errResp ErrorResponse
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			return nil, fmt.Errorf("HTTP %d: failed to decode error response", resp.StatusCode)
		}
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, errResp.Error)
	}

	var fileInfo FileInfoResponse
	if err := json.NewDecoder(resp.Body).Decode(&fileInfo); err != nil {
		return nil, fmt.Errorf("failed to decode file info response: %w", err)
	}

	modTime, _ := time.Parse(time.RFC3339Nano, fileInfo.ModTime)

	return &FileInfo{
		Name:    fileInfo.Name,
		Size:    fileInfo.Size,
		Mode:    fileInfo.Mode,
		ModTime: modTime,
		IsDir:   fileInfo.IsDir,
		Meta:    fileInfo.Meta,
	}, nil
}
