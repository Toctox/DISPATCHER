package main

import (
	"os"
	"time"
)

const executorLifetimeHeartbeatInterval = 15 * time.Second

// startExecutorLifetimeHeartbeat keeps executor.json fresh for the entire
// executor process lifetime. It is intentionally independent from both the
// command polling interval and the action heartbeat, so an idle executor
// cannot be killed merely because the polling loop is sleeping.
func startExecutorLifetimeHeartbeat(cfg Config, started time.Time, interval time.Duration) func() {
	if interval <= 0 {
		interval = executorLifetimeHeartbeatInterval
	}

	_ = writeExecutorStatus(cfg, started)
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
				if state, err := readJSONFile[ExecutorStatus](statusPath(cfg, executorStatusFileName)); err == nil && state.PID == os.Getpid() {
					if parsed, parseErr := time.Parse(time.RFC3339, state.StartedAt); parseErr == nil {
						currentStarted = parsed
					}
				}
				_ = writeExecutorStatus(cfg, currentStarted)
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

// The executor used to refresh its idle heartbeat only when its polling loop
// returned to the top. Starting the lifetime heartbeat before main enters the
// executor loop removes that coupling without widening the Drive command
// protocol or changing the supervisor's hang threshold.
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
