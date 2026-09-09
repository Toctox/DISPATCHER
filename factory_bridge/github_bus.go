package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	githubBusProtocol       = "FACTORY_BUS_V2"
	githubBusLegacyProtocol = "FACTORY_BUS_V1"
	githubBusRepo           = "Toctox/DISPATCHER"
	githubBusIssue          = 7
	githubBusTrustedAuthor  = "Toctox"
	githubBusPollInterval   = 60 * time.Second
	githubBusMarker         = "<!-- FACTORY_BUS_V2 -->"
	githubBusLegacyMarker   = "<!-- FACTORY_BUS_V1 -->"
)

var githubAPIBase = "https://api.github.com"

type githubBusEnvelope struct {
	Protocol      string `json:"protocol"`
	Type          string `json:"type"`
	ID            string `json:"id"`
	Kind          string `json:"kind,omitempty"`
	Objective     string `json:"objective,omitempty"`
	TargetCommit  string `json:"targetCommit,omitempty"`
	IssuedAt      string `json:"issuedAt,omitempty"`
	ExpiresAt     string `json:"expiresAt,omitempty"`
	PayloadHash   string `json:"payloadHash,omitempty"`
	Action        string `json:"action,omitempty"`
	State         string `json:"state,omitempty"`
	Summary       string `json:"summary,omitempty"`
	Commit        string `json:"commit,omitempty"`
	DurationMs    int64  `json:"durationMs,omitempty"`
	BridgeVersion string `json:"bridgeVersion,omitempty"`
	ObservedAt    string `json:"observedAt,omitempty"`
}

type githubIssueComment struct {
	ID        int64     `json:"id"`
	Body      string    `json:"body"`
	CreatedAt time.Time `json:"created_at"`
	User      struct {
		Login string `json:"login"`
	} `json:"user"`
}

type githubBusState struct {
	LastCommentID int64  `json:"lastCommentId"`
	LastSeenAt    string `json:"lastSeenAt,omitempty"`
	LastPollAt    string `json:"lastPollAt,omitempty"`
}

func githubBusStatePath() (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	dir := filepath.Join(base, "FactoryBridge", "state")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	return filepath.Join(dir, "github-bus.json"), nil
}

func readGitHubBusState() githubBusState {
	path, err := githubBusStatePath()
	if err != nil {
		return githubBusState{}
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return githubBusState{}
	}
	var state githubBusState
	if json.Unmarshal(data, &state) != nil {
		return githubBusState{}
	}
	return state
}

func writeGitHubBusState(state githubBusState) error {
	path, err := githubBusStatePath()
	if err != nil {
		return err
	}
	return writeJSONAtomic(path, state)
}

func parseGitCredentialOutput(raw string) (username, token string) {
	for _, line := range strings.Split(raw, "\n") {
		key, value, ok := strings.Cut(strings.TrimSpace(line), "=")
		if !ok {
			continue
		}
		switch strings.ToLower(key) {
		case "username":
			username = value
		case "password":
			token = value
		}
	}
	return strings.TrimSpace(username), strings.TrimSpace(token)
}

func githubCredential(cfg Config) (username, token string, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git.exe", "credential", "fill")
	if strings.TrimSpace(cfg.DispatcherWorkDir) != "" {
		cmd.Dir = cfg.DispatcherWorkDir
	}
	cmd.Stdin = strings.NewReader("protocol=https\nhost=github.com\n\n")
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if runErr := cmd.Run(); runErr != nil {
		return "", "", fmt.Errorf("git credential fill failed: %w: %s", runErr, compact(stderr.String(), 300))
	}
	username, token = parseGitCredentialOutput(stdout.String())
	if token == "" {
		return "", "", errors.New("GitHub credential helper returned no token")
	}
	return username, token, nil
}

func githubBusRequest(token, method, endpoint string, body any) (*http.Response, error) {
	var payload io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		payload = bytes.NewReader(data)
	}
	req, err := http.NewRequest(method, endpoint, payload)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	req.Header.Set("User-Agent", "FactoryBridge/"+bridgeVersion)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if strings.TrimSpace(token) != "" {
		req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(token))
	}
	client := &http.Client{Timeout: 15 * time.Second}
	return client.Do(req)
}

