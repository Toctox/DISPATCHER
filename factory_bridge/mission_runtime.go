package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	brainDirName         = "00_BRAIN"
	missionInboxDirName  = "01_INBOX"
	missionOutboxDirName = "02_OUTBOX"
	docsDirName          = "03_DOCS"
	missionPollInterval  = 2 * time.Second
)

type Mission struct {
	ID           string `json:"id"`
	Kind         string `json:"kind"`
	CreatedAt    string `json:"createdAt,omitempty"`
	Objective    string `json:"objective,omitempty"`
	TargetCommit string `json:"targetCommit,omitempty"`
	IssuedAt     string `json:"issuedAt,omitempty"`
	ExpiresAt    string `json:"expiresAt,omitempty"`
	PayloadHash  string `json:"payloadHash,omitempty"`
}

type MissionStepEvidence struct {
	Name       string `json:"name"`
	Status     string `json:"status"`
	DurationMs int64  `json:"durationMs"`
	Output     string `json:"output,omitempty"`
	Error      string `json:"error,omitempty"`
}

type MissionCheckpoint struct {
	MissionID         string                `json:"missionId"`
	Kind              string                `json:"kind"`
	State             string                `json:"state"`
	StartedAt         string                `json:"startedAt"`
	FinishedAt        string                `json:"finishedAt,omitempty"`
	DurationMs        int64                 `json:"durationMs,omitempty"`
	Objective         string                `json:"objective,omitempty"`
	Commit            string                `json:"commit,omitempty"`
	Summary           string                `json:"summary,omitempty"`
	DecisionQuestion  string                `json:"decisionQuestion,omitempty"`
	LocalEvidencePath string                `json:"localEvidencePath,omitempty"`
	ShowcasePath      string                `json:"showcasePath,omitempty"`
	Steps             []MissionStepEvidence `json:"steps,omitempty"`
	BridgeVersion     string                `json:"bridgeVersion"`
}

type missionEvidence struct {
	Mission    Mission           `json:"mission"`
	Checkpoint MissionCheckpoint `json:"checkpoint"`
	Results    map[string]Result `json:"results"`
	Notes      []string          `json:"notes,omitempty"`
}

func missionLocalRoot() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if strings.TrimSpace(base) == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	root := filepath.Join(base, "FactoryBridge", "missions")
	if err := os.MkdirAll(root, 0o755); err != nil {
		return "", err
	}
	return root, nil
}

func missionLocalDir(id string) (string, error) {
	root, err := missionLocalRoot()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(root, id)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	return dir, nil
}

func ensureMissionMailbox(cfg Config) error {
	for _, name := range []string{brainDirName, missionInboxDirName, missionOutboxDirName, docsDirName} {
		if err := os.MkdirAll(filepath.Join(cfg.BridgeRoot, name), 0o755); err != nil {
			return err
		}
	}
	if err := os.MkdirAll(filepath.Join(cfg.BridgeRoot, "03_ARCHIVE", "MISSIONS"), 0o755); err != nil {
		return err
	}
	return nil
}

func decodeMission(path string) (Mission, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Mission{}, err
	}
	var mission Mission
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&mission); err != nil {
		return Mission{}, err
	}
	if err := validateMissionIntegrity(mission); err != nil {
		return Mission{}, err
	}
	return mission, nil
}

func missionEntries(dir string) ([]os.DirEntry, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	filtered := make([]os.DirEntry, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasPrefix(strings.ToUpper(entry.Name()), "MISSION__") || !strings.EqualFold(filepath.Ext(entry.Name()), ".json") {
			continue
		}
		filtered = append(filtered, entry)
	}
	return orderCommandEntries(filtered), nil
}

func missionStep(name string, result Result) MissionStepEvidence {
	return MissionStepEvidence{
		Name:       name,
		Status:     result.Status,
		DurationMs: result.DurationMs,
		Output:     compact(result.Output, 600),
		Error:      compact(result.Error, 600),
	}
}

