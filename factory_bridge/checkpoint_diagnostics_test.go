package main

import (
	"strings"
	"testing"
)

func TestCheckpointEnvelopePublishesStructuredSanitizedDiagnostics(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	missionID := "M-structured-diagnostics"
	dir, err := missionLocalDir(missionID)
	if err != nil {
		t.Fatal(err)
	}
	code := 1
	res := Result{
		Status:   "failed",
		Stdout:   strings.Repeat("prefix-", 500) + "PHASE=build\nFINAL-STDOUT Password=hunter2",
		Stderr:   "file.cs(10,2): error xUnit2031 TOKEN=abc123",
		Error:    "exit status 1 Authorization: Bearer deadbeef",
		ExitCode: &code,
		Meta:     map[string]any{"trxSummary": "total=10 passed=9 failed=1 firstFailures=SecretTest token=trx-secret"},
	}
	evidence := missionEvidence{Results: map[string]Result{"system-command": res}}
	if err := writeMissionEvidence(dir, evidence); err != nil {
		t.Fatal(err)
	}

	envelope := checkpointEnvelope(MissionCheckpoint{MissionID: missionID, Kind: "system.command", State: "NEEDS_BRAIN", Summary: "failed", BridgeVersion: bridgeVersion})
	if envelope.Diagnostics == nil {
		t.Fatal("expected structured diagnostics")
	}
	d := *envelope.Diagnostics
	if d.Phase != "build" || d.FailureClass != "compile" || d.Confidence != "high" || d.ExitCode == nil || *d.ExitCode != 1 {
		t.Fatalf("unexpected diagnostics: %+v", d)
	}
	if !strings.Contains(d.StdoutTail, "FINAL-STDOUT") {
		t.Fatalf("stdout tail lost final cause: %q", d.StdoutTail)
	}
	for _, secret := range []string{"hunter2", "abc123", "deadbeef", "trx-secret"} {
		if strings.Contains(d.Error+d.StdoutTail+d.StderrTail+d.Evidence+d.TRXSummary, secret) {
			t.Fatalf("secret leaked in remote diagnostics: %s", secret)
		}
	}
}

func TestBlockedIntegrityCheckpointIsStructuredWithoutLocalEvidence(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	envelope := blockedEnvelope(Mission{ID: "M-integrity-structured", Kind: "system.command"}, "payloadHash does not match the canonical mission payload")
	if envelope.Diagnostics == nil {
		t.Fatal("expected diagnostics for blocked integrity failure")
	}
	if envelope.Diagnostics.FailureClass != "integrity_payload_hash" || envelope.Diagnostics.Confidence != "high" {
		t.Fatalf("unexpected integrity diagnosis: %+v", envelope.Diagnostics)
	}
}

func TestDoneCheckpointOmitsDiagnostics(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	envelope := checkpointEnvelope(MissionCheckpoint{MissionID: "M-done-no-diagnostics", Kind: "system.command", State: "DONE", Summary: "ok", BridgeVersion: bridgeVersion})
	if envelope.Diagnostics != nil {
		t.Fatalf("DONE checkpoint must not publish failure diagnostics: %+v", envelope.Diagnostics)
	}
}
