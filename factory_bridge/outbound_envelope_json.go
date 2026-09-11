package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type githubBusEnvelopeAlias githubBusEnvelope

type githubBusEnvelopeWire struct {
	githubBusEnvelopeAlias
	Diagnostic *FailureDiagnostic `json:"diagnostic,omitempty"`
}

// MarshalJSON is the canonical remote-publication boundary for Factory Bus
// envelopes. It sanitizes human text and derives failure diagnostics from the
// durable local evidence before the envelope is hashed, persisted or posted.
func (e githubBusEnvelope) MarshalJSON() ([]byte, error) {
	safe := e
	safe.Summary = remoteCheckpointSummary(safe.Summary)
	wire := githubBusEnvelopeWire{
		githubBusEnvelopeAlias: githubBusEnvelopeAlias(safe),
		Diagnostic:             sanitizeFailureDiagnostic(outboundDiagnosticForEnvelope(safe)),
	}
	return json.Marshal(wire)
}

// UnmarshalJSON keeps strict unknown-field rejection while accepting the
// diagnostic field emitted on terminal CHECKPOINTs. Diagnostics are output
// evidence, never an input authority field, so they are rejected on missions
// and controls and intentionally discarded after parsing.
func (e *githubBusEnvelope) UnmarshalJSON(data []byte) error {
	var wire githubBusEnvelopeWire
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&wire); err != nil {
		return err
	}
	decoded := githubBusEnvelope(wire.githubBusEnvelopeAlias)
	if wire.Diagnostic != nil {
		t := strings.ToUpper(strings.TrimSpace(decoded.Type))
		if t != "CHECKPOINT" {
			return errors.New("diagnostic is output-only and only valid on CHECKPOINT envelopes")
		}
	}
	*e = decoded
	return nil
}

func outboundDiagnosticForEnvelope(e githubBusEnvelope) *FailureDiagnostic {
	if !strings.EqualFold(strings.TrimSpace(e.Type), "CHECKPOINT") {
		return nil
	}
	state := strings.ToUpper(strings.TrimSpace(e.State))
	if state == "" || state == "DONE" || state == "ACCEPTED" || state == "ALREADY_RESERVED" {
		return nil
	}
	if result, ok := failureResultForMission(e.ID); ok {
		started := time.Now()
		if parsed, err := time.Parse(time.RFC3339, result.StartedAt); err == nil {
			started = parsed
		}
		return buildFailureDiagnostic(result, resultWorkingDir(result), started)
	}
	if strings.TrimSpace(e.Summary) == "" {
		return nil
	}
	return buildFailureDiagnostic(Result{Status: "failed", Error: e.Summary}, "", time.Now())
}

func resultWorkingDir(result Result) string {
	if result.Meta == nil {
		return ""
	}
	if value, ok := result.Meta["workingDir"].(string); ok {
		return strings.TrimSpace(value)
	}
	return ""
}

func failureResultForMission(id string) (Result, bool) {
	if !idPattern.MatchString(strings.TrimSpace(id)) {
		return Result{}, false
	}
	root, err := missionLocalRoot()
	if err != nil {
		return Result{}, false
	}
	data, err := os.ReadFile(filepath.Join(root, id, "evidence.json"))
	if err != nil {
		return Result{}, false
	}
	var evidence missionEvidence
	if json.Unmarshal(data, &evidence) != nil {
		return Result{}, false
	}
	if result, ok := evidence.Results["system-command"]; ok && result.Status != "ok" {
		return result, true
	}
	for _, key := range []string{"verify", "showcase", "sync", "cafe-ccc-scan"} {
		if result, ok := evidence.Results[key]; ok && result.Status != "ok" {
			return result, true
		}
	}
	for _, result := range evidence.Results {
		if result.Status != "ok" {
			return result, true
		}
	}
	return Result{}, false
}

// Mission steps are compact navigation evidence. The full Result remains in
// evidence.Results; the step view is tail-preserving and sanitized so the
// critical final cause is never lost to prefix truncation.
func (s MissionStepEvidence) MarshalJSON() ([]byte, error) {
	type stepWire struct {
		Name       string `json:"name"`
		Status     string `json:"status"`
		DurationMs int64  `json:"durationMs"`
		Output     string `json:"output,omitempty"`
		Error      string `json:"error,omitempty"`
	}
	return json.Marshal(stepWire{
		Name:       s.Name,
		Status:     s.Status,
		DurationMs: s.DurationMs,
		Output:     tailCompact(sanitizeRemoteText(s.Output), 600),
		Error:      tailCompact(sanitizeRemoteText(s.Error), 600),
	})
}
