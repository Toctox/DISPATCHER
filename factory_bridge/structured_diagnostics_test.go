package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStructuredCheckpointDiagnosticFromEvidence(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	id := "M-structured-diagnostic"
	dir, err := missionLocalDir(id)
	if err != nil {
		t.Fatal(err)
	}
	code := 1
	result := Result{
		ID: id, Action: "system.command", Status: "failed",
		StartedAt: time.Now().Add(-time.Second).Format(time.RFC3339),
		ExitCode: &code,
		Stdout: "PHASE=build\n" + strings.Repeat("prefix\n", 400) + "FINAL STDOUT CAUSE Password=hunter2",
		Stderr: "file.cs(10,2): error xUnit2031: analyzer failed token=abc123",
		Error: "exit status 1",
		Meta: map[string]any{"workingDir": t.TempDir()},
	}
	evidence := missionEvidence{Mission: Mission{ID: id, Kind: "system.command"}, Results: map[string]Result{"system-command": result}}
	if err := writeMissionEvidence(dir, evidence); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(githubBusEnvelope{Type: "CHECKPOINT", ID: id, Kind: "system.command", State: "NEEDS_BRAIN", Summary: "failed"})
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	for _, leaked := range []string{"hunter2", "abc123"} {
		if strings.Contains(text, leaked) {
			t.Fatalf("secret leaked in outbound envelope: %s", text)
		}
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		t.Fatal(err)
	}
	diagnostic, ok := payload["diagnostic"].(map[string]any)
	if !ok {
		t.Fatalf("structured diagnostic missing: %s", text)
	}
	if diagnostic["phase"] != "build" || diagnostic["class"] != "compile" || diagnostic["confidence"] != "high" {
		t.Fatalf("unexpected diagnostic: %#v", diagnostic)
	}
	if tail, _ := diagnostic["stdoutTail"].(string); !strings.Contains(tail, "FINAL STDOUT CAUSE") {
		t.Fatalf("stdout tail lost final cause: %#v", diagnostic)
	}
}

func TestCheckpointDiagnosticRoundTripAndMissionRejection(t *testing.T) {
	checkpoint := `{"protocol":"FACTORY_BUS_V2","type":"CHECKPOINT","id":"M-x","state":"BLOCKED","diagnostic":{"phase":"integrity","error":"bad","class":"integrity_payload_hash","confidence":"high","recommendedNextAction":"regenerate"}}`
	var envelope githubBusEnvelope
	if err := json.Unmarshal([]byte(checkpoint), &envelope); err != nil {
		t.Fatalf("checkpoint diagnostic should parse for reconciliation: %v", err)
	}
	mission := `{"protocol":"FACTORY_BUS_V2","type":"MISSION","id":"M-x","diagnostic":{"phase":"x","error":"x","class":"x","confidence":"low","recommendedNextAction":"x"}}`
	if err := json.Unmarshal([]byte(mission), &envelope); err == nil {
		t.Fatal("diagnostic input authority unexpectedly accepted on MISSION")
	}
}

func TestTRXDiagnosticSummary(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "TestResults", "run")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	trx := `<?xml version="1.0" encoding="utf-8"?><TestRun><Results><UnitTestResult testName="Passes" outcome="Passed"/><UnitTestResult testName="FailsA" outcome="Failed"/><UnitTestResult testName="FailsB" outcome="Failed"/></Results><ResultSummary><Counters total="4" executed="3" passed="1" failed="2" notExecuted="1"/></ResultSummary></TestRun>`
	path := filepath.Join(dir, "result.trx")
	if err := os.WriteFile(path, []byte(trx), 0600); err != nil {
		t.Fatal(err)
	}
	summary := newestTRXDiagnostic(root, time.Now().Add(-time.Minute))
	if summary == nil || summary.Total != 4 || summary.Passed != 1 || summary.Failed != 2 || summary.Skipped != 1 {
		t.Fatalf("unexpected TRX summary: %#v", summary)
	}
	if len(summary.FailedTests) != 2 || summary.FailedTests[0] != "FailsA" {
		t.Fatalf("failed tests missing: %#v", summary)
	}
}

func TestMissionStepJSONPreservesTailAndRedacts(t *testing.T) {
	step := MissionStepEvidence{Output: strings.Repeat("prefix-", 200) + "FINAL Password=hunter2", Error: strings.Repeat("err-", 200) + "ROOT-CAUSE token=abc123"}
	data, err := json.Marshal(step)
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	if !strings.Contains(text, "FINAL") || !strings.Contains(text, "ROOT-CAUSE") {
		t.Fatalf("tail cause lost: %s", text)
	}
	if strings.Contains(text, "hunter2") || strings.Contains(text, "abc123") {
		t.Fatalf("secret leaked: %s", text)
	}
}
