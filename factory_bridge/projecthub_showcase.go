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

const projectHubShowcaseURL = "http://127.0.0.1:5080"

func projectHubShowcaseRoot() (string, error) {
	home := strings.TrimSpace(os.Getenv("USERPROFILE"))
	if home == "" {
		return "", errors.New("USERPROFILE is not set")
	}
	root := filepath.Join(home, "ProjectHub-Lab")
	if err := os.MkdirAll(filepath.Join(root, "BUILDS"), 0o755); err != nil {
		return "", err
	}
	return root, nil
}

func projectHubLocalLogsDir() (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	dir := filepath.Join(base, "FactoryBridge", "logs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	return dir, nil
}

func writeShowcaseLaunchers(root string) error {
	start := `@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
if not exist "%ROOT%LATEST.txt" (
  echo ERROR: LATEST.txt not found.
  pause
  exit /b 1
)
set /p BUILD=<"%ROOT%LATEST.txt"
if not exist "%BUILD%\ProjectHub.Server.exe" (
  echo ERROR: ProjectHub.Server.exe not found in latest build:
  echo   %BUILD%
  pause
  exit /b 1
)
start "ProjectHub" /D "%BUILD%" "%BUILD%\ProjectHub.Server.exe" --urls http://127.0.0.1:5080
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:5080
endlocal
`
	stop := `@echo off
taskkill /IM ProjectHub.Server.exe /F >nul 2>&1
if errorlevel 1 (
  echo ProjectHub was not running.
) else (
  echo ProjectHub stopped.
)
`
	readme := "PROJECTHUB LAB\r\n\r\nSTART_LATEST.cmd abre a build aprovada mais recente.\r\nSTOP_PROJECTHUB.cmd encerra a instancia manual.\r\nBUILDS contem versoes publicadas e imutaveis para teste e inspiracao.\r\nLATEST.txt aponta para a build atual.\r\n"
	if err := os.WriteFile(filepath.Join(root, "START_LATEST.cmd"), []byte(start), 0o600); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(root, "STOP_PROJECTHUB.cmd"), []byte(stop), 0o600); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(root, "README.txt"), []byte(readme), 0o600)
}

func projectHubHead(cfg Config, r runner) string {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	stdout, _, code, err := r.Run(ctx, runSpec{
		logical: "git rev-parse HEAD",
		exe: "git.exe",
		args: []string{"-C", cfg.ProjectHubWorkDir, "rev-parse", "HEAD"},
		dir: cfg.ProjectHubWorkDir,
	})
	if err != nil || code != 0 {
		return "unknown"
	}
	return strings.TrimSpace(stdout)
}

