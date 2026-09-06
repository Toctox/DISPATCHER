package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	supervisorHeartbeatInterval = 5 * time.Second
	executorHangThreshold       = 90 * time.Second
	bridgeRetryInterval         = 5 * time.Second
)

func writeSupervisorStatus(cfg Config, started time.Time, executorPID, restartCount int, lastStart, lastExit time.Time, lastErr string) error {
	status := SupervisorStatus{
		BridgeVersion: bridgeVersion,
		PID:           os.Getpid(),
		StartedAt:     started.Format(time.RFC3339),
		HeartbeatAt:   time.Now().Format(time.RFC3339Nano),
		ExecutorPID:   executorPID,
		RestartCount:  restartCount,
		LastError:     strings.TrimSpace(lastErr),
	}
	if !lastStart.IsZero() {
		status.LastExecutorStartAt = lastStart.Format(time.RFC3339Nano)
	}
	if !lastExit.IsZero() {
		status.LastExecutorExitAt = lastExit.Format(time.RFC3339Nano)
	}
	return writeJSONAtomic(statusPath(cfg, supervisorStatusFileName), status)
}

func waitForBridge(cfg Config, started time.Time, restartCount int, lastErr string) {
	for {
		if err := ensureBridgeDirs(cfg); err == nil {
			return
		} else {
			lastErr = fmt.Sprintf("bridge unavailable: %v", err)
			fmt.Fprintln(os.Stderr, lastErr)
		}
		_ = writeSupervisorStatus(cfg, started, 0, restartCount, time.Time{}, time.Time{}, lastErr)
		time.Sleep(bridgeRetryInterval)
	}
}

func executorHeartbeatHealthy(cfg Config, executorPID int, now time.Time, launchedAt time.Time) bool {
	state, err := readJSONFile[ExecutorStatus](statusPath(cfg, executorStatusFileName))
	if err != nil {
		return now.Sub(launchedAt) <= executorHangThreshold
	}
	if state.PID != executorPID {
		return now.Sub(launchedAt) <= executorHangThreshold
	}
	parsed, err := time.Parse(time.RFC3339Nano, state.HeartbeatAt)
	if err != nil {
		return now.Sub(launchedAt) <= executorHangThreshold
	}
	age := now.Sub(parsed)
	if age < 0 {
		age = 0
	}
	return age <= executorHangThreshold
}

func restartDelay(restartCount int) time.Duration {
	if restartCount <= 1 {
		return time.Second
	}
	if restartCount == 2 {
		return 2 * time.Second
	}
	if restartCount == 3 {
		return 5 * time.Second
	}
	return 10 * time.Second
}

func runSupervisor(cfg Config) error {
	started := time.Now()
	restartCount := 0
	lastErr := ""
	var lastStart, lastExit time.Time

	fmt.Printf("FactoryBridge %s SUPERVISOR\n", bridgeVersion)
	fmt.Printf("Bridge root: %s\n", cfg.BridgeRoot)

	for {
		waitForBridge(cfg, started, restartCount, lastErr)

		exePath, err := os.Executable()
		if err != nil {
			return fmt.Errorf("resolve executable: %w", err)
		}
		cmd := exec.Command(exePath, "--mode", "executor")
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Start(); err != nil {
			restartCount++
			lastErr = fmt.Sprintf("executor start failed: %v", err)
			lastExit = time.Now()
			_ = writeSupervisorStatus(cfg, started, 0, restartCount, lastStart, lastExit, lastErr)
			time.Sleep(restartDelay(restartCount))
			continue
		}

		lastStart = time.Now()
		executorPID := cmd.Process.Pid
		lastErr = ""
		_ = writeSupervisorStatus(cfg, started, executorPID, restartCount, lastStart, lastExit, lastErr)
		fmt.Printf("Executor started pid=%d\n", executorPID)

		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		ticker := time.NewTicker(supervisorHeartbeatInterval)
		monitoring := true
		for monitoring {
			select {
			case err := <-done:
				ticker.Stop()
				lastExit = time.Now()
				restartCount++
				if err != nil {
					lastErr = fmt.Sprintf("executor exited: %v", err)
				} else {
					lastErr = "executor exited"
				}
				_ = writeSupervisorStatus(cfg, started, 0, restartCount, lastStart, lastExit, lastErr)
				fmt.Fprintln(os.Stderr, lastErr)
				monitoring = false

			case <-ticker.C:
				now := time.Now()
				_ = writeSupervisorStatus(cfg, started, executorPID, restartCount, lastStart, lastExit, lastErr)
				if !executorHeartbeatHealthy(cfg, executorPID, now, lastStart) {
					lastErr = fmt.Sprintf("executor heartbeat stale for more than %s; restarting", executorHangThreshold)
					fmt.Fprintln(os.Stderr, lastErr)
					_ = cmd.Process.Kill()
				}
			}
		}
		time.Sleep(restartDelay(restartCount))
	}
}
