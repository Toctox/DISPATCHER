package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type systemCommandRequest struct {
	Shell        string `json:"shell"`
	Command      string `json:"command"`
	WorkingDir   string `json:"workingDir,omitempty"`
	RiskApproval string `json:"riskApproval,omitempty"`
	TimeoutSec   int    `json:"timeoutSec,omitempty"`
}

type systemCommandRisk struct {
	Level            string
	Rule             string
	Reason           string
	RequiresApproval bool
	Forbidden        bool
}

type systemCommandRiskRule struct {
	Name    string
	Reason  string
	Pattern *regexp.Regexp
}

var forbiddenSystemCommandRules = []systemCommandRiskRule{
	{Name: "disk-format", Reason: "disk formatting or partition destruction is never executed automatically", Pattern: regexp.MustCompile(`(?i)\b(format(?:\.com)?\s+[a-z]:|diskpart(?:\.exe)?\b|clear-disk\b|remove-partition\b|initialize-disk\b)`)} ,
	{Name: "security-disable", Reason: "disabling endpoint security is never executed automatically", Pattern: regexp.MustCompile(`(?i)\b(set-mppreference\b[^\r\n;|]*(disable(realtime|behavior|ioav|script)monitoring|disableintrusionpreventionsystem)|add-mppreference\b[^\r\n;|]*-exclusion(path|process|extension))`)},
	{Name: "credential-dump", Reason: "credential or LSASS extraction is never executed automatically", Pattern: regexp.MustCompile(`(?i)\b(mimikatz|sekurlsa|procdump(?:\.exe)?\b[^\r\n;|]*\blsass\b|reg(?:\.exe)?\s+save\s+hklm\\(sam|security|system)\b)`)} ,
	{Name: "obfuscated-execution", Reason: "obfuscated or dynamically evaluated command text is not accepted", Pattern: regexp.MustCompile(`(?i)(-encodedcommand\b|frombase64string\s*\(|\binvoke-expression\b|(^|[;&|]\s*)iex\s*[\s(])`)},
	{Name: "critical-root-delete", Reason: "deleting a filesystem root is never executed automatically", Pattern: regexp.MustCompile(`(?i)(\bremove-item\b[^\r\n;|]{0,160}\b[a-z]:\\(?:[\s'\"]|$)|(^|[;&|]\s*)(rd|rmdir|del|erase)(?:\.exe)?\b[^\r\n;|]{0,160}\b[a-z]:\\(?:[\s'\"]|$))`)},
}

var approvalSystemCommandRules = []systemCommandRiskRule{
	{Name: "file-delete", Reason: "file or directory deletion requires explicit approval", Pattern: regexp.MustCompile(`(?i)(\bremove-item\b|(^|[;&|]\s*)(del|erase|rd|rmdir)(?:\.exe)?\s+)`)},
	{Name: "git-destructive", Reason: "destructive or force Git operations require explicit approval", Pattern: regexp.MustCompile(`(?i)\bgit(?:\.exe)?\s+([^\r\n;&|]*\s)?(reset\s+--hard|clean\b|push\b[^\r\n;&|]*(--force(?:-with-lease)?|-f)(\s|$)|branch\s+-D\b)`)},
	{Name: "registry-delete", Reason: "registry deletion requires explicit approval", Pattern: regexp.MustCompile(`(?i)\breg(?:\.exe)?\s+delete\b`)},
	{Name: "service-delete", Reason: "service deletion requires explicit approval", Pattern: regexp.MustCompile(`(?i)(\bsc(?:\.exe)?\s+delete\b|\bremove-service\b)`)},
	{Name: "scheduled-task-delete", Reason: "scheduled task deletion requires explicit approval", Pattern: regexp.MustCompile(`(?i)\bunregister-scheduledtask\b`)},
	{Name: "shutdown-restart", Reason: "machine shutdown or restart requires explicit approval", Pattern: regexp.MustCompile(`(?i)(\bshutdown(?:\.exe)?\b|\brestart-computer\b|\bstop-computer\b)`)},
	{Name: "acl-change", Reason: "permission and ACL changes require explicit approval", Pattern: regexp.MustCompile(`(?i)(\bset-acl\b|\bicacls(?:\.exe)?\b[^\r\n;&|]*\s/(grant|deny|remove|reset)\b)`)},
	{Name: "security-config", Reason: "firewall or endpoint-security changes require explicit approval", Pattern: regexp.MustCompile(`(?i)(\bset-mppreference\b|\badd-mppreference\b|\bnetsh(?:\.exe)?\b[^\r\n;&|]*advfirewall[^\r\n;&|]*state\s+off\b|\bset-netfirewallprofile\b[^\r\n;&|]*-enabled\s+false\b)`)},
	{Name: "global-uninstall", Reason: "global software removal requires explicit approval", Pattern: regexp.MustCompile(`(?i)(\bwinget(?:\.exe)?\s+uninstall\b|\bchoco(?:\.exe)?\s+uninstall\b|\bmsiexec(?:\.exe)?\b[^\r\n;&|]*/x\b)`)},
}