func githubBusCommentsEndpoint(state githubBusState, page int) string {
	if page < 1 {
		page = 1
	}
	endpoint := fmt.Sprintf("%s/repos/%s/issues/%d/comments?per_page=100&page=%d", githubAPIBase, githubBusRepo, githubBusIssue, page)
	if strings.TrimSpace(state.LastSeenAt) != "" {
		if parsed, err := time.Parse(time.RFC3339, state.LastSeenAt); err == nil {
			since := parsed.Add(-time.Second).UTC().Format(time.RFC3339)
			endpoint += "&since=" + url.QueryEscape(since)
		}
	}
	return endpoint
}

func listGitHubBusComments(token string, state githubBusState) ([]githubIssueComment, error) {
	comments := []githubIssueComment{}
	for page := 1; page <= 20; page++ {
		resp, err := githubBusRequest(token, http.MethodGet, githubBusCommentsEndpoint(state, page), nil)
		if err != nil {
			return nil, err
		}
		if resp.StatusCode != http.StatusOK {
			data, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
			_ = resp.Body.Close()
			return nil, fmt.Errorf("GitHub comments GET returned %d: %s", resp.StatusCode, compact(string(data), 500))
		}
		var pageComments []githubIssueComment
		err = json.NewDecoder(io.LimitReader(resp.Body, 2*1024*1024)).Decode(&pageComments)
		_ = resp.Body.Close()
		if err != nil {
			return nil, err
		}
		comments = append(comments, pageComments...)
		if len(pageComments) < 100 {
			break
		}
	}
	sort.Slice(comments, func(i, j int) bool { return comments[i].ID < comments[j].ID })
	return comments, nil
}

func postGitHubBusEnvelope(token string, envelope githubBusEnvelope) error {
	envelope.Protocol = githubBusProtocol
	if envelope.ObservedAt == "" {
		envelope.ObservedAt = time.Now().UTC().Format(time.RFC3339)
	}
	data, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return err
	}
	body := githubBusMarker + "\n```json\n" + string(data) + "\n```"
	endpoint := fmt.Sprintf("%s/repos/%s/issues/%d/comments", githubAPIBase, githubBusRepo, githubBusIssue)
	resp, err := githubBusRequest(token, http.MethodPost, endpoint, map[string]string{"body": body})
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusCreated {
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return fmt.Errorf("GitHub comment POST returned %d: %s", resp.StatusCode, compact(string(data), 500))
	}
	return nil
}

func parseGitHubBusEnvelope(body string) (githubBusEnvelope, error) {
	marker := githubBusMarker
	markerIndex := strings.Index(body, marker)
	if markerIndex < 0 {
		marker = githubBusLegacyMarker
		markerIndex = strings.Index(body, marker)
	}
	if markerIndex < 0 {
		return githubBusEnvelope{}, errors.New("Factory bus marker not found")
	}
	raw := strings.TrimSpace(body[markerIndex+len(marker):])
	first := strings.Index(raw, "{")
	last := strings.LastIndex(raw, "}")
	if first < 0 || last < first {
		return githubBusEnvelope{}, errors.New("Factory bus JSON payload not found")
	}
	dec := json.NewDecoder(strings.NewReader(raw[first : last+1]))
	dec.DisallowUnknownFields()
	var envelope githubBusEnvelope
	if err := dec.Decode(&envelope); err != nil {
		return githubBusEnvelope{}, err
	}
	if envelope.Protocol != githubBusProtocol && envelope.Protocol != githubBusLegacyProtocol {
		return githubBusEnvelope{}, errors.New("unsupported Factory bus protocol")
	}
	if envelope.Protocol == githubBusProtocol && marker != githubBusMarker {
		return githubBusEnvelope{}, errors.New("V2 protocol requires V2 marker")
	}
	return envelope, nil
}

func existingMissionCheckpoint(id string) (*MissionCheckpoint, bool) {
	dir, err := missionLocalDir(id)
	if err != nil {
		return nil, false
	}
	path := filepath.Join(dir, "evidence.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, false
	}
	var evidence missionEvidence
	if json.Unmarshal(data, &evidence) != nil || strings.TrimSpace(evidence.Checkpoint.State) == "" {
		return nil, false
	}
	return &evidence.Checkpoint, true
}

func checkpointEnvelope(cp MissionCheckpoint) githubBusEnvelope {
	return githubBusEnvelope{
		Type:          "CHECKPOINT",
		ID:            cp.MissionID,
		Kind:          cp.Kind,
		State:         cp.State,
		Summary:       compact(cp.Summary, 800),
		Commit:        cp.Commit,
		DurationMs:    cp.DurationMs,
		BridgeVersion: cp.BridgeVersion,
	}
}

