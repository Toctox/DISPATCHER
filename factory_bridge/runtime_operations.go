package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	runtimeOperationsFileName = "runtime-operations.json"
	gatewayTokenMetaFileName  = "gateway-token-meta.json"
	gatewayTokenMaxAge        = 30 * 24 * time.Hour
	missionRetentionAge       = 30 * 24 * time.Hour
	missionRetentionBytes     = int64(512 * 1024 * 1024)
	workspaceRetentionAge     = 24 * time.Hour
)

type runtimeOperationsState struct {
	BusEpoch              string `json:"busEpoch"`
	EpochCreatedAt        string `json:"epochCreatedAt"`
	RateLimitLimit        int    `json:"rateLimitLimit,omitempty"`
	RateLimitRemaining    int    `json:"rateLimitRemaining,omitempty"`
	RateLimitResetUnix    int64  `json:"rateLimitResetUnix,omitempty"`
	RateLimitObservedAt   string `json:"rateLimitObservedAt,omitempty"`
	RateLimitError        string `json:"rateLimitError,omitempty"`
	LastHousekeepingAt    string `json:"lastHousekeepingAt,omitempty"`
	MissionBytes          int64  `json:"missionBytes,omitempty"`
	MissionDirectories    int    `json:"missionDirectories,omitempty"`
	RemovedMissionDirs    int    `json:"removedMissionDirs,omitempty"`
	RemovedWorkspaceDirs  int    `json:"removedWorkspaceDirs,omitempty"`
	PermissionModel       string `json:"permissionModel"`
}

type gatewayTokenMetadata struct {
	CreatedAt   string `json:"createdAt"`
	RotatedAt   string `json:"rotatedAt,omitempty"`
	RotateAfter string `json:"rotateAfter"`
	Fingerprint string `json:"fingerprint"`
}

type runtimeCapabilities struct {
	SourceCommit          string         `json:"sourceCommit"`
	ProtocolVersion       string         `json:"protocolVersion"`
	PolicyVersion         string         `json:"policyVersion"`
	MissionKinds          []string       `json:"missionKinds"`
	ControlActions        []string       `json:"controlActions"`
	TypedScripts          []string       `json:"typedScripts"`
	RiskRules             map[string][]string `json:"riskRules"`
	ExecutionLimits       map[string]int `json:"executionLimits"`
	DeliverySemantics     string         `json:"deliverySemantics"`
	GenericShellAuthority string         `json:"genericShellAuthority"`
}

type runtimeOperationsSnapshot struct {
	Capabilities runtimeCapabilities      `json:"capabilities"`
	Bus          runtimeOperationsState   `json:"bus"`
	GatewayToken *gatewayTokenMetadata    `json:"gatewayToken,omitempty"`
}

var runtimeOperationsMu sync.Mutex

func runtimeOperationsPath() (string, error) {
	dir, err := localRuntimeStateDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, runtimeOperationsFileName), nil
}

func randomEpoch() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

func loadRuntimeOperationsState() runtimeOperationsState {
	path, err := runtimeOperationsPath()
	if err == nil {
		if state, readErr := readJSONFile[runtimeOperationsState](path); readErr == nil && strings.TrimSpace(state.BusEpoch) != "" {
			return *state
		}
	}
	epoch, _ := randomEpoch()
	return runtimeOperationsState{
		BusEpoch:        epoch,
		EpochCreatedAt:  time.Now().UTC().Format(time.RFC3339Nano),
		PermissionModel: "per-user LOCALAPPDATA; private state written 0600/0700 where supported",
	}
}