var guardedSystemCommandRules = []systemCommandRiskRule{
	{Name: "filesystem-write", Reason: "filesystem state change is allowed and audited", Pattern: regexp.MustCompile(`(?i)(\b(set-content|add-content|out-file|new-item|copy-item|move-item|rename-item)\b|(^|[;&|]\s*)(copy|xcopy|robocopy|move|ren|rename|mkdir|md)(?:\.exe)?\s+)`)},
	{Name: "process-service", Reason: "process or service state change is allowed and audited", Pattern: regexp.MustCompile(`(?i)\b(start-process|stop-process|start-service|stop-service|restart-service)\b`)},
	{Name: "scheduled-task", Reason: "non-destructive scheduled-task operations are allowed and audited", Pattern: regexp.MustCompile(`(?i)\b(start-scheduledtask|stop-scheduledtask|register-scheduledtask|set-scheduledtask|enable-scheduledtask|disable-scheduledtask)\b`)},
	{Name: "git-write", Reason: "non-destructive Git write operation is allowed and audited", Pattern: regexp.MustCompile(`(?i)\bgit(?:\.exe)?\s+([^\r\n;&|]*\s)?(add|commit|merge|rebase|checkout|switch|pull|push|fetch)\b`)},
	{Name: "package-change", Reason: "package installation or update is allowed and audited", Pattern: regexp.MustCompile(`(?i)\b(winget(?:\.exe)?\s+(install|upgrade)|choco(?:\.exe)?\s+(install|upgrade)|npm(?:\.cmd|\.exe)?\s+(install|update)|pip(?:\.exe)?\s+install|dotnet(?:\.exe)?\s+(restore|tool\s+install))\b`)},
}

func decodeSystemCommandObjective(mission Mission) (systemCommandRequest, error) {
	var req systemCommandRequest
	raw := strings.TrimSpace(mission.Objective)
	if raw == "" {
		return req, errors.New("system.command objective must contain JSON command parameters")
	}
	if len(raw) > 32*1024 {
		return req, errors.New("system.command objective exceeds 32 KiB")
	}
	dec := json.NewDecoder(strings.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		return req, fmt.Errorf("invalid system.command objective: %w", err)
	}
	req.Shell = strings.ToLower(strings.TrimSpace(req.Shell))
	switch req.Shell {
	case "powershell", "cmd":
	default:
		return req, errors.New("shell must be powershell or cmd")
	}
	req.Command = strings.TrimSpace(req.Command)
	if req.Command == "" {
		return req, errors.New("command is required")
	}
	if len(req.Command) > 16*1024 {
		return req, errors.New("command exceeds 16 KiB")
	}
	if strings.ContainsRune(req.Command, '\x00') || strings.ContainsRune(req.WorkingDir, '\x00') {
		return req, errors.New("command parameters contain NUL")
	}
	req.WorkingDir = strings.TrimSpace(req.WorkingDir)
	req.RiskApproval = strings.ToLower(strings.TrimSpace(req.RiskApproval))
	if req.RiskApproval != "" && req.RiskApproval != "approved" {
		return req, errors.New("riskApproval must be omitted or set to approved")
	}
	if req.TimeoutSec < 0 || req.TimeoutSec > 1800 {
		return req, errors.New("timeoutSec must be between 0 and 1800")
	}
	return req, nil
}

