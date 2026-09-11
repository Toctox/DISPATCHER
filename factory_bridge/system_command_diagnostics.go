package main

import (
	"encoding/xml"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type systemCommandDiagnosis struct {
	Phase      string
	Class      string
	Confidence string
	Evidence   string
	Next       string
}

type trxDiagnosticSummary struct {
	Total       int      `json:"total"`
	Passed      int      `json:"passed"`
	Failed      int      `json:"failed"`
	Skipped     int      `json:"skipped"`
	FailedTests []string `json:"failedTests,omitempty"`
}

type FailureDiagnostic struct {
	Phase                 string                `json:"phase"`
	ExitCode              *int                  `json:"exitCode,omitempty"`
	Error                 string                `json:"error"`
	Class                 string                `json:"class"`
	Confidence            string                `json:"confidence"`
	Evidence              string                `json:"evidence,omitempty"`
	StderrTail            string                `json:"stderrTail,omitempty"`
	StdoutTail            string                `json:"stdoutTail,omitempty"`
	RecommendedNextAction string                `json:"recommendedNextAction"`
	TRX                   *trxDiagnosticSummary `json:"trx,omitempty"`
}

var (
	phaseMarkerPattern        = regexp.MustCompile(`(?i)PHASE=([A-Za-z0-9_.-]+)`)
	secretValuePattern        = regexp.MustCompile(`(?i)(password|pgpassword|token|secret|authorization|api[_-]?key|access[_-]?key)\s*[=:]\s*([^;\s\r\n]+)`)
	connectionPasswordPattern = regexp.MustCompile(`(?i)(Password\s*=\s*)[^;\r\n]+`)
	bearerPattern             = regexp.MustCompile(`(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*`)
	compileCodePattern        = regexp.MustCompile(`(?i)\b(CS\d{4}|xUnit\d{4})\b`)
)

func sanitizeRemoteText(s string) string {
	if strings.TrimSpace(s) == "" {
		return ""
	}
	s = bearerPattern.ReplaceAllString(s, `${1}[REDACTED]`)
	s = connectionPasswordPattern.ReplaceAllString(s, `${1}[REDACTED]`)
	s = secretValuePattern.ReplaceAllString(s, `${1}=[REDACTED]`)
	return s
}

func tailCompact(value string, max int) string {
	value = strings.TrimSpace(value)
	if max <= 0 || len(value) <= max {
		return value
	}
	marker := "...[truncated; tail preserved]...\n"
	keep := max - len(marker)
	if keep <= 0 {
		return value[len(value)-max:]
	}
	return marker + value[len(value)-keep:]
}

func remoteCheckpointSummary(value string) string {
	return tailCompact(sanitizeRemoteText(value), 4096)
}

func sanitizeFailureDiagnostic(in *FailureDiagnostic) *FailureDiagnostic {
	if in == nil {
		return nil
	}
	out := *in
	out.Phase = tailCompact(sanitizeRemoteText(out.Phase), 128)
	out.Error = tailCompact(sanitizeRemoteText(out.Error), 1200)
	out.Class = tailCompact(sanitizeRemoteText(out.Class), 128)
	out.Confidence = tailCompact(sanitizeRemoteText(out.Confidence), 32)
	out.Evidence = tailCompact(sanitizeRemoteText(out.Evidence), 1800)
	out.StderrTail = tailCompact(sanitizeRemoteText(out.StderrTail), 1800)
	out.StdoutTail = tailCompact(sanitizeRemoteText(out.StdoutTail), 1800)
	out.RecommendedNextAction = tailCompact(sanitizeRemoteText(out.RecommendedNextAction), 800)
	if in.TRX != nil {
		trx := *in.TRX
		trx.FailedTests = append([]string(nil), in.TRX.FailedTests...)
		for i := range trx.FailedTests {
			trx.FailedTests[i] = tailCompact(sanitizeRemoteText(trx.FailedTests[i]), 240)
		}
		if len(trx.FailedTests) > 5 {
			trx.FailedTests = trx.FailedTests[:5]
		}
		out.TRX = &trx
	}
	return &out
}

func systemCommandFailureSummary(res Result) string {
	d := diagnoseSystemCommandFailure(res)
	code := -1
	if res.ExitCode != nil {
		code = *res.ExitCode
	}
	return remoteCheckpointSummary(fmt.Sprintf("phase=%s class=%s confidence=%s exitCode=%d error=%s evidence=%s next=%s",
		d.Phase, d.Class, d.Confidence, code, sanitizeRemoteText(res.Error), d.Evidence, d.Next))
}

func buildFailureDiagnostic(res Result, workingDir string, started time.Time) *FailureDiagnostic {
	d := diagnoseSystemCommandFailure(res)
	errText := strings.TrimSpace(res.Error)
	if errText == "" {
		errText = "command failed without an explicit error string"
	}
	out := &FailureDiagnostic{
		Phase:                 d.Phase,
		ExitCode:              res.ExitCode,
		Error:                 errText,
		Class:                 d.Class,
		Confidence:            d.Confidence,
		Evidence:              d.Evidence,
		StderrTail:            tailCompact(res.Stderr, 1800),
		StdoutTail:            tailCompact(res.Stdout, 1800),
		RecommendedNextAction: d.Next,
	}
	if strings.TrimSpace(workingDir) != "" {
		out.TRX = newestTRXDiagnostic(workingDir, started)
	}
	return sanitizeFailureDiagnostic(out)
}

func diagnoseSystemCommandFailure(res Result) systemCommandDiagnosis {
	raw := strings.Join([]string{res.Error, res.Stderr, res.Stdout, res.Output}, "\n")
	safe := sanitizeRemoteText(raw)
	lower := strings.ToLower(safe)
	phase := "unknown"
	matches := phaseMarkerPattern.FindAllStringSubmatch(safe, -1)
	if len(matches) > 0 {
		phase = strings.ToLower(matches[len(matches)-1][1])
	}

	class, confidence, next := "unknown", "low", "inspect sanitized local evidence"
	switch {
	case strings.Contains(lower, "payloadhash") && strings.Contains(lower, "match"):
		class, confidence, next = "integrity_payload_hash", "high", "regenerate the canonical mission hash from the exact posted payload"
	case strings.Contains(lower, "nu1004") || strings.Contains(lower, "packages.lock.json") || strings.Contains(lower, "locked mode"):
		class, confidence, next = "dependency_lock", "high", "refresh the package lock deterministically, then rerun locked restore"
	case compileCodePattern.MatchString(safe) || strings.Contains(lower, "build failed"):
		class, confidence, next = "compile", "high", "fix the first compiler or analyzer error, then rerun build"
	case strings.Contains(lower, "xunitexception") || strings.Contains(lower, "assert.") || strings.Contains(lower, "test run failed") || strings.Contains(lower, "failed!"):
		class, confidence, next = "test_assertion", "high", "inspect the first failing test and its TRX evidence"
	case strings.Contains(lower, "connection refused") || strings.Contains(lower, "no connection could be made") || strings.Contains(lower, "npgsqlexception") && strings.Contains(lower, "connect"):
		class, confidence, next = "database_connectivity", "high", "check pg_isready, host, port and runtime metadata"
	case strings.Contains(lower, "postgresexception") || strings.Contains(lower, "relation") && strings.Contains(lower, "does not exist") || strings.Contains(lower, "sqlstate"):
		class, confidence, next = "database_schema_or_sql", "high", "inspect the reported SQLSTATE and schema operation"
	case strings.Contains(lower, "timed out") || strings.Contains(lower, "timeout") || strings.Contains(lower, "deadline exceeded"):
		class, confidence, next = "timeout", "high", "identify the active phase and verify process-tree cancellation before retrying"
	case strings.Contains(lower, "is not recognized") || strings.Contains(lower, "command not found") || strings.Contains(lower, "file not found") || strings.Contains(lower, "runtime_metadata_missing"):
		class, confidence, next = "environment_missing", "high", "restore the missing executable, file or runtime prerequisite"
	case strings.Contains(lower, "fatal:") || strings.Contains(lower, "git_") || strings.Contains(lower, "repository") && strings.Contains(lower, "not found"):
		class, confidence, next = "git_state", "medium", "verify repository path, ref and authorized commit"
	case strings.Contains(lower, "access is denied") || strings.Contains(lower, "permission denied") || strings.Contains(lower, "requires explicit riskapproval") || strings.Contains(lower, "blocked") && strings.Contains(lower, "policy"):
		class, confidence, next = "permission_or_policy", "high", "review the policy boundary and use explicit approval only when required"
	}

	return systemCommandDiagnosis{Phase: phase, Class: class, Confidence: confidence, Evidence: diagnosticEvidence(safe), Next: next}
}

func diagnosticEvidence(s string) string {
	lines := strings.Split(strings.ReplaceAll(s, "\r\n", "\n"), "\n")
	critical := make([]string, 0, 8)
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		l := strings.ToLower(trimmed)
		if strings.Contains(l, "error") || strings.Contains(l, "failed") || strings.Contains(l, "exception") || strings.Contains(l, "phase=") || compileCodePattern.MatchString(trimmed) || strings.Contains(l, "nu1004") || strings.Contains(l, "sqlstate") {
			critical = append(critical, trimmed)
		}
	}
	if len(critical) > 6 {
		critical = critical[len(critical)-6:]
	}
	if len(critical) == 0 {
		for i := len(lines) - 1; i >= 0 && len(critical) < 4; i-- {
			if t := strings.TrimSpace(lines[i]); t != "" {
				critical = append([]string{t}, critical...)
			}
		}
	}
	return tailCompact(strings.Join(critical, " | "), 1600)
}

