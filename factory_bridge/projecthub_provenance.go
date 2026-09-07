package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const projectHubProcessStateFileName = "projecthub-process.json"

type projectHubGitState struct {
	Branch     string
	Head       string
	OriginMain string
	Dirty      bool
	Ahead      int
	Behind     int
}

type projectHubProcessState struct {
	PID           int    `json:"pid"`
	StartedCommit string `json:"startedCommit"`
	StartedAt     string `json:"startedAt"`
	URL           string `json:"url"`
}

var projectHubStopTimeout = 10 * time.Second

func projectHubWorkDir(cfg Config) (string, error) {
	workDir := strings.TrimSpace(cfg.ProjectHubWorkDir)
	if workDir == "" {
		return "", errors.New("projectHubWorkDir is not configured")
	}
	stat, err := os.Stat(workDir)
	if err != nil || !stat.IsDir() {
		return "", errors.New("projectHubWorkDir is unavailable")
	}
	return workDir, nil
}

func projectHubCommandContext(cfg Config) (context.Context, context.CancelFunc) {
	timeout := cfg.CommandTimeoutSec
	if timeout <= 0 {
		timeout = 120
	}
	return context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
}

func runProjectHubGit(ctx context.Context, r runner, workDir string, args ...string) (string, error) {
	stdout, stderr, code, err := r.Run(ctx, runSpec{
		logical: "git " + strings.Join(args, " "),
		exe:     "git.exe",
		args:    append([]string{"-C", workDir}, args...),
		dir:     workDir,
	})
	if err != nil || code != 0 {
		message := strings.TrimSpace(stderr)
		if message == "" {
			message = strings.TrimSpace(stdout)
		}
		if message == "" && err != nil {
			message = err.Error()
		}
		if message == "" {
			message = fmt.Sprintf("exit code %d", code)
		}
		return stdout, fmt.Errorf("git %s failed: %s", strings.Join(args, " "), message)
	}
	return stdout, nil
}

func readProjectHubGitState(ctx context.Context, workDir string, r runner) (projectHubGitState, error) {
	var state projectHubGitState
	branch, err := runProjectHubGit(ctx, r, workDir, "branch", "--show-current")
	if err != nil {
		return state, err
	}
	head, err := runProjectHubGit(ctx, r, workDir, "rev-parse", "HEAD")
	if err != nil {
		return state, err
	}
	originMain, err := runProjectHubGit(ctx, r, workDir, "rev-parse", "origin/main")
	if err != nil {
		return state, err
	}
	status, err := runProjectHubGit(ctx, r, workDir, "status", "--porcelain")
	if err != nil {
		return state, err
	}
	counts, err := runProjectHubGit(ctx, r, workDir, "rev-list", "--left-right", "--count", "HEAD...origin/main")
	if err != nil {
		return state, err
	}
	fields := strings.Fields(counts)
	if len(fields) != 2 {
		return state, fmt.Errorf("unexpected ahead/behind output: %q", strings.TrimSpace(counts))
	}
	ahead, err := strconv.Atoi(fields[0])
	if err != nil {
		return state, fmt.Errorf("invalid ahead count: %w", err)
	}
	behind, err := strconv.Atoi(fields[1])
	if err != nil {
		return state, fmt.Errorf("invalid behind count: %w", err)
	}
	state = projectHubGitState{
		Branch:     strings.TrimSpace(branch),
		Head:       strings.TrimSpace(head),
		OriginMain: strings.TrimSpace(originMain),
		Dirty:      strings.TrimSpace(status) != "",
		Ahead:      ahead,
		Behind:     behind,
	}
	return state, nil
}

func addProjectHubGitMeta(meta map[string]any, state projectHubGitState) {
	meta["branch"] = state.Branch
	meta["head"] = state.Head
	meta["originMain"] = state.OriginMain
	meta["dirty"] = state.Dirty
	meta["ahead"] = state.Ahead
	meta["behind"] = state.Behind
}

func requireCanonicalProjectHub(cfg Config, r runner) (projectHubGitState, error) {
	workDir, err := projectHubWorkDir(cfg)
	if err != nil {
		return projectHubGitState{}, err
	}
	ctx, cancel := projectHubCommandContext(cfg)
	defer cancel()
	state, err := readProjectHubGitState(ctx, workDir, r)
	if err != nil {
		return state, err
	}
	if state.Branch != "main" {
		return state, fmt.Errorf("ProjectHub checkout is not canonical: branch=%s; run projecthub.stop then projecthub.sync", state.Branch)
	}
	if state.Dirty {
		return state, errors.New("ProjectHub checkout is dirty; refusing canonical action")
	}
	if state.Head == "" || state.OriginMain == "" || state.Head != state.OriginMain {
		return state, fmt.Errorf("ProjectHub checkout is not synchronized: HEAD=%s origin/main=%s; run projecthub.sync", state.Head, state.OriginMain)
	}
	return state, nil
}

func projectHubProcessStatePath(cfg Config) string {
	return statusPath(cfg, projectHubProcessStateFileName)
}