func blockedEnvelope(mission Mission, summary string) githubBusEnvelope {
	return checkpointEnvelope(MissionCheckpoint{
		MissionID:     mission.ID,
		Kind:          mission.Kind,
		State:         "BLOCKED",
		StartedAt:     time.Now().Format(time.RFC3339),
		FinishedAt:    time.Now().Format(time.RFC3339),
		Summary:       summary,
		BridgeVersion: bridgeVersion,
	})
}

func executeGitHubMission(cfg Config, token string, mission Mission) {
	cp := executeMission(cfg, mission, osRunner{})
	if err := postGitHubBusEnvelope(token, checkpointEnvelope(cp)); err != nil {
		fmt.Fprintf(os.Stderr, "github bus checkpoint publish error mission=%s: %v\n", mission.ID, err)
	}
}

func processGitHubControl(token string, envelope githubBusEnvelope) (bool, error) {
	if envelope.Protocol != githubBusProtocol {
		return true, nil
	}
	if !idPattern.MatchString(envelope.ID) {
		return true, postGitHubBusEnvelope(token, githubBusEnvelope{Type: "CONTROL_ACK", ID: envelope.ID, State: "BLOCKED", Summary: "invalid mission id", BridgeVersion: bridgeVersion})
	}
	if _, err := readMissionJournal(envelope.ID); err != nil {
		return true, postGitHubBusEnvelope(token, githubBusEnvelope{Type: "CONTROL_ACK", ID: envelope.ID, State: "BLOCKED", Summary: "mission is not known locally", BridgeVersion: bridgeVersion})
	}
	if err := setMissionControl(envelope.ID, envelope.Action); err != nil {
		return true, postGitHubBusEnvelope(token, githubBusEnvelope{Type: "CONTROL_ACK", ID: envelope.ID, State: "BLOCKED", Summary: err.Error(), BridgeVersion: bridgeVersion})
	}
	return true, postGitHubBusEnvelope(token, githubBusEnvelope{Type: "CONTROL_ACK", ID: envelope.ID, State: strings.ToUpper(strings.TrimSpace(envelope.Action)), Summary: "Mission control state updated.", BridgeVersion: bridgeVersion})
}

func processGitHubBusComment(cfg Config, token string, comment githubIssueComment) (bool, error) {
	if !strings.EqualFold(strings.TrimSpace(comment.User.Login), githubBusTrustedAuthor) {
		return true, nil
	}
	envelope, err := parseGitHubBusEnvelope(comment.Body)
	if err != nil {
		return true, nil
	}
	messageType := strings.ToUpper(strings.TrimSpace(envelope.Type))
	if messageType == "CONTROL" {
		return processGitHubControl(token, envelope)
	}
	if messageType != "MISSION" {
		return true, nil
	}

	mission := Mission{
		ID:           envelope.ID,
		Kind:         envelope.Kind,
		CreatedAt:    comment.CreatedAt.Format(time.RFC3339),
		Objective:    envelope.Objective,
		TargetCommit: envelope.TargetCommit,
		IssuedAt:     envelope.IssuedAt,
		ExpiresAt:    envelope.ExpiresAt,
		PayloadHash:  envelope.PayloadHash,
	}

	if envelope.Protocol == githubBusLegacyProtocol {
		if cp, ok := existingMissionCheckpoint(mission.ID); ok {
			postErr := postGitHubBusEnvelope(token, checkpointEnvelope(*cp))
			return postErr == nil, postErr
		}
		// Historical V1 commands are never newly executed after the V2 cutover.
		return true, nil
	}

	if err := validateMissionIntegrity(mission); err != nil {
		postErr := postGitHubBusEnvelope(token, blockedEnvelope(mission, err.Error()))
		return postErr == nil, postErr
	}
	if cp, ok := existingMissionCheckpoint(mission.ID); ok {
		postErr := postGitHubBusEnvelope(token, checkpointEnvelope(*cp))
		return postErr == nil, postErr
	}
	journal, created, err := reserveMission(mission)
	if err != nil {
		postErr := postGitHubBusEnvelope(token, blockedEnvelope(mission, err.Error()))
		return postErr == nil, postErr
	}
	if !created {
		state := strings.ToUpper(strings.TrimSpace(journal.State))
		ack := githubBusEnvelope{Type: "ACK", ID: mission.ID, Kind: mission.Kind, State: "ALREADY_RESERVED", Summary: "Mission payload is already reserved locally in state " + state + ".", BridgeVersion: bridgeVersion}
		postErr := postGitHubBusEnvelope(token, ack)
		return postErr == nil, postErr
	}
	_ = markMissionJournalState(mission.ID, "QUEUED")
	ack := githubBusEnvelope{
		Type:          "ACK",
		ID:            mission.ID,
		Kind:          mission.Kind,
		State:         "ACCEPTED",
		Summary:       "Mission accepted and durably queued by the local FactoryBridge runtime.",
		BridgeVersion: bridgeVersion,
	}
	if err := postGitHubBusEnvelope(token, ack); err != nil {
		return false, err
	}
	_ = markMissionJournalState(mission.ID, "ACKED")
	go executeGitHubMission(cfg, token, mission)
	return true, nil
}

