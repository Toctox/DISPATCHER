package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const cafeCCCResultPrefix = "CAFE_CCC_RESULT "

type installedRuntimeState struct {
	SourceCommit string `json:"sourceCommit"`
}

func factoryBridgeInstalledStatePath() (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	return filepath.Join(base, "FactoryBridge", "state", "installed-runtime.json"), nil
}

func ensureFactoryBridgeInstalledCommit(mission Mission) error {
	if actual := runtimeSourceCommit(); actual != "development" {
		if !strings.EqualFold(actual, strings.TrimSpace(mission.TargetCommit)) {
			return fmt.Errorf("running FactoryBridge does not match authorized target: authorized=%s running=%s", mission.TargetCommit, actual)
		}
		return nil
	}
	path, err := factoryBridgeInstalledStatePath()
	if err != nil {
		return err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("installed FactoryBridge state unavailable: %w", err)
	}
	var state installedRuntimeState
	if err := json.Unmarshal(data, &state); err != nil {
		return fmt.Errorf("installed FactoryBridge state is invalid: %w", err)
	}
	installed := strings.ToLower(strings.TrimSpace(state.SourceCommit))
	authorized := strings.ToLower(strings.TrimSpace(mission.TargetCommit))
	if installed == "" {
		return errors.New("installed FactoryBridge source commit is empty")
	}
	if installed != authorized {
		return fmt.Errorf("installed FactoryBridge does not match authorized target: authorized=%s installed=%s", authorized, installed)
	}
	return nil
}

func cafeCCCScriptPath(cfg Config) (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base != "" {
		candidate := filepath.Join(base, "FactoryBridge", "source", "DISPATCHER", "scripts", "cafe-ccc-scan.ps1")
		if stat, err := os.Stat(candidate); err == nil && !stat.IsDir() {
			return candidate, nil
		}
	}
	if workDir := strings.TrimSpace(cfg.DispatcherWorkDir); workDir != "" {
		candidate := filepath.Join(workDir, "scripts", "cafe-ccc-scan.ps1")
		if stat, err := os.Stat(candidate); err == nil && !stat.IsDir() {
			return candidate, nil
		}
	}
	return "", errors.New("fixed Café CCC scan script was not found")
}

func parseCafeCCCResult(stdout string) map[string]any {
	lines := strings.Split(strings.ReplaceAll(stdout, "\r\n", "\n"), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(line, cafeCCCResultPrefix) {
			continue
		}
		raw := strings.TrimSpace(strings.TrimPrefix(line, cafeCCCResultPrefix))
		var payload map[string]any
		if json.Unmarshal([]byte(raw), &payload) == nil {
			return payload
		}
	}
	return nil
}

func executeCafeCCCScan(cfg Config, mission Mission, start time.Time, r runner) Result {
	res := baseResult(Command{ID: mission.ID, Action: "cafe.ccc.scan"}, start)
	script, err := cafeCCCScriptPath(cfg)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}

	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Minute)
	defer cancel()
	spec := runSpec{
		logical: "cafe.ccc.scan (fixed script)",
		exe:     "powershell.exe",
		args: []string{
			"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
			"-File", script, "-MissionId", mission.ID,
		},
		dir: filepath.Dir(script),
	}
	stdout, stderr, code, runErr := r.Run(ctx, spec)
	res.LogicalCommand = spec.logical
	res.Stdout = stdout
	res.Stderr = stderr
	res.ExitCode = &code
	res.Meta["authorizedFactoryBridgeCommit"] = strings.ToLower(strings.TrimSpace(mission.TargetCommit))

	if payload := parseCafeCCCResult(stdout); payload != nil {
		res.Meta["cafeCCC"] = payload
		if summary, ok := payload["summary"].(string); ok {
			res.Output = compact(summary, 1200)
		}
	}
	if res.Output == "" {
		res.Output = compact(stdout, 1200)
	}

	if runErr == nil && code == 0 {
		res.Status = "ok"
	} else {
		res.Status = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = "Café CCC scan timed out after 25 minutes"
		} else if runErr != nil {
			res.Error = runErr.Error()
		} else {
			res.Error = fmt.Sprintf("Café CCC scan exited with code %d", code)
		}
	}
	finish(&res, start)
	return res
}
