package main

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const gatewayListenAddress = "127.0.0.1:8787"

type publicMissionCheckpoint struct {
	RuntimeIdentity
	MissionID     string `json:"missionId,omitempty"`
	Kind          string `json:"kind,omitempty"`
	State         string `json:"state"`
	StartedAt     string `json:"startedAt,omitempty"`
	FinishedAt    string `json:"finishedAt,omitempty"`
	DurationMs    int64  `json:"durationMs,omitempty"`
	Commit        string `json:"commit,omitempty"`
	BridgeVersion string `json:"bridgeVersion"`
}

type publicRuntimeStatus struct {
	RuntimeIdentity
	OK                bool                     `json:"ok"`
	BridgeVersion     string                   `json:"bridgeVersion"`
	ExecutorOnline    bool                     `json:"executorOnline"`
	ExecutorPID       int                      `json:"executorPid,omitempty"`
	ExecutorHeartbeat string                   `json:"executorHeartbeat,omitempty"`
	AttentionRequired bool                     `json:"attentionRequired"`
	Checkpoint        *publicMissionCheckpoint `json:"checkpoint,omitempty"`
	ObservedAt        string                   `json:"observedAt"`
}

type publicShowcaseStatus struct {
	Available bool   `json:"available"`
	Build     string `json:"build,omitempty"`
	Commit    string `json:"commit,omitempty"`
	BuiltAt   string `json:"builtAt,omitempty"`
}

func gatewayLocalDir() (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	dir := filepath.Join(base, "FactoryBridge", "gateway")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	return dir, nil
}

func gatewayTokenPath() (string, error) {
	dir, err := gatewayLocalDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "gateway.token"), nil
}

func ensureGatewayToken() (string, error) {
	path, err := gatewayTokenPath()
	if err != nil {
		return "", err
	}
	if data, readErr := os.ReadFile(path); readErr == nil {
		token := strings.TrimSpace(string(data))
		if len(token) >= 32 {
			return token, nil
		}
	}
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	token := hex.EncodeToString(buf)
	if err := os.WriteFile(path, []byte(token+"\n"), 0o600); err != nil {
		return "", err
	}
	return token, nil
}

func sanitizeCheckpoint(cp MissionCheckpoint) publicMissionCheckpoint {
	return publicMissionCheckpoint{
		RuntimeIdentity: cp.RuntimeIdentity,
		MissionID:       cp.MissionID,
		Kind:            cp.Kind,
		State:           cp.State,
		StartedAt:       cp.StartedAt,
		FinishedAt:      cp.FinishedAt,
		DurationMs:      cp.DurationMs,
		Commit:          cp.Commit,
		BridgeVersion:   cp.BridgeVersion,
	}
}

func localCheckpointFallback() (*MissionCheckpoint, error) {
	root, err := missionLocalRoot()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	type candidate struct {
		path string
		mod  time.Time
	}
	candidates := []candidate{}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(root, entry.Name(), "evidence.json")
		info, statErr := os.Stat(path)
		if statErr == nil {
			candidates = append(candidates, candidate{path: path, mod: info.ModTime()})
		}
	}
	if len(candidates) == 0 {
		return nil, os.ErrNotExist
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].mod.After(candidates[j].mod) })
	data, err := os.ReadFile(candidates[0].path)
	if err != nil {
		return nil, err
	}
	var evidence missionEvidence
	if err := json.Unmarshal(data, &evidence); err != nil {
		return nil, err
	}
	return &evidence.Checkpoint, nil
}

func latestMissionCheckpoint(cfg Config) (*MissionCheckpoint, error) {
	path := filepath.Join(cfg.BridgeRoot, brainDirName, "CURRENT_STATE.json")
	if cp, err := readJSONFile[MissionCheckpoint](path); err == nil && strings.TrimSpace(cp.State) != "" {
		return cp, nil
	}
	return localCheckpointFallback()
}

