package main

import (
	"context"
	"fmt"
	"strings"
	"time"
)

var missionGitFetchRetryDelays = []time.Duration{0, 2 * time.Second, 5 * time.Second}

func isTransientGitNetworkError(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	for _, marker := range []string{
		"could not resolve host",
		"temporary failure in name resolution",
		"failed to connect",
		"connection reset",
		"connection timed out",
		"operation timed out",
		"tls handshake timeout",
		"remote end hung up unexpectedly",
		"network is unreachable",
	} {
		if strings.Contains(message, marker) {
			return true
		}
	}
	return false
}

func fetchProjectHubOriginMainWithRetry(cfg Config, mission Mission, r runner, workDir string) error {
	var lastErr error
	for attempt, delay := range missionGitFetchRetryDelays {
		if err := validateMissionIntegrity(mission); err != nil {
			return err
		}
		if err := waitForMissionPermission(mission.ID); err != nil {
			return err
		}
		if delay > 0 {
			time.Sleep(delay)
			if err := validateMissionIntegrity(mission); err != nil {
				return err
			}
			if err := waitForMissionPermission(mission.ID); err != nil {
				return err
			}
		}

		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		_, err := runProjectHubGit(ctx, r, workDir, "fetch", "origin", "main")
		cancel()
		if err == nil {
			return nil
		}
		lastErr = err
		if !isTransientGitNetworkError(err) {
			return err
		}
		_ = attempt
	}
	return fmt.Errorf("git fetch origin main failed after %d transient attempts: %w", len(missionGitFetchRetryDelays), lastErr)
}
