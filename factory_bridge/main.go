package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

const bridgeVersion = "0.14.0"

var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

type Config struct {
	BridgeRoot        string `json:"bridgeRoot"`
	DispatcherWorkDir string `json:"dispatcherWorkDir"`
	ProjectHubWorkDir string `json:"projectHubWorkDir,omitempty"`
	AllowGitPull      bool   `json:"allowGitPull"`
	AllowGitPush      bool   `json:"allowGitPush"`
	CommandTimeoutSec int    `json:"commandTimeoutSec"`
	PollIntervalMs    int    `json:"pollIntervalMs,omitempty"`
}

type Command struct {
	ID     string `json:"id"`
	Action string `json:"action"`
}

type Result struct {
	ID             string         `json:"id"`
	Action         string         `json:"action"`
	Status         string         `json:"status"`
	StartedAt      string         `json:"startedAt"`
	FinishedAt     string         `json:"finishedAt"`
	DurationMs     int64          `json:"durationMs"`
	LogicalCommand string         `json:"logicalCommand,omitempty"`
	ExitCode       *int           `json:"exitCode,omitempty"`
	Stdout         string         `json:"stdout,omitempty"`
	Stderr         string         `json:"stderr,omitempty"`
	Output         string         `json:"output,omitempty"`
	Error          string         `json:"error,omitempty"`
	Meta           map[string]any `json:"meta,omitempty"`
}

type runSpec struct {
	logical string
	exe     string
	args    []string
	dir     string
}

type runner interface {
	Run(context.Context, runSpec) (stdout, stderr string, exitCode int, err error)
}

type osRunner struct{}

func (osRunner) Run(ctx context.Context, spec runSpec) (string, string, int, error) {
	cmd := exec.CommandContext(ctx, spec.exe, spec.args...)
	cmd.Dir = spec.dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	code := 0
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			code = exitErr.ExitCode()
		} else {
			code = -1
		}
	}
	return truncate(stdout.String(), 128*1024), truncate(stderr.String(), 128*1024), code, err
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "\n...[truncated by FactoryBridge]"
}

func baseResult(cmd Command, start time.Time) Result {
	host, _ := os.Hostname()
	return Result{
		ID:        cmd.ID,
		Action:    cmd.Action,
		Status:    "failed",
		StartedAt: start.Format(time.RFC3339),
		Meta:      map[string]any{"bridgeVersion": bridgeVersion, "hostname": host},
	}
}

func finish(res *Result, start time.Time) {
	end := time.Now()
	res.FinishedAt = end.Format(time.RFC3339)
	res.DurationMs = end.Sub(start).Milliseconds()
}

func dispatcherScript(cfg Config, res *Result, start time.Time) (string, string, bool) {
	workDir := strings.TrimSpace(cfg.DispatcherWorkDir)
	if workDir == "" {
		res.Error = "dispatcherWorkDir is not configured"
		finish(res, start)
		return "", "", false
	}
	script := filepath.Join(workDir, "scripts", "run-tick.ps1")
	if stat, err := os.Stat(script); err != nil || stat.IsDir() {
		res.Error = "Dispatcher script not found: scripts\\run-tick.ps1"
		finish(res, start)
		return "", "", false
	}
	return workDir, script, true
}