func writeMissionCheckpoint(cfg Config, cp MissionCheckpoint) error {
	out := filepath.Join(cfg.BridgeRoot, missionOutboxDirName, "CHECKPOINT__"+cp.MissionID+".json")
	if err := writeJSONAtomic(out, cp); err != nil {
		return err
	}
	current := filepath.Join(cfg.BridgeRoot, brainDirName, "CURRENT_STATE.json")
	return writeJSONAtomic(current, cp)
}

func writeMissionEvidence(dir string, evidence missionEvidence) error {
	return writeJSONAtomic(filepath.Join(dir, "evidence.json"), evidence)
}

func archiveMission(path string, mission Mission) error {
	archive := filepath.Join(filepath.Dir(filepath.Dir(path)), "03_ARCHIVE", "MISSIONS")
	return archiveCommand(path, archive, mission.ID)
}

func missionCommit(result Result) string {
	for _, key := range []string{"validatedCommit", "verifiedCommit", "head"} {
		if v, ok := result.Meta[key].(string); ok && strings.TrimSpace(v) != "" {
			return v
		}
	}
	if v, ok := result.Meta["git"].(map[string]any); ok {
		if head, ok := v["head"].(string); ok {
			return head
		}
	}
	return ""
}

func executeMission(cfg Config, mission Mission, r runner) MissionCheckpoint {
	started := time.Now()
	cp := MissionCheckpoint{
		MissionID:     mission.ID,
		Kind:          mission.Kind,
		State:         "RUNNING",
		StartedAt:     started.Format(time.RFC3339),
		Objective:     strings.TrimSpace(mission.Objective),
		BridgeVersion: bridgeVersion,
	}
	localDir, err := missionLocalDir(mission.ID)
	if err != nil {
		cp.State = "BLOCKED"
		cp.Summary = err.Error()
		cp.FinishedAt = time.Now().Format(time.RFC3339)
		cp.DurationMs = time.Since(started).Milliseconds()
		return cp
	}
	if _, _, err := reserveMission(mission); err != nil {
		cp.State = "BLOCKED"
		cp.Summary = err.Error()
		cp.FinishedAt = time.Now().Format(time.RFC3339)
		cp.DurationMs = time.Since(started).Milliseconds()
		return cp
	}

	missionExecutionMu.Lock()
	defer missionExecutionMu.Unlock()
	_ = markMissionJournalState(mission.ID, "RUNNING")

	cp.LocalEvidencePath = localDir
	_ = writeJSONAtomic(filepath.Join(localDir, "mission.json"), mission)
	_ = writeMissionCheckpoint(cfg, cp)

	evidence := missionEvidence{Mission: mission, Results: map[string]Result{}}
	fail := func(summary string, result Result) MissionCheckpoint {
		cp.State = "NEEDS_BRAIN"
		cp.Summary = summary
		cp.DecisionQuestion = "Review the local evidence and choose the next implementation or product decision."
		cp.FinishedAt = time.Now().Format(time.RFC3339)
		cp.DurationMs = time.Since(started).Milliseconds()
		evidence.Checkpoint = cp
		_ = writeMissionEvidence(localDir, evidence)
		_ = markMissionJournalState(mission.ID, cp.State)
		return cp
	}

	if err := validateMissionIntegrity(mission); err != nil {
		return fail("Mission integrity validation failed: "+err.Error(), Result{})
	}
	if err := waitForMissionPermission(mission.ID); err != nil {
		return fail(err.Error(), Result{})
	}
	if err := ensureMissionTargetCommit(cfg, mission, r); err != nil {
		return fail("Authorized target commit validation failed: "+err.Error(), Result{})
	}

	switch mission.Kind {
	case "system.command":
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}
		command := executeSystemCommand(cfg, mission, time.Now(), r)
		evidence.Results["system-command"] = command
		cp.Steps = append(cp.Steps, missionStep("system-command", command))
		cp.Commit = strings.ToLower(strings.TrimSpace(mission.TargetCommit))
		if command.Status == "blocked" {
			cp.State = "BLOCKED"
			cp.Summary = command.Error
			if command.Meta["requiresApproval"] == true {
				cp.DecisionQuestion = "If this destructive or sensitive operation is intended, submit a new system.command mission with riskApproval=approved."
			}
			break
		}
		if command.Status != "ok" {
			return fail("System command failed; stdout, stderr and risk classification were retained locally.", command)
		}
		cp.State = "DONE"
		cp.Summary = command.Output
		if strings.TrimSpace(cp.Summary) == "" {
			cp.Summary = "System command completed successfully."
		}

	case "cafe.ccc.scan":
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}
		scan := executeCafeCCCScan(cfg, mission, time.Now(), r)
		evidence.Results["cafe-ccc-scan"] = scan
		cp.Steps = append(cp.Steps, missionStep("cafe-ccc-scan", scan))
		cp.Commit = strings.ToLower(strings.TrimSpace(mission.TargetCommit))
		if scan.Status != "ok" {
			return fail("Café CCC/SVRS scan failed; local evidence and sanitized errors were retained.", scan)
		}
		cp.State = "DONE"
		cp.Summary = scan.Output
		if strings.TrimSpace(cp.Summary) == "" {
			cp.Summary = "Café CCC/SVRS scan completed successfully."
		}

	case "projecthub.verify":
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}
		verify := executeProjectHubVerify(cfg, Command{ID: mission.ID, Action: "projecthub.verify"}, time.Now(), r)
		evidence.Results["verify"] = verify
		cp.Steps = append(cp.Steps, missionStep("verify", verify))
		cp.Commit = missionCommit(verify)
		if verify.Status != "ok" {
			if strings.Contains(strings.ToLower(verify.Error), "test") {
				for i := 1; i <= 2; i++ {
					retry := executeProjectHubTest(cfg, Command{ID: fmt.Sprintf("%s-retry-%d", mission.ID, i), Action: "projecthub.test"}, time.Now(), r)
					name := fmt.Sprintf("test-retry-%d", i)
					evidence.Results[name] = retry
					cp.Steps = append(cp.Steps, missionStep(name, retry))
				}
			}
			return fail("ProjectHub verification failed; deterministic evidence was collected locally.", verify)
		}
		cp.State = "DONE"
		cp.Summary = "ProjectHub verification completed successfully."

	case "projecthub.showcase":
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}
		publish := executeProjectHubShowcasePublish(cfg, Command{ID: mission.ID, Action: "projecthub.showcase"}, time.Now(), r)
		evidence.Results["showcase"] = publish
		cp.Steps = append(cp.Steps, missionStep("showcase", publish))
		cp.Commit = missionCommit(publish)
		if path, ok := publish.Meta["showcasePath"].(string); ok {
			cp.ShowcasePath = path
		}
		if publish.Status != "ok" {
			return fail("ProjectHub showcase publication failed; evidence was retained locally.", publish)
		}
		cp.State = "DONE"
		cp.Summary = "ProjectHub showcase build published successfully."

	case "projecthub.full_cycle":
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}
		syncResult := executeProjectHubSync(cfg, Command{ID: mission.ID, Action: "projecthub.sync"}, time.Now(), r)
		evidence.Results["sync"] = syncResult
		cp.Steps = append(cp.Steps, missionStep("sync", syncResult))
		if syncResult.Status != "ok" {
			return fail("ProjectHub sync failed before the autonomous cycle could continue.", syncResult)
		}
		if err := ensureCanonicalCheckoutAtTarget(cfg, mission, r); err != nil {
			return fail("ProjectHub moved away from the authorized target during sync: "+err.Error(), syncResult)
		}
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}

		verify := executeProjectHubVerify(cfg, Command{ID: mission.ID, Action: "projecthub.verify"}, time.Now(), r)
		evidence.Results["verify"] = verify
		cp.Steps = append(cp.Steps, missionStep("verify", verify))
		cp.Commit = missionCommit(verify)
		if verify.Status != "ok" {
			if strings.Contains(strings.ToLower(verify.Error), "test") {
				for i := 1; i <= 2; i++ {
					retry := executeProjectHubTest(cfg, Command{ID: fmt.Sprintf("%s-retry-%d", mission.ID, i), Action: "projecthub.test"}, time.Now(), r)
					name := fmt.Sprintf("test-retry-%d", i)
					evidence.Results[name] = retry
					cp.Steps = append(cp.Steps, missionStep(name, retry))
				}
			}
			return fail("ProjectHub verify stage failed; retries and complete logs are local.", verify)
		}
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}

		publish := executeProjectHubShowcasePublish(cfg, Command{ID: mission.ID, Action: "projecthub.showcase"}, time.Now(), r)
		evidence.Results["showcase"] = publish
		cp.Steps = append(cp.Steps, missionStep("showcase", publish))
		if path, ok := publish.Meta["showcasePath"].(string); ok {
			cp.ShowcasePath = path
		}
		if publish.Status != "ok" {
			return fail("Verification passed but showcase publication failed.", publish)
		}
		if err := waitForMissionPermission(mission.ID); err != nil {
			return fail(err.Error(), Result{})
		}

		smoke := executeProjectHubShowcaseSmoke(cfg, Command{ID: mission.ID, Action: "projecthub.showcase.smoke"}, time.Now())
		evidence.Results["showcase-smoke"] = smoke
		cp.Steps = append(cp.Steps, missionStep("showcase-smoke", smoke))
		if smoke.Status != "ok" {
			return fail("Build and tests passed, but the published showcase failed its local health smoke test.", smoke)
		}
		cp.State = "DONE"
		cp.Summary = "Autonomous ProjectHub cycle completed: sync, verify, publish and local smoke all passed."
	}

	cp.FinishedAt = time.Now().Format(time.RFC3339)
	cp.DurationMs = time.Since(started).Milliseconds()
	evidence.Checkpoint = cp
	_ = writeMissionEvidence(localDir, evidence)
	_ = markMissionJournalState(mission.ID, cp.State)
	return cp
}

