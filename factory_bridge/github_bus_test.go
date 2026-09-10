package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestParseGitHubBusEnvelopeMission(t *testing.T) {
	body := githubBusMarker + "\n```json\n" + `{
  "protocol":"FACTORY_BUS_V2",
  "type":"MISSION",
  "id":"M-TEST-001",
  "kind":"projecthub.verify",
  "objective":"Validate current main",
  "targetCommit":"6494e7b51aae694f4559f836199cb78212976edc",
  "issuedAt":"2026-09-09T03:00:00Z",
  "expiresAt":"2026-09-09T04:00:00Z",
  "payloadHash":"deadbeef"
}` + "\n```"
	env, err := parseGitHubBusEnvelope(body)
	if err != nil {
		t.Fatalf("parseGitHubBusEnvelope: %v", err)
	}
	if env.Type != "MISSION" || env.ID != "M-TEST-001" || env.Kind != "projecthub.verify" || env.Protocol != githubBusProtocol {
		t.Fatalf("unexpected envelope: %#v", env)
	}
}

func TestParseGitHubBusEnvelopeRejectsUnknownField(t *testing.T) {
	body := githubBusMarker + `
{"protocol":"FACTORY_BUS_V2","type":"MISSION","id":"M-1","kind":"projecthub.verify","shell":"whoami"}`
	if _, err := parseGitHubBusEnvelope(body); err == nil {
		t.Fatal("expected unknown field to be rejected")
	}
}

func TestParseLegacyGitHubBusEnvelopeForHistoricalCompatibility(t *testing.T) {
	body := githubBusLegacyMarker + `
{"protocol":"FACTORY_BUS_V1","type":"CHECKPOINT","id":"M-OLD","state":"DONE"}`
	env, err := parseGitHubBusEnvelope(body)
	if err != nil {
		t.Fatalf("legacy parse failed: %v", err)
	}
	if env.Protocol != githubBusLegacyProtocol || env.Type != "CHECKPOINT" {
		t.Fatalf("unexpected legacy envelope: %#v", env)
	}
}

func TestParseGitCredentialOutput(t *testing.T) {
	user, token := parseGitCredentialOutput("protocol=https\nhost=github.com\nusername=Toctox\npassword=secret-token\n")
	if user != "Toctox" || token != "secret-token" {
		t.Fatalf("unexpected credential parse user=%q token=%q", user, token)
	}
}

func TestGitHubBusHTTPReadAndWrite(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	oldBase := githubAPIBase
	defer func() { githubAPIBase = oldBase }()

	created := time.Date(2026, 9, 9, 3, 0, 0, 0, time.UTC)
	var posted map[string]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/issues/7/comments"):
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode([]githubIssueComment{{
				ID:        101,
				Body:      githubBusMarker + `\n{"protocol":"FACTORY_BUS_V2","type":"CHECKPOINT","id":"M-1","state":"DONE"}`,
				CreatedAt: created,
				User: struct {
					Login string `json:"login"`
				}{Login: "Toctox"},
			}})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/issues/7/comments"):
			if got := r.Header.Get("Authorization"); got != "Bearer token" {
				t.Fatalf("unexpected auth header: %q", got)
			}
			if err := json.NewDecoder(r.Body).Decode(&posted); err != nil {
				t.Fatalf("decode post: %v", err)
			}
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"id":102}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	githubAPIBase = server.URL

	comments, err := listGitHubBusComments("token", githubBusState{})
	if err != nil {
		t.Fatalf("listGitHubBusComments: %v", err)
	}
	if len(comments) != 1 || comments[0].ID != 101 {
		t.Fatalf("unexpected comments: %#v", comments)
	}
	if err := postGitHubBusEnvelope("token", githubBusEnvelope{Type: "ACK", ID: "M-1"}); err != nil {
		t.Fatalf("postGitHubBusEnvelope: %v", err)
	}
	if !strings.Contains(posted["body"], githubBusMarker) || !strings.Contains(posted["body"], `"type": "ACK"`) || !strings.Contains(posted["body"], `"protocol": "FACTORY_BUS_V2"`) {
		t.Fatalf("unexpected posted body: %q", posted["body"])
	}
}

func TestGitHubBusStateIsLocal(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	state := githubBusState{LastCommentID: 42, LastSeenAt: "2026-09-09T03:00:00Z"}
	if err := writeGitHubBusState(state); err != nil {
		t.Fatalf("writeGitHubBusState: %v", err)
	}
	got := readGitHubBusState()
	if got.LastCommentID != 42 || got.LastSeenAt != state.LastSeenAt {
		t.Fatalf("unexpected state: %#v", got)
	}
}