func readProjectHubProcessState(cfg Config) (*projectHubProcessState, error) {
	return readJSONFile[projectHubProcessState](projectHubProcessStatePath(cfg))
}

func writeProjectHubProcessState(cfg Config, state projectHubProcessState) error {
	if strings.TrimSpace(cfg.BridgeRoot) == "" {
		return errors.New("bridgeRoot is not configured")
	}
	if err := os.MkdirAll(filepath.Join(cfg.BridgeRoot, statusDirName), 0o755); err != nil {
		return err
	}
	return writeJSONAtomic(projectHubProcessStatePath(cfg), state)
}

func executeProjectHubStatusCanonical(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.status"
	res.Meta["project"] = "ProjectHub"
	res.Meta["repositoryAvailable"] = false
	res.Meta["applicationRunning"] = false
	res.Meta["applicationUrl"] = projectHubDefaultURL
	res.Meta["provenanceKnown"] = false

	workDir, err := projectHubWorkDir(cfg)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	ctx, cancel := projectHubCommandContext(cfg)
	defer cancel()
	state, err := readProjectHubGitState(ctx, workDir, r)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	addProjectHubGitMeta(res.Meta, state)
	res.Meta["repositoryAvailable"] = true
	running := projectHubHealthProbe()
	res.Meta["applicationRunning"] = running
	if running {
		if processState, err := readProjectHubProcessState(cfg); err == nil && processState != nil && processState.StartedCommit != "" {
			res.Meta["runningCommit"] = processState.StartedCommit
			res.Meta["runningPid"] = processState.PID
			res.Meta["provenanceKnown"] = true
		}
	}
	code := 0
	res.ExitCode = &code
	res.Stdout = fmt.Sprintf("branch=%s\nhead=%s\noriginMain=%s\ndirty=%t\nahead=%d\nbehind=%d\n", state.Branch, state.Head, state.OriginMain, state.Dirty, state.Ahead, state.Behind)
	res.Status = "ok"
	res.Output = fmt.Sprintf("repositoryAvailable=true branch=%s head=%s originMain=%s dirty=%t ahead=%d behind=%d applicationRunning=%t provenanceKnown=%t url=%s", state.Branch, state.Head, state.OriginMain, state.Dirty, state.Ahead, state.Behind, running, res.Meta["provenanceKnown"], projectHubDefaultURL)
	finish(&res, start)
	return res
}

func canonicalGuardFailure(cmd Command, start time.Time, state projectHubGitState, err error) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.canonical-preflight"
	addProjectHubGitMeta(res.Meta, state)
	res.Error = err.Error()
	finish(&res, start)
	return res
}

func executeProjectHubBuildCanonical(cfg Config, cmd Command, start time.Time, r runner) Result {
	state, err := requireCanonicalProjectHub(cfg, r)
	if err != nil {
		return canonicalGuardFailure(cmd, start, state, err)
	}
	res := executeProjectHubBuild(cfg, cmd, start, r)
	addProjectHubGitMeta(res.Meta, state)
	res.Meta["builtCommit"] = state.Head
	if res.Status == "ok" {
		res.Output += " builtCommit=" + state.Head
	}
	return res
}

func executeProjectHubTestCanonical(cfg Config, cmd Command, start time.Time, r runner) Result {
	state, err := requireCanonicalProjectHub(cfg, r)
	if err != nil {
		return canonicalGuardFailure(cmd, start, state, err)
	}
	res := executeProjectHubTest(cfg, cmd, start, r)
	addProjectHubGitMeta(res.Meta, state)
	res.Meta["testedCommit"] = state.Head
	if res.Status == "ok" {
		res.Output += " testedCommit=" + state.Head
	}
	return res
}

func executeProjectHubStartCanonical(cfg Config, cmd Command, start time.Time, r runner) Result {
	state, err := requireCanonicalProjectHub(cfg, r)
	if err != nil {
		return canonicalGuardFailure(cmd, start, state, err)
	}
	if projectHubHealthProbe() {
		processState, stateErr := readProjectHubProcessState(cfg)
		if stateErr != nil || processState == nil || processState.StartedCommit != state.Head {
			res := canonicalGuardFailure(cmd, start, state, errors.New("ProjectHub is already healthy but running commit provenance is unknown or stale; stop the existing server before canonical start"))
			res.Meta["applicationRunning"] = true
			res.Meta["applicationUrl"] = projectHubDefaultURL
			return res
		}
		res := executeProjectHubStart(cfg, cmd, start)
		addProjectHubGitMeta(res.Meta, state)
		res.Meta["startedCommit"] = state.Head
		res.Meta["provenanceKnown"] = true
		return res
	}

	res := executeProjectHubStart(cfg, cmd, start)
	addProjectHubGitMeta(res.Meta, state)
	res.Meta["startedCommit"] = state.Head
	if res.Status != "ok" {
		return res
	}
	pid, ok := res.Meta["pid"].(int)
	if !ok || pid <= 0 {
		res.Status = "failed"
		res.Error = "ProjectHub started without a valid managed pid"
		return res
	}
	processState := projectHubProcessState{
		PID:           pid,
		StartedCommit: state.Head,
		StartedAt:     time.Now().Format(time.RFC3339Nano),
		URL:           projectHubDefaultURL,
	}
	if err := writeProjectHubProcessState(cfg, processState); err != nil {
		_ = projectHubKillProcess(pid)
		res.Status = "failed"
		res.Error = "failed to persist ProjectHub process provenance: " + err.Error()
		res.Meta["processKilled"] = true
		return res
	}
	res.Meta["provenanceKnown"] = true
	res.Output += " startedCommit=" + state.Head
	return res
}

