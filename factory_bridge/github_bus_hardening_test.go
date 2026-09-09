package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"
)

func TestGitHubBusPaginatesBeyondOneHundredComments(t *testing.T) {
	oldBase := githubAPIBase
	defer func() { githubAPIBase = oldBase }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		page, _ := strconv.Atoi(r.URL.Query().Get("page"))
		if page == 0 {
			page = 1
		}
		w.Header().Set("Content-Type", "application/json")
		items := []githubIssueComment{}
		count := 100
		if page == 2 {
			count = 1
		}
		if page > 2 {
			count = 0
		}
		for i := 0; i < count; i++ {
			items = append(items, githubIssueComment{ID: int64((page-1)*100 + i + 1), CreatedAt: time.Now().UTC()})
		}
		_ = json.NewEncoder(w).Encode(items)
	}))
	defer server.Close()
	githubAPIBase = server.URL

	comments, err := listGitHubBusComments("token", githubBusState{})
	if err != nil {
		t.Fatal(err)
	}
	if len(comments) != 101 || comments[0].ID != 1 || comments[100].ID != 101 {
		t.Fatalf("unexpected pagination result count=%d first=%d last=%d", len(comments), comments[0].ID, comments[len(comments)-1].ID)
	}
}

func TestLegacyMissionWithoutEvidenceIsNeverNewlyExecuted(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	comment := githubIssueComment{
		ID:        777,
		Body:      githubBusLegacyMarker + "\n```json\n" + `{"protocol":"FACTORY_BUS_V1","type":"MISSION","id":"M-LEGACY-777","kind":"projecthub.verify"}` + "\n```",
		CreatedAt: time.Now().UTC(),
		User: struct {
			Login string `json:"login"`
		}{Login: githubBusTrustedAuthor},
	}
	processed, err := processGitHubBusComment(Config{}, "", comment)
	if err != nil || !processed {
		t.Fatalf("legacy mission should be ignored safely processed=%t err=%v", processed, err)
	}
	if _, err := readMissionJournal("M-LEGACY-777"); err == nil {
		t.Fatal("legacy mission unexpectedly created a local execution reservation")
	}
}

func TestRecoverInterruptedMissionFailsClosedWithoutReplay(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-RECOVERY-001")
	if _, _, err := reserveMission(m); err != nil {
		t.Fatal(err)
	}
	if err := markMissionJournalState(m.ID, "RUNNING"); err != nil {
		t.Fatal(err)
	}
	recoverInterruptedGitHubMissions("")
	cp, ok := existingMissionCheckpoint(m.ID)
	if !ok {
		t.Fatal("recovery did not persist a terminal checkpoint")
	}
	if cp.State != "NEEDS_BRAIN" {
		t.Fatalf("interrupted mission should fail closed, got %s", cp.State)
	}
	journal, err := readMissionJournal(m.ID)
	if err != nil {
		t.Fatal(err)
	}
	if journal.State != "NEEDS_BRAIN" {
		t.Fatalf("journal not reconciled: %s", journal.State)
	}
}

func TestControlMessageCanCancelKnownMission(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-CONTROL-BUS-001")
	if _, _, err := reserveMission(m); err != nil {
		t.Fatal(err)
	}

	oldBase := githubAPIBase
	defer func() { githubAPIBase = oldBase }()
	posted := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "unexpected method", http.StatusBadRequest)
			return
		}
		posted++
		w.WriteHeader(http.StatusCreated)
		_, _ = fmt.Fprint(w, `{"id":1}`)
	}))
	defer server.Close()
	githubAPIBase = server.URL

	processed, err := processGitHubControl("token", githubBusEnvelope{Protocol: githubBusProtocol, Type: "CONTROL", ID: m.ID, Action: "CANCEL"})
	if err != nil || !processed {
		t.Fatalf("control failed processed=%t err=%v", processed, err)
	}
	if posted != 1 {
		t.Fatalf("expected one control acknowledgement, got %d", posted)
	}
	if got := readMissionControl(m.ID); got != "CANCEL" {
		t.Fatalf("expected CANCEL, got %q", got)
	}
}
