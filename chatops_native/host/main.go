package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	protocolVersion = "CHATOPS_NATIVE_V1"
	hostVersion     = "0.1.0"
	maxInputBytes   = 8 * 1024 * 1024
	maxOutputText   = 120 * 1024
	hostName        = "com.toctox.chatops_codex"
)

type request struct {
	Version      string `json:"version"`
	RequestID    string `json:"requestId"`
	Operation    string `json:"operation"`
	Prompt       string `json:"prompt,omitempty"`
	Workspace    string `json:"workspace,omitempty"`
	Sandbox      string `json:"sandbox,omitempty"`
	ConfirmWrite bool   `json:"confirmWrite,omitempty"`
	TimeoutSec   int    `json:"timeoutSec,omitempty"`
	JobID        string `json:"jobId,omitempty"`
}

type response struct {
	Version      string `json:"version"`
	Type         string `json:"type"`
	RequestID    string `json:"requestId,omitempty"`
	JobID        string `json:"jobId,omitempty"`
	Status       string `json:"status"`
	Error        string `json:"error,omitempty"`
	HostVersion  string `json:"hostVersion,omitempty"`
	CodexVersion string `json:"codexVersion,omitempty"`
	Sandbox      string `json:"sandbox,omitempty"`
	Workspace    string `json:"workspace,omitempty"`
	ExitCode     *int   `json:"exitCode,omitempty"`
	FinalMessage string `json:"finalMessage,omitempty"`
	StdoutTail   string `json:"stdoutTail,omitempty"`
	StderrTail   string `json:"stderrTail,omitempty"`
	StartedAt    string `json:"startedAt,omitempty"`
	FinishedAt   string `json:"finishedAt,omitempty"`
}

type job struct {
	mu           sync.Mutex
	id           string
	requestID    string
	status       string
	sandbox      string
	workspace    string
	startedAt    string
	finishedAt   string
	exitCode     *int
	finalMessage string
	stdout       string
	stderr       string
	errText      string
	cancel       context.CancelFunc
	canceled     bool
}

var (
	jobsMu sync.Mutex
	jobs   = map[string]*job{}

	writeMu sync.Mutex
)

func main() {
	for {
		raw, err := readNativeMessage(os.Stdin)
		if err != nil {
			if !errors.Is(err, io.EOF) {
				fmt.Fprintln(os.Stderr, "chatops native read:", err)
			}
			return
		}
		var req request
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&req); err != nil {
			_ = send(response{Version: protocolVersion, Type: "error", Status: "error", Error: "invalid request: " + err.Error()})
			continue
		}
		handle(req)
	}
}

func handle(req request) {
	if req.Version != protocolVersion {
		_ = send(response{Version: protocolVersion, Type: "error", RequestID: req.RequestID, Status: "error", Error: "unsupported protocol version"})
		return
	}
	switch req.Operation {
	case "ping":
		exe, ver, err := findCodex()
		if err != nil {
			_ = send(response{Version: protocolVersion, Type: "ping", RequestID: req.RequestID, Status: "error", HostVersion: hostVersion, Error: err.Error()})
			return
		}
		_ = exe
		_ = send(response{Version: protocolVersion, Type: "ping", RequestID: req.RequestID, Status: "ok", HostVersion: hostVersion, CodexVersion: ver})
	case "codex.exec":
		startCodex(req)
	case "codex.status":
		sendJobStatus(req)
	case "codex.cancel":
		cancelJob(req)
	default:
		_ = send(response{Version: protocolVersion, Type: "error", RequestID: req.RequestID, Status: "error", Error: "unsupported operation"})
	}
}