func executeAction(cfg Config, cmd Command, r runner) Result {
	start := time.Now()
	res := baseResult(cmd, start)

	switch cmd.Action {
	case "bridge.ping":
		code := 0
		host, _ := os.Hostname()
		res.Status = "ok"
		res.ExitCode = &code
		res.LogicalCommand = "bridge.ping"
		res.Output = fmt.Sprintf("FactoryBridge %s active on %s (%s/%s)", bridgeVersion, host, runtime.GOOS, runtime.GOARCH)
		finish(&res, start)
		return res

	case "bridge.doctor":
		return executeBridgeDoctor(cfg, cmd, start)

	case "system.info":
		code := 0
		host, _ := os.Hostname()
		res.Status = "ok"
		res.ExitCode = &code
		res.LogicalCommand = "system.info"
		res.Output = fmt.Sprintf("hostname=%s os=%s arch=%s", host, runtime.GOOS, runtime.GOARCH)
		finish(&res, start)
		return res

	case "postgres.install":
		return executePostgresInstall(cfg, cmd, start, r)

	case "projecthub.status":
		return executeProjectHubStatusCanonical(cfg, cmd, start, r)

	case "projecthub.sync":
		return executeProjectHubSync(cfg, cmd, start, r)

	case "projecthub.build":
		return executeProjectHubBuildCanonical(cfg, cmd, start, r)

	case "projecthub.test":
		return executeProjectHubTestCanonical(cfg, cmd, start, r)

	case "projecthub.start":
		return executeProjectHubStartCanonical(cfg, cmd, start, r)

	case "projecthub.stop":
		return executeProjectHubStop(cfg, cmd, start)

	case "projecthub.logs":
		return executeProjectHubLogs(cfg, cmd, start)

	case "projecthub.validate":
		return executeProjectHubValidate(cfg, cmd, start, r)

	case "projecthub.snapshot":
		return executeProjectHubSnapshot(cfg, cmd, start, r)

	case "projecthub.verify":
		return executeProjectHubVerify(cfg, cmd, start, r)

	case "projecthub.refresh_validate":
		return executeProjectHubRefreshValidate(cfg, cmd, start, r)

	case "git.status":
		if strings.TrimSpace(cfg.DispatcherWorkDir) == "" {
			res.Error = "dispatcherWorkDir is not configured"
			finish(&res, start)
			return res
		}
		return runKnown(cfg, cmd, start, r, runSpec{
			logical: "git status --short --branch",
			exe:     "git.exe",
			args:    []string{"-C", cfg.DispatcherWorkDir, "status", "--short", "--branch"},
			dir:     cfg.DispatcherWorkDir,
		})

	case "git.pull":
		if !cfg.AllowGitPull {
			res.Error = "git.pull is disabled locally (allowGitPull=false)"
			finish(&res, start)
			return res
		}
		if strings.TrimSpace(cfg.DispatcherWorkDir) == "" {
			res.Error = "dispatcherWorkDir is not configured"
			finish(&res, start)
			return res
		}
		return runKnown(cfg, cmd, start, r, runSpec{
			logical: "git pull --ff-only",
			exe:     "git.exe",
			args:    []string{"-C", cfg.DispatcherWorkDir, "pull", "--ff-only"},
			dir:     cfg.DispatcherWorkDir,
		})

	case "git.push":
		if !cfg.AllowGitPush {
			res.Error = "git.push is disabled locally (allowGitPush=false)"
			finish(&res, start)
			return res
		}
		if strings.TrimSpace(cfg.DispatcherWorkDir) == "" {
			res.Error = "dispatcherWorkDir is not configured"
			finish(&res, start)
			return res
		}
		return runKnown(cfg, cmd, start, r, runSpec{
			logical: "git push --porcelain",
			exe:     "git.exe",
			args:    []string{"-C", cfg.DispatcherWorkDir, "push", "--porcelain"},
			dir:     cfg.DispatcherWorkDir,
		})

	case "dispatcher.test":
		workDir, script, ok := dispatcherScript(cfg, &res, start)
		if !ok {
			return res
		}
		return runKnown(cfg, cmd, start, r, runSpec{
			logical: "scripts\\run-tick.ps1 -DryRun",
			exe:     "powershell.exe",
			args:    []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-DryRun"},
			dir:     workDir,
		})

	case "dispatcher.tick":
		workDir, script, ok := dispatcherScript(cfg, &res, start)
		if !ok {
			return res
		}
		return runKnown(cfg, cmd, start, r, runSpec{
			logical: "scripts\\run-tick.ps1",
			exe:     "powershell.exe",
			args:    []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script},
			dir:     workDir,
		})

	default:
		res.Error = "unsupported action"
		finish(&res, start)
		return res
	}
}

func runKnown(cfg Config, cmd Command, start time.Time, r runner, spec runSpec) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = spec.logical
	timeout := cfg.CommandTimeoutSec
	if timeout <= 0 {
		timeout = 120
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()
	stdout, stderr, code, err := r.Run(ctx, spec)
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if err == nil && code == 0 {
		res.Status = "ok"
	} else {
		res.Status = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if err != nil {
			res.Error = err.Error()
		}
	}
	finish(&res, start)
	return res
}

