package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const missionJournalFileName = "journal.json"

var missionExecutionMu sync.Mutex

type missionJournal struct {
	Mission     Mission `json:"mission"`
	PayloadHash string  `json:"payloadHash"`
	State       string  `json:"state"`
	UpdatedAt   string  `json:"updatedAt"`
}

type missionControl struct {
	Action    string `json:"action"`
	UpdatedAt string `json:"updatedAt"`
}

func canonicalMissionPayload(m Mission) any {
	return struct {
		ID           string `json:"id"`
		Kind         string `json:"kind"`
		Objective    string `json:"objective,omitempty"`
		TargetCommit string `json:"targetCommit,omitempty"`
		IssuedAt     string `json:"issuedAt,omitempty"`
		ExpiresAt    string `json:"expiresAt,omitempty"`
	}{
		ID:           strings.TrimSpace(m.ID),
		Kind:         strings.TrimSpace(m.Kind),
		Objective:    strings.TrimSpace(m.Objective),
		TargetCommit: strings.ToLower(strings.TrimSpace(m.TargetCommit)),
		IssuedAt:     strings.TrimSpace(m.IssuedAt),
		ExpiresAt:    strings.TrimSpace(m.ExpiresAt),
	}
}

func missionPayloadHash(m Mission) (string, error) {
	data, err := json.Marshal(canonicalMissionPayload(m))
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func validateMissionIntegrity(m Mission) error {
	if !idPattern.MatchString(m.ID) {
		return errors.New("invalid mission id")
	}
	switch m.Kind {
	case "projecthub.verify", "projecthub.full_cycle", "projecthub.showcase":
	default:
		return fmt.Errorf("unsupported mission kind: %s", m.Kind)
	}

	hardened := strings.TrimSpace(m.TargetCommit) != "" || strings.TrimSpace(m.IssuedAt) != "" || strings.TrimSpace(m.ExpiresAt) != "" || strings.TrimSpace(m.PayloadHash) != ""
	if !hardened {
		return nil
	}
	commit := strings.TrimSpace(m.TargetCommit)
	if len(commit) != 40 {
		return errors.New("targetCommit must be a full 40-character Git commit SHA")
	}
	if _, err := hex.DecodeString(commit); err != nil {
		return errors.New("targetCommit is not hexadecimal")
	}
	issued, err := time.Parse(time.RFC3339, strings.TrimSpace(m.IssuedAt))
	if err != nil {
		return errors.New("issuedAt must be RFC3339")
	}
	expires, err := time.Parse(time.RFC3339, strings.TrimSpace(m.ExpiresAt))
	if err != nil {
		return errors.New("expiresAt must be RFC3339")
	}
	if !expires.After(issued) {
		return errors.New("expiresAt must be after issuedAt")
	}
	if time.Now().UTC().After(expires.UTC()) {
		return errors.New("mission has expired")
	}
	want, err := missionPayloadHash(m)
	if err != nil {
		return err
	}
	if !strings.EqualFold(strings.TrimSpace(m.PayloadHash), want) {
		return errors.New("payloadHash does not match the canonical mission payload")
	}
	return nil
}

func missionJournalPath(id string) (string, error) {
	dir, err := missionLocalDir(id)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, missionJournalFileName), nil
}

func readMissionJournal(id string) (*missionJournal, error) {
	path, err := missionJournalPath(id)
	if err != nil {
		return nil, err
	}
	return readJSONFile[missionJournal](path)
}

func reserveMission(m Mission) (*missionJournal, bool, error) {
	hash, err := missionPayloadHash(m)
	if err != nil {
		return nil, false, err
	}
	path, err := missionJournalPath(m.ID)
	if err != nil {
		return nil, false, err
	}
	if existing, readErr := readJSONFile[missionJournal](path); readErr == nil && existing != nil {
		if !strings.EqualFold(existing.PayloadHash, hash) {
			return existing, false, errors.New("mission id was already reserved with a different payload")
		}
		return existing, false, nil
	}
	j := &missionJournal{
		Mission:     m,
		PayloadHash: hash,
		State:       "RECEIVED",
		UpdatedAt:   time.Now().UTC().Format(time.RFC3339Nano),
	}
	if err := writeJSONAtomic(path, j); err != nil {
		return nil, false, err
	}
	return j, true, nil
}

func markMissionJournalState(id, state string) error {
	j, err := readMissionJournal(id)
	if err != nil {
		return err
	}
	j.State = strings.ToUpper(strings.TrimSpace(state))
	j.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	path, err := missionJournalPath(id)
	if err != nil {
		return err
	}
	return writeJSONAtomic(path, j)
}

func missionControlPath(id string) (string, error) {
	dir, err := missionLocalDir(id)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "control.json"), nil
}

func setMissionControl(id, action string) error {
	action = strings.ToUpper(strings.TrimSpace(action))
	switch action {
	case "CANCEL", "PAUSE", "RESUME":
	default:
		return fmt.Errorf("unsupported mission control action: %s", action)
	}
	path, err := missionControlPath(id)
	if err != nil {
		return err
	}
	return writeJSONAtomic(path, missionControl{Action: action, UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano)})
}

func readMissionControl(id string) string {
	path, err := missionControlPath(id)
	if err != nil {
		return ""
	}
	control, err := readJSONFile[missionControl](path)
	if err != nil || control == nil {
		return ""
	}
	return strings.ToUpper(strings.TrimSpace(control.Action))
}

func waitForMissionPermission(id string) error {
	for {
		switch readMissionControl(id) {
		case "CANCEL":
			return errors.New("mission cancelled by control message")
		case "PAUSE":
			time.Sleep(2 * time.Second)
			continue
		default:
			return nil
		}
	}
}

func interruptedMissionJournals() ([]missionJournal, error) {
	root, err := missionLocalRoot()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	out := []missionJournal{}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(root, entry.Name(), missionJournalFileName)
		j, readErr := readJSONFile[missionJournal](path)
		if readErr != nil || j == nil {
			continue
		}
		switch strings.ToUpper(j.State) {
		case "RECEIVED", "ACKED", "QUEUED", "RUNNING":
			out = append(out, *j)
		}
	}
	return out, nil
}

func ensureMissionTargetCommit(cfg Config, mission Mission, r runner) error {
	target := strings.ToLower(strings.TrimSpace(mission.TargetCommit))
	if target == "" {
		return nil
	}
	workDir, err := projectHubWorkDir(cfg)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	if _, err := runProjectHubGit(ctx, r, workDir, "fetch", "origin", "main"); err != nil {
		return err
	}
	origin, err := runProjectHubGit(ctx, r, workDir, "rev-parse", "origin/main")
	if err != nil {
		return err
	}
	origin = strings.ToLower(strings.TrimSpace(origin))
	if origin != target {
		return fmt.Errorf("target commit moved before execution: authorized=%s origin/main=%s", target, origin)
	}
	if mission.Kind != "projecthub.full_cycle" {
		state, err := requireCanonicalProjectHub(cfg, r)
		if err != nil {
			return err
		}
		if strings.ToLower(strings.TrimSpace(state.Head)) != target {
			return fmt.Errorf("canonical checkout does not match authorized target: authorized=%s head=%s", target, state.Head)
		}
	}
	return nil
}
