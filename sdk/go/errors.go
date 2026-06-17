package openviking

import (
	"errors"
	"fmt"
)

// Error is returned for OpenViking API failures.
type Error struct {
	Code       string
	Message    string
	Details    map[string]any
	StatusCode int
}

func (e *Error) Error() string {
	if e == nil {
		return "<nil>"
	}
	if e.Code == "" {
		return e.Message
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

// IsCode reports whether err is an OpenViking API error with the given code.
func IsCode(err error, code string) bool {
	var apiErr *Error
	return errors.As(err, &apiErr) && apiErr.Code == code
}

// ErrorInfo mirrors the API error object inside an OpenViking response envelope.
type ErrorInfo struct {
	Code    string         `json:"code"`
	Message string         `json:"message"`
	Details map[string]any `json:"details,omitempty"`
}
