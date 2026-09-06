package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDispatcherTickUsesFixedLocalCommandWithoutDryRun(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "scripts", "run-tick.ps1"), []byte("# test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "live tick ok\n", code: 0}
	res := executeAction(Config{DispatcherWorkDir: root, CommandTimeoutSec: 10}, Command{ID: "LIVE-1", Action: "dispatcher.tick"}, f)
	if res.Status != "ok" || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.LogicalCommand != "scripts\\run-tick.ps1" {
		t.Fatalf("logical=%q", res.LogicalCommand)
	}
	if len(f.specs) != 1 {
		t.Fatalf("runs=%d", len(f.specs))
	}
	spec := f.specs[0]
	if spec.exe != "powershell.exe" {
		t.Fatalf("exe=%q", spec.exe)
	}
	joined := strings.Join(spec.args, " ")
	if !strings.Contains(joined, "-File") || !strings.Contains(joined, "run-tick.ps1") {
		t.Fatalf("args=%q", joined)
	}
	if strings.Contains(joined, "-DryRun") || strings.Contains(joined, "--dry-run") {
		t.Fatalf("live tick must not contain dry-run: %q", joined)
	}
}

func TestDispatcherTestStillUsesDryRun(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "scripts", "run-tick.ps1"), []byte("# test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{code: 0}
	res := executeAction(Config{DispatcherWorkDir: root}, Command{ID: "DRY-1", Action: "dispatcher.test"}, f)
	if res.Status != "ok" || len(f.specs) != 1 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if !strings.Contains(strings.Join(f.specs[0].args, " "), "-DryRun") {
		t.Fatal("dispatcher.test must remain dry-run")
	}
}
