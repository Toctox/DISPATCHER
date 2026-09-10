package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMissionHashCLIUsesCanonicalRuntimeHash(t *testing.T) {
	mission := Mission{
		ID:           "M-HASH-TEST-001",
		Kind:         "system.command",
		Objective:    `{"shell":"powershell","command":"Write-Output 'A&B'","timeoutSec":30}`,
		TargetCommit: "107D7A9A87E29B5322DC63C7B9AAE7842F2D8CCA",
		IssuedAt:     "2026-09-10T15:00:00Z",
		ExpiresAt:    "2026-09-10T16:00:00Z",
	}

	input, err := json.Marshal(mission)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "mission.json")
	if err := os.WriteFile(path, input, 0o600); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	if code := runMissionHashCLI([]string{path}, &stdout, &stderr); code != 0 {
		t.Fatalf("exit=%d stderr=%s", code, stderr.String())
	}

	var got struct {
		PayloadHash string          `json:"payloadHash"`
		Canonical   json.RawMessage `json:"canonical"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	want, err := missionPayloadHash(mission)
	if err != nil {
		t.Fatal(err)
	}
	if got.PayloadHash != want {
		t.Fatalf("hash mismatch: got %s want %s", got.PayloadHash, want)
	}
	if !strings.Contains(string(got.Canonical), `A\u0026B`) {
		t.Fatalf("canonical JSON must preserve Go HTML escaping, got %s", got.Canonical)
	}
}
