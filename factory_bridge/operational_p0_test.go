package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestOutboxOutageRetryAndUncertainPostReconciliation(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	old := githubAPIBase
	defer func() { githubAPIBase = old }()
	posts := 0
	var remote []githubIssueComment
	fail := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			json.NewEncoder(w).Encode(remote)
			return
		}
		posts++
		if fail {
			w.WriteHeader(503)
			return
		}
		var body map[string]string
		json.NewDecoder(r.Body).Decode(&body)
		c := githubIssueComment{ID: 1, Body: body["body"]}
		c.User.Login = githubBusTrustedAuthor
		remote = append(remote, c)
		w.WriteHeader(201)
	}))
	defer server.Close()
	githubAPIBase = server.URL
	e := githubBusEnvelope{Type: "CHECKPOINT", ID: "M-outage", State: "DONE", Summary: "Password=canary123", RuntimeIdentity: RuntimeIdentity{SourceCommit: strings.Repeat("a", 40), BinarySHA256: strings.Repeat("b", 64), ProtocolVersion: githubBusProtocol, PolicyVersion: "old-policy"}}
	path, err := enqueueGitHubEnvelope(e)
	if err != nil {
		t.Fatal(err)
	}
	if err := flushGitHubOutbox("test"); err == nil {
		t.Fatal("expected outage")
	}
	pending, err := readJSONFile[outboundEvent](path)
	if err != nil || pending.Delivered || pending.Envelope.Attempt != 1 {
		t.Fatalf("lost pending record: %+v %v", pending, err)
	}
	if strings.Contains(pending.Envelope.Summary, "canary123") {
		t.Fatal("secret persisted outbound")
	}
	fail = false
	if err := flushGitHubOutbox("test"); err != nil {
		t.Fatal(err)
	}
	done, _ := readJSONFile[outboundEvent](path)
	if !done.Delivered || done.Envelope.SourceCommit != e.SourceCommit || done.Envelope.Attempt != 2 {
		t.Fatalf("receipt/identity drift: %+v", done)
	}
	// Simulate crash after remote POST but before saving the delivered receipt.
	done.Delivered = false
	if err := writeJSONAtomic(path, done); err != nil {
		t.Fatal(err)
	}
	if err := flushGitHubOutbox("test"); err != nil {
		t.Fatal(err)
	}
	if posts != 2 || len(remote) != 1 {
		t.Fatalf("replayed remote side effect: posts=%d", posts)
	}
	if again, err := enqueueGitHubEnvelope(e); err != nil || again != path {
		t.Fatalf("unstable event ID: %s %v", again, err)
	}
}

func TestOutboxPersistenceFailureIsNotAcknowledged(t *testing.T) {
	base := t.TempDir()
	t.Setenv("LOCALAPPDATA", base)
	if err := os.WriteFile(filepath.Join(base, "FactoryBridge"), []byte("blocked"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := postGitHubBusEnvelope("", githubBusEnvelope{ID: "M-disk", Type: "ACK"}); err == nil {
		t.Fatal("ack accepted without durable state")
	}
}

func TestControlFreshnessReplayAndCorruption(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-control-v2")
	if _, _, err := reserveMission(m); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	e := githubBusEnvelope{Protocol: githubBusProtocol, Type: "CONTROL", ID: m.ID, ControlID: "C-1", ControlSequence: 1, Action: "PAUSE", IssuedAt: now.Add(-time.Minute).Format(time.RFC3339), ExpiresAt: now.Add(time.Hour).Format(time.RFC3339)}
	e.PayloadHash = controlPayloadHash(e)
	if err := applyControlV2(e, now); err != nil {
		t.Fatal(err)
	}
	if err := applyControlV2(e, now); err != nil {
		t.Fatal("duplicate not idempotent", err)
	}
	if readMissionControl(m.ID) != "PAUSE" {
		t.Fatal("control not durable")
	}
	bad := e
	bad.Action = "RESUME"
	bad.PayloadHash = controlPayloadHash(bad)
	if applyControlV2(bad, now) == nil {
		t.Fatal("changed replay accepted")
	}
	bad.ControlID = "C-2"
	bad.PayloadHash = controlPayloadHash(bad)
	if applyControlV2(bad, now) == nil {
		t.Fatal("old sequence accepted")
	}
	bad.ControlSequence = 2
	bad.ExpiresAt = now.Add(-time.Second).Format(time.RFC3339)
	bad.PayloadHash = controlPayloadHash(bad)
	if applyControlV2(bad, now) == nil {
		t.Fatal("expired control accepted")
	}
	bad.IssuedAt = now.Add(2 * time.Minute).Format(time.RFC3339)
	bad.ExpiresAt = now.Add(time.Hour).Format(time.RFC3339)
	bad.PayloadHash = controlPayloadHash(bad)
	if applyControlV2(bad, now) == nil {
		t.Fatal("future control accepted")
	}
	bad = e
	bad.ControlID = "C-2"
	bad.ControlSequence = 2
	bad.Action = "RESUME"
	bad.PayloadHash = controlPayloadHash(bad)
	if err := applyControlV2(bad, now); err != nil {
		t.Fatal(err)
	}
	if err := applyControlV2(e, now); err != nil {
		t.Fatal(err)
	}
	if readMissionControl(m.ID) != "RESUME" {
		t.Fatal("duplicate pause overwrote newer resume")
	}
	dir, _ := missionLocalDir(m.ID)
	os.WriteFile(filepath.Join(dir, "control-ledger.json"), []byte("{"), 0600)
	if readMissionControl(m.ID) != "CANCEL" {
		t.Fatal("corrupt state did not fail closed")
	}
}

func TestRuntimeIdentityMatchesExecutingBinary(t *testing.T) {
	i := currentRuntimeIdentity()
	if len(i.BinarySHA256) != 64 || i.ProtocolVersion != githubBusProtocol || i.PolicyVersion == "" {
		t.Fatalf("invalid identity %+v", i)
	}
	cp := MissionCheckpoint{RuntimeIdentity: i, MissionID: "M-identity"}
	if checkpointEnvelope(cp).RuntimeIdentity != i || sanitizeCheckpoint(cp).RuntimeIdentity != i {
		t.Fatal("identity lost at transport boundary")
	}
}
