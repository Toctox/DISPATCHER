package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func gatewayTestEnv(t *testing.T) Config {
	t.Helper()
	local := t.TempDir()
	home := t.TempDir()
	bridge := t.TempDir()
	t.Setenv("LOCALAPPDATA", local)
	t.Setenv("USERPROFILE", home)
	cfg := Config{BridgeRoot: bridge, PollIntervalMs: 1000}
	if err := ensureBridgeDirs(cfg); err != nil {
		t.Fatal(err)
	}
	if err := ensureMissionMailbox(cfg); err != nil {
		t.Fatal(err)
	}
	return cfg
}

func TestGatewayPublicHealthUsesLocalExecutorState(t *testing.T) {
	cfg := gatewayTestEnv(t)
	if err := writeLocalExecutorStatus(time.Now().Add(-time.Minute)); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(gatewayMux(cfg, "test-token-abcdefghijklmnopqrstuvwxyz"))
	defer server.Close()

	resp, err := http.Get(server.URL + "/public/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status=%d", resp.StatusCode)
	}
	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["executorOnline"] != true {
		t.Fatalf("expected local executor online, got %+v", body)
	}
}

func TestGatewayPrivateMissionRequiresBearerToken(t *testing.T) {
	cfg := gatewayTestEnv(t)
	token := "test-token-abcdefghijklmnopqrstuvwxyz"
	server := httptest.NewServer(gatewayMux(cfg, token))
	defer server.Close()

	mission := hardenedTestMission(t, "M-GW-1")
	mission.Objective = "gateway test"
	hash, err := missionPayloadHash(mission)
	if err != nil {
		t.Fatal(err)
	}
	mission.PayloadHash = hash
	payload, err := json.Marshal(mission)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.Post(server.URL+"/api/missions", "application/json", bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated status=%d want=%d", resp.StatusCode, http.StatusUnauthorized)
	}

	req, err := http.NewRequest(http.MethodPost, server.URL+"/api/missions", bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("authenticated status=%d want=%d", resp.StatusCode, http.StatusAccepted)
	}
	if _, err := os.Stat(filepath.Join(cfg.BridgeRoot, missionInboxDirName, "MISSION__M-GW-1.json")); err != nil {
		t.Fatalf("mission not written to allowlisted inbox: %v", err)
	}
}

func TestGatewayPublicCheckpointIsMetadataOnly(t *testing.T) {
	cfg := gatewayTestEnv(t)
	cp := MissionCheckpoint{
		MissionID:         "M-GW-2",
		Kind:              "projecthub.full_cycle",
		State:             "NEEDS_BRAIN",
		StartedAt:         time.Now().Add(-time.Minute).Format(time.RFC3339),
		Objective:         "sensitive objective",
		Summary:           "decision required with internal details",
		DecisionQuestion:  "Choose next step",
		LocalEvidencePath: `C:\private\evidence`,
		ShowcasePath:      `C:\private\showcase`,
		Steps:             []MissionStepEvidence{{Name: "secret-step", Output: "internal output"}},
		BridgeVersion:     bridgeVersion,
	}
	if err := writeJSONAtomic(filepath.Join(cfg.BridgeRoot, brainDirName, "CURRENT_STATE.json"), cp); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(gatewayMux(cfg, "test-token-abcdefghijklmnopqrstuvwxyz"))
	defer server.Close()

	resp, err := http.Get(server.URL + "/public/checkpoint")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"localEvidencePath", "showcasePath", "objective", "summary", "decisionQuestion", "steps"} {
		if _, ok := body[forbidden]; ok {
			t.Fatalf("public checkpoint leaked %s", forbidden)
		}
	}
	if body["missionId"] != "M-GW-2" || body["state"] != "NEEDS_BRAIN" {
		t.Fatalf("public metadata missing: %+v", body)
	}
}
