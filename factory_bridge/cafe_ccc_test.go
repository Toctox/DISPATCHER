package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCafeCCCResultParsing(t *testing.T) {
	stdout := "noise\nCAFE_CCC_RESULT {\"state\":\"DONE\",\"summary\":\"ok\",\"enabledIEs\":3}\n"
	got := parseCafeCCCResult(stdout)
	if got == nil || got["state"] != "DONE" || got["summary"] != "ok" {
		t.Fatalf("unexpected payload: %#v", got)
	}
}

func TestCafeCCCInstalledCommitMustMatchMission(t *testing.T) {
	root := t.TempDir()
	t.Setenv("LOCALAPPDATA", root)
	stateDir := filepath.Join(root, "FactoryBridge", "state")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	installed := strings.Repeat("a", 40)
	data, _ := json.Marshal(installedRuntimeState{SourceCommit: installed})
	if err := os.WriteFile(filepath.Join(stateDir, "installed-runtime.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ensureFactoryBridgeInstalledCommit(Mission{TargetCommit: installed}); err != nil {
		t.Fatalf("matching commit rejected: %v", err)
	}
	if err := ensureFactoryBridgeInstalledCommit(Mission{TargetCommit: strings.Repeat("b", 40)}); err == nil {
		t.Fatal("expected mismatched installed commit to be rejected")
	}
}

func TestCafeCCCExecutorUsesOnlyFixedScriptAndMissionID(t *testing.T) {
	local := t.TempDir()
	t.Setenv("LOCALAPPDATA", local)
	work := t.TempDir()
	scripts := filepath.Join(work, "scripts")
	if err := os.MkdirAll(scripts, 0o755); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(scripts, "cafe-ccc-scan.ps1")
	if err := os.WriteFile(script, []byte("# fixed test script"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "CAFE_CCC_RESULT {\"state\":\"DONE\",\"summary\":\"scan ok\",\"enabledIEs\":2}\n", code: 0}
	mission := Mission{ID: "M-CAFE-1", TargetCommit: strings.Repeat("c", 40)}
	res := executeCafeCCCScan(Config{DispatcherWorkDir: work}, mission, time.Now(), f)
	if res.Status != "ok" || res.Output != "scan ok" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 1 {
		t.Fatalf("runner calls=%d", len(f.specs))
	}
	spec := f.specs[0]
	if spec.exe != "powershell.exe" {
		t.Fatalf("exe=%q", spec.exe)
	}
	joined := strings.Join(spec.args, " ")
	if !strings.Contains(joined, script) || !strings.Contains(joined, "-MissionId M-CAFE-1") {
		t.Fatalf("unexpected args: %q", joined)
	}
	if strings.Contains(strings.ToLower(joined), "-command") {
		t.Fatalf("arbitrary command surface detected: %q", joined)
	}
}