func startCodex(req request) {
	req.Prompt = strings.TrimSpace(req.Prompt)
	if req.Prompt == "" {
		_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "prompt is required"})
		return
	}
	if len(req.Prompt) > 256*1024 {
		_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "prompt exceeds 256 KiB"})
		return
	}

	sandbox := strings.TrimSpace(req.Sandbox)
	if sandbox == "" {
		sandbox = "read-only"
	}
	if sandbox != "read-only" && sandbox != "workspace-write" {
		_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "sandbox must be read-only or workspace-write"})
		return
	}
	if sandbox == "workspace-write" && !req.ConfirmWrite {
		_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "workspace-write requires confirmWrite=true"})
		return
	}

	workspace := strings.TrimSpace(req.Workspace)
	if workspace != "" {
		p, err := filepath.Abs(workspace)
		if err != nil {
			_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "workspace is invalid"})
			return
		}
		info, err := os.Stat(p)
		if err != nil || !info.IsDir() {
			_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "workspace does not exist or is not a directory"})
			return
		}
		workspace = p
	}

	timeout := req.TimeoutSec
	if timeout == 0 {
		timeout = 180
	}
	if timeout < 10 || timeout > 1800 {
		_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: "timeoutSec must be between 10 and 1800"})
		return
	}

	exe, codexVersion, err := findCodex()
	if err != nil {
		_ = send(response{Version: protocolVersion, Type: "codex.accepted", RequestID: req.RequestID, Status: "error", Error: err.Error()})
		return
	}

	jobID := fmt.Sprintf("J-%d", time.Now().UnixNano())
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	j := &job{
		id:        jobID,
		requestID: req.RequestID,
		status:    "running",
		sandbox:   sandbox,
		workspace: workspace,
		startedAt: time.Now().UTC().Format(time.RFC3339Nano),
		cancel:    cancel,
	}
	jobsMu.Lock()
	jobs[jobID] = j
	jobsMu.Unlock()

	_ = send(response{
		Version:      protocolVersion,
		Type:         "codex.accepted",
		RequestID:    req.RequestID,
		JobID:        jobID,
		Status:       "running",
		HostVersion:  hostVersion,
		CodexVersion: codexVersion,
		Sandbox:      sandbox,
		Workspace:    workspace,
		StartedAt:    j.startedAt,
	})

	go runCodex(ctx, exe, codexVersion, req.Prompt, j)
}

func runCodex(ctx context.Context, exe, codexVersion, prompt string, j *job) {
	defer j.cancel()

	root := filepath.Join(os.Getenv("LOCALAPPDATA"), "ChatOpsCodex", "runs", j.id)
	_ = os.MkdirAll(root, 0o700)
	finalPath := filepath.Join(root, "final-message.txt")

	args := []string{
		"exec",
		"--skip-git-repo-check",
		"--ephemeral",
		"--sandbox", j.sandbox,
		"--color", "never",
	}
	if j.workspace != "" {
		args = append(args, "--cd", j.workspace)
	}
	args = append(args, "--output-last-message", finalPath, "-")

	cmd := exec.CommandContext(ctx, exe, args...)
	if j.workspace != "" {
		cmd.Dir = j.workspace
	}
	cmd.Stdin = strings.NewReader(prompt)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	finished := time.Now().UTC().Format(time.RFC3339Nano)

	exitCode := -1
	if cmd.ProcessState != nil {
		exitCode = cmd.ProcessState.ExitCode()
	}

	finalMessage := ""
	if data, readErr := os.ReadFile(finalPath); readErr == nil {
		finalMessage = strings.TrimSpace(string(data))
	}
	if finalMessage == "" && err == nil {
		finalMessage = strings.TrimSpace(stdout.String())
	}
	finalMessage = truncate(finalMessage, maxOutputText)

	status := "succeeded"
	errText := ""
	if ctx.Err() == context.DeadlineExceeded {
		status = "timed_out"
		errText = "Codex execution timed out"
	} else {
		j.mu.Lock()
		wasCanceled := j.canceled
		j.mu.Unlock()
		if wasCanceled {
			status = "canceled"
			errText = "Codex execution canceled"
		} else if err != nil {
			status = "failed"
			errText = err.Error()
		}
	}

	j.mu.Lock()
	j.status = status
	j.finishedAt = finished
	j.exitCode = &exitCode
	j.finalMessage = finalMessage
	j.stdout = tail(stdout.String(), maxOutputText)
	j.stderr = tail(stderr.String(), maxOutputText)
	j.errText = errText
	j.mu.Unlock()

	_ = send(response{
		Version:      protocolVersion,
		Type:         "codex.result",
		RequestID:    j.requestID,
		JobID:        j.id,
		Status:       status,
		Error:        errText,
		HostVersion:  hostVersion,
		CodexVersion: codexVersion,
		Sandbox:      j.sandbox,
		Workspace:    j.workspace,
		ExitCode:     &exitCode,
		FinalMessage: finalMessage,
		StdoutTail:   tail(stdout.String(), 16*1024),
		StderrTail:   tail(stderr.String(), 16*1024),
		StartedAt:    j.startedAt,
		FinishedAt:   finished,
	})
}