func classifySystemCommand(command string) systemCommandRisk {
	for _, rule := range forbiddenSystemCommandRules {
		if rule.Pattern.MatchString(command) {
			return systemCommandRisk{Level: "forbidden", Rule: rule.Name, Reason: rule.Reason, Forbidden: true}
		}
	}
	for _, rule := range approvalSystemCommandRules {
		if rule.Pattern.MatchString(command) {
			return systemCommandRisk{Level: "approval", Rule: rule.Name, Reason: rule.Reason, RequiresApproval: true}
		}
	}
	for _, rule := range guardedSystemCommandRules {
		if rule.Pattern.MatchString(command) {
			return systemCommandRisk{Level: "guarded", Rule: rule.Name, Reason: rule.Reason}
		}
	}
	return systemCommandRisk{Level: "standard", Rule: "default", Reason: "command is not classified as destructive or sensitive"}
}

func resolveSystemCommandWorkingDir(cfg Config, requested string) (string, error) {
	dir := strings.TrimSpace(requested)
	if dir == "" {
		dir = strings.TrimSpace(cfg.DispatcherWorkDir)
	}
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		dir = home
	}
	if !filepath.IsAbs(dir) && strings.TrimSpace(cfg.DispatcherWorkDir) != "" {
		dir = filepath.Join(cfg.DispatcherWorkDir, dir)
	}
	dir = filepath.Clean(dir)
	info, err := os.Stat(dir)
	if err != nil {
		return "", fmt.Errorf("workingDir unavailable: %w", err)
	}
	if !info.IsDir() {
		return "", errors.New("workingDir is not a directory")
	}
	return dir, nil
}

func executeSystemCommand(cfg Config, mission Mission, start time.Time, r runner) Result {
	res := baseResult(Command{ID: mission.ID, Action: "system.command"}, start)
	req, err := decodeSystemCommandObjective(mission)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	risk := classifySystemCommand(req.Command)
	res.Meta["riskLevel"] = risk.Level
	res.Meta["riskRule"] = risk.Rule
	res.Meta["riskReason"] = risk.Reason
	res.Meta["shell"] = req.Shell
	res.LogicalCommand = fmt.Sprintf("system.command shell=%s risk=%s rule=%s", req.Shell, risk.Level, risk.Rule)
	if risk.Forbidden {
		res.Status = "blocked"
		res.Error = "command blocked: " + risk.Reason
		res.Meta["forbidden"] = true
		finish(&res, start)
		return res
	}
	if risk.RequiresApproval && req.RiskApproval != "approved" {
		res.Status = "blocked"
		res.Error = "command requires explicit riskApproval=approved: " + risk.Reason
		res.Meta["requiresApproval"] = true
		finish(&res, start)
		return res
	}
	if risk.RequiresApproval {
		res.Meta["riskApproved"] = true
	}
	dir, err := resolveSystemCommandWorkingDir(cfg, req.WorkingDir)
	if err != nil {
		res.Error = err.Error()
		finish(&res, start)
		return res
	}
	res.Meta["workingDir"] = dir

	timeout := req.TimeoutSec
	if timeout == 0 {
		timeout = cfg.CommandTimeoutSec
	}
	if timeout <= 0 {
		timeout = 120
	}
	if timeout > 1800 {
		timeout = 1800
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	spec := runSpec{dir: dir}
	if req.Shell == "powershell" {
		spec.exe = "powershell.exe"
		spec.args = []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", req.Command}
	} else {
		spec.exe = "cmd.exe"
		spec.args = []string{"/d", "/s", "/c", req.Command}
	}
	spec.logical = res.LogicalCommand
	stdout, stderr, code, runErr := r.Run(ctx, spec)
	res.Stdout, res.Stderr, res.ExitCode = stdout, stderr, &code
	if runErr == nil && code == 0 {
		res.Status = "ok"
		res.Output = compact(strings.TrimSpace(stdout), 1200)
		if res.Output == "" {
			res.Output = fmt.Sprintf("Command completed successfully (risk=%s).", risk.Level)
		}
	} else {
		res.Status = "failed"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			res.Error = fmt.Sprintf("command timed out after %ds", timeout)
		} else if runErr != nil {
			res.Error = runErr.Error()
		} else {
			res.Error = fmt.Sprintf("command exited with code %d", code)
		}
	}
	finish(&res, start)
	return res
}
