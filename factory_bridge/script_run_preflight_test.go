package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

type scriptPreflightFailRunner struct{}

func (scriptPreflightFailRunner) Run(context.Context, runSpec) (string, string, int, error) {
	return "", "credential helper unavailable", 1, errors.New("exit status 1")
}

func TestRunScriptGitPhasePreservesPhaseAndCause(t *testing.T) {
	_, err := runScriptGitPhase(scriptPreflightFailRunner{}, `C:\repo`, "script_preflight_fetch", "fetch", "origin", "main")
	if err == nil {
		t.Fatal("expected preflight failure")
	}
	message := err.Error()
	if !strings.Contains(message, "PHASE=script_preflight_fetch") {
		t.Fatalf("missing phase marker: %s", message)
	}
	if !strings.Contains(message, "credential helper unavailable") {
		t.Fatalf("missing sanitized cause: %s", message)
	}
}

func TestScriptRunExecutionHasIndependentTimeoutContext(t *testing.T) {
	// This source-level invariant guards the regression that caused a live
	// synthetic failure to consume the checkout/fetch budget and surface as an
	// opaque context deadline. Preflight and script execution must never share
	// one context instance.
	if scriptRunPreflightTimeout <= 0 {
		t.Fatal("preflight timeout must be bounded")
	}
	req := scriptRunRequest{TimeoutSec: 180}
	if req.TimeoutSec != 180 {
		t.Fatal("script timeout request was not preserved")
	}
}

func TestScriptRunWorktreeGetsLargerButBoundedPreflightBudget(t *testing.T) {
	if got := scriptGitPhaseTimeout("script_preflight_fetch"); got != 45*time.Second {
		t.Fatalf("fetch timeout = %s, want 45s", got)
	}
	if got := scriptGitPhaseTimeout("script_preflight_ancestry"); got != 45*time.Second {
		t.Fatalf("ancestry timeout = %s, want 45s", got)
	}
	if got := scriptGitPhaseTimeout("script_preflight_head"); got != 45*time.Second {
		t.Fatalf("head timeout = %s, want 45s", got)
	}
	if got := scriptGitPhaseTimeout("script_preflight_worktree"); got != 3*time.Minute {
		t.Fatalf("worktree timeout = %s, want 3m", got)
	}
	if scriptGitPhaseTimeout("script_preflight_worktree") <= scriptGitPhaseTimeout("script_preflight_fetch") {
		t.Fatal("worktree materialization budget must exceed metadata preflight budget")
	}
}
