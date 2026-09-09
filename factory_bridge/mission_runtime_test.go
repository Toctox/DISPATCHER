package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDecodeMissionAllowlistAndStrictSchema(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	dir := t.TempDir()
	valid := filepath.Join(dir, "MISSION__OK.json")
	m := hardenedTestMission(t, "M-001")
	m.Kind = "projecthub.full_cycle"
	m.Objective = "validate and publish"
	hash, err := missionPayloadHash(m)
	if err != nil {
		t.Fatal(err)
	}
	m.PayloadHash = hash
	data, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(valid, data, 0o600); err != nil {
		t.Fatal(err)
	}
	mission, err := decodeMission(valid)
	if err != nil {
		t.Fatalf("valid mission rejected: %v", err)
	}
	if mission.ID != "M-001" || mission.Kind != "projecthub.full_cycle" {
		t.Fatalf("unexpected mission: %+v", mission)
	}

	unknownKind := filepath.Join(dir, "MISSION__BAD_KIND.json")
	if err := os.WriteFile(unknownKind, []byte(`{"id":"M-002","kind":"shell.run"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := decodeMission(unknownKind); err == nil {
		t.Fatal("unsupported mission kind must be rejected")
	}

	unknownField := filepath.Join(dir, "MISSION__BAD_FIELD.json")
	if err := os.WriteFile(unknownField, []byte(`{"id":"M-003","kind":"projecthub.verify","command":"whoami"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := decodeMission(unknownField); err == nil {
		t.Fatal("unknown mission fields must be rejected")
	}
}

func TestEnsureMissionMailboxCreatesBrainInboxOutboxDocs(t *testing.T) {
	bridge := t.TempDir()
	cfg := Config{BridgeRoot: bridge}
	if err := ensureMissionMailbox(cfg); err != nil {
		t.Fatal(err)
	}
	for _, rel := range []string{brainDirName, missionInboxDirName, missionOutboxDirName, docsDirName, filepath.Join("03_ARCHIVE", "MISSIONS")} {
		stat, err := os.Stat(filepath.Join(bridge, rel))
		if err != nil || !stat.IsDir() {
			t.Fatalf("missing mailbox directory %s: %v", rel, err)
		}
	}
}

func TestMissionEntriesUsesOldestFileFirst(t *testing.T) {
	dir := t.TempDir()
	older := filepath.Join(dir, "MISSION__Z.json")
	newer := filepath.Join(dir, "MISSION__A.json")
	if err := os.WriteFile(older, []byte(`{"id":"M-Z","kind":"projecthub.verify"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(newer, []byte(`{"id":"M-A","kind":"projecthub.verify"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	base := time.Now().Add(-time.Minute).Truncate(time.Second)
	if err := os.Chtimes(older, base, base); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(newer, base.Add(time.Second), base.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	entries, err := missionEntries(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 || entries[0].Name() != "MISSION__Z.json" {
		t.Fatalf("unexpected mission order: %+v", entries)
	}
}

func TestWriteShowcaseLaunchers(t *testing.T) {
	root := t.TempDir()
	if err := writeShowcaseLaunchers(root); err != nil {
		t.Fatal(err)
	}
	start, err := os.ReadFile(filepath.Join(root, "START_LATEST.cmd"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(start), "LATEST.txt") || !strings.Contains(string(start), "ProjectHub.Server.exe") {
		t.Fatalf("launcher missing required references: %s", string(start))
	}
	if _, err := os.Stat(filepath.Join(root, "STOP_PROJECTHUB.cmd")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "README.txt")); err != nil {
		t.Fatal(err)
	}
}
