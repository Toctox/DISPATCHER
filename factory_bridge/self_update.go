package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	adminUpdaterTaskName       = "FactoryBridge Admin Updater"
	selfUpdateRequestFileName = "update-request.json"
	selfUpdateResultFileName  = "update-result.json"
)

type selfUpdateRequest struct {
	MissionID       string `json:"missionId"`
	CandidateCommit string `json:"candidateCommit"`
	PayloadHash     string `json:"payloadHash"`
	RequestedAt     string `json:"requestedAt"`
}

type selfUpdateResult struct {
	MissionID       string `json:"missionId"`
	CandidateCommit string `json:"candidateCommit"`
	Status          string `json:"status"`
	Detail          string `json:"detail,omitempty"`
	ObservedAt      string `json:"observedAt"`
}

func selfUpdateAdminDir() (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	dir := filepath.Join(base, "FactoryBridge", "admin")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	return dir, nil
}

func selfUpdateRequestPath() (string, error) {
	dir, err := selfUpdateAdminDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, selfUpdateRequestFileName), nil
}

func selfUpdateResultPath() (string, error) {
	dir, err := selfUpdateAdminDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, selfUpdateResultFileName), nil
}

func runDispatcherGit(ctx context.Context, r runner, workDir string, args ...string) (string, error) {
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

func ensureDispatcherUpdateCandidate(cfg Config, mission Mission, r runner) error {
	workDir := strings.TrimSpace(cfg.DispatcherWorkDir)
	if workDir == "" {
		return errors.New("dispatcherWorkDir is not configured")
	}
	if stat, err := os.Stat(workDir); err != nil || !stat.IsDir() {
		return errors.New("dispatcherWorkDir is unavailable")
	}
	candidate := strings.ToLower(strings.TrimSpace(mission.TargetCommit))
	var lastErr error
	for attempt, delay := range missionGitFetchRetryDelays {
		if attempt > 0 && delay > 0 {
			missionSleep(delay)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		_, err := runDispatcherGit(ctx, r, workDir, "fetch", "--prune", "origin", "main")
		cancel()
		if err == nil {
			lastErr = nil
			break
		}
		lastErr = err
		if !isTransientGitNetworkError(err) {
			return err
		}
	}
	if lastErr != nil {
		return fmt.Errorf("dispatcher fetch remained unavailable after retries: %w", lastErr)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	origin, err := runDispatcherGit(ctx, r, workDir, "rev-parse", "origin/main")
	if err != nil {
		return err
	}
	origin = strings.ToLower(strings.TrimSpace(origin))
	if origin != candidate {
		return fmt.Errorf("self-update candidate is no longer current origin/main: authorized=%s origin/main=%s", candidate, origin)
	}
	return nil
}

func writeSelfUpdateRequest(mission Mission) error {
	path, err := selfUpdateRequestPath()
	if err != nil {
		return err
	}
	resultPath, err := selfUpdateResultPath()
	if err != nil {
		return err
	}
	_ = os.Remove(resultPath)
	request := selfUpdateRequest{
		MissionID:       mission.ID,
		CandidateCommit: strings.ToLower(strings.TrimSpace(mission.TargetCommit)),
		PayloadHash:     strings.ToLower(strings.TrimSpace(mission.PayloadHash)),
		RequestedAt:     time.Now().UTC().Format(time.RFC3339Nano),
	}
	return writeJSONAtomic(path, request)
}

func triggerSelfUpdateTask(r runner) error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	stdout, stderr, code, err := r.Run(ctx, runSpec{
		logical: "run fixed FactoryBridge Admin Updater scheduled task",
		exe:     "schtasks.exe",
		args:    []string{"/Run", "/TN", adminUpdaterTaskName},
	})
	if err != nil || code != 0 {
		message := strings.TrimSpace(stderr)
		if message == "" {
			message = strings.TrimSpace(stdout)
		}
		if message == "" && err != nil {
			message = err.Error()
		}
		return fmt.Errorf("fixed admin updater task could not be started: %s", message)
	}
	return nil
}

func selfUpdateRecoveryCheckpoint(mission Mission, journal missionJournal) (*MissionCheckpoint, bool) {
	path, err := selfUpdateResultPath()
	if err != nil {
		return nil, false
	}
	result, err := readJSONFile[selfUpdateResult](path)
	if err != nil || result == nil {
		return nil, false
	}
	if result.MissionID != mission.ID || !strings.EqualFold(result.CandidateCommit, mission.TargetCommit) {
		return nil, false
	}
	cp := &MissionCheckpoint{
		MissionID:     mission.ID,
		Kind:          mission.Kind,
		StartedAt:     journal.UpdatedAt,
		FinishedAt:    strings.TrimSpace(result.ObservedAt),
		Objective:     mission.Objective,
		Commit:        strings.ToLower(strings.TrimSpace(result.CandidateCommit)),
		BridgeVersion: bridgeVersion,
	}
	if cp.FinishedAt == "" {
		cp.FinishedAt = time.Now().UTC().Format(time.RFC3339)
	}
	switch strings.ToLower(strings.TrimSpace(result.Status)) {
	case "ok":
		cp.State = "DONE"
		cp.Summary = "FactoryBridge self-update promoted the authorized commit and the restarted runtime passed its local health check."
	default:
		cp.State = "NEEDS_BRAIN"
		cp.Summary = "FactoryBridge privileged self-update did not complete cleanly: " + compact(result.Detail, 600)
		cp.DecisionQuestion = "Review the privileged updater result and choose whether to retry with a new mission id or use golden recovery."
	}
	return cp, true
}
