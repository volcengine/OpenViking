package s3fs

import "testing"

func TestNormalizeDirectoryMarkerModeConfigDefaultsToEmpty(t *testing.T) {
	mode, err := normalizeDirectoryMarkerModeConfig(map[string]interface{}{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mode != DirectoryMarkerModeEmpty {
		t.Fatalf("expected default mode %q, got %q", DirectoryMarkerModeEmpty, mode)
	}
}

func TestNormalizeDirectoryMarkerModeConfigRejectsUnknownMode(t *testing.T) {
	_, err := normalizeDirectoryMarkerModeConfig(map[string]interface{}{
		"directory_marker_mode": "mystery",
	})
	if err == nil {
		t.Fatal("expected unknown mode error, got nil")
	}
}

func TestDirectoryMarkerPayload(t *testing.T) {
	payload, shouldCreate := directoryMarkerPayload(DirectoryMarkerModeNone)
	if shouldCreate {
		t.Fatal("expected none mode to skip marker creation")
	}
	if payload != nil {
		t.Fatalf("expected nil payload for none mode, got %v", payload)
	}

	payload, shouldCreate = directoryMarkerPayload(DirectoryMarkerModeEmpty)
	if !shouldCreate {
		t.Fatal("expected empty mode to create marker")
	}
	if len(payload) != 0 {
		t.Fatalf("expected empty marker payload, got %d bytes", len(payload))
	}

	payload, shouldCreate = directoryMarkerPayload(DirectoryMarkerModeNonEmpty)
	if !shouldCreate {
		t.Fatal("expected nonempty mode to create marker")
	}
	if len(payload) != 1 || payload[0] != '\n' {
		t.Fatalf("expected newline marker payload, got %v", payload)
	}
}

func TestShouldEnforceParentDirectoryExistence(t *testing.T) {
	client := &S3Client{directoryMarkerMode: DirectoryMarkerModeNone}
	if client.shouldEnforceParentDirectoryExistence() {
		t.Fatal("expected none mode to skip parent directory enforcement")
	}

	client.directoryMarkerMode = DirectoryMarkerModeEmpty
	if !client.shouldEnforceParentDirectoryExistence() {
		t.Fatal("expected empty mode to enforce parent directories")
	}

	client.directoryMarkerMode = DirectoryMarkerModeNonEmpty
	if !client.shouldEnforceParentDirectoryExistence() {
		t.Fatal("expected nonempty mode to enforce parent directories")
	}
}
