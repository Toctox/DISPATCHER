package main

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const agentRuntimeVersion = "1.0"

func executeBridgeDoctor(cfg Config, cmd Command, start time.Time) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "bridge.doctor"
	res.Meta["agentRuntimeVersion"] = agentRuntimeVersion

	checks := map[string]any{}
	missing := []string{}
	for _, tool := range []string{"git.exe", "dotnet.exe", "powershell.exe"} {
		path, err := exec.LookPath(tool)
		if err != nil {
			checks[tool] = map[string]any{"available": false}
			missing = append(missing, tool)
			continue
		}
		checks[tool] = map[string]any{"available": true, "path": path}
	}
	if path, err := exec.LookPath("go.exe"); err == nil {
		checks["go.exe"] = map[string]any{"available": true, "path": path, "requiredForRuntime": false, "requiredForSelfUpdate": true}
	} else {
		checks["go.exe"] = map[string]any{"available": false, "requiredForRuntime": false, "requiredForSelfUpdate": true}
	}

	paths := map[string]any{}
	for name, path := range map[string]string{
		"bridgeRoot":        cfg.BridgeRoot,
		"dispatcherWorkDir": cfg.DispatcherWorkDir,
		"projectHubWorkDir": cfg.ProjectHubWorkDir,
	} {
		available := false
		if strings.TrimSpace(path) != "" {
			if stat, err := os.Stat(path); err == nil && stat.IsDir() {
				available = true
			}
		}
		paths[name] = map[string]any{"path": path, "available": available}
		if !available {
			missing = append(missing, name)
		}
	}
	res.Meta["tools"] = checks
	res.Meta["paths"] = paths
	res.Meta["allowGitPull"] = cfg.AllowGitPull
	res.Meta["allowGitPush"] = cfg.AllowGitPush

	code := 0
	res.ExitCode = &code
	if len(missing) > 0 {
		res.Status = "failed"
		res.Error = "required runtime checks failed: " + strings.Join(missing, ", ")
		res.Output = "doctor=failed"
		finish(&res, start)
		return res
	}
	res.Status = "ok"
	res.Output = "doctor=ok git=ok dotnet=ok powershell=ok bridgeRoot=ok dispatcherWorkDir=ok projectHubWorkDir=ok"
	finish(&res, start)
	return res
}

func tailFile(path string, maxBytes int64) (string, bool, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", false, nil
		}
		return "", false, err
	}
	defer f.Close()
	stat, err := f.Stat()
	if err != nil {
		return "", true, err
	}
	start := int64(0)
	if stat.Size() > maxBytes {
		start = stat.Size() - maxBytes
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return "", true, err
	}
	data, err := io.ReadAll(io.LimitReader(f, maxBytes))
	if err != nil {
		return "", true, err
	}
	prefix := ""
	if start > 0 {
		prefix = "...[tail truncated by FactoryBridge]\n"
	}
	return prefix + string(data), true, nil
}

func executeProjectHubLogs(cfg Config, cmd Command, start time.Time) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.logs"
	res.Meta["project"] = "ProjectHub"
	res.Meta["maxBytesPerStream"] = 64 * 1024

	if strings.TrimSpace(cfg.BridgeRoot) == "" {
		res.Error = "bridgeRoot is not configured"
		finish(&res, start)
		return res
	}
	statusDir := filepath.Join(cfg.BridgeRoot, "00_STATUS")
	stdout, stdoutExists, err := tailFile(filepath.Join(statusDir, "projecthub-server.stdout.log"), 64*1024)
	if err != nil {
		res.Error = "failed to read ProjectHub stdout log: " + err.Error()
		finish(&res, start)
		return res
	}
	stderr, stderrExists, err := tailFile(filepath.Join(statusDir, "projecthub-server.stderr.log"), 64*1024)
	if err != nil {
		res.Error = "failed to read ProjectHub stderr log: " + err.Error()
		finish(&res, start)
		return res
	}
	res.Stdout = stdout
	res.Stderr = stderr
	res.Meta["stdoutLogExists"] = stdoutExists
	res.Meta["stderrLogExists"] = stderrExists
	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Output = fmt.Sprintf("logs=ok stdoutExists=%t stderrExists=%t", stdoutExists, stderrExists)
	finish(&res, start)
	return res
}

func stepSummary(name string, result Result) map[string]any {
	step := map[string]any{
		"name":       name,
		"status":     result.Status,
		"durationMs": result.DurationMs,
		"output":     result.Output,
	}
	if result.ExitCode != nil {
		step["exitCode"] = *result.ExitCode
	}
	if result.Error != "" {
		step["error"] = result.Error
	}
	return step
}

func executeProjectHubValidate(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.validate"
	res.Meta["project"] = "ProjectHub"
	res.Meta["agentRuntimeVersion"] = agentRuntimeVersion
	res.Meta["pipeline"] = []string{"build", "test", "start", "status", "stop"}
	steps := []map[string]any{}

	if projectHubHealthProbe() {
		res.Error = "ProjectHub is already running; validation owns the application lifecycle and refuses to stop an existing process"
		res.Meta["applicationRunning"] = true
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	state, err := requireCanonicalProjectHub(cfg, r)
	if err != nil {
		addProjectHubGitMeta(res.Meta, state)
		res.Error = err.Error()
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}
	addProjectHubGitMeta(res.Meta, state)
	res.Meta["validatedCommit"] = state.Head

	build := executeProjectHubBuildCanonical(cfg, Command{ID: cmd.ID, Action: "projecthub.build"}, time.Now(), r)
	steps = append(steps, stepSummary("build", build))
	if build.Status != "ok" {
		res.Error = "validation failed at build: " + build.Error
		res.Stdout, res.Stderr = build.Stdout, build.Stderr
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	test := executeProjectHubTestCanonical(cfg, Command{ID: cmd.ID, Action: "projecthub.test"}, time.Now(), r)
	steps = append(steps, stepSummary("test", test))
	if test.Status != "ok" {
		res.Error = "validation failed at test: " + test.Error
		res.Stdout, res.Stderr = test.Stdout, test.Stderr
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	started := executeProjectHubStartCanonical(cfg, Command{ID: cmd.ID, Action: "projecthub.start"}, time.Now(), r)
	steps = append(steps, stepSummary("start", started))
	if started.Status != "ok" {
		res.Error = "validation failed at start: " + started.Error
		res.Stdout, res.Stderr = started.Stdout, started.Stderr
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	status := executeProjectHubStatusCanonical(cfg, Command{ID: cmd.ID, Action: "projecthub.status"}, time.Now(), r)
	steps = append(steps, stepSummary("status", status))

	stopped := executeProjectHubStop(cfg, Command{ID: cmd.ID, Action: "projecthub.stop"}, time.Now())
	steps = append(steps, stepSummary("stop", stopped))
	if stopped.Status != "ok" {
		res.Error = "validation could not restore stopped state: " + stopped.Error
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}
	if status.Status != "ok" {
		res.Error = "validation failed at status: " + status.Error
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}
	if running, _ := status.Meta["applicationRunning"].(bool); !running {
		res.Error = "validation health/status step did not observe ProjectHub running"
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}
	if provenance, _ := status.Meta["provenanceKnown"].(bool); !provenance {
		res.Error = "validation status did not confirm running commit provenance"
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Meta["steps"] = steps
	res.Meta["applicationRunning"] = false
	res.Output = fmt.Sprintf("validate=ok commit=%s build=ok test=ok health=ok provenance=ok stopped=true", state.Head)
	finish(&res, start)
	return res
}
