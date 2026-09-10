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

func TestSystemCommandFailureSummaryRedactsSecrets(t *testing.T) {
	res := failedResult("PHASE=test\nPassword=hunter2;Host=127.0.0.1\nTOKEN=abc123\nAssert.Equal failed", "", "", 1)
	s := systemCommandFailureSummary(res)
	for _, forbidden := range []string{"hunter2", "abc123"} {
		if strings.Contains(s, forbidden) {
			t.Fatalf("secret leaked in summary: %s", s)
		}
	}
	if !strings.Contains(s, "[REDACTED]") {
		t.Fatalf("expected redaction marker: %s", s)
	}
}

func TestDiagnoseSystemCommandFailureUnknownDoesNotInvent(t *testing.T) {
	d := diagnoseSystemCommandFailure(failedResult("PHASE=custom\nunclassified failure text", "", "exit status 7", 7))
	if d.Phase != "custom" || d.Class != "unknown" || d.Confidence != "low" {
		t.Fatalf("unexpected diagnosis: %+v", d)
	}
}

func TestDiagnoseSystemCommandFailurePayloadHash(t *testing.T) {
	d := diagnoseSystemCommandFailure(failedResult("", "payloadHash does not match the canonical mission payload", "", 1))
	if d.Class != "integrity_payload_hash" || d.Confidence != "high" {
		t.Fatalf("unexpected diagnosis: %+v", d)
	}
}
