package handlers

import (
	"testing"
)

func TestParseRipgrepLine(t *testing.T) {
	tests := []struct {
		name        string
		line        string
		wantFile    string
		wantLine    int
		wantContent string
		wantOk      bool
	}{
		{
			name:        "normal line",
			line:        "/path/to/file.go:10:5:some content",
			wantFile:    "/path/to/file.go",
			wantLine:    10,
			wantContent: "some content",
			wantOk:      true,
		},
		{
			name:        "content contains :digit:digit: pattern",
			line:        "/path/to/file.go:10:5:error at position 20:3: invalid token",
			wantFile:    "/path/to/file.go",
			wantLine:    10,
			wantContent: "error at position 20:3: invalid token",
			wantOk:      true,
		},
		{
			name:        "content contains multiple :digit:digit: patterns",
			line:        "/path/to/file.go:42:1:fmt.Sprintf(\"%d:%d:\", 1, 2)",
			wantFile:    "/path/to/file.go",
			wantLine:    42,
			wantContent: "fmt.Sprintf(\"%d:%d:\", 1, 2)",
			wantOk:      true,
		},
		{
			name:    "no separator",
			line:    "just some text",
			wantOk:  false,
		},
		{
			name:    "empty line",
			line:    "",
			wantOk:  false,
		},
		{
			name:        "col is zero",
			line:        "/src/main.go:1:0:package main",
			wantFile:    "/src/main.go",
			wantLine:    1,
			wantContent: "package main",
			wantOk:      true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			file, line, content, ok := parseRipgrepLine(tt.line)
			if ok != tt.wantOk {
				t.Fatalf("ok = %v, want %v", ok, tt.wantOk)
			}
			if !ok {
				return
			}
			if file != tt.wantFile {
				t.Errorf("file = %q, want %q", file, tt.wantFile)
			}
			if line != tt.wantLine {
				t.Errorf("line = %d, want %d", line, tt.wantLine)
			}
			if content != tt.wantContent {
				t.Errorf("content = %q, want %q", content, tt.wantContent)
			}
		})
	}
}