func executeProjectHubStop(cfg Config, cmd Command, start time.Time) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.stop"
	res.Meta["project"] = "ProjectHub"
	res.Meta["applicationUrl"] = projectHubDefaultURL
	if strings.TrimSpace(cfg.BridgeRoot) == "" {
		res.Error = "bridgeRoot is not configured"
		finish(&res, start)
		return res
	}

	running := projectHubHealthProbe()
	processState, err := readProjectHubProcessState(cfg)
	if err != nil {
		if os.IsNotExist(err) {
			if running {
				res.Error = "ProjectHub is healthy but no Bridge-managed process provenance exists; refusing to kill an unknown process"
				res.Meta["applicationRunning"] = true
				finish(&res, start)
				return res
			}
			code := 0
			res.ExitCode = &code
			res.Status = "ok"
			res.Meta["applicationRunning"] = false
			res.Meta["stopState"] = "already_stopped"
			res.Output = "applicationRunning=false state=already_stopped"
			finish(&res, start)
			return res
		}
		res.Error = "failed to read ProjectHub process provenance: " + err.Error()
		finish(&res, start)
		return res
	}
	if processState == nil || processState.PID <= 0 {
		res.Error = "ProjectHub process provenance is invalid"
		finish(&res, start)
		return res
	}
	res.Meta["stoppedCommit"] = processState.StartedCommit
	res.Meta["pid"] = processState.PID

	if !running {
		_ = os.Remove(projectHubProcessStatePath(cfg))
		code := 0
		res.ExitCode = &code
		res.Status = "ok"
		res.Meta["applicationRunning"] = false
		res.Meta["stopState"] = "stale_state_cleared"
		res.Output = "applicationRunning=false state=stale_state_cleared"
		finish(&res, start)
		return res
	}

	if err := projectHubKillProcess(processState.PID); err != nil {
		res.Error = "failed to stop Bridge-managed ProjectHub process: " + err.Error()
		finish(&res, start)
		return res
	}
	deadline := time.Now().Add(projectHubStopTimeout)
	for projectHubHealthProbe() && time.Now().Before(deadline) {
		projectHubSleep(200 * time.Millisecond)
	}
	if projectHubHealthProbe() {
		res.Error = fmt.Sprintf("ProjectHub remained healthy after stopping managed pid=%d", processState.PID)
		finish(&res, start)
		return res
	}
	_ = os.Remove(projectHubProcessStatePath(cfg))
	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Meta["applicationRunning"] = false
	res.Meta["stopState"] = "stopped"
	res.Output = fmt.Sprintf("applicationRunning=false state=stopped pid=%d stoppedCommit=%s", processState.PID, processState.StartedCommit)
	finish(&res, start)
	return res
}

func executeProjectHubSync(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "git status --porcelain && git fetch origin main && git switch main && git merge --ff-only origin/main"
	res.Meta["project"] = "ProjectHub"
	if projectHubHealthProbe() {
		res.Error = "ProjectHub is running; run projecthub.stop before projecthub.sync"
		res.Meta["applicationRunning"] = true
		finish(&res, start)
		return res
	}
	workDir, err := projectHubWorkDir(cfg)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	ctx, cancel := projectHubCommandContext(cfg)
	defer cancel()

	status, err := runProjectHubGit(ctx, r, workDir, "status", "--porcelain")
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	if strings.TrimSpace(status) != "" {
		res.Error = "ProjectHub checkout is dirty; refusing sync"
		res.Meta["dirty"] = true
		finish(&res, start)
		return res
	}
	steps := [][]string{
		{"fetch", "origin", "main"},
		{"switch", "main"},
		{"merge", "--ff-only", "origin/main"},
	}
	for _, args := range steps {
		stdout, err := runProjectHubGit(ctx, r, workDir, args...)
		res.Stdout += fmt.Sprintf("[%s]\n%s", strings.Join(args, " "), stdout)
		if err != nil {
			res.Error = err.Error()
			finish(&res, start)
			return res
		}
	}
	state, err := readProjectHubGitState(ctx, workDir, r)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	addProjectHubGitMeta(res.Meta, state)
	if state.Branch != "main" || state.Dirty || state.Head == "" || state.Head != state.OriginMain {
		res.Error = fmt.Sprintf("sync verification failed: branch=%s head=%s origin/main=%s dirty=%t", state.Branch, state.Head, state.OriginMain, state.Dirty)
		finish(&res, start)
		return res
	}
	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Meta["syncedCommit"] = state.Head
	res.Output = fmt.Sprintf("sync=ok branch=main syncedCommit=%s dirty=false ahead=0 behind=0", state.Head)
	finish(&res, start)
	return res
}
