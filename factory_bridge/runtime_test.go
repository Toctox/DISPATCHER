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
		{[]string{"--mode", "supervisor"}, "supervisor", true},
		{[]string{"--mode", "SUPERVISOR"}, "supervisor", true},
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

func TestHeartbeatStateUsesTolerantWindows(t *testing.T) {
	now := time.Now()
	cases := []struct {
		age  time.Duration
		want string
	}{
		{time.Second, "ONLINE"},
		{29 * time.Second, "ONLINE"},
		{45 * time.Second, "STALE"},
		{89 * time.Second, "STALE"},
		{91 * time.Second, "OFFLINE"},
	}
	for _, tc := range cases {
		got := heartbeatState(now, now.Add(-tc.age).Format(time.RFC3339Nano), executorOnlineThreshold, executorStaleThreshold)
		if got != tc.want {
			t.Fatalf("age=%s got=%s want=%s", tc.age, got, tc.want)
		}
	}
}

func TestPanelSnapshotIsReadOnlyAndReportsSupervisorAndExecutor(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge, PollIntervalMs: 1000}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}

	now := time.Now()
	if err := writeJSONAtomic(statusPath(cfg, supervisorStatusFileName), SupervisorStatus{
		BridgeVersion: bridgeVersion,
		PID: 100,
		HeartbeatAt: now.Add(-2 * time.Second).Format(time.RFC3339Nano),
		RestartCount: 3,
	}); err != nil {
		t.Fatal(err)
	}
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{
		BridgeVersion: bridgeVersion,
		PID: 4321,
		StartedAt: now.Add(-time.Minute).Format(time.RFC3339),
		HeartbeatAt: now.Add(-time.Second).Format(time.RFC3339Nano),
	}); err != nil {
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
	if snapshot.SupervisorState != "ONLINE" || snapshot.SupervisorPID != 100 || snapshot.SupervisorRestarts != 3 {
		t.Fatalf("unexpected supervisor snapshot: %+v", snapshot)
	}
	if !snapshot.ExecutorOnline || snapshot.ExecutorState != "ONLINE" || snapshot.ExecutorPID != 4321 || snapshot.PendingCount != 1 {
		t.Fatalf("unexpected executor snapshot: %+v", snapshot)
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
	archive, err := os.ReadDir(filepath.Join(bridge, "03_ARCHIVE"))
	if err != nil {
		t.Fatal(err)
	}
	if len(archive) != 1 || !strings.Contains(archive[0].Name(), "OBS-2") {
		t.Fatalf("archive mismatch: %+v", archive)
	}
}

func TestStaleAndOfflineHeartbeatClassification(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge, PollIntervalMs: 1000}
	if err := os.MkdirAll(filepath.Join(bridge, statusDirName), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 99, HeartbeatAt: now.Add(-45 * time.Second).Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if got := panelSnapshot(cfg, now).ExecutorState; got != "STALE" {
		t.Fatalf("got=%s want=STALE", got)
	}
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 99, HeartbeatAt: now.Add(-2 * time.Minute).Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if got := panelSnapshot(cfg, now).ExecutorState; got != "OFFLINE" {
		t.Fatalf("got=%s want=OFFLINE", got)
	}
}
