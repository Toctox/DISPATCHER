package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSelectedMode(t *testing.T) {
	cases := []struct {
		args []string
		want string
		ok   bool
	}{
		{nil, "executor", true},
		{[]string{"--mode", "executor"}, "executor", true},
		{[]string{"--mode", "panel"}, "panel", true},
		{[]string{"--mode", "PANEL"}, "panel", true},
		{[]string{"--mode"}, "", false},
		{[]string{"--mode", "shell"}, "", false},
		{[]string{"--command", "whoami"}, "", false},
	}
	for _, tc := range cases {
		got, err := selectedMode(tc.args)
		if tc.ok && err != nil {
			t.Fatalf("args=%v unexpected error: %v", tc.args, err)
		}
		if !tc.ok && err == nil {
			t.Fatalf("args=%v expected error", tc.args)
		}
		if tc.ok && got != tc.want {
			t.Fatalf("args=%v got=%q want=%q", tc.args, got, tc.want)
		}
	}
}

func TestPanelSnapshotIsReadOnlyAndReportsExecutorState(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge, PollIntervalMs: 1000}
	for _, name := range []string{statusDirName, "01_COMMANDS", "02_RESULTS", "03_ARCHIVE"} {
		if err := os.MkdirAll(filepath.Join(bridge, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	now := time.Now()
	status := ExecutorStatus{BridgeVersion: bridgeVersion, PID: 4321, StartedAt: now.Add(-time.Minute).Format(time.RFC3339), HeartbeatAt: now.Add(-time.Second).Format(time.RFC3339Nano)}
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), status); err != nil {
		t.Fatal(err)
	}
	if err := writeJSONAtomic(statusPath(cfg, lastCommandFileName), Command{ID: "OBS-1", Action: "bridge.ping"}); err != nil {
		t.Fatal(err)
	}
	code := 0
	if err := writeJSONAtomic(statusPath(cfg, lastResultFileName), Result{ID: "OBS-1", Action: "bridge.ping", Status: "ok", ExitCode: &code, DurationMs: 3}); err != nil {
		t.Fatal(err)
	}
	pending := filepath.Join(bridge, "01_COMMANDS", "pending.json")
	payload := []byte(`{"id":"PENDING-1","action":"bridge.ping"}`)
	if err := os.WriteFile(pending, payload, 0o600); err != nil {
		t.Fatal(err)
	}

	snapshot := panelSnapshot(cfg, now)
	if !snapshot.ExecutorOnline || snapshot.ExecutorPID != 4321 || snapshot.PendingCount != 1 {
		t.Fatalf("unexpected snapshot: %+v", snapshot)
	}
	if snapshot.LastCommand == nil || snapshot.LastCommand.ID != "OBS-1" {
		t.Fatalf("last command missing: %+v", snapshot.LastCommand)
	}
	if snapshot.LastResult == nil || snapshot.LastResult.Status != "ok" {
		t.Fatalf("last result missing: %+v", snapshot.LastResult)
	}
	got, err := os.ReadFile(pending)
	if err != nil {
		t.Fatalf("panel snapshot modified/deleted command: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatal("panel snapshot changed command contents")
	}
	entries, err := os.ReadDir(filepath.Join(bridge, "02_RESULTS"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("panel must not create results; count=%d", len(entries))
	}
}

func TestProcessOneObservedPreservesExecutionEvidenceAndArchive(t *testing.T) {
	bridge := t.TempDir()
	work := t.TempDir()
	cfg := Config{BridgeRoot: bridge, DispatcherWorkDir: work, PollIntervalMs: 1000, CommandTimeoutSec: 10}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(work, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(work, "scripts", "run-tick.ps1"), []byte("# test"), 0o600); err != nil {
		t.Fatal(err)
	}
	commandPath := filepath.Join(bridge, "01_COMMANDS", "cmd.json")
	if err := os.WriteFile(commandPath, []byte(`{"id":"OBS-2","action":"dispatcher.test"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "stdout evidence\n", stderr: "stderr evidence\n", code: 0}
	if err := processOneObserved(cfg, commandPath, f); err != nil {
		t.Fatal(err)
	}

	data, err := os.ReadFile(filepath.Join(bridge, "02_RESULTS", "RESULT__OBS-2.json"))
	if err != nil {
		t.Fatal(err)
	}
	var res Result
	if err := json.Unmarshal(data, &res); err != nil {
		t.Fatal(err)
	}
	if res.Status != "ok" || res.ExitCode == nil || *res.ExitCode != 0 || res.Stdout != "stdout evidence\n" || res.Stderr != "stderr evidence\n" {
		t.Fatalf("execution evidence incomplete: %+v", res)
	}
	if res.FinishedAt == "" || res.StartedAt == "" || res.DurationMs < 0 {
		t.Fatalf("timing evidence incomplete: %+v", res)
	}
	if _, err := os.Stat(statusPath(cfg, lastCommandFileName)); err != nil {
		t.Fatalf("last command status missing: %v", err)
	}
	if _, err := os.Stat(statusPath(cfg, lastResultFileName)); err != nil {
		t.Fatalf("last result status missing: %v", err)
	}
	archive, err := os.ReadDir(filepath.Join(bridge, "03_ARCHIVE"))
	if err != nil {
		t.Fatal(err)
	}
	if len(archive) != 1 || !strings.Contains(archive[0].Name(), "OBS-2") {
		t.Fatalf("archive mismatch: %+v", archive)
	}
}

func TestStaleHeartbeatReportsOffline(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge, PollIntervalMs: 1000}
	if err := os.MkdirAll(filepath.Join(bridge, statusDirName), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 99, HeartbeatAt: now.Add(-10 * time.Second).Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if panelSnapshot(cfg, now).ExecutorOnline {
		t.Fatal("stale executor heartbeat must report OFFLINE")
	}
}
