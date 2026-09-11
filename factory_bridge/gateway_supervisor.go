package main

import (
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	gatewayProbeInterval     = 5 * time.Second
	gatewayProbeTimeout      = time.Second
	gatewayFailureThreshold  = 3
	gatewayCrashWindow       = 2 * time.Minute
	gatewayCrashLimit        = 5
	gatewayQuarantineDuration = 2 * time.Minute
)

type gatewaySupervisorState struct {
	Status          string `json:"status"`
	ObservedAt      string `json:"observedAt"`
	RestartCount    int    `json:"restartCount"`
	ConsecutiveFail int    `json:"consecutiveProbeFailures"`
	LastError       string `json:"lastError,omitempty"`
	LastStartAt     string `json:"lastStartAt,omitempty"`
	LastExitAt      string `json:"lastExitAt,omitempty"`
	QuarantineUntil string `json:"quarantineUntil,omitempty"`
}

type gatewayCrashTracker struct {
	crashes         []time.Time
	quarantineUntil time.Time
}

func (t *gatewayCrashTracker) recordExit(now time.Time) bool {
	cutoff := now.Add(-gatewayCrashWindow)
	kept := t.crashes[:0]
	for _, crash := range t.crashes {
		if !crash.Before(cutoff) {
			kept = append(kept, crash)
		}
	}
	t.crashes = append(kept, now)
	if len(t.crashes) >= gatewayCrashLimit {
		t.quarantineUntil = now.Add(gatewayQuarantineDuration)
		return true
	}
	return false
}

func (t *gatewayCrashTracker) quarantined(now time.Time) bool {
	if t.quarantineUntil.IsZero() {
		return false
	}
	if now.Before(t.quarantineUntil) {
		return true
	}
	t.quarantineUntil = time.Time{}
	t.crashes = nil
	return false
}

func gatewaySupervisorStatePath() (string, error) {
	dir, err := localRuntimeStateDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "gateway-supervisor.json"), nil
}

func writeGatewaySupervisorState(state gatewaySupervisorState) {
	path, err := gatewaySupervisorStatePath()
	if err == nil {
		_ = writeJSONAtomic(path, state)
	}
}

func gatewayReachable() bool {
	conn, err := net.DialTimeout("tcp", gatewayListenAddress, gatewayProbeTimeout)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func superviseGateway(cfg Config) {
	// gateway.go starts the initial instance. This watchdog owns recovery if that
	// instance disappears, without creating a second listener during healthy boot.
	time.Sleep(3 * time.Second)
	tracker := gatewayCrashTracker{}
	restarts := 0
	consecutiveFailures := 0
	var lastStart, lastExit time.Time
	lastErr := ""

	for {
		now := time.Now()
		if gatewayReachable() {
			consecutiveFailures = 0
			lastErr = ""
			tracker.quarantineUntil = time.Time{}
			writeGatewaySupervisorState(gatewaySupervisorState{Status: "OK", ObservedAt: now.Format(time.RFC3339Nano), RestartCount: restarts, LastStartAt: formatGatewayTime(lastStart), LastExitAt: formatGatewayTime(lastExit)})
			time.Sleep(gatewayProbeInterval)
			continue
		}

		consecutiveFailures++
		if tracker.quarantined(now) {
			writeGatewaySupervisorState(gatewaySupervisorState{Status: "QUARANTINED", ObservedAt: now.Format(time.RFC3339Nano), RestartCount: restarts, ConsecutiveFail: consecutiveFailures, LastError: lastErr, LastStartAt: formatGatewayTime(lastStart), LastExitAt: formatGatewayTime(lastExit), QuarantineUntil: tracker.quarantineUntil.Format(time.RFC3339Nano)})
			time.Sleep(gatewayProbeInterval)
			continue
		}
		if consecutiveFailures < gatewayFailureThreshold {
			writeGatewaySupervisorState(gatewaySupervisorState{Status: "DEGRADED", ObservedAt: now.Format(time.RFC3339Nano), RestartCount: restarts, ConsecutiveFail: consecutiveFailures, LastError: "gateway probe failed", LastStartAt: formatGatewayTime(lastStart), LastExitAt: formatGatewayTime(lastExit)})
			time.Sleep(gatewayProbeInterval)
			continue
		}

		restarts++
		consecutiveFailures = 0
		lastStart = time.Now()
		writeGatewaySupervisorState(gatewaySupervisorState{Status: "RESTARTING", ObservedAt: lastStart.Format(time.RFC3339Nano), RestartCount: restarts, LastStartAt: lastStart.Format(time.RFC3339Nano), LastExitAt: formatGatewayTime(lastExit)})

		done := make(chan error, 1)
		go func() { done <- runGateway(cfg) }()
		ticker := time.NewTicker(gatewayProbeInterval)
		running := true
		for running {
			select {
			case err := <-done:
				ticker.Stop()
				lastExit = time.Now()
				if err != nil && !errors.Is(err, http.ErrServerClosed) {
					lastErr = sanitizeRemoteText(err.Error())
				} else {
					lastErr = "gateway exited"
				}
				quarantined := tracker.recordExit(lastExit)
				status := "DEGRADED"
				quarantineUntil := ""
				if quarantined {
					status = "QUARANTINED"
					quarantineUntil = tracker.quarantineUntil.Format(time.RFC3339Nano)
				}
				writeGatewaySupervisorState(gatewaySupervisorState{Status: status, ObservedAt: lastExit.Format(time.RFC3339Nano), RestartCount: restarts, LastError: lastErr, LastStartAt: formatGatewayTime(lastStart), LastExitAt: lastExit.Format(time.RFC3339Nano), QuarantineUntil: quarantineUntil})
				running = false
			case observed := <-ticker.C:
				writeGatewaySupervisorState(gatewaySupervisorState{Status: "OK", ObservedAt: observed.Format(time.RFC3339Nano), RestartCount: restarts, LastStartAt: formatGatewayTime(lastStart), LastExitAt: formatGatewayTime(lastExit)})
			}
		}
		time.Sleep(restartDelay(restarts))
	}
}

func formatGatewayTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.Format(time.RFC3339Nano)
}

func init() {
	mode, err := selectedMode(os.Args[1:])
	if err != nil || mode != "supervisor" {
		return
	}
	cfgPath, err := configPath()
	if err != nil {
		return
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil || strings.TrimSpace(cfg.BridgeRoot) == "" {
		return
	}
	go superviseGateway(cfg)
}
