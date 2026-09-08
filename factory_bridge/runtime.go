package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	statusDirName             = "00_STATUS"
	executorStatusFileName    = "executor.json"
	supervisorStatusFileName  = "supervisor.json"
	lastCommandFileName       = "last_command.json"
	lastResultFileName        = "last_result.json"
	executorOnlineThreshold   = 30 * time.Second
	executorStaleThreshold    = 90 * time.Second
	supervisorOnlineThreshold = 30 * time.Second
	supervisorStaleThreshold  = 90 * time.Second
	actionHeartbeatInterval   = 10 * time.Second
)

type ExecutorStatus struct {
	BridgeVersion string `json:"bridgeVersion"`
	PID           int    `json:"pid"`
	StartedAt     string `json:"startedAt"`
	HeartbeatAt   string `json:"heartbeatAt"`
}

type SupervisorStatus struct {
	BridgeVersion       string `json:"bridgeVersion"`
	PID                 int    `json:"pid"`
	StartedAt           string `json:"startedAt"`
	HeartbeatAt         string `json:"heartbeatAt"`
	ExecutorPID         int    `json:"executorPid,omitempty"`
	RestartCount        int    `json:"restartCount"`
	LastExecutorStartAt string `json:"lastExecutorStartAt,omitempty"`
	LastExecutorExitAt  string `json:"lastExecutorExitAt,omitempty"`
	LastError           string `json:"lastError,omitempty"`
}

type PanelSnapshot struct {
	BridgeVersion       string
	BridgeRoot          string
	SupervisorState     string
	SupervisorPID       int
	SupervisorHeartbeat string
	SupervisorRestarts  int
	ExecutorState       string
	ExecutorOnline      bool
	ExecutorPID         int
	HeartbeatAt         string
	PendingCount        int
	LastCommand         *Command
	LastResult          *Result
}

func ensureBridgeDirs(cfg Config) error {
	for _, name := range []string{statusDirName, "01_COMMANDS", "02_RESULTS", "03_ARCHIVE"} {
		if err := os.MkdirAll(filepath.Join(cfg.BridgeRoot, name), 0o755); err != nil {
			return err
		}
	}
	return nil
}

func statusPath(cfg Config, name string) string {
	return filepath.Join(cfg.BridgeRoot, statusDirName, name)
}

func writeExecutorStatus(cfg Config, started time.Time) error {
	now := time.Now()
	return writeJSONAtomic(statusPath(cfg, executorStatusFileName), ExecutorStatus{
		BridgeVersion: bridgeVersion,
		PID:           os.Getpid(),
		StartedAt:     started.Format(time.RFC3339),
		HeartbeatAt:   now.Format(time.RFC3339Nano),
	})
}

// startExecutorActionHeartbeat keeps executor.json fresh while a single
// allowlisted action is running. Without this, the executor loop cannot touch
// its heartbeat until the action returns and the supervisor may incorrectly
// classify a healthy long-running build/test as hung after 90 seconds.
func startExecutorActionHeartbeat(cfg Config, interval time.Duration) func() {
	if interval <= 0 {
		interval = actionHeartbeatInterval
	}

	started := time.Now()
	if state, err := readJSONFile[ExecutorStatus](statusPath(cfg, executorStatusFileName)); err == nil && state.PID == os.Getpid() {
		if parsed, parseErr := time.Parse(time.RFC3339, state.StartedAt); parseErr == nil {
			started = parsed
		}
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
				_ = writeExecutorStatus(cfg, started)
			case <-stop:
				return
			}
		}
	}()

	return func() {
		close(stop)
		<-done
		_ = writeExecutorStatus(cfg, started)
	}
}

func observeCommand(cfg Config, path string) {
	cmd, err := decodeCommand(path)
	if err != nil {
		return
	}
	_ = writeJSONAtomic(statusPath(cfg, lastCommandFileName), cmd)
}

func observeResult(cfg Config, commandPath string) {
	cmd, err := decodeCommand(commandPath)
	if err != nil {
		return
	}
	data, err := os.ReadFile(filepath.Join(cfg.BridgeRoot, "02_RESULTS", "RESULT__"+cmd.ID+".json"))
	if err != nil {
		return
	}
	var res Result
	if json.Unmarshal(data, &res) != nil {
		return
	}
	_ = writeJSONAtomic(statusPath(cfg, lastResultFileName), res)
}

