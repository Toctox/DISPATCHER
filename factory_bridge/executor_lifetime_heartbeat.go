package main

import (
	"os"
	"time"
)

const executorLifetimeHeartbeatInterval = 15 * time.Second

// startExecutorLifetimeHeartbeat keeps a private executor heartbeat fresh for
// the entire process lifetime. The private heartbeat lives in LOCALAPPDATA and
// is intentionally independent from Drive, command polling, and action status.
func startExecutorLifetimeHeartbeat(cfg Config, started time.Time, interval time.Duration) func() {
	if interval <= 0 {
		interval = executorLifetimeHeartbeatInterval
	}

	_ = cfg // Drive config remains relevant to the executor, not to local health.
	_ = writeLocalExecutorStatus(started)
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				currentStarted := started
				if path, err := localRuntimeStatusPath(executorStatusFileName); err == nil {
					if state, readErr := readJSONFile[ExecutorStatus](path); readErr == nil && state.PID == os.Getpid() {
						if parsed, parseErr := time.Parse(time.RFC3339, state.StartedAt); parseErr == nil {
							currentStarted = parsed
						}
					}
				}
				_ = writeLocalExecutorStatus(currentStarted)
			case <-stop:
				return
			}
		}
	}()

	return func() {
		close(stop)
		<-done
	}
}

// Starting the private lifetime heartbeat before main enters the executor loop
// makes process supervision independent from Google Drive synchronization.
func init() {
	mode, err := selectedMode(os.Args[1:])
	if err != nil || mode != "executor" {
		return
	}
	cfgPath, err := configPath()
	if err != nil {
		return
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		return
	}
	if err := ensureBridgeDirs(cfg); err != nil {
		return
	}
	_ = startExecutorLifetimeHeartbeat(cfg, time.Now(), executorLifetimeHeartbeatInterval)
}