func mutateRuntimeOperationsState(fn func(*runtimeOperationsState)) runtimeOperationsState {
	runtimeOperationsMu.Lock()
	defer runtimeOperationsMu.Unlock()
	state := loadRuntimeOperationsState()
	fn(&state)
	if strings.TrimSpace(state.BusEpoch) == "" {
		state.BusEpoch, _ = randomEpoch()
		state.EpochCreatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	if state.PermissionModel == "" {
		state.PermissionModel = "per-user LOCALAPPDATA; private state written 0600/0700 where supported"
	}
	if path, err := runtimeOperationsPath(); err == nil {
		_ = writeJSONAtomic(path, state)
		_ = os.Chmod(path, 0o600)
	}
	return state
}

func readRuntimeOperationsState() runtimeOperationsState {
	runtimeOperationsMu.Lock()
	defer runtimeOperationsMu.Unlock()
	state := loadRuntimeOperationsState()
	if path, err := runtimeOperationsPath(); err == nil {
		_ = writeJSONAtomic(path, state)
		_ = os.Chmod(path, 0o600)
	}
	return state
}

func parseRateHeader(header http.Header, name string) int {
	value, _ := strconv.Atoi(strings.TrimSpace(header.Get(name)))
	return value
}

func refreshGitHubRateTelemetry(cfg Config) {
	_, token, err := githubCredential(cfg)
	if err != nil {
		mutateRuntimeOperationsState(func(state *runtimeOperationsState) {
			state.RateLimitObservedAt = time.Now().UTC().Format(time.RFC3339Nano)
			state.RateLimitError = tailCompact(sanitizeRemoteText(err.Error()), 300)
		})
		return
	}
	resp, err := githubBusRequest(token, http.MethodGet, githubAPIBase+"/rate_limit", nil)
	if err != nil {
		mutateRuntimeOperationsState(func(state *runtimeOperationsState) {
			state.RateLimitObservedAt = time.Now().UTC().Format(time.RFC3339Nano)
			state.RateLimitError = tailCompact(sanitizeRemoteText(err.Error()), 300)
		})
		return
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64*1024))
	reset, _ := strconv.ParseInt(strings.TrimSpace(resp.Header.Get("X-RateLimit-Reset")), 10, 64)
	mutateRuntimeOperationsState(func(state *runtimeOperationsState) {
		state.RateLimitLimit = parseRateHeader(resp.Header, "X-RateLimit-Limit")
		state.RateLimitRemaining = parseRateHeader(resp.Header, "X-RateLimit-Remaining")
		state.RateLimitResetUnix = reset
		state.RateLimitObservedAt = time.Now().UTC().Format(time.RFC3339Nano)
		state.RateLimitError = ""
	})
}

type retentionCandidate struct {
	path string
	mod  time.Time
	size int64
}

func directorySize(path string) int64 {
	var total int64
	_ = filepath.WalkDir(path, func(child string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		if info, infoErr := d.Info(); infoErr == nil {
			total += info.Size()
		}
		return nil
	})
	return total
}

func terminalMissionDirectory(path string) bool {
	data, err := os.ReadFile(filepath.Join(path, missionJournalFileName))
	if err != nil {
		return false
	}
	var journal missionJournal
	if json.Unmarshal(data, &journal) != nil {
		return false
	}
	switch strings.ToUpper(strings.TrimSpace(journal.State)) {
	case "DONE", "BLOCKED", "NEEDS_BRAIN":
		return true
	default:
		return false
	}
}

func housekeepMissionEvidence(now time.Time) (bytes int64, dirs, removed int) {
	root, err := missionLocalRoot()
	if err != nil {
		return 0, 0, 0
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return 0, 0, 0
	}
	candidates := make([]retentionCandidate, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(root, entry.Name())
		info, statErr := entry.Info()
		if statErr != nil {
			continue
		}
		size := directorySize(path)
		if terminalMissionDirectory(path) && now.Sub(info.ModTime()) > missionRetentionAge {
			if os.RemoveAll(path) == nil {
				removed++
				continue
			}
		}
		candidates = append(candidates, retentionCandidate{path: path, mod: info.ModTime(), size: size})
		bytes += size
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].mod.Before(candidates[j].mod) })
	for _, candidate := range candidates {
		if bytes <= missionRetentionBytes {
			break
		}
		if !terminalMissionDirectory(candidate.path) {
			continue
		}
		if os.RemoveAll(candidate.path) == nil {
			bytes -= candidate.size
			removed++
		}
	}
	dirs = len(candidates) - removed
	if dirs < 0 {
		dirs = 0
	}
	return bytes, dirs, removed
}