func processOneObserved(cfg Config, path string, r runner) error {
	observeCommand(cfg, path)
	cmd, decodeErr := decodeCommand(path)
	stopHeartbeat := startExecutorActionHeartbeat(cfg, actionHeartbeatInterval)
	err := processOne(cfg, path, r)
	stopHeartbeat()
	if decodeErr == nil {
		data, readErr := os.ReadFile(filepath.Join(cfg.BridgeRoot, "02_RESULTS", "RESULT__"+cmd.ID+".json"))
		if readErr == nil {
			var res Result
			if json.Unmarshal(data, &res) == nil {
				_ = writeJSONAtomic(statusPath(cfg, lastResultFileName), res)
			}
		}
	}
	return err
}

func runExecutor(cfg Config) error {
	if err := ensureBridgeDirs(cfg); err != nil {
		return err
	}
	commandsDir := filepath.Join(cfg.BridgeRoot, "01_COMMANDS")
	started := time.Now()
	fmt.Printf("FactoryBridge %s EXECUTOR. Allowlisted commands only; no arbitrary shell.\n", bridgeVersion)
	fmt.Printf("Watching: %s\n", commandsDir)

	for {
		if err := writeExecutorStatus(cfg, started); err != nil {
			fmt.Fprintf(os.Stderr, "heartbeat error: %v\n", err)
		}
		entries, err := os.ReadDir(commandsDir)
		if err != nil {
			fmt.Fprintf(os.Stderr, "command directory error: %v\n", err)
		} else {
			sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
			for _, entry := range entries {
				if entry.IsDir() || !strings.EqualFold(filepath.Ext(entry.Name()), ".json") {
					continue
				}
				path := filepath.Join(commandsDir, entry.Name())
				if err := processOneObserved(cfg, path, osRunner{}); err != nil {
					fmt.Fprintf(os.Stderr, "process error %s: %v\n", entry.Name(), err)
				}
				_ = writeExecutorStatus(cfg, started)
			}
		}
		time.Sleep(time.Duration(cfg.PollIntervalMs) * time.Millisecond)
	}
}

func readJSONFile[T any](path string) (*T, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var value T
	if err := json.Unmarshal(data, &value); err != nil {
		return nil, err
	}
	return &value, nil
}

func heartbeatState(now time.Time, heartbeat string, onlineThreshold, staleThreshold time.Duration) string {
	parsed, err := time.Parse(time.RFC3339Nano, heartbeat)
	if err != nil {
		return "OFFLINE"
	}
	age := now.Sub(parsed)
	if age < 0 {
		age = 0
	}
	if age <= onlineThreshold {
		return "ONLINE"
	}
	if age <= staleThreshold {
		return "STALE"
	}
	return "OFFLINE"
}

func panelSnapshot(cfg Config, now time.Time) PanelSnapshot {
	s := PanelSnapshot{BridgeVersion: bridgeVersion, BridgeRoot: cfg.BridgeRoot, ExecutorState: "OFFLINE", SupervisorState: "OFFLINE"}
	if state, err := readJSONFile[SupervisorStatus](statusPath(cfg, supervisorStatusFileName)); err == nil {
		s.SupervisorPID = state.PID
		s.SupervisorHeartbeat = state.HeartbeatAt
		s.SupervisorRestarts = state.RestartCount
		s.SupervisorState = heartbeatState(now, state.HeartbeatAt, supervisorOnlineThreshold, supervisorStaleThreshold)
	}
	if state, err := readJSONFile[ExecutorStatus](statusPath(cfg, executorStatusFileName)); err == nil {
		s.ExecutorPID = state.PID
		s.HeartbeatAt = state.HeartbeatAt
		s.ExecutorState = heartbeatState(now, state.HeartbeatAt, executorOnlineThreshold, executorStaleThreshold)
		s.ExecutorOnline = s.ExecutorState == "ONLINE"
	}
	if cmd, err := readJSONFile[Command](statusPath(cfg, lastCommandFileName)); err == nil {
		s.LastCommand = cmd
	}
	if res, err := readJSONFile[Result](statusPath(cfg, lastResultFileName)); err == nil {
		s.LastResult = res
	}
	if entries, err := os.ReadDir(filepath.Join(cfg.BridgeRoot, "01_COMMANDS")); err == nil {
		for _, entry := range entries {
			if !entry.IsDir() && strings.EqualFold(filepath.Ext(entry.Name()), ".json") {
				s.PendingCount++
			}
		}
	}
	return s
}

