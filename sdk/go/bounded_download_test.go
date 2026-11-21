package openviking

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

type downloadRoundTrip func(*http.Request) (*http.Response, error)

func (f downloadRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type downloadBody struct {
	io.Reader
	closed bool
	read   int
}

func (b *downloadBody) Read(p []byte) (int, error) {
	n, e := b.Reader.Read(p)
	b.read += n
	return n, e
}
func (b *downloadBody) Close() error { b.closed = true; return nil }

func boundedClient(t *testing.T, data string, length int64, status int) (*Client, *downloadBody) {
	t.Helper()
	body := &downloadBody{Reader: strings.NewReader(data)}
	client, err := NewClient(Config{BaseURL: "https://example.invalid", Account: "a", User: "u", ActorPeerID: "actor", HTTPClient: &http.Client{Transport: downloadRoundTrip(func(req *http.Request) (*http.Response, error) {
		if req.URL.Query().Get("max_bytes") == "" {
			t.Error("limit must travel to server")
		}
		if req.Header.Get("X-OpenViking-User") != "u" || req.Header.Get("X-OpenViking-Actor-Peer") != "actor" {
			t.Error("identity headers lost")
		}
		return &http.Response{StatusCode: status, Header: make(http.Header), Body: body, ContentLength: length, Request: req}, nil
	})}})
	if err != nil {
		t.Fatal(err)
	}
	return client, body
}

func TestBoundedDownloadRejectsDeclaredLengthBeforeRead(t *testing.T) {
	client, body := boundedClient(t, "abcdef", 6, 200)
	_, err := client.DownloadBytesWithLimit(context.Background(), "resources/demo/input.bin", 1)
	if !IsCode(err, "RESOURCE_EXHAUSTED") || !body.closed || body.read != 0 {
		t.Fatalf("err=%v closed=%v read=%d", err, body.closed, body.read)
	}
}
func TestBoundedDownloadStopsAnOldChunkedServer(t *testing.T) {
	client, body := boundedClient(t, "abcdef-never-consumed", -1, 200)
	_, err := client.DownloadBytesWithLimit(context.Background(), "resources/demo/input.bin", 3)
	if !IsCode(err, "RESOURCE_EXHAUSTED") || !body.closed || body.read != 4 {
		t.Fatalf("err=%v closed=%v read=%d", err, body.closed, body.read)
	}
}
func TestBoundedDownloadExactAndEmpty(t *testing.T) {
	for _, data := range []string{"", "abcd"} {
		client, body := boundedClient(t, data, -1, 200)
		got, err := client.DownloadBytesWithLimit(context.Background(), "resources/demo/input.bin", int64(len(data)))
		if err != nil || string(got) != data || !body.closed {
			t.Fatalf("got=%q err=%v closed=%v", got, err, body.closed)
		}
	}
}
func TestBoundedDownloadPreservesServerError(t *testing.T) {
	client, body := boundedClient(t, `{"status":"error","error":{"code":"NOT_FOUND","message":"missing"}}`, -1, 404)
	_, err := client.DownloadBytesWithLimit(context.Background(), "resources/demo/input.bin", 1024)
	if !IsCode(err, "NOT_FOUND") || !body.closed {
		t.Fatalf("err=%v closed=%v", err, body.closed)
	}
}
func TestBoundedDownloadInvalidLimitDoesNotSend(t *testing.T) {
	c, e := NewClient(Config{BaseURL: "https://example.invalid", HTTPClient: &http.Client{Transport: downloadRoundTrip(func(*http.Request) (*http.Response, error) { t.Fatal("invalid limit reached network"); return nil, nil })}})
	if e != nil {
		t.Fatal(e)
	}
	for _, limit := range []int64{-1, math.MaxInt64} {
		if _, err := c.DownloadBytesWithLimit(context.Background(), "resources/demo/input.bin", limit); !IsCode(err, "INVALID_ARGUMENT") {
			t.Fatal(err)
		}
	}
}
func TestBoundedDownloadCancellationClosesActualHTTPStream(t *testing.T) {
	started, closed := make(chan struct{}), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
		close(closed)
	}))
	defer server.Close()
	c, e := NewClient(Config{BaseURL: server.URL})
	if e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() { _, err := c.DownloadBytesWithLimit(ctx, "resources/demo/input.bin", 10); result <- err }()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("headers not received")
	}
	cancel()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancelled read did not return")
	}
	select {
	case <-closed:
	case <-time.After(2 * time.Second):
		t.Fatal("HTTP stream was not closed")
	}
}

func TestBoundedDownloadCountsDecodedHTTPBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var compressed bytes.Buffer
		writer := gzip.NewWriter(&compressed)
		_, _ = writer.Write([]byte(strings.Repeat("x", 100)))
		_ = writer.Close()
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Set("Content-Length", strconv.Itoa(compressed.Len()))
		_, _ = w.Write(compressed.Bytes())
	}))
	defer server.Close()
	client, e := NewClient(Config{BaseURL: server.URL})
	if e != nil {
		t.Fatal(e)
	}
	_, err := client.DownloadBytesWithLimit(context.Background(), "resources/demo/input.bin", 50)
	if !IsCode(err, "RESOURCE_EXHAUSTED") {
		t.Fatal(err)
	}
}
