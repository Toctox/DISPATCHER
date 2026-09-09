package main

import (
	"errors"
	"os"
	"path/filepath"
	"time"
)

const localRuntimeStateDirName = "state"

// localRuntimeStateDir is the control-plane state directory used for process
// health decisions. It intentionally lives outside Google Drive so sync lag,
// conflict files, and remote timestamp regressions cannot affect supervision.
func localRuntimeStateDir() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	dir := filepath.Join(base, "FactoryBridge", localRuntimeStateDirName)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	return dir, nil
}

func localRuntimeStatusPath(name string) (string, error) {
	dir, err := localRuntimeStateDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, name), nil
}

func writeLocalExecutorStatus(started time.Time) error {
	path, err := localRuntimeStatusPath(executorStatusFileName)
	if err != nil {
		return err
	}
	return writeJSONAtomic(path, ExecutorStatus{
		BridgeVersion: bridgeVersion,
		PID:           os.Getpid(),
		StartedAt:     started.Format(time.RFC3339),
		HeartbeatAt:   time.Now().Format(time.RFC3339Nano),
	})
}
