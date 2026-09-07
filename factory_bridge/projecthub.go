package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const projectHubDefaultURL = "http://127.0.0.1:5080"

var projectHubHealthProbe = func() bool {
	client := &http.Client{Timeout: 1500 * time.Millisecond}
	resp, err := client.Get(projectHubDefaultURL + "/health")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode >= 200 && resp.StatusCode < 300
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