func decodeCommand(path string) (Command, error) {
	f, err := os.Open(path)
	if err != nil {
		return Command{}, err
	}
	defer f.Close()
	dec := json.NewDecoder(io.LimitReader(f, 64*1024))
	dec.DisallowUnknownFields()
	var cmd Command
	if err := dec.Decode(&cmd); err != nil {
		return Command{}, err
	}
	if !idPattern.MatchString(cmd.ID) {
		return Command{}, errors.New("invalid id")
	}
	if strings.TrimSpace(cmd.Action) == "" {
		return Command{}, errors.New("invalid action")
	}
	return cmd, nil
}

func writeJSONAtomic(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func archiveCommand(path, archiveDir, id string) error {
	if err := os.MkdirAll(archiveDir, 0o755); err != nil {
		return err
	}
	name := fmt.Sprintf("%s__%s__%s", time.Now().Format("20060102_150405"), id, filepath.Base(path))
	dst := filepath.Join(archiveDir, name)
	if err := os.Rename(path, dst); err == nil {
		return nil
	}
	src, err := os.Open(path)
	if err != nil {
		return err
	}
	defer src.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err = io.Copy(out, src); err != nil {
		out.Close()
		return err
	}
	if err = out.Close(); err != nil {
		return err
	}
	return os.Remove(path)
}

func processOne(cfg Config, path string, r runner) error {
	resultsDir := filepath.Join(cfg.BridgeRoot, "02_RESULTS")
	archiveDir := filepath.Join(cfg.BridgeRoot, "03_ARCHIVE")
	if err := os.MkdirAll(resultsDir, 0o755); err != nil {
		return err
	}

	cmd, err := decodeCommand(path)
	if err != nil {
		badID := fmt.Sprintf("BAD-%d", time.Now().UnixNano())
		start := time.Now()
		res := baseResult(Command{ID: badID, Action: "invalid_command"}, start)
		res.Error = err.Error()
		finish(&res, start)
		_ = writeJSONAtomic(filepath.Join(resultsDir, "RESULT__"+badID+".json"), res)
		return archiveCommand(path, archiveDir, badID)
	}

	resultPath := filepath.Join(resultsDir, "RESULT__"+cmd.ID+".json")
	if _, err := os.Stat(resultPath); err == nil {
		return archiveCommand(path, archiveDir, cmd.ID)
	}

	res := executeAction(cfg, cmd, r)
	if err := writeJSONAtomic(resultPath, res); err != nil {
		return err
	}
	return archiveCommand(path, archiveDir, cmd.ID)
}

func loadConfig(path string) (Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var cfg Config
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&cfg); err != nil {
		return Config{}, err
	}
	if strings.TrimSpace(cfg.BridgeRoot) == "" {
		return Config{}, errors.New("bridgeRoot is empty")
	}
	if cfg.CommandTimeoutSec <= 0 {
		cfg.CommandTimeoutSec = 120
	}
	if cfg.PollIntervalMs <= 0 {
		cfg.PollIntervalMs = 1000
	}
	return cfg, nil
}

func configPath() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	return filepath.Join(base, "FactoryBridge", "config.json"), nil
}

func selectedMode(args []string) (string, error) {
	mode := "executor"
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--mode":
			if i+1 >= len(args) {
				return "", errors.New("--mode requires executor, supervisor, or panel")
			}
			i++
			mode = strings.ToLower(strings.TrimSpace(args[i]))
		default:
			return "", fmt.Errorf("unsupported argument: %s", args[i])
		}
	}
	if mode != "executor" && mode != "supervisor" && mode != "panel" {
		return "", fmt.Errorf("unsupported mode: %s", mode)
	}
	return mode, nil
}

func main() {
	mode, err := selectedMode(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "FactoryBridge:", err)
		os.Exit(2)
	}
	cfgPath, err := configPath()
	if err != nil {
		fmt.Fprintln(os.Stderr, "FactoryBridge:", err)
		os.Exit(2)
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "FactoryBridge:", err)
		os.Exit(2)
	}

	switch mode {
	case "supervisor":
		if err := runSupervisor(cfg); err != nil {
			fmt.Fprintln(os.Stderr, "FactoryBridge supervisor:", err)
			os.Exit(1)
		}
	case "panel":
		if err := runPanel(cfg); err != nil {
			fmt.Fprintln(os.Stderr, "FactoryBridge panel:", err)
			os.Exit(1)
		}
	default:
		if err := runExecutor(cfg); err != nil {
			fmt.Fprintln(os.Stderr, "FactoryBridge executor:", err)
			os.Exit(1)
		}
	}
}