func processMissionFile(cfg Config, path string) error {
	mission, err := decodeMission(path)
	if err != nil {
		bad := MissionCheckpoint{
			MissionID:     fmt.Sprintf("BAD-%d", time.Now().UnixNano()),
			Kind:          "invalid",
			State:         "BLOCKED",
			StartedAt:     time.Now().Format(time.RFC3339),
			FinishedAt:    time.Now().Format(time.RFC3339),
			Summary:       err.Error(),
			BridgeVersion: bridgeVersion,
		}
		_ = writeMissionCheckpoint(cfg, bad)
		return archiveCommand(path, filepath.Join(cfg.BridgeRoot, "03_ARCHIVE", "MISSIONS"), bad.MissionID)
	}
	localDir, localErr := missionLocalDir(mission.ID)
	if localErr == nil {
		if data, readErr := os.ReadFile(path); readErr == nil {
			_ = os.WriteFile(filepath.Join(localDir, "inbox-copy.json"), data, 0o600)
		}
	}
	cp := executeMission(cfg, mission, osRunner{})
	if err := writeMissionCheckpoint(cfg, cp); err != nil {
		return err
	}
	return archiveMission(path, mission)
}

func runMissionService(cfg Config) {
	if err := ensureMissionMailbox(cfg); err != nil {
		fmt.Fprintf(os.Stderr, "mission mailbox unavailable: %v\n", err)
		return
	}
	inbox := filepath.Join(cfg.BridgeRoot, missionInboxDirName)
	for {
		entries, err := missionEntries(inbox)
		if err != nil {
			fmt.Fprintf(os.Stderr, "mission inbox error: %v\n", err)
			time.Sleep(missionPollInterval)
			continue
		}
		for _, entry := range entries {
			path := filepath.Join(inbox, entry.Name())
			if err := processMissionFile(cfg, path); err != nil {
				fmt.Fprintf(os.Stderr, "mission process error %s: %v\n", entry.Name(), err)
			}
		}
		time.Sleep(missionPollInterval)
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
	go runMissionService(cfg)
}

// Keep sort imported explicitly documented by the mission FIFO contract when
// future mailbox ordering evolves independently from command ordering.
var _ = sort.SliceStable