func localExecutorSnapshot(now time.Time) (ExecutorStatus, bool) {
	path, err := localRuntimeStatusPath(executorStatusFileName)
	if err != nil {
		return ExecutorStatus{}, false
	}
	state, err := readJSONFile[ExecutorStatus](path)
	if err != nil {
		return ExecutorStatus{}, false
	}
	return *state, heartbeatState(now, state.HeartbeatAt, executorOnlineThreshold, executorStaleThreshold) == "ONLINE"
}

func runtimePublicStatus(cfg Config) publicRuntimeStatus {
	now := time.Now()
	executor, online := localExecutorSnapshot(now)
	status := publicRuntimeStatus{
		RuntimeIdentity:   currentRuntimeIdentity(),
		OK:                online,
		BridgeVersion:     bridgeVersion,
		ExecutorOnline:    online,
		ExecutorPID:       executor.PID,
		ExecutorHeartbeat: executor.HeartbeatAt,
		ObservedAt:        now.Format(time.RFC3339Nano),
	}
	if cp, err := latestMissionCheckpoint(cfg); err == nil {
		public := sanitizeCheckpoint(*cp)
		status.Checkpoint = &public
		status.AttentionRequired = cp.State == "NEEDS_BRAIN" || cp.State == "BLOCKED"
	}
	return status
}

func latestShowcaseStatus() publicShowcaseStatus {
	root, err := projectHubShowcaseRoot()
	if err != nil {
		return publicShowcaseStatus{}
	}
	latest, err := os.ReadFile(filepath.Join(root, "LATEST.txt"))
	if err != nil {
		return publicShowcaseStatus{}
	}
	buildPath := strings.TrimSpace(string(latest))
	if buildPath == "" {
		return publicShowcaseStatus{}
	}
	status := publicShowcaseStatus{Available: true, Build: filepath.Base(buildPath)}
	version, err := os.ReadFile(filepath.Join(buildPath, "VERSION.txt"))
	if err != nil {
		return status
	}
	for _, line := range strings.Split(string(version), "\n") {
		key, value, ok := strings.Cut(strings.TrimSpace(line), "=")
		if !ok {
			continue
		}
		switch key {
		case "commit":
			status.Commit = value
		case "builtAt":
			status.BuiltAt = value
		}
	}
	return status
}

func writeGatewayJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func bearerAuthorized(r *http.Request, token string) bool {
	provided := strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
	if provided == "" || len(provided) != len(token) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(provided), []byte(token)) == 1
}

func validateMission(m Mission) error {
	return validateMissionIntegrity(m)
}

func submitMission(cfg Config, mission Mission) error {
	if err := validateMission(mission); err != nil {
		return err
	}
	if mission.CreatedAt == "" {
		mission.CreatedAt = time.Now().Format(time.RFC3339)
	}
	if err := ensureMissionMailbox(cfg); err != nil {
		return err
	}
	path := filepath.Join(cfg.BridgeRoot, missionInboxDirName, "MISSION__"+mission.ID+".json")
	if _, err := os.Stat(path); err == nil {
		return errors.New("mission already exists in inbox")
	}
	if root, err := missionLocalRoot(); err == nil {
		if _, statErr := os.Stat(filepath.Join(root, mission.ID)); statErr == nil {
			return errors.New("mission id was already used")
		}
	}
	return writeJSONAtomic(path, mission)
}

