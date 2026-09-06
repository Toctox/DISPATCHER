package main

import (
	"os"
	"path/filepath"
	"testing"
)

func writeConfigForTest(t *testing.T, payload string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestLegacyPollSecondsMapsToPollIntervalMs(t *testing.T) {
	cfg, err := loadConfig(writeConfigForTest(t, `{"bridgeRoot":"C:\\bridge","pollSeconds":2}`))
	if err != nil {
		t.Fatalf("legacy pollSeconds should load: %v", err)
	}
	if cfg.PollIntervalMs != 2000 {
		t.Fatalf("PollIntervalMs=%d want=2000", cfg.PollIntervalMs)
	}
}

func TestCurrentPollIntervalMsTakesPrecedenceOverLegacyPollSeconds(t *testing.T) {
	cfg, err := loadConfig(writeConfigForTest(t, `{"bridgeRoot":"C:\\bridge","pollSeconds":9,"pollIntervalMs":750}`))
	if err != nil {
		t.Fatalf("mixed compatibility config should load: %v", err)
	}
	if cfg.PollIntervalMs != 750 {
		t.Fatalf("PollIntervalMs=%d want=750", cfg.PollIntervalMs)
	}
}

func TestUnknownConfigFieldStillFailsClosed(t *testing.T) {
	_, err := loadConfig(writeConfigForTest(t, `{"bridgeRoot":"C:\\bridge","arbitraryShell":"whoami"}`))
	if err == nil {
		t.Fatal("unknown config field must remain rejected")
	}
}