func housekeepScriptWorkspaces(now time.Time) int {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		return 0
	}
	root := filepath.Join(base, "FactoryNode", "workspaces")
	entries, err := os.ReadDir(root)
	if err != nil {
		return 0
	}
	removed := 0
	for _, entry := range entries {
		if !entry.IsDir() || !strings.HasPrefix(strings.ToLower(entry.Name()), "script-") {
			continue
		}
		info, statErr := entry.Info()
		if statErr == nil && now.Sub(info.ModTime()) > workspaceRetentionAge {
			if os.RemoveAll(filepath.Join(root, entry.Name())) == nil {
				removed++
			}
		}
	}
	return removed
}

func performRuntimeHousekeeping() {
	now := time.Now()
	bytes, dirs, removedMissions := housekeepMissionEvidence(now)
	removedWorkspaces := housekeepScriptWorkspaces(now)
	mutateRuntimeOperationsState(func(state *runtimeOperationsState) {
		state.LastHousekeepingAt = now.UTC().Format(time.RFC3339Nano)
		state.MissionBytes = bytes
		state.MissionDirectories = dirs
		state.RemovedMissionDirs += removedMissions
		state.RemovedWorkspaceDirs += removedWorkspaces
	})
}

func tokenMetadataPath() (string, error) {
	dir, err := gatewayLocalDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, gatewayTokenMetaFileName), nil
}

func tokenFingerprint(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:8])
}

func writePrivateTextAtomic(path, value string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".private-*")
	if err != nil {
		return err
	}
	tmp := file.Name()
	defer os.Remove(tmp)
	_ = file.Chmod(0o600)
	if _, err := file.WriteString(value); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func maintainGatewayTokenLifecycle() error {
	token, err := ensureGatewayToken()
	if err != nil {
		return err
	}
	metaPath, err := tokenMetadataPath()
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	meta, readErr := readJSONFile[gatewayTokenMetadata](metaPath)
	if readErr != nil || strings.TrimSpace(meta.CreatedAt) == "" {
		created := now
		if tokenPath, pathErr := gatewayTokenPath(); pathErr == nil {
			if info, statErr := os.Stat(tokenPath); statErr == nil {
				created = info.ModTime().UTC()
			}
		}
		fresh := gatewayTokenMetadata{CreatedAt: created.Format(time.RFC3339Nano), RotateAfter: created.Add(gatewayTokenMaxAge).Format(time.RFC3339Nano), Fingerprint: tokenFingerprint(token)}
		if err := writeJSONAtomic(metaPath, fresh); err != nil {
			return err
		}
		return os.Chmod(metaPath, 0o600)
	}
	created, parseErr := time.Parse(time.RFC3339Nano, meta.CreatedAt)
	if parseErr != nil {
		return errors.New("gateway token metadata createdAt is invalid")
	}
	if now.Sub(created) < gatewayTokenMaxAge {
		return nil
	}
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return err
	}
	newToken := hex.EncodeToString(buf)
	tokenPath, err := gatewayTokenPath()
	if err != nil {
		return err
	}
	if err := writePrivateTextAtomic(tokenPath, newToken+"\n"); err != nil {
		return err
	}
	fresh := gatewayTokenMetadata{CreatedAt: now.Format(time.RFC3339Nano), RotatedAt: now.Format(time.RFC3339Nano), RotateAfter: now.Add(gatewayTokenMaxAge).Format(time.RFC3339Nano), Fingerprint: tokenFingerprint(newToken)}
	if err := writeJSONAtomic(metaPath, fresh); err != nil {
		return err
	}
	return os.Chmod(metaPath, 0o600)
}

