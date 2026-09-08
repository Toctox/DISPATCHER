package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestLastNonEmptyLine(t *testing.T) {
	got := lastNonEmptyLine("first\r\n\r\n{\"status\":\"OK\"}\r\n")
	if got != `{"status":"OK"}` {
		t.Fatalf("got %q", got)
	}
}

func TestPostgresInstallUsesFixedScriptAndNoDriveArguments(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("FactoryBridge PostgreSQL runtime is Windows-only")
	}
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(scripts, 0o755); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(scripts, "postgres-install-local.ps1")
	if err := os.WriteFile(script, []byte("# test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: `{"kind":"POSTGRES_INSTALL","status":"OK","version":"18.6"}` + "\r\n", code: 0}
	res := executeAction(Config{DispatcherWorkDir: root}, Command{ID: "PG-1", Action: "postgres.install"}, f)
	if res.Status != "ok" || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 1 {
		t.Fatalf("runs=%d", len(f.specs))
	}
	spec := f.specs[0]
	if spec.exe != "powershell.exe" || spec.dir != root {
		t.Fatalf("unexpected spec: %+v", spec)
	}
	joined := strings.Join(spec.args, " ")
	if !strings.Contains(joined, script) || strings.Contains(joined, "PG-1") {
		t.Fatalf("Drive command leaked into executable arguments: %q", joined)
	}
	if res.Meta["credentials"] == nil {
		t.Fatal("missing credential-boundary metadata")
	}
}
