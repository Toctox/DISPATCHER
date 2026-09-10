package main

import (
	"strings"
	"testing"
)

func failedResult(stdout, stderr, errText string, code int) Result {
	return Result{Status: "failed", Stdout: stdout, Stderr: stderr, Error: errText, ExitCode: &code}
}

func TestDiagnoseSystemCommandFailureCompileAndPhase(t *testing.T) {
	d := diagnoseSystemCommandFailure(failedResult("PHASE=build\nfile.cs(10,2): error xUnit2031: Prefer Assert.Single", "", "exit status 1", 1))
	if d.Phase != "build" || d.Class != "compile" || d.Confidence != "high" {
		t.Fatalf("unexpected diagnosis: %+v", d)
	}
}

func TestDiagnoseSystemCommandFailureDependencyLock(t *testing.T) {
	d := diagnoseSystemCommandFailure(failedResult("PHASE=restore\nerror NU1004: packages.lock.json is inconsistent with project dependencies", "", "", 1))
	if d.Phase != "restore" || d.Class != "dependency_lock" || d.Confidence != "high" {
		t.Fatalf("unexpected diagnosis: %+v", d)
	}
}

func TestRemoteCheckpointSummaryRedactsSuccessSecrets(t *testing.T) {
	s := remoteCheckpointSummary("OK Password=hunter2;Host=127.0.0.1 TOKEN=abc123 Authorization: Bearer deadbeef")
	for _, forbidden := range []string{"hunter2", "abc123", "deadbeef"} {
		if strings.Contains(s, forbidden) {
			t.Fatalf("secret leaked: %s", s)
		}
	}
	if !strings.Contains(s, "[REDACTED]") {
		t.Fatalf("expected redaction marker: %s", s)
	}
}

func TestRemoteCheckpointSummaryPreservesTail(t *testing.T) {
	input := strings.Repeat("prefix-", 1000) + "FINAL-CAUSE"
	got := remoteCheckpointSummary(input)
	if !strings.Contains(got, "FINAL-CAUSE") {
		t.Fatalf("tail cause was lost: %s", got)
	}
	if len(got) > 4096 {
		t.Fatalf("summary too large: %d", len(got))
	}
}

func TestSystemCommandFailureSummaryUsesUnknownWhenUnclassified(t *testing.T) {
	s := systemCommandFailureSummary(failedResult("PHASE=custom\nunclassified failure", "", "exit status 7", 7))
	if !strings.Contains(s, "phase=custom") || !strings.Contains(s, "class=unknown") || !strings.Contains(s, "confidence=low") {
		t.Fatalf("unexpected summary: %s", s)
	}
}

func TestDiagnoseSystemCommandFailurePayloadHash(t *testing.T) {
	d := diagnoseSystemCommandFailure(failedResult("", "payloadHash does not match the canonical mission payload", "", 1))
	if d.Class != "integrity_payload_hash" || d.Confidence != "high" {
		t.Fatalf("unexpected diagnosis: %+v", d)
	}
}
