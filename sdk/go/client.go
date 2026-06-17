package openviking

import (
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const defaultTimeout = 60 * time.Second

// Client is an HTTP client for an OpenViking server.
type Client struct {
	baseURL      string
	httpClient   *http.Client
	apiKey       string
	account      string
	user         string
	actorPeerID  string
	extraHeaders map[string]string
	profile      bool
	uploadMode   string
}

// NewClient creates an OpenViking HTTP client.
func NewClient(cfg Config) (*Client, error) {
	if strings.TrimSpace(cfg.BaseURL) == "" {
		return nil, fmt.Errorf("openviking: BaseURL is required")
	}
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("openviking: invalid BaseURL %q", cfg.BaseURL)
	}

	httpClient := cfg.HTTPClient
	if httpClient == nil {
		timeout := cfg.Timeout
		if timeout == 0 {
			timeout = defaultTimeout
		}
		httpClient = &http.Client{Timeout: timeout}
	}

	headers := make(map[string]string, len(cfg.ExtraHeaders))
	for k, v := range cfg.ExtraHeaders {
		headers[k] = v
	}

	return &Client{
		baseURL:      baseURL,
		httpClient:   httpClient,
		apiKey:       cfg.APIKey,
		account:      cfg.Account,
		user:         cfg.User,
		actorPeerID:  cfg.ActorPeerID,
		extraHeaders: headers,
		profile:      cfg.Profile,
		uploadMode:   cfg.UploadMode,
	}, nil
}

// CloseIdleConnections closes idle HTTP connections owned by the underlying client.
func (c *Client) CloseIdleConnections() {
	if c == nil || c.httpClient == nil {
		return
	}
	c.httpClient.CloseIdleConnections()
}

// NormalizeURI normalizes a short OpenViking URI into viking:// form.
func NormalizeURI(uri string) string {
	if strings.HasPrefix(uri, "viking://") {
		return uri
	}
	return "viking://" + strings.TrimLeft(uri, "/")
}
