package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeRunner struct {
	specs          []runSpec
	stdout, stderr string
	code           int
	err            error
}

func (f *fakeRunner) Run(_ context.Context, spec runSpec) (string, string, int, error) {
	f.specs = append(f.specs, spec)
	return f.stdout, f.stderr, f.code, f.err
}

func TestDispatcherTestUsesFixedLocalCommand(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "scripts", "run-tick.ps1"), []byte("# test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "dry run ok\n", stderr: "warn\n", code: 0}
	res := executeAction(Config{DispatcherWorkDir: root, CommandTimeoutSec: 10}, Command{ID: "T-1", Action: "dispatcher.test"}, f)
	if res.Status != "ok" || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Stdout != "dry run ok\n" || res.Stderr != "warn\n" {
		t.Fatalf("streams not preserved: %+v", res)
	}
	if res.LogicalCommand != "scripts\\run-tick.ps1 -DryRun" {
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
	if !strings.Contains(joined, "-File") || !strings.Contains(joined, "-DryRun") {
		t.Fatalf("args=%q", joined)
	}
}

func TestDriveCommandCannotInjectShell(t *testing.T) {
	p := filepath.Join(t.TempDir(), "cmd.json")
	// Extra command-bearing fields are rejected instead of interpreted.
	if err := os.WriteFile(p, []byte(`{"id":"X-1","action":"dispatcher.test","command":"whoami"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := decodeCommand(p); err == nil {
		t.Fatal("expected unknown field rejection")
	}
}

func TestIdempotenceExistingResultSkipsExecutionAndArchives(t *testing.T) {
	bridge := t.TempDir()
	for _, d := range []string{"01_COMMANDS", "02_RESULTS", "03_ARCHIVE"} {
		if err := os.MkdirAll(filepath.Join(bridge, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	commandPath := filepath.Join(bridge, "01_COMMANDS", "cmd.json")
	if err := os.WriteFile(commandPath, []byte(`{"id":"DUP-1","action":"bridge.ping"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	existing := filepath.Join(bridge, "02_RESULTS", "RESULT__DUP-1.json")
	if err := os.WriteFile(existing, []byte(`{"status":"ok"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{err: errors.New("must not run")}
	if err := processOne(Config{BridgeRoot: bridge}, commandPath, f); err != nil {
		t.Fatal(err)
	}
	if len(f.specs) != 0 {
		t.Fatalf("runner invoked %d times", len(f.specs))
	}
	entries, err := os.ReadDir(filepath.Join(bridge, "03_ARCHIVE"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("archive count=%d", len(entries))
	}
}

func TestFailureResultHasRequiredTraceFields(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "scripts", "run-tick.ps1"), []byte("#"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "out", stderr: "err", code: 7, err: errors.New("exit status 7")}
	res := executeAction(Config{DispatcherWorkDir: root}, Command{ID: "T-2", Action: "dispatcher.test"}, f)
	data, err := json.Marshal(res)
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	for _, key := range []string{`"stdout":"out"`, `"stderr":"err"`, `"exitCode":7`, `"durationMs":`, `"logicalCommand":`, `"startedAt":`, `"finishedAt":`, `"status":"failed"`} {
		if !strings.Contains(text, key) {
			t.Fatalf("missing %s in %s", key, text)
		}
	}
}

func TestLegacyDispatcherCommandConfigIsAcceptedButIgnored(t *testing.T) {
	bridge := t.TempDir()
	work := t.TempDir()
	if err := os.MkdirAll(filepath.Join(work, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(work, "scripts", "run-tick.ps1"), []byte("#"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	payload := fmt.Sprintf(`{"bridgeRoot":%q,"dispatcherWorkDir":%q,"dispatcherStart":"calc.exe","dispatcherTest":"whoami & calc.exe","allowGitPull":false,"commandTimeoutSec":10}`, bridge, work)
	if err := os.WriteFile(cfgPath, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		t.Fatalf("legacy config should load: %v", err)
	}
	f := &fakeRunner{code: 0}
	res := executeAction(cfg, Command{ID: "LEGACY-1", Action: "dispatcher.test"}, f)
	if res.Status != "ok" || len(f.specs) != 1 {
		t.Fatalf("unexpected result: %+v", res)
	}
	joined := strings.Join(f.specs[0].args, " ")
	if strings.Contains(joined, "whoami") || strings.Contains(joined, "calc.exe") {
		t.Fatalf("legacy command leaked into execution: %q", joined)
	}
	if !strings.Contains(joined, "run-tick.ps1") || !strings.Contains(joined, "-DryRun") {
		t.Fatalf("fixed dispatcher test missing: %q", joined)
	}
}
