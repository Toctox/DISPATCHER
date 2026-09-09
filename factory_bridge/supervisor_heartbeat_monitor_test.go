package main

import (
	"testing"
	"time"
)

func TestExecutorHeartbeatMonitorIgnoresTransientReadFailure(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}
	monitor := executorHeartbeatMonitor{pid: 1234}
	if !monitor.healthy(cfg, time.Now()) {
		t.Fatal("transient missing heartbeat file must not kill executor")
	}
}

func TestExecutorHeartbeatMonitorDoesNotRegressToOlderHeartbeat(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	monitor := executorHeartbeatMonitor{pid: 1234}

	fresh := now.Add(-time.Second)
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 1234, HeartbeatAt: fresh.Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if !monitor.healthy(cfg, now) {
		t.Fatal("fresh heartbeat must be healthy")
	}

	older := now.Add(-5 * time.Minute)
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 1234, HeartbeatAt: older.Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	if !monitor.healthy(cfg, now.Add(2*time.Second)) {
		t.Fatal("older/regressive status view must not erase a newer observed heartbeat")
	}
}

func TestExecutorHeartbeatMonitorRejectsValidSamePidStaleHeartbeat(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 1234, HeartbeatAt: now.Add(-2 * time.Minute).Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	monitor := executorHeartbeatMonitor{pid: 1234}
	if monitor.healthy(cfg, now) {
		t.Fatal("valid same-PID heartbeat older than threshold must be unhealthy")
	}
}

func TestExecutorHeartbeatMonitorIgnoresOtherPidStatus(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if err := writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{PID: 9999, HeartbeatAt: now.Add(-10 * time.Minute).Format(time.RFC3339Nano)}); err != nil {
		t.Fatal(err)
	}
	monitor := executorHeartbeatMonitor{pid: 1234}
	if !monitor.healthy(cfg, now) {
		t.Fatal("status from another PID must not be used to kill current executor")
	}
}
