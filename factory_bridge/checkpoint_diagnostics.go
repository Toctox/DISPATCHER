package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type remoteCheckpointDiagnostics struct {
	Phase                 string `json:"phase,omitempty"`
	ExitCode              *int   `json:"exitCode,omitempty"`
	Error                 string `json:"error,omitempty"`
	StdoutTail            string `json:"stdoutTail,omitempty"`
	StderrTail            string `json:"stderrTail,omitempty"`
	FailureClass          string `json:"failureClass,omitempty"`
	Confidence            string `json:"confidence,omitempty"`
	Evidence              string `json:"evidence,omitempty"`
	RecommendedNextAction string `json:"recommendedNextAction,omitempty"`
	TRXSummary            string `json:"trxSummary,omitempty"`
}

func sanitizeRemoteDiagnostics(d remoteCheckpointDiagnostics) remoteCheckpointDiagnostics {
	d.Error = tailCompact(sanitizeRemoteText(d.Error), 1200)
	d.StdoutTail = tailCompact(sanitizeRemoteText(d.StdoutTail), 1600)
	d.StderrTail = tailCompact(sanitizeRemoteText(d.StderrTail), 1600)
	d.Evidence = tailCompact(sanitizeRemoteText(d.Evidence), 1600)
	d.RecommendedNextAction = tailCompact(sanitizeRemoteText(d.RecommendedNextAction), 800)
	d.TRXSummary = tailCompact(sanitizeRemoteText(d.TRXSummary), 1600)
	return d
}

func checkpointDiagnostics(cp MissionCheckpoint) remoteCheckpointDiagnostics {
	if strings.EqualFold(cp.State, "DONE") {
		return remoteCheckpointDiagnostics{}
	}

	result, ok := checkpointFailureResult(cp.MissionID)
	if !ok {
		if strings.TrimSpace(cp.Summary) == "" {
			return remoteCheckpointDiagnostics{}
		}
		res := Result{Status: "failed", Error: cp.Summary}
		d := diagnoseSystemCommandFailure(res)
		return sanitizeRemoteDiagnostics(remoteCheckpointDiagnostics{
			Phase:                 d.Phase,
			Error:                 cp.Summary,
			FailureClass:          d.Class,
			Confidence:            d.Confidence,
			Evidence:              d.Evidence,
			RecommendedNextAction: d.Next,
		})
	}

	d := diagnoseSystemCommandFailure(result)
	remote := remoteCheckpointDiagnostics{
		Phase:                 d.Phase,
		ExitCode:              result.ExitCode,
		Error:                 result.Error,
		StdoutTail:            result.Stdout,
		StderrTail:            result.Stderr,
		FailureClass:          d.Class,
		Confidence:            d.Confidence,
		Evidence:              d.Evidence,
		RecommendedNextAction: d.Next,
	}
	if summary, ok := result.Meta["trxSummary"].(string); ok {
		remote.TRXSummary = summary
	}
	return sanitizeRemoteDiagnostics(remote)
}

func checkpointFailureResult(missionID string) (Result, bool) {
	if !idPattern.MatchString(missionID) {
		return Result{}, false
	}
	root, err := missionLocalRoot()
	if err != nil {
		return Result{}, false
	}
	path := filepath.Join(root, missionID, "evidence.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return Result{}, false
	}
	var evidence missionEvidence
	if json.Unmarshal(data, &evidence) != nil || len(evidence.Results) == 0 {
		return Result{}, false
	}
	keys := make([]string, 0, len(evidence.Results))
	for key := range evidence.Results {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for i := len(keys) - 1; i >= 0; i-- {
		result := evidence.Results[keys[i]]
		if result.Status != "ok" {
			return result, true
		}
	}
	return Result{}, false
}