func recoverInterruptedGitHubMissions(token string) {
	journals, err := interruptedMissionJournals()
	if err != nil {
		fmt.Fprintf(os.Stderr, "github bus interrupted mission scan error: %v\n", err)
		return
	}
	for _, journal := range journals {
		mission := journal.Mission
		if cp, ok := existingMissionCheckpoint(mission.ID); ok {
			_ = markMissionJournalState(mission.ID, cp.State)
			if token != "" {
				_ = postGitHubBusEnvelope(token, checkpointEnvelope(*cp))
			}
			continue
		}
		cp := MissionCheckpoint{
			MissionID:        mission.ID,
			Kind:             mission.Kind,
			State:            "NEEDS_BRAIN",
			StartedAt:        journal.UpdatedAt,
			FinishedAt:       time.Now().Format(time.RFC3339),
			Summary:          "Runtime restarted while the mission was non-terminal; FactoryBridge refused automatic replay to avoid duplicate side effects.",
			DecisionQuestion: "Review the interruption and explicitly submit a new mission id if retry is desired.",
			BridgeVersion:    bridgeVersion,
		}
		dir, dirErr := missionLocalDir(mission.ID)
		if dirErr == nil {
			_ = writeMissionEvidence(dir, missionEvidence{Mission: mission, Checkpoint: cp, Results: map[string]Result{}, Notes: []string{"Recovered fail-closed after runtime restart."}})
		}
		_ = markMissionJournalState(mission.ID, cp.State)
		if token != "" {
			_ = postGitHubBusEnvelope(token, checkpointEnvelope(cp))
		}
	}
}

func pollGitHubBusOnce(cfg Config) error {
	_, token, credentialErr := githubCredential(cfg)
	if credentialErr != nil {
		token = ""
	}
	state := readGitHubBusState()
	comments, err := listGitHubBusComments(token, state)
	if err != nil {
		return err
	}
	for _, comment := range comments {
		if comment.ID <= state.LastCommentID {
			continue
		}
		trustedV2 := strings.EqualFold(strings.TrimSpace(comment.User.Login), githubBusTrustedAuthor) && strings.Contains(comment.Body, githubBusMarker)
		if trustedV2 && token == "" {
			return errors.New("trusted Factory bus V2 message is pending but GitHub write credential is unavailable")
		}
		processed, err := processGitHubBusComment(cfg, token, comment)
		if err != nil || !processed {
			if err != nil {
				return err
			}
			return errors.New("Factory bus comment was not fully processed")
		}
		state.LastCommentID = comment.ID
		if !comment.CreatedAt.IsZero() {
			state.LastSeenAt = comment.CreatedAt.UTC().Format(time.RFC3339)
		}
		state.LastPollAt = time.Now().UTC().Format(time.RFC3339)
		if err := writeGitHubBusState(state); err != nil {
			return err
		}
	}
	state.LastPollAt = time.Now().UTC().Format(time.RFC3339)
	return writeGitHubBusState(state)
}

func runGitHubBusService(cfg Config) {
	time.Sleep(3 * time.Second)
	_, token, _ := githubCredential(cfg)
	recoverInterruptedGitHubMissions(token)
	backoff := githubBusPollInterval
	for {
		if err := pollGitHubBusOnce(cfg); err != nil {
			fmt.Fprintf(os.Stderr, "github bus poll error: %v\n", err)
			backoff *= 2
			if backoff > 5*time.Minute {
				backoff = 5 * time.Minute
			}
		} else {
			backoff = githubBusPollInterval
		}
		time.Sleep(backoff)
	}
}

func init() {
	mode, err := selectedMode(os.Args[1:])
	if err != nil || mode != "executor" {
		return
	}
	cfgPath, err := configPath()
	if err != nil {
		return
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		return
	}
	go runGitHubBusService(cfg)
}