func sendJobStatus(req request) {
	j := getJob(req.JobID)
	if j == nil {
		_ = send(response{Version: protocolVersion, Type: "codex.status", RequestID: req.RequestID, JobID: req.JobID, Status: "not_found", Error: "job not found"})
		return
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	_ = send(response{
		Version:      protocolVersion,
		Type:         "codex.status",
		RequestID:    req.RequestID,
		JobID:        j.id,
		Status:       j.status,
		Error:        j.errText,
		Sandbox:      j.sandbox,
		Workspace:    j.workspace,
		ExitCode:     j.exitCode,
		FinalMessage: truncate(j.finalMessage, maxOutputText),
		StdoutTail:   tail(j.stdout, 16*1024),
		StderrTail:   tail(j.stderr, 16*1024),
		StartedAt:    j.startedAt,
		FinishedAt:   j.finishedAt,
	})
}

func cancelJob(req request) {
	j := getJob(req.JobID)
	if j == nil {
		_ = send(response{Version: protocolVersion, Type: "codex.cancel", RequestID: req.RequestID, JobID: req.JobID, Status: "not_found", Error: "job not found"})
		return
	}
	j.mu.Lock()
	if j.status != "running" {
		status := j.status
		j.mu.Unlock()
		_ = send(response{Version: protocolVersion, Type: "codex.cancel", RequestID: req.RequestID, JobID: req.JobID, Status: status})
		return
	}
	j.canceled = true
	cancel := j.cancel
	j.mu.Unlock()
	cancel()
	_ = send(response{Version: protocolVersion, Type: "codex.cancel", RequestID: req.RequestID, JobID: req.JobID, Status: "cancel_requested"})
}

func getJob(id string) *job {
	jobsMu.Lock()
	defer jobsMu.Unlock()
	return jobs[id]
}

func findCodex() (string, string, error) {
	if explicit := strings.TrimSpace(os.Getenv("CODEX_EXE")); explicit != "" {
		if filepath.IsAbs(explicit) {
			if ver, err := validateCodex(explicit); err == nil {
				return explicit, ver, nil
			}
		}
	}

	local := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if local == "" {
		return "", "", errors.New("LOCALAPPDATA is not set")
	}
	matches, _ := filepath.Glob(filepath.Join(local, "OpenAI", "Codex", "bin", "*", "codex.exe"))
	type candidate struct {
		path string
		mod  time.Time
	}
	var candidates []candidate
	for _, p := range matches {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			candidates = append(candidates, candidate{path: p, mod: st.ModTime()})
		}
	}
	sort.Slice(candidates, func(i, k int) bool { return candidates[i].mod.After(candidates[k].mod) })
	for _, c := range candidates {
		if ver, err := validateCodex(c.path); err == nil {
			return c.path, ver, nil
		}
	}
	return "", "", errors.New("Codex executable was not found or failed validation")
}

func validateCodex(path string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, "--version")
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Run(); err != nil {
		return "", err
	}
	version := strings.TrimSpace(out.String())
	if version == "" {
		return "", errors.New("empty Codex version")
	}
	return truncate(version, 512), nil
}

func readNativeMessage(r io.Reader) ([]byte, error) {
	var length uint32
	if err := binary.Read(r, binary.LittleEndian, &length); err != nil {
		return nil, err
	}
	if length == 0 || length > maxInputBytes {
		return nil, fmt.Errorf("invalid native message length %d", length)
	}
	buf := make([]byte, length)
	_, err := io.ReadFull(r, buf)
	return buf, err
}

func send(v response) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if len(data) > 1024*1024 {
		v.FinalMessage = truncate(v.FinalMessage, 32*1024)
		v.StdoutTail = truncate(v.StdoutTail, 8*1024)
		v.StderrTail = truncate(v.StderrTail, 8*1024)
		data, err = json.Marshal(v)
		if err != nil {
			return err
		}
	}
	writeMu.Lock()
	defer writeMu.Unlock()
	if err := binary.Write(os.Stdout, binary.LittleEndian, uint32(len(data))); err != nil {
		return err
	}
	_, err = os.Stdout.Write(data)
	return err
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "\n...[truncated]"
}

func tail(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return "...[tail]\n" + s[len(s)-max:]
}

var _ = hostName
