package openviking

import (
	"net/url"
	"strconv"
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