func gatewayMux(cfg Config, token string) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /public/health", func(w http.ResponseWriter, r *http.Request) {
		status := runtimePublicStatus(cfg)
		writeGatewayJSON(w, http.StatusOK, map[string]any{
			"ok":              status.OK,
			"sourceCommit":    status.SourceCommit,
			"binarySha256":    status.BinarySHA256,
			"protocolVersion": status.ProtocolVersion,
			"policyVersion":   status.PolicyVersion,
			"bridgeVersion":   status.BridgeVersion,
			"executorOnline":  status.ExecutorOnline,
			"observedAt":      status.ObservedAt,
		})
	})
	mux.HandleFunc("GET /public/attention", func(w http.ResponseWriter, r *http.Request) {
		status := runtimePublicStatus(cfg)
		body := map[string]any{
			"attentionRequired": status.AttentionRequired,
			"executorOnline":    status.ExecutorOnline,
			"observedAt":        status.ObservedAt,
		}
		if status.Checkpoint != nil {
			body["missionId"] = status.Checkpoint.MissionID
			body["state"] = status.Checkpoint.State
		}
		writeGatewayJSON(w, http.StatusOK, body)
	})
	mux.HandleFunc("GET /public/checkpoint", func(w http.ResponseWriter, r *http.Request) {
		cp, err := latestMissionCheckpoint(cfg)
		if err != nil {
			writeGatewayJSON(w, http.StatusNotFound, map[string]any{"error": "checkpoint unavailable"})
			return
		}
		writeGatewayJSON(w, http.StatusOK, sanitizeCheckpoint(*cp))
	})
	mux.HandleFunc("GET /public/showcase", func(w http.ResponseWriter, r *http.Request) {
		writeGatewayJSON(w, http.StatusOK, latestShowcaseStatus())
	})

	private := func(next http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if !bearerAuthorized(r, token) {
				writeGatewayJSON(w, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
				return
			}
			next(w, r)
		}
	}
	mux.HandleFunc("GET /api/runtime/status", private(func(w http.ResponseWriter, r *http.Request) {
		writeGatewayJSON(w, http.StatusOK, runtimePublicStatus(cfg))
	}))
	mux.HandleFunc("POST /api/missions", private(func(w http.ResponseWriter, r *http.Request) {
		dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32*1024))
		dec.DisallowUnknownFields()
		var mission Mission
		if err := dec.Decode(&mission); err != nil {
			writeGatewayJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid mission payload"})
			return
		}
		if err := submitMission(cfg, mission); err != nil {
			writeGatewayJSON(w, http.StatusConflict, map[string]any{"error": err.Error()})
			return
		}
		writeGatewayJSON(w, http.StatusAccepted, map[string]any{"accepted": true, "missionId": mission.ID})
	}))

	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		status := runtimePublicStatus(cfg)
		showcase := latestShowcaseStatus()
		tpl := template.Must(template.New("dashboard").Parse(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FactoryBridge Gateway</title><style>body{font-family:system-ui;margin:2rem;max-width:900px}code,pre{background:#f4f4f4;padding:.25rem .4rem;border-radius:4px}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}.ok{font-weight:700}</style></head><body><h1>FactoryBridge Gateway</h1><p class="ok">Executor: {{if .Status.ExecutorOnline}}ONLINE{{else}}OFFLINE{{end}}</p><table><tr><th>Bridge</th><td>{{.Status.BridgeVersion}}</td></tr><tr><th>Mission</th><td>{{if .Status.Checkpoint}}{{.Status.Checkpoint.MissionID}} — {{.Status.Checkpoint.State}}{{else}}-{{end}}</td></tr><tr><th>Attention</th><td>{{.Status.AttentionRequired}}</td></tr><tr><th>Latest showcase</th><td>{{if .Showcase.Available}}{{.Showcase.Build}}{{else}}-{{end}}</td></tr></table><p>Read-only endpoints: <a href="/public/health">health</a>, <a href="/public/attention">attention</a>, <a href="/public/checkpoint">checkpoint</a>, <a href="/public/showcase">showcase</a>.</p></body></html>`))
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
		_ = tpl.Execute(w, map[string]any{"Status": status, "Showcase": showcase})
	})
	return mux
}

func runGateway(cfg Config) error {
	token, err := ensureGatewayToken()
	if err != nil {
		return err
	}
	server := &http.Server{
		Addr:              gatewayListenAddress,
		Handler:           gatewayMux(cfg, token),
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	fmt.Printf("FactoryBridge gateway listening on http://%s\n", gatewayListenAddress)
	return server.ListenAndServe()
}

func init() {
	mode, err := selectedMode(os.Args[1:])
	if err != nil || mode != "supervisor" {
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
	go func() {
		if err := runGateway(cfg); err != nil && !errors.Is(err, http.ErrServerClosed) {
			fmt.Fprintf(os.Stderr, "gateway error: %v\n", err)
		}
	}()
}
