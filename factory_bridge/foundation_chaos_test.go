package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMissionIntegrityValidationDoesNotReserveMissionDirectory(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-SIDE-EFFECT-001")
	root, err := missionLocalRoot()
	if err != nil {
		t.Fatal(err)
	}
	if err := validateMissionIntegrity(m); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, m.ID)); !os.IsNotExist(err) {
		t.Fatalf("validation must not reserve/create the mission directory; stat err=%v", err)
	}
}

func TestInterruptedMissionRecoveryFailsClosedWithoutReplay(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-CRASH-RECOVERY-001")
	if _, created, err := reserveMission(m); err != nil || !created {
		t.Fatalf("reserve created=%t err=%v", created, err)
	}
	if err := markMissionJournalState(m.ID, "RUNNING"); err != nil {
		t.Fatal(err)
	}

	// Empty token prevents any network post; recovery must still persist a
	// terminal local checkpoint instead of replaying the mission.
	recoverInterruptedGitHubMissions("")

	cp, ok := existingMissionCheckpoint(m.ID)
	if !ok {
		t.Fatal("expected recovery checkpoint")
	}
	if cp.State != "NEEDS_BRAIN" || !strings.Contains(cp.Summary, "refused automatic replay") {
		t.Fatalf("unexpected recovery checkpoint: %#v", cp)
	}
	journal, err := readMissionJournal(m.ID)
	if err != nil {
		t.Fatal(err)
	}
	if journal.State != "NEEDS_BRAIN" {
		t.Fatalf("journal must be terminal after recovery, got %q", journal.State)
	}
}

func TestCancellationCannotBeResumed(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-CANCEL-TERMINAL-001")
	if _, _, err := reserveMission(m); err != nil {
		t.Fatal(err)
	}
	if err := setMissionControl(m.ID, "CANCEL"); err != nil {
		t.Fatal(err)
	}
	if err := setMissionControl(m.ID, "RESUME"); err == nil || !strings.Contains(err.Error(), "terminal") {
		t.Fatalf("cancelled mission must not resume, got %v", err)
	}
}
