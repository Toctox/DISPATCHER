package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// The disk record, including original execution identity, is authoritative.
// Publication is at-least-once; eventId provides stable remote reconciliation
// when GitHub accepted a POST but the response or local receipt was lost.
type outboundEvent struct {
	Envelope  githubBusEnvelope `json:"envelope"`
	Delivered bool              `json:"delivered"`
}

var outboundMu sync.Mutex

func outboxDir() (string, error) {
	base, err := localRuntimeStateDir()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(base, "outbox")
	return dir, os.MkdirAll(dir, 0700)
}

func enqueueGitHubEnvelope(envelope githubBusEnvelope) (string, error) {
	outboundMu.Lock()
	defer outboundMu.Unlock()
	dir, err := outboxDir()
	if err != nil {
		return "", err
	}
	envelope.Protocol = githubBusProtocol
	envelope.Summary = sanitizeRemoteText(envelope.Summary)
	if envelope.SourceCommit == "" {
		envelope.RuntimeIdentity = currentRuntimeIdentity()
	}
	if envelope.BridgeVersion == "" {
		envelope.BridgeVersion = bridgeVersion
	}
	// Delivery bookkeeping must not change the event identity on resubmission.
	key := envelope
	key.EventID, key.ObservedAt = "", ""
	key.Sequence, key.Attempt = 0, 0
	data, err := json.Marshal(key)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	envelope.EventID = hex.EncodeToString(sum[:])
	path := filepath.Join(dir, envelope.EventID+".json")
	if _, err := os.Stat(path); err == nil {
		_, readErr := readJSONFile[outboundEvent](path)
		return path, readErr
	} else if !os.IsNotExist(err) {
		return "", err
	}
	if envelope.ObservedAt == "" {
		envelope.ObservedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	envelope.Sequence = uint64(time.Now().UnixNano())
	return path, writeJSONAtomic(path, outboundEvent{Envelope: envelope})
}

func flushGitHubOutbox(token string) error {
	outboundMu.Lock()
	defer outboundMu.Unlock()
	dir, err := outboxDir()
	if err != nil {
		return err
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	type pending struct {
		path  string
		event outboundEvent
	}
	var queue []pending
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		path := filepath.Join(dir, entry.Name())
		event, err := readJSONFile[outboundEvent](path)
		if err != nil {
			return fmt.Errorf("outbox record unreadable: %w", err)
		}
		if !event.Delivered {
			queue = append(queue, pending{path, *event})
		}
	}
	sort.Slice(queue, func(i, j int) bool { return queue[i].event.Envelope.Sequence < queue[j].event.Envelope.Sequence })
	for _, item := range queue {
		event := item.event
		if event.Envelope.Attempt > 0 {
			// Reconcile an uncertain POST before repeating it. A failed read also
			// leaves the record pending; never discard it based on network failure.
			comments, err := listGitHubBusComments(token, githubBusState{LastSeenAt: event.Envelope.ObservedAt})
			if err != nil {
				return err
			}
			for _, c := range comments {
				if c.User.Login != githubBusTrustedAuthor {
					continue
				}
				e, err := parseGitHubBusEnvelope(c.Body)
				if err == nil && e.EventID == event.Envelope.EventID {
					event.Delivered = true
					break
				}
			}
		}
		if !event.Delivered {
			event.Envelope.Attempt++
			if err := writeJSONAtomic(item.path, event); err != nil {
				return err
			}
			if err := sendGitHubBusEnvelope(token, event.Envelope); err != nil {
				return err
			}
			event.Delivered = true
		}
		if err := writeJSONAtomic(item.path, event); err != nil {
			return err
		}
	}
	return nil
}

func postGitHubBusEnvelope(token string, envelope githubBusEnvelope) error {
	if _, err := enqueueGitHubEnvelope(envelope); err != nil {
		return err
	}
	if token == "" {
		return nil
	}
	// Once durable, execution/cursor progress is independent of connectivity.
	// Every poll retries; the original record remains until a durable receipt.
	if err := flushGitHubOutbox(token); err != nil {
		fmt.Fprintf(os.Stderr, "github outbound pending: %s\n", sanitizeRemoteText(err.Error()))
	}
	return nil
}

// Repair the crash window between terminal evidence/journal persistence and
// enqueueing its CHECKPOINT. Legacy evidence has no execution identity and is
// deliberately excluded to avoid replaying years of historical bus messages.
func reconcileTerminalOutbox() error {
	root, err := missionLocalRoot()
	if err != nil { return err }
	entries, err := os.ReadDir(root)
	if err != nil { return err }
	for _, entry := range entries {
		if !entry.IsDir() { continue }
		cp, ok := existingMissionCheckpoint(entry.Name())
		if !ok || cp.SourceCommit == "" || !isTerminalMissionState(cp.State) { continue }
		if _, err := enqueueGitHubEnvelope(checkpointEnvelope(*cp)); err != nil { return err }
	}
	return nil
}