func currentGatewayTokenMetadata() *gatewayTokenMetadata {
	path, err := tokenMetadataPath()
	if err != nil {
		return nil
	}
	meta, err := readJSONFile[gatewayTokenMetadata](path)
	if err != nil {
		return nil
	}
	return meta
}

func currentRuntimeCapabilities() runtimeCapabilities {
	typed := make([]string, 0, len(scriptPolicy))
	for key := range scriptPolicy {
		typed = append(typed, key)
	}
	sort.Strings(typed)
	collect := func(rules []systemCommandRiskRule) []string {
		out := make([]string, 0, len(rules))
		for _, rule := range rules {
			out = append(out, rule.Name)
		}
		sort.Strings(out)
		return out
	}
	return runtimeCapabilities{
		SourceCommit:    runtimeSourceCommit(),
		ProtocolVersion: factoryBusProtocolVersion,
		PolicyVersion:   factoryRiskPolicyVersion,
		MissionKinds: []string{"cafe.ccc.scan", "projecthub.full_cycle", "projecthub.showcase", "projecthub.verify", "repo.script", "script.run", "system.command"},
		ControlActions: []string{"CANCEL", "PAUSE", "RESUME"},
		TypedScripts: typed,
		RiskRules: map[string][]string{
			"forbidden": append([]string{"indirect-execution"}, collect(forbiddenSystemCommandRules)...),
			"approval":  collect(approvalSystemCommandRules),
			"guarded":   collect(guardedSystemCommandRules),
		},
		ExecutionLimits: map[string]int{"missionObjectiveBytes": 32768, "scriptObjectiveBytes": 8192, "commandBytes": 16384, "maxTimeoutSeconds": 1800, "missionRetentionDays": 30, "missionRetentionMiB": 512, "scriptWorkspaceRetentionHours": 24},
		DeliverySemantics: "durable at-least-once outbound publication with stable eventId/sequence/attempt and reconciliation",
		GenericShellAuthority: "local-HMAC privileged only; routine automation uses versioned typed scripts",
	}
}

func currentRuntimeOperationsSnapshot() runtimeOperationsSnapshot {
	return runtimeOperationsSnapshot{Capabilities: currentRuntimeCapabilities(), Bus: readRuntimeOperationsState(), GatewayToken: currentGatewayTokenMetadata()}
}

// Extends the authenticated /api/runtime/status endpoint into the canonical
// capability-discovery surface without exposing bearer material. Public health
// remains deliberately minimal.
func (s publicRuntimeStatus) MarshalJSON() ([]byte, error) {
	type alias publicRuntimeStatus
	return json.Marshal(struct {
		alias
		Operations runtimeOperationsSnapshot `json:"operations"`
	}{alias: alias(s), Operations: currentRuntimeOperationsSnapshot()})
}

func runRuntimeOperationsService(cfg Config) {
	mutateRuntimeOperationsState(func(*runtimeOperationsState) {})
	performRuntimeHousekeeping()
	refreshGitHubRateTelemetry(cfg)
	housekeepingTicker := time.NewTicker(time.Hour)
	telemetryTicker := time.NewTicker(5 * time.Minute)
	defer housekeepingTicker.Stop()
	defer telemetryTicker.Stop()
	for {
		select {
		case <-housekeepingTicker.C:
			performRuntimeHousekeeping()
		case <-telemetryTicker.C:
			refreshGitHubRateTelemetry(cfg)
		}
	}
}

func init() {
	mode, err := selectedMode(os.Args[1:])
	if err != nil {
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
	switch mode {
	case "executor":
		go runRuntimeOperationsService(cfg)
	case "supervisor":
		_ = maintainGatewayTokenLifecycle()
	}
}
