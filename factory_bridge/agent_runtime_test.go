package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestProjectHubValidateRefusesRunningApplication(t *testing.T) {
	oldProbe := projectHubHealthProbe
	defer func() { projectHubHealthProbe = oldProbe }()
	projectHubHealthProbe = func() bool { return true }

	f := &fakeRunner{}
	res := executeProjectHubValidate(Config{}, Command{ID: "VALIDATE-RUNNING", Action: "projecthub.validate"}, time.Now(), f)
	if res.Status != "failed" {
		t.Fatalf("expected failed, got %+v", res)
	}
	if !strings.Contains(res.Error, "already running") {
		t.Fatalf("unexpected error: %q", res.Error)
	}
	if len(f.specs) != 0 {
		t.Fatalf("runner invoked %d times", len(f.specs))
	}
}

func TestProjectHubLogsReadsOnlyFixedStatusFiles(t *testing.T) {
	bridge := t.TempDir()
	statusDir := filepath.Join(bridge, "00_STATUS")
	if err := os.MkdirAll(statusDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(statusDir, "projecthub-server.stdout.log"), []byte("server-out\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(statusDir, "projecthub-server.stderr.log"), []byte("server-err\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	res := executeProjectHubLogs(Config{BridgeRoot: bridge}, Command{ID: "LOGS-1", Action: "projecthub.logs"}, time.Now())
	if res.Status != "ok" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Stdout != "server-out\n" || res.Stderr != "server-err\n" {
		t.Fatalf("unexpected streams: stdout=%q stderr=%q", res.Stdout, res.Stderr)
	}
}

func TestTailFileIsBounded(t *testing.T) {
	path := filepath.Join(t.TempDir(), "large.log")
	if err := os.WriteFile(path, []byte("0123456789"), 0o600); err != nil {
		t.Fatal(err)
	}
	text, exists, err := tailFile(path, 4)
	if err != nil || !exists {
		t.Fatalf("exists=%t err=%v", exists, err)
	}
	if !strings.HasSuffix(text, "6789") || !strings.Contains(text, "truncated") {
		t.Fatalf("unexpected tail: %q", text)
	}
}
