package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const postgresInstallTimeout = 15 * time.Minute

func executePostgresInstall(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "postgres.install"
	res.Meta["scope"] = "current-user"
	res.Meta["network"] = "127.0.0.1:5432"
	res.Meta["credentials"] = "DPAPI CurrentUser; never returned through Drive"

	if runtime.GOOS != "windows" {
		res.Error = "postgres.install is supported only on Windows"
		finish(&res, start)
		return res
	}
	workDir := strings.TrimSpace(cfg.DispatcherWorkDir)
	if workDir == "" {
		res.Error = "dispatcherWorkDir is not configured"
		finish(&res, start)
		return res
	}
	script := filepath.Join(workDir, "scripts", "postgres-install-local.ps1")
	if stat, err := os.Stat(script); err != nil || stat.IsDir() {
		res.Error = "PostgreSQL installer script not found: scripts\\postgres-install-local.ps1"
		finish(&res, start)
		return res
	}

	spec := runSpec{
		logical: "scripts\\postgres-install-local.ps1",
		exe:     "powershell.exe",
		args:    []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script},
		dir:     workDir,
	}
	ctx, cancel := context.WithTimeout(context.Background(), postgresInstallTimeout)
	defer cancel()
	stdout, stderr, code, err := r.Run(ctx, spec)
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code

	if err != nil || code != 0 {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("postgres.install timed out after %s", postgresInstallTimeout)
		} else if err != nil {
			res.Error = err.Error()
		} else {
			res.Error = fmt.Sprintf("postgres.install failed with exit code %d", code)
		}
		finish(&res, start)
		return res
	}

	payload := map[string]any{}
	line := lastNonEmptyLine(stdout)
	if line != "" {
		if json.Unmarshal([]byte(line), &payload) == nil {
			res.Meta["postgres"] = payload
		}
	}
	version := "unknown"
	if value, ok := payload["version"].(string); ok && strings.TrimSpace(value) != "" {
		version = strings.TrimSpace(value)
	}
	res.Status = "ok"
	res.Output = fmt.Sprintf("postgres.install=ok version=%s host=127.0.0.1 port=5432 secret=dpapi", version)
	finish(&res, start)
	return res
}

func lastNonEmptyLine(value string) string {
	lines := strings.Split(strings.ReplaceAll(value, "\r\n", "\n"), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if line := strings.TrimSpace(lines[i]); line != "" {
			return line
		}
	}
	return ""
}
