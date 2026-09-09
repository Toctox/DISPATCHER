package main

import (
	"strings"
	"testing"
	"time"
)

func hardenedTestMission(t *testing.T, id string) Mission {
	t.Helper()
	now := time.Now().UTC()
	m := Mission{
		ID:           id,
		Kind:         "projecthub.verify",
		Objective:    "verify authorized commit",
		TargetCommit: "6494e7b51aae694f4559f836199cb78212976edc",
		IssuedAt:     now.Add(-time.Minute).Format(time.RFC3339),
		ExpiresAt:    now.Add(time.Hour).Format(time.RFC3339),
	}
	hash, err := missionPayloadHash(m)
	if err != nil {
		t.Fatal(err)
	}
	m.PayloadHash = hash
	return m
}

func TestMissionPayloadHashKnownVector(t *testing.T) {
	m := Mission{
		ID:           "M-VECTOR-001",
		Kind:         "projecthub.verify",
		Objective:    "verify exact commit",
		TargetCommit: "6494e7b51aae694f4559f836199cb78212976edc",
		IssuedAt:     "2026-09-09T04:00:00Z",
		ExpiresAt:    "2026-09-09T05:00:00Z",
	}
	got, err := missionPayloadHash(m)
	if err != nil {
		t.Fatal(err)
	}
	const want = "d0ccba7b22fa5cd70d386dfc64c5a36d9fd762b2a15b809dc7d1931108b2b348"
	if got != want {
		t.Fatalf("canonical hash changed got=%s want=%s", got, want)
	}
}

func TestMissionIntegrityAcceptsCanonicalHardenedEnvelope(t *testing.T) {
	m := hardenedTestMission(t, "M-HARDENED-001")
	if err := validateMissionIntegrity(m); err != nil {
		t.Fatalf("valid hardened mission rejected: %v", err)
	}
}

func TestMissionIntegrityRejectsTamperedPayload(t *testing.T) {
	m := hardenedTestMission(t, "M-HARDENED-002")
	m.Objective = "tampered after hash"
	if err := validateMissionIntegrity(m); err == nil || !strings.Contains(err.Error(), "payloadHash") {
		t.Fatalf("expected payloadHash rejection, got %v", err)
	}
}

func TestMissionIntegrityRejectsExpiredMission(t *testing.T) {
	now := time.Now().UTC()
	m := Mission{
		ID:           "M-HARDENED-003",
		Kind:         "projecthub.verify",
		Objective:    "expired",
		TargetCommit: "6494e7b51aae694f4559f836199cb78212976edc",
		IssuedAt:     now.Add(-2 * time.Hour).Format(time.RFC3339),
		ExpiresAt:    now.Add(-time.Hour).Format(time.RFC3339),
	}
	hash, err := missionPayloadHash(m)
	if err != nil {
		t.Fatal(err)
	}
	m.PayloadHash = hash
	if err := validateMissionIntegrity(m); err == nil || !strings.Contains(err.Error(), "expired") {
		t.Fatalf("expected expiry rejection, got %v", err)
	}
}

func TestMissionReservationRejectsSameIDDifferentPayload(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	first := hardenedTestMission(t, "M-REPLAY-001")
	if _, created, err := reserveMission(first); err != nil || !created {
		t.Fatalf("first reservation failed created=%t err=%v", created, err)
	}
	second := first
	second.Objective = "different payload"
	hash, err := missionPayloadHash(second)
	if err != nil {
		t.Fatal(err)
	}
	second.PayloadHash = hash
	if _, _, err := reserveMission(second); err == nil || !strings.Contains(err.Error(), "different payload") {
		t.Fatalf("expected same-id payload conflict, got %v", err)
	}
}

func TestMissionReservationIsIdempotentForSamePayload(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-REPLAY-002")
	if _, created, err := reserveMission(m); err != nil || !created {
		t.Fatalf("first reservation failed created=%t err=%v", created, err)
	}
	j, created, err := reserveMission(m)
	if err != nil || created || j == nil || j.State != "RECEIVED" {
		t.Fatalf("same payload should reuse reservation created=%t journal=%#v err=%v", created, j, err)
	}
}

func TestMissionControlPauseResumeCancel(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-CONTROL-001")
	if _, _, err := reserveMission(m); err != nil {
		t.Fatal(err)
	}
	if err := setMissionControl(m.ID, "PAUSE"); err != nil {
		t.Fatal(err)
	}
	if got := readMissionControl(m.ID); got != "PAUSE" {
		t.Fatalf("expected PAUSE, got %q", got)
	}
	if err := setMissionControl(m.ID, "RESUME"); err != nil {
		t.Fatal(err)
	}
	if got := readMissionControl(m.ID); got != "RESUME" {
		t.Fatalf("expected RESUME, got %q", got)
	}
	if err := setMissionControl(m.ID, "CANCEL"); err != nil {
		t.Fatal(err)
	}
	if err := waitForMissionPermission(m.ID); err == nil || !strings.Contains(err.Error(), "cancelled") {
		t.Fatalf("expected cancellation, got %v", err)
	}
}

func TestInterruptedMissionJournalDetection(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-INTERRUPT-001")
	if _, _, err := reserveMission(m); err != nil {
		t.Fatal(err)
	}
	if err := markMissionJournalState(m.ID, "RUNNING"); err != nil {
		t.Fatal(err)
	}
	journals, err := interruptedMissionJournals()
	if err != nil {
		t.Fatal(err)
	}
	if len(journals) != 1 || journals[0].Mission.ID != m.ID || journals[0].State != "RUNNING" {
		t.Fatalf("unexpected interrupted journals: %#v", journals)
	}
}