func renderPanel(s PanelSnapshot) string {
	var b strings.Builder
	fmt.Fprintf(&b, "FACTORY BRIDGE %s - READ ONLY PANEL\n", s.BridgeVersion)
	fmt.Fprintf(&b, "Bridge root: %s\n", s.BridgeRoot)
	fmt.Fprintf(&b, "Supervisor: %s", s.SupervisorState)
	if s.SupervisorPID != 0 {
		fmt.Fprintf(&b, " (pid=%d restarts=%d)", s.SupervisorPID, s.SupervisorRestarts)
	}
	b.WriteString("\n")
	fmt.Fprintf(&b, "Supervisor heartbeat: %s\n", valueOrDash(s.SupervisorHeartbeat))
	fmt.Fprintf(&b, "Executor: %s", s.ExecutorState)
	if s.ExecutorPID != 0 {
		fmt.Fprintf(&b, " (pid=%d)", s.ExecutorPID)
	}
	b.WriteString("\n")
	fmt.Fprintf(&b, "Executor heartbeat: %s\n", valueOrDash(s.HeartbeatAt))
	fmt.Fprintf(&b, "Pending commands: %d\n", s.PendingCount)
	b.WriteString("\nLAST COMMAND\n")
	if s.LastCommand == nil {
		b.WriteString("  -\n")
	} else {
		fmt.Fprintf(&b, "  id=%s action=%s\n", s.LastCommand.ID, s.LastCommand.Action)
	}
	b.WriteString("\nLAST RESULT\n")
	if s.LastResult == nil {
		b.WriteString("  -\n")
	} else {
		fmt.Fprintf(&b, "  id=%s action=%s status=%s durationMs=%d\n", s.LastResult.ID, s.LastResult.Action, s.LastResult.Status, s.LastResult.DurationMs)
		if s.LastResult.ExitCode != nil {
			fmt.Fprintf(&b, "  exitCode=%d\n", *s.LastResult.ExitCode)
		}
		if s.LastResult.Stdout != "" {
			fmt.Fprintf(&b, "  stdout=%s\n", compact(s.LastResult.Stdout, 1200))
		}
		if s.LastResult.Stderr != "" {
			fmt.Fprintf(&b, "  stderr=%s\n", compact(s.LastResult.Stderr, 1200))
		}
		if s.LastResult.Error != "" {
			fmt.Fprintf(&b, "  error=%s\n", compact(s.LastResult.Error, 1200))
		}
	}
	b.WriteString("\nPanel is read-only. It never executes commands. Ctrl+C to close.\n")
	return b.String()
}

func valueOrDash(value string) string {
	if strings.TrimSpace(value) == "" {
		return "-"
	}
	return value
}

func compact(value string, max int) string {
	value = strings.TrimSpace(value)
	if len(value) <= max {
		return value
	}
	return value[:max] + "...[truncated for panel]"
}

func panelStateKey(s PanelSnapshot) string {
	value := struct {
		BridgeVersion      string
		BridgeRoot         string
		SupervisorState    string
		SupervisorPID      int
		SupervisorRestarts int
		ExecutorState      string
		ExecutorPID        int
		PendingCount       int
		LastCommand        *Command
		LastResult         *Result
	}{
		BridgeVersion:      s.BridgeVersion,
		BridgeRoot:         s.BridgeRoot,
		SupervisorState:    s.SupervisorState,
		SupervisorPID:      s.SupervisorPID,
		SupervisorRestarts: s.SupervisorRestarts,
		ExecutorState:      s.ExecutorState,
		ExecutorPID:        s.ExecutorPID,
		PendingCount:       s.PendingCount,
		LastCommand:        s.LastCommand,
		LastResult:         s.LastResult,
	}
	data, _ := json.Marshal(value)
	return string(data)
}

func runPanel(cfg Config) error {
	refresh := cfg.PollIntervalMs
	if refresh < 500 {
		refresh = 500
	}
	lastKey := ""
	for {
		snapshot := panelSnapshot(cfg, time.Now())
		key := panelStateKey(snapshot)
		if key != lastKey {
			if lastKey != "" {
				fmt.Print("\n--- PANEL UPDATE ---\n")
			}
			fmt.Print(renderPanel(snapshot))
			lastKey = key
		}
		time.Sleep(time.Duration(refresh) * time.Millisecond)
	}
}
