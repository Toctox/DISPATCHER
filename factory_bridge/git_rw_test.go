package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGitPullAndPushPermissionsLoadFromConfig(t *testing.T) {
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	payload := `{"bridgeRoot":"C:\\bridge","dispatcherWorkDir":"C:\\dispatcher","allowGitPull":true,"allowGitPush":true}`
	if err := os.WriteFile(cfgPath, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.AllowGitPull || !cfg.AllowGitPush {
		t.Fatalf("permissions not loaded: pull=%v push=%v", cfg.AllowGitPull, cfg.AllowGitPush)
	}
}

func TestGitPushFailsClosedWhenDisabled(t *testing.T) {
	f := &fakeRunner{code: 0}
	res := executeAction(Config{DispatcherWorkDir: t.TempDir()}, Command{ID: "PUSH-OFF", Action: "git.push"}, f)
	if res.Status != "failed" || !strings.Contains(res.Error, "allowGitPush=false") {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 0 {
		t.Fatalf("runner invoked while push disabled: %d", len(f.specs))
	}
}

func TestGitPushUsesFixedCommandWhenEnabled(t *testing.T) {
	root := t.TempDir()
	f := &fakeRunner{stdout: "ok", code: 0}
	res := executeAction(Config{DispatcherWorkDir: root, AllowGitPush: true}, Command{ID: "PUSH-ON", Action: "git.push"}, f)
	if res.Status != "ok" || len(f.specs) != 1 {
		t.Fatalf("unexpected result: %+v runs=%d", res, len(f.specs))
	}
	spec := f.specs[0]
	if spec.exe != "git.exe" || spec.dir != root {
		t.Fatalf("unexpected spec: %+v", spec)
	}
	joined := strings.Join(spec.args, " ")
	if joined != "-C "+root+" push --porcelain" {
		t.Fatalf("unexpected git push args: %q", joined)
	}
}
