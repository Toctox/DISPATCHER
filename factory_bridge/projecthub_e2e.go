package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const projectHubE2EFilter = "Category=E2E"
const projectHubE2ETestProject = "tests/ProjectHub.Server.Tests/ProjectHub.Server.Tests.csproj"

func executeProjectHubE2ECanonical(cfg Config, cmd Command, start time.Time, r runner) Result {
	state, err := requireCanonicalProjectHub(cfg, r)
	if err != nil {
		return canonicalGuardFailure(cmd, start, state, err)
	}

	res := baseResult(cmd, start)
	res.LogicalCommand = "dotnet test tests/ProjectHub.Server.Tests/ProjectHub.Server.Tests.csproj --configuration Release --no-build --no-restore --filter Category=E2E"
	res.Meta["project"] = "ProjectHub"
	res.Meta["configuration"] = "Release"
	res.Meta["e2e"] = "not_run"
	res.Meta["e2eCommit"] = state.Head
	addProjectHubGitMeta(res.Meta, state)

	workDir, err := projectHubWorkDir(cfg)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	testProject := filepath.Join(workDir, filepath.FromSlash(projectHubE2ETestProject))
	if stat, err := os.Stat(testProject); err != nil || stat.IsDir() {
		res.Error = "ProjectHub E2E test project is unavailable"
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
		logical: res.LogicalCommand,
		exe:     "dotnet.exe",
		args: []string{
			"test",
			projectHubE2ETestProject,
			"--configuration", "Release",
			"--no-build",
			"--no-restore",
			"--filter", projectHubE2EFilter,
		},
		dir: workDir,
	})
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if runErr != nil || code != 0 {
		res.Meta["e2e"] = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if runErr != nil {
			res.Error = runErr.Error()
		} else {
			res.Error = "ProjectHub E2E test failed"
		}
		finish(&res, start)
		return res
	}

	res.Meta["e2e"] = "ok"
	res.Status = "ok"
	res.Output = fmt.Sprintf("e2e=ok filter=%s e2eCommit=%s", projectHubE2EFilter, state.Head)
	finish(&res, start)
	return res
}
