package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var controlMu sync.Mutex

type controlReceipt struct {
	ID       string `json:"controlId"`
	Sequence uint64 `json:"sequence"`
	Hash     string `json:"payloadHash"`
	Action   string `json:"action"`
}

type controlLedger struct {
	LastSequence uint64                    `json:"lastSequence"`
	Receipts     map[string]controlReceipt `json:"receipts"`
}

func controlPayloadHash(e githubBusEnvelope) string {
	data, _ := json.Marshal(struct {
		Protocol  string `json:"protocol"`
		Type      string `json:"type"`
		ID        string `json:"id"`
		ControlID string `json:"controlId"`
		Sequence  uint64 `json:"controlSequence"`
		Action    string `json:"action"`
		IssuedAt  string `json:"issuedAt"`
		ExpiresAt string `json:"expiresAt"`
	}{e.Protocol, e.Type, e.ID, e.ControlID, e.ControlSequence, e.Action, e.IssuedAt, e.ExpiresAt})
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

// GitHub trusted-author filtering is the authentication boundary. This digest
// binds intent, while the durable sequence/receipts prevent stale control replay.
func applyControlV2(e githubBusEnvelope, now time.Time) error {
	controlMu.Lock()
	defer controlMu.Unlock()
	if e.Protocol != githubBusProtocol || e.Type != "CONTROL" || !idPattern.MatchString(e.ID) || !idPattern.MatchString(e.ControlID) || e.ControlSequence == 0 {
		return errors.New("CONTROL V2 requires valid mission/control IDs and positive sequence")
	}
	if e.Action != "CANCEL" && e.Action != "PAUSE" && e.Action != "RESUME" {
		return errors.New("invalid control action")
	}
	issued, err := time.Parse(time.RFC3339, e.IssuedAt)
	if err != nil {
		return errors.New("control issuedAt must be RFC3339")
	}
	expires, err := time.Parse(time.RFC3339, e.ExpiresAt)
	if err != nil || !expires.After(issued) || expires.Sub(issued) > 2*time.Hour || issued.After(now.Add(time.Minute)) || !now.Before(expires) {
		return errors.New("control freshness window invalid or expired")
	}
	hash := controlPayloadHash(e)
	if !strings.EqualFold(hash, e.PayloadHash) {
		return errors.New("control payloadHash mismatch")
	}
	if _, err := readMissionJournal(e.ID); err != nil {
		return errors.New("mission is not known locally")
	}
	dir, err := missionLocalDir(e.ID)
	if err != nil {
		return err
	}
	path := filepath.Join(dir, "control-ledger.json")
	ledger, err := readJSONFile[controlLedger](path)
	if os.IsNotExist(err) {
		ledger = &controlLedger{Receipts: map[string]controlReceipt{}}
	} else if err != nil {
		return err
	}
	if ledger.Receipts == nil {
		return errors.New("control ledger is corrupt")
	}
	if previous, ok := ledger.Receipts[e.ControlID]; ok {
		if previous.Hash != hash {
			return errors.New("control id reused with different payload")
		}
		// Receipt and desired control are one atomic write (below); no reapply.
		return nil
	}
	if e.ControlSequence <= ledger.LastSequence {
		return errors.New("stale control sequence")
	}
	j, err := readMissionJournal(e.ID)
	if err != nil {
		return err
	}
	if isTerminalMissionState(j.State) {
		return errors.New("mission is already terminal")
	}
	if readMissionControl(e.ID) == "CANCEL" && e.Action != "CANCEL" {
		return errors.New("mission cancellation is terminal")
	}
	ledger.LastSequence = e.ControlSequence
	ledger.Receipts[e.ControlID] = controlReceipt{e.ControlID, e.ControlSequence, hash, e.Action}
	return writeJSONAtomic(path, ledger)
}

func isTerminalMissionState(state string) bool {
	switch strings.ToUpper(state) {
	case "DONE", "NEEDS_BRAIN", "BLOCKED", "CANCELLED", "TIMEOUT":
		return true
	}
	return false
}
