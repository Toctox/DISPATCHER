package main

import (
	"fmt"
	"regexp"
	"strings"
)

type systemCommandDiagnosis struct {
	Phase      string
	Class      string
	Confidence string
	Evidence   string
	Next       string
}

var (
	phaseMarkerPattern = regexp.MustCompile(`(?i)PHASE=([A-Za-z0-9_.-]+)`)
	secretValuePattern = regexp.MustCompile(`(?i)(password|pgpassword|token|secret|authorization)\s*[=:]\s*([^;\s\r\n]+)`)
	connectionPasswordPattern = regexp.MustCompile(`(?i)(Password\s*=\s*)[^;\r\n]+`)
	compileCodePattern = regexp.MustCompile(`(?i)\b(CS\d{4}|xUnit\d{4})\b`)
)

func systemCommandFailureSummary(res Result) string {
	d := diagnoseSystemCommandFailure(res)
	code := -1
	if res.ExitCode != nil {
		code = *res.ExitCode
	}
	return compact(fmt.Sprintf("phase=%s class=%s confidence=%s exitCode=%d error=%s evidence=%s next=%s",
		d.Phase, d.Class, d.Confidence, code, redactDiagnosticText(res.Error), d.Evidence, d.Next), 1800)
}

func diagnoseSystemCommandFailure(res Result) systemCommandDiagnosis {
	raw := strings.Join([]string{res.Error, res.Stderr, res.Stdout}, "\n")
	safe := redactDiagnosticText(raw)
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
	case strings.Contains(lower, "timed out") || strings.Contains(lower, "timeout"):
		class, confidence, next = "timeout", "high", "identify the active phase and retry only after checking whether the operation is making progress"
	case strings.Contains(lower, "is not recognized") || strings.Contains(lower, "command not found") || strings.Contains(lower, "file not found") || strings.Contains(lower, "runtime_metadata_missing"):
		class, confidence, next = "environment_missing", "high", "restore the missing executable, file or runtime prerequisite"
	case strings.Contains(lower, "fatal:") || strings.Contains(lower, "git_") || strings.Contains(lower, "repository") && strings.Contains(lower, "not found"):
		class, confidence, next = "git_state", "medium", "verify repository path, ref and authorized commit"
	case strings.Contains(lower, "access is denied") || strings.Contains(lower, "permission denied") || strings.Contains(lower, "requires explicit riskapproval"):
		class, confidence, next = "permission_or_policy", "high", "review the policy boundary and use explicit approval only when the intended operation requires it"
	}

	return systemCommandDiagnosis{
		Phase: phase,
		Class: class,
		Confidence: confidence,
		Evidence: diagnosticEvidence(safe),
		Next: next,
	}
}

func redactDiagnosticText(s string) string {
	if strings.TrimSpace(s) == "" {
		return ""
	}
	s = connectionPasswordPattern.ReplaceAllString(s, `${1}[REDACTED]`)
	s = secretValuePattern.ReplaceAllString(s, `${1}=[REDACTED]`)
	return s
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
	if len(critical) > 4 {
		critical = critical[len(critical)-4:]
	}
	if len(critical) == 0 {
		for i := len(lines) - 1; i >= 0 && len(critical) < 3; i-- {
			if t := strings.TrimSpace(lines[i]); t != "" {
				critical = append([]string{t}, critical...)
			}
		}
	}
	return compact(strings.Join(critical, " | "), 900)
}