type trxDocument struct {
	ResultSummary struct {
		Counters struct {
			Total       string `xml:"total,attr"`
			Executed    string `xml:"executed,attr"`
			Passed      string `xml:"passed,attr"`
			Failed      string `xml:"failed,attr"`
			NotExecuted string `xml:"notExecuted,attr"`
		} `xml:"Counters"`
	} `xml:"ResultSummary"`
	Results struct {
		UnitTestResults []struct {
			TestName string `xml:"testName,attr"`
			Outcome  string `xml:"outcome,attr"`
		} `xml:"UnitTestResult"`
	} `xml:"Results"`
}

func parseTRXDiagnostic(path string) *trxDiagnosticSummary {
	data, err := os.ReadFile(path)
	if err != nil || len(data) > 16*1024*1024 {
		return nil
	}
	var doc trxDocument
	if xml.Unmarshal(data, &doc) != nil {
		return nil
	}
	atoi := func(v string) int { n, _ := strconv.Atoi(strings.TrimSpace(v)); return n }
	out := &trxDiagnosticSummary{
		Total:   atoi(doc.ResultSummary.Counters.Total),
		Passed:  atoi(doc.ResultSummary.Counters.Passed),
		Failed:  atoi(doc.ResultSummary.Counters.Failed),
		Skipped: atoi(doc.ResultSummary.Counters.NotExecuted),
	}
	if out.Skipped == 0 {
		executed := atoi(doc.ResultSummary.Counters.Executed)
		if out.Total > executed {
			out.Skipped = out.Total - executed
		}
	}
	for _, result := range doc.Results.UnitTestResults {
		if strings.EqualFold(result.Outcome, "Failed") && strings.TrimSpace(result.TestName) != "" {
			out.FailedTests = append(out.FailedTests, result.TestName)
			if len(out.FailedTests) == 5 {
				break
			}
		}
	}
	return out
}

func newestTRXDiagnostic(root string, started time.Time) *trxDiagnosticSummary {
	root = strings.TrimSpace(root)
	if root == "" {
		return nil
	}
	var newestPath string
	var newestTime time.Time
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			name := strings.ToLower(d.Name())
			if name == ".git" || name == "node_modules" || name == "bin" || name == "obj" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.EqualFold(filepath.Ext(path), ".trx") {
			return nil
		}
		info, err := d.Info()
		if err != nil || info.ModTime().Before(started.Add(-5*time.Second)) {
			return nil
		}
		if newestPath == "" || info.ModTime().After(newestTime) {
			newestPath, newestTime = path, info.ModTime()
		}
		return nil
	})
	if newestPath == "" {
		return nil
	}
	return parseTRXDiagnostic(newestPath)
}
