package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
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
