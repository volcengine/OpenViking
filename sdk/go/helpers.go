package openviking

import (
	"encoding/base64"
	"fmt"
	"mime"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func setString(m map[string]any, key, value string) {
	if value != "" {
		m[key] = value
	}
}

func setAny(m map[string]any, key string, value any) {
	if value != nil {
		m[key] = value
	}
}

func setQueryString(values url.Values, key, value string) {
	if value != "" {
		values.Set(key, value)
	}
}

// setQueryAny mirrors setAny for query parameters: it sets key only when value
// is non-nil, so an unset optional (e.g. a skill TargetURI) is omitted entirely.
func setQueryAny(values url.Values, key string, value any) {
	if value == nil {
		return
	}
	if s, ok := value.(string); ok {
		values.Set(key, s)
		return
	}
	values.Set(key, fmt.Sprint(value))
}

func setFloatPtr(m map[string]any, key string, value *float64) {
	if value != nil {
		m[key] = *value
	}
}

func boolValue(ptr *bool, fallback bool) bool {
	if ptr == nil {
		return fallback
	}
	return *ptr
}

// String returns a string pointer for request fields that must distinguish empty from omitted.
func String(s string) *string {
	return &s
}

// Bool returns a bool pointer for optional request fields.
func Bool(v bool) *bool {
	return &v
}

// Int returns an int pointer for optional request fields.
func Int(v int) *int {
	return &v
}

// Float64 returns a float64 pointer for optional request fields.
func Float64(v float64) *float64 {
	return &v
}

func queryBool(values url.Values, key string, value bool) {
	values.Set(key, strconv.FormatBool(value))
}

func queryInt(values url.Values, key string, value int) {
	values.Set(key, strconv.Itoa(value))
}

func queryFloat(values url.Values, key string, value float64) {
	values.Set(key, strconv.FormatFloat(value, 'f', -1, 64))
}

func normalizeTarget(target any) any {
	switch v := target.(type) {
	case nil:
		return ""
	case string:
		if v == "" {
			return ""
		}
		return NormalizeURI(v)
	case []string:
		uris := make([]string, 0, len(v))
		for _, uri := range v {
			if uri == "" {
				uris = append(uris, uri)
			} else {
				uris = append(uris, NormalizeURI(uri))
			}
		}
		return uris
	default:
		return v
	}
}

func normalizeImageInput(image string) (string, error) {
	if image == "" || strings.HasPrefix(image, "data:image/") ||
		strings.HasPrefix(image, "http://") ||
		strings.HasPrefix(image, "https://") ||
		strings.HasPrefix(image, "viking://") {
		return image, nil
	}
	info, err := os.Stat(image)
	if err != nil {
		if os.IsNotExist(err) {
			return image, nil
		}
		return "", err
	}
	if info.IsDir() {
		return image, nil
	}
	data, err := os.ReadFile(image)
	if err != nil {
		return "", err
	}
	mimeType := mime.TypeByExtension(strings.ToLower(filepath.Ext(image)))
	if mimeType == "" || !strings.HasPrefix(mimeType, "image/") {
		mimeType = "image/png"
	}
	return fmt.Sprintf("data:%s;base64,%s", mimeType, base64.StdEncoding.EncodeToString(data)), nil
}
