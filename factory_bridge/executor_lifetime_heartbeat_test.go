package main

import (
	"os"
	"testing"
	"time"
)

func TestLifetimeHeartbeatRefreshesIdleExecutor(t *testing.T) {
	localAppData := t.TempDir()
	t.Setenv("LOCALAPPDATA", localAppData)
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}

	started := time.Now().Add(-time.Minute).Truncate(time.Second)
	stop := startExecutorLifetimeHeartbeat(cfg, started, 15*time.Millisecond)
	defer stop()

	localStatus, err := localRuntimeStatusPath(executorStatusFileName)
	if err != nil {
		t.Fatal(err)
	}
	before, err := readJSONFile[ExecutorStatus](localStatus)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(55 * time.Millisecond)
	after, err := readJSONFile[ExecutorStatus](localStatus)
	if err != nil {
		t.Fatal(err)
	}

	if after.PID != os.Getpid() {
		t.Fatalf("heartbeat pid=%d want=%d", after.PID, os.Getpid())
	}
	if after.StartedAt != before.StartedAt {
		t.Fatalf("heartbeat changed executor start: before=%s after=%s", before.StartedAt, after.StartedAt)
	}
	beforeBeat, err := time.Parse(time.RFC3339Nano, before.HeartbeatAt)
	if err != nil {
		t.Fatal(err)
	}
	afterBeat, err := time.Parse(time.RFC3339Nano, after.HeartbeatAt)
	if err != nil {
		t.Fatal(err)
	}
	if !afterBeat.After(beforeBeat) {
		t.Fatalf("idle heartbeat did not advance: before=%s after=%s", before.HeartbeatAt, after.HeartbeatAt)
	}
}
