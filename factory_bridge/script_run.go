package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

type scriptRunRequest struct {
	Repo       string            `json:"repo"`
	Script     string            `json:"script"`
	Args       map[string]string `json:"args,omitempty"`
	TimeoutSec int               `json:"timeoutSec,omitempty"`
}

// This is an execution allowlist, not a list of arbitrary repository scripts.
// Expanding it requires a reviewed runtime/policy change.
var scriptPolicy = map[string]map[string]*regexp.Regexp{
	"dispatcher:scripts/factorybridge-verify.ps1": {"TestPattern": regexp.MustCompile(`^[A-Za-z0-9_/.|^-]{1,200}$`)},
	"dispatcher:scripts/factorybridge-smoke.ps1":  {"Message": regexp.MustCompile(`^[A-Za-z0-9 _.-]{0,200}$`)},
}

func decodeScriptRun(m Mission) (scriptRunRequest, error) {
	var req scriptRunRequest
	if len(m.Objective) > 8192 {
		return req, errors.New("script parameters exceed 8 KiB")
	}
	dec := json.NewDecoder(strings.NewReader(m.Objective))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		return req, err
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		return req, errors.New("trailing script parameters")
	}
	policy, ok := scriptPolicy[req.Repo+":"+req.Script]
	if !ok {
		return req, errors.New("script is not in the versioned allowlist")
	}
	if req.TimeoutSec < 0 || req.TimeoutSec > 1800 {
		return req, errors.New("script timeout must be between 0 and 1800")
	}
	for name, value := range req.Args {
		pattern, ok := policy[name]
		if !ok || !pattern.MatchString(value) {
			return req, fmt.Errorf("invalid structured script argument %q", name)
		}
	}
	return req, nil
}

func executeScriptRun(cfg Config, m Mission, start time.Time, r runner) (res Result) {
	res = baseResult(Command{ID: m.ID, Action: m.Kind}, start)
	defer finish(&res, start)
	blocked := func(err error) Result { res.Status = "blocked"; res.Error = err.Error(); return res }
	req, err := decodeScriptRun(m)
	if err != nil {
		return blocked(err)
	}
	repo := cfg.DispatcherWorkDir
	if req.Repo != "dispatcher" {
		return blocked(errors.New("unsupported script repository"))
	}
	repo, err = resolveExecutionRoot(cfg, repo)
	if err != nil {
		return blocked(err)
	}
	seconds := req.TimeoutSec
	if seconds == 0 {
		seconds = 600
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(seconds)*time.Second)
	defer cancel()
	git := func(args ...string) (string, error) {
		base := []string{"-c", "core.hooksPath=NUL", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false"}
		out, stderr, code, err := r.Run(ctx, runSpec{exe: "git.exe", args: append(base, args...), dir: repo})
		if err != nil || code != 0 {
			return "", fmt.Errorf("exact script checkout failed (exit=%d): %s", code, tailCompact(sanitizeRemoteText(stderr), 500))
		}
		return strings.TrimSpace(out), nil
	}
	// Only already reviewed commits reachable from the canonical main are
	// eligible; a mutable branch is never substituted for the requested object.
	if _, err = git("fetch", "origin", "main"); err != nil {
		return blocked(err)
	}
	if _, err = git("merge-base", "--is-ancestor", m.TargetCommit, "origin/main"); err != nil {
		return blocked(errors.New("script commit is not reviewed on origin/main"))
	}
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		return blocked(errors.New("LOCALAPPDATA is not set"))
	}
	workspace := filepath.Join(base, "FactoryNode", "workspaces", "script-"+m.ID)
	if _, err = os.Lstat(workspace); !os.IsNotExist(err) {
		return blocked(errors.New("script workspace already exists; refusing reuse"))
	}
	if err = os.MkdirAll(filepath.Dir(workspace), 0700); err != nil {
		return blocked(err)
	}
	if _, err = git("worktree", "add", "--detach", workspace, m.TargetCommit); err != nil {
		return blocked(err)
	}
	oldRepo := repo
	repo = workspace
	head, err := git("rev-parse", "HEAD")
	repo = oldRepo
	if err != nil || !strings.EqualFold(head, m.TargetCommit) {
		return blocked(errors.New("script workspace commit mismatch"))
	}
	workspaceReal, err := filepath.EvalSymlinks(workspace)
	if err != nil {
		return blocked(errors.New("script workspace cannot be canonicalized"))
	}
	script := filepath.Join(workspace, filepath.FromSlash(req.Script))
	real, err := filepath.EvalSymlinks(script)
	if err != nil || !withinRoot(real, workspaceReal) {
		return blocked(errors.New("script path escapes workspace or is missing"))
	}
	// Arguments are serialized as data and consumed by a fixed wrapper, never
	// interpolated into PowerShell source or accepted as extra shell options.
	dir, err := missionLocalDir(m.ID)
	if err != nil {
		return blocked(err)
	}
	argsPath := filepath.Join(dir, "script-args.json")
	if err = writeJSONAtomic(argsPath, req.Args); err != nil {
		return blocked(err)
	}
	wrapper := filepath.Join(dir, "invoke-script.ps1")
	const wrapperSource = `param([string]$ScriptPath,[string]$ArgumentsPath)
$ErrorActionPreference='Stop'
$data=Get-Content -LiteralPath $ArgumentsPath -Raw|ConvertFrom-Json
$named=@{}
if($null -ne $data){foreach($p in $data.PSObject.Properties){$named[$p.Name]=[string]$p.Value}}
& $ScriptPath @named
if($LASTEXITCODE){exit $LASTEXITCODE}
`
	if err = os.WriteFile(wrapper, []byte(wrapperSource), 0600); err != nil {
		return blocked(err)
	}
	names := make([]string, 0, len(req.Args))
	for name := range req.Args {
		names = append(names, name)
	}
	sort.Strings(names)
	res.LogicalCommand = m.Kind + " " + req.Repo + ":" + req.Script
	res.Meta["sourceCommit"] = m.TargetCommit
	res.Meta["argumentNames"] = names
	res.Meta["workingDir"] = workspaceReal
	stdout, stderr, code, runErr := r.Run(ctx, runSpec{exe: "powershell.exe", args: []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-File", wrapper, "-ScriptPath", real, "-ArgumentsPath", argsPath}, dir: workspaceReal, logical: res.LogicalCommand})
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if runErr != nil || code != 0 {
		res.Status = "failed"
		res.Error = fmt.Sprintf("script failed (exit=%d)", code)
		if ctx.Err() != nil {
			res.Error = ctx.Err().Error()
		}
	} else {
		res.Status = "ok"
		res.Output = remoteCheckpointSummary(stdout)
	}
	return res
}
