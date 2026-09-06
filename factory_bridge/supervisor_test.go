package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestExecutorHeartbeatHealthy(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := os.MkdirAll(filepath.Join(bridge, statusDirName), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	pid := 4242
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{
		PID: pid,
		HeartbeatAt: now.Add(-5 * time.Second).Format(time.RFC3339Nano),
	}); err != nil {
		t.Fatal(err)
	}
	if !executorHeartbeatHealthy(cfg, pid, now, now.Add(-time.Minute)) {
		t.Fatal("fresh heartbeat should be healthy")
	}
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{
		PID: pid,
		HeartbeatAt: now.Add(-2 * time.Minute).Format(time.RFC3339Nano),
	}); err != nil {
		t.Fatal(err)
	}
	if executorHeartbeatHealthy(cfg, pid, now, now.Add(-2*time.Minute)) {
		t.Fatal("stale heartbeat should be unhealthy")
	}
}

func TestExecutorGetsGracePeriodBeforeFirstHeartbeat(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := os.MkdirAll(filepath.Join(bridge, statusDirName), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if !executorHeartbeatHealthy(cfg, 1234, now, now.Add(-10*time.Second)) {
		t.Fatal("new executor should get a startup grace period")
	}
	if executorHeartbeatHealthy(cfg, 1234, now, now.Add(-2*time.Minute)) {
		t.Fatal("missing heartbeat after grace period should be unhealthy")
	}
}

func TestRestartDelayIsBounded(t *testing.T) {
	if got := restartDelay(1); got != time.Second {
		t.Fatalf("restartDelay(1)=%s", got)
	}
	if got := restartDelay(2); got != 2*time.Second {
		t.Fatalf("restartDelay(2)=%s", got)
	}
	if got := restartDelay(3); got != 5*time.Second {
		t.Fatalf("restartDelay(3)=%s", got)
	}
	if got := restartDelay(100); got != 10*time.Second {
		t.Fatalf("restartDelay(100)=%s", got)
	}
}
