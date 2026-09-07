package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const projectHubDefaultURL = "http://127.0.0.1:5080"

var projectHubStartupTimeout = 30 * time.Second
var projectHubSleep = time.Sleep

var projectHubHealthProbe = func() bool {
	client := &http.Client{Timeout: 1500 * time.Millisecond}
	resp, err := client.Get(projectHubDefaultURL + "/health")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode >= 200 && resp.StatusCode < 300
}

var projectHubStartProcess = func(bridgeRoot string, spec runSpec) (int, error) {
	statusDir := filepath.Join(bridgeRoot, "00_STATUS")
	if err := os.MkdirAll(statusDir, 0o755); err != nil {
		return 0, err
	}
	stdoutLog, err := os.OpenFile(filepath.Join(statusDir, "projecthub-server.stdout.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return 0, err
	}
	defer stdoutLog.Close()
	stderrLog, err := os.OpenFile(filepath.Join(statusDir, "projecthub-server.stderr.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return 0, err
	}
	defer stderrLog.Close()

	process := exec.Command(spec.exe, spec.args...)
	process.Dir = spec.dir
	process.Stdout = stdoutLog
	process.Stderr = stderrLog
	if err := process.Start(); err != nil {
		return 0, err
	}
	pid := process.Process.Pid
	_ = process.Process.Release()
	return pid, nil
}

var projectHubKillProcess = func(pid int) error {
	process, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	return process.Kill()
}

func executeProjectHubStatus(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.status"
	res.Meta["project"] = "ProjectHub"
	res.Meta["repositoryAvailable"] = false
	res.Meta["applicationRunning"] = false
	res.Meta["applicationUrl"] = projectHubDefaultURL

	workDir := strings.TrimSpace(cfg.ProjectHubWorkDir)
	if workDir == "" {
		res.Error = "projectHubWorkDir is not configured"
		finish(&res, start)
		return res
	}
	stat, err := os.Stat(workDir)
	if err != nil || !stat.IsDir() {
		res.Error = "projectHubWorkDir is unavailable"
		finish(&res, start)
		return res
	}

	timeout := cfg.CommandTimeoutSec
	if timeout <= 0 {
		timeout = 120
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	stdout, stderr, code, err := r.Run(ctx, runSpec{
		logical: "git branch --show-current",
		exe:     "git.exe",
		args:    []string{"-C", workDir, "branch", "--show-current"},
		dir:     workDir,
	})
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if err != nil || code != 0 {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if err != nil {
			res.Error = err.Error()
		} else {
			res.Error = "git branch --show-current failed"
		}
		finish(&res, start)
		return res
	}

	branch := strings.TrimSpace(stdout)
	res.Meta["repositoryAvailable"] = true
	res.Meta["branch"] = branch
	res.Meta["applicationRunning"] = projectHubHealthProbe()
	res.Status = "ok"
	res.Output = fmt.Sprintf(
		"repositoryAvailable=true branch=%s applicationRunning=%t url=%s",
		branch,
		res.Meta["applicationRunning"],
		projectHubDefaultURL,
	)
	finish(&res, start)
	return res
}

func executeProjectHubBuild(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "dotnet restore ProjectHub.slnx --locked-mode && dotnet build ProjectHub.slnx --configuration Release --no-restore"
	res.Meta["project"] = "ProjectHub"
	res.Meta["solution"] = "ProjectHub.slnx"
	res.Meta["configuration"] = "Release"
	res.Meta["restore"] = "not_run"
	res.Meta["build"] = "not_run"

	workDir := strings.TrimSpace(cfg.ProjectHubWorkDir)
	if workDir == "" {
		res.Error = "projectHubWorkDir is not configured"
		finish(&res, start)
		return res
	}
	stat, err := os.Stat(workDir)
	if err != nil || !stat.IsDir() {
		res.Error = "projectHubWorkDir is unavailable"
		finish(&res, start)
		return res
	}
	solution := filepath.Join(workDir, "ProjectHub.slnx")
	if stat, err := os.Stat(solution); err != nil || stat.IsDir() {
		res.Error = "ProjectHub.slnx is unavailable"
		finish(&res, start)
		return res
	}

	timeout := cfg.CommandTimeoutSec
	if timeout <= 0 {
		timeout = 120
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	restoreOut, restoreErr, restoreCode, restoreRunErr := r.Run(ctx, runSpec{
		logical: "dotnet restore ProjectHub.slnx --locked-mode",
		exe:     "dotnet.exe",
		args:    []string{"restore", "ProjectHub.slnx", "--locked-mode"},
		dir:     workDir,
	})
	res.Stdout = "[restore]\n" + restoreOut
	res.Stderr = "[restore]\n" + restoreErr
	res.ExitCode = &restoreCode
	if restoreRunErr != nil || restoreCode != 0 {
		res.Meta["restore"] = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if restoreRunErr != nil {
			res.Error = restoreRunErr.Error()
		} else {
			res.Error = "dotnet restore failed"
		}
		finish(&res, start)
		return res
	}
	res.Meta["restore"] = "ok"

	buildOut, buildErr, buildCode, buildRunErr := r.Run(ctx, runSpec{
		logical: "dotnet build ProjectHub.slnx --configuration Release --no-restore",
		exe:     "dotnet.exe",
		args:    []string{"build", "ProjectHub.slnx", "--configuration", "Release", "--no-restore"},
		dir:     workDir,
	})
	res.Stdout += "\n[build]\n" + buildOut
	res.Stderr += "\n[build]\n" + buildErr
	res.ExitCode = &buildCode
	if buildRunErr != nil || buildCode != 0 {
		res.Meta["build"] = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if buildRunErr != nil {
			res.Error = buildRunErr.Error()
		} else {
			res.Error = "dotnet build failed"
		}
		finish(&res, start)
		return res
	}

	res.Meta["build"] = "ok"
	res.Status = "ok"
	res.Output = "restore=ok build=ok configuration=Release"
	finish(&res, start)
	return res
}

func executeProjectHubTest(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "dotnet test ProjectHub.slnx --configuration Release --no-build --no-restore"
	res.Meta["project"] = "ProjectHub"
	res.Meta["solution"] = "ProjectHub.slnx"
	res.Meta["configuration"] = "Release"
	res.Meta["test"] = "not_run"

	workDir := strings.TrimSpace(cfg.ProjectHubWorkDir)
	if workDir == "" {
		res.Error = "projectHubWorkDir is not configured"
		finish(&res, start)
		return res
	}
	stat, err := os.Stat(workDir)
	if err != nil || !stat.IsDir() {
		res.Error = "projectHubWorkDir is unavailable"
		finish(&res, start)
		return res
	}
	solution := filepath.Join(workDir, "ProjectHub.slnx")
	if stat, err := os.Stat(solution); err != nil || stat.IsDir() {
		res.Error = "ProjectHub.slnx is unavailable"
		finish(&res, start)
		return res
	}

	timeout := cfg.CommandTimeoutSec
	if timeout <= 0 {
		timeout = 120
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	stdout, stderr, code, runErr := r.Run(ctx, runSpec{
		logical: "dotnet test ProjectHub.slnx --configuration Release --no-build --no-restore",
		exe:     "dotnet.exe",
		args:    []string{"test", "ProjectHub.slnx", "--configuration", "Release", "--no-build", "--no-restore"},
		dir:     workDir,
	})
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if runErr != nil || code != 0 {
		res.Meta["test"] = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if runErr != nil {
			res.Error = runErr.Error()
		} else {
			res.Error = "dotnet test failed"
		}
		finish(&res, start)
		return res
	}

	res.Meta["test"] = "ok"
	res.Status = "ok"
	res.Output = "test=ok configuration=Release"
	finish(&res, start)
	return res
}

func executeProjectHubStart(cfg Config, cmd Command, start time.Time) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "dotnet run --project src/ProjectHub.Server --configuration Release --no-build --no-launch-profile --urls http://127.0.0.1:5080"
	res.Meta["project"] = "ProjectHub"
	res.Meta["configuration"] = "Release"
	res.Meta["applicationUrl"] = projectHubDefaultURL
	res.Meta["applicationRunning"] = false
	res.Meta["startState"] = "not_started"

	bridgeRoot := strings.TrimSpace(cfg.BridgeRoot)
	if bridgeRoot == "" {
		res.Error = "bridgeRoot is not configured"
		finish(&res, start)
		return res
	}
	workDir := strings.TrimSpace(cfg.ProjectHubWorkDir)
	if workDir == "" {
		res.Error = "projectHubWorkDir is not configured"
		finish(&res, start)
		return res
	}
	stat, err := os.Stat(workDir)
	if err != nil || !stat.IsDir() {
		res.Error = "projectHubWorkDir is unavailable"
		finish(&res, start)
		return res
	}
	projectFile := filepath.Join(workDir, "src", "ProjectHub.Server", "ProjectHub.Server.csproj")
	if stat, err := os.Stat(projectFile); err != nil || stat.IsDir() {
		res.Error = "ProjectHub.Server project is unavailable"
		finish(&res, start)
		return res
	}

	if projectHubHealthProbe() {
		code := 0
		res.ExitCode = &code
		res.Status = "ok"
		res.Meta["applicationRunning"] = true
		res.Meta["startState"] = "already_running"
		res.Output = fmt.Sprintf("applicationRunning=true state=already_running url=%s", projectHubDefaultURL)
		finish(&res, start)
		return res
	}

	spec := runSpec{
		logical: res.LogicalCommand,
		exe:     "dotnet.exe",
		args: []string{
			"run",
			"--project", "src/ProjectHub.Server",
			"--configuration", "Release",
			"--no-build",
			"--no-launch-profile",
			"--urls", projectHubDefaultURL,
		},
		dir: workDir,
	}
	pid, err := projectHubStartProcess(bridgeRoot, spec)
	if err != nil {
		res.Meta["startState"] = "start_failed"
		res.Error = "failed to start ProjectHub: " + err.Error()
		finish(&res, start)
		return res
	}
	res.Meta["pid"] = pid
	res.Meta["startState"] = "started"

	deadline := time.Now().Add(projectHubStartupTimeout)
	for {
		if projectHubHealthProbe() {
			code := 0
			res.ExitCode = &code
			res.Status = "ok"
			res.Meta["applicationRunning"] = true
			res.Output = fmt.Sprintf("applicationRunning=true state=started pid=%d url=%s", pid, projectHubDefaultURL)
			finish(&res, start)
			return res
		}
		if !time.Now().Before(deadline) {
			break
		}
		projectHubSleep(250 * time.Millisecond)
	}

	res.Meta["startState"] = "health_failed"
	if err := projectHubKillProcess(pid); err == nil {
		res.Meta["processKilled"] = true
	}
	res.Error = fmt.Sprintf("ProjectHub did not become healthy at %s within %s", projectHubDefaultURL, projectHubStartupTimeout)
	finish(&res, start)
	return res
}