func executeProjectHubShowcasePublish(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "dotnet publish ProjectHub.Server to local ProjectHub-Lab"
	res.Meta["project"] = "ProjectHub"

	workDir := strings.TrimSpace(cfg.ProjectHubWorkDir)
	if workDir == "" {
		res.Error = "projectHubWorkDir is not configured"
		finish(&res, start)
		return res
	}
	project := filepath.Join(workDir, "src", "ProjectHub.Server", "ProjectHub.Server.csproj")
	if stat, err := os.Stat(project); err != nil || stat.IsDir() {
		res.Error = "ProjectHub.Server project is unavailable"
		finish(&res, start)
		return res
	}
	root, err := projectHubShowcaseRoot()
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	commit := projectHubHead(cfg, r)
	short := commit
	if len(short) > 12 {
		short = short[:12]
	}
	stamp := time.Now().Format("20060102_150405")
	buildDir := filepath.Join(root, "BUILDS", stamp+"_"+short)
	if err := os.MkdirAll(buildDir, 0o755); err != nil {
		res.Error = "failed to create showcase build directory: " + err.Error()
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
		logical: "dotnet publish src/ProjectHub.Server/ProjectHub.Server.csproj --configuration Release --no-restore",
		exe: "dotnet.exe",
		args: []string{"publish", "src/ProjectHub.Server/ProjectHub.Server.csproj", "--configuration", "Release", "--no-restore", "--output", buildDir},
		dir: workDir,
	})
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if runErr != nil || code != 0 {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("showcase publish timed out after %ds", timeout)
		} else if runErr != nil {
			res.Error = runErr.Error()
		} else {
			res.Error = "dotnet publish failed"
		}
		finish(&res, start)
		return res
	}
	if _, err := os.Stat(filepath.Join(buildDir, "ProjectHub.Server.exe")); err != nil {
		res.Error = "publish succeeded but ProjectHub.Server.exe is missing"
		finish(&res, start)
		return res
	}
	version := fmt.Sprintf("commit=%s\nbuiltAt=%s\nconfiguration=Release\n", commit, time.Now().Format(time.RFC3339))
	_ = os.WriteFile(filepath.Join(buildDir, "VERSION.txt"), []byte(version), 0o600)
	if err := os.WriteFile(filepath.Join(root, "LATEST.txt"), []byte(buildDir+"\r\n"), 0o600); err != nil {
		res.Error = "failed to update LATEST.txt: " + err.Error()
		finish(&res, start)
		return res
	}
	if err := writeShowcaseLaunchers(root); err != nil {
		res.Error = "failed to write showcase launchers: " + err.Error()
		finish(&res, start)
		return res
	}
	zero := 0
	res.ExitCode = &zero
	res.Status = "ok"
	res.Meta["showcasePath"] = root
	res.Meta["buildPath"] = buildDir
	res.Meta["verifiedCommit"] = commit
	res.Output = fmt.Sprintf("showcase=ok commit=%s build=%s", commit, buildDir)
	finish(&res, start)
	return res
}

func executeProjectHubShowcaseSmoke(cfg Config, cmd Command, start time.Time) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "start latest published ProjectHub, healthcheck, stop"
	root, err := projectHubShowcaseRoot()
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	latestBytes, err := os.ReadFile(filepath.Join(root, "LATEST.txt"))
	if err != nil {
		res.Error = "LATEST.txt unavailable: " + err.Error()
		finish(&res, start)
		return res
	}
	buildDir := strings.TrimSpace(string(latestBytes))
	exePath := filepath.Join(buildDir, "ProjectHub.Server.exe")
	if _, err := os.Stat(exePath); err != nil {
		res.Error = "latest ProjectHub.Server.exe unavailable"
		finish(&res, start)
		return res
	}
	if projectHubHealthProbe() {
		res.Error = "port 5080 is already serving ProjectHub; showcase smoke refuses to interfere with a manual instance"
		finish(&res, start)
		return res
	}
	logsDir, err := projectHubLocalLogsDir()
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	stdoutLog, err := os.OpenFile(filepath.Join(logsDir, "showcase-smoke.stdout.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	defer stdoutLog.Close()
	stderrLog, err := os.OpenFile(filepath.Join(logsDir, "showcase-smoke.stderr.log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	defer stderrLog.Close()

	process := exec.Command(exePath, "--urls", projectHubShowcaseURL)
	process.Dir = buildDir
	process.Stdout = stdoutLog
	process.Stderr = stderrLog
	if err := process.Start(); err != nil {
		res.Error = "failed to start showcase: " + err.Error()
		finish(&res, start)
		return res
	}
	defer func() { _ = process.Process.Kill(); _, _ = process.Process.Wait() }()

	client := &http.Client{Timeout: 1500 * time.Millisecond}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		resp, getErr := client.Get(projectHubShowcaseURL + "/health")
		if getErr == nil {
			ok := resp.StatusCode >= 200 && resp.StatusCode < 300
			resp.Body.Close()
			if ok {
				zero := 0
				res.ExitCode = &zero
				res.Status = "ok"
				res.Meta["showcasePath"] = root
				res.Meta["buildPath"] = buildDir
				res.Meta["logsPath"] = logsDir
				res.Output = "showcase-smoke=ok health=ok stopped=true"
				finish(&res, start)
				return res
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
	res.Error = "published showcase did not become healthy within 30s; local logs retained"
	res.Meta["logsPath"] = logsDir
	finish(&res, start)
	return res
}
