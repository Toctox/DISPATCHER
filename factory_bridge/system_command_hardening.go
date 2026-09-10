package main

import "regexp"

// Generic system.command is intentionally not a script launcher. Repository
// scripts must move through the typed exact-commit execution path so the policy
// boundary can reason about immutable code instead of a wrapper shell string.
func init() {
	scriptRules := []systemCommandRiskRule{
		{Name: "indirect-script-execution", Reason: "generic system.command cannot invoke script files; use typed exact-commit script execution", Pattern: regexp.MustCompile(`(?i)(powershell(?:\.exe)?\b[^\r\n;&|]*\s-file\b|pwsh(?:\.exe)?\b[^\r\n;&|]*\s-file\b|(^|[;&|]\s*)[^\s'\"]+\.(ps1|cmd|bat)(?:[\s'\"]|$)|\bcall\s+[^\r\n;&|]*\.(cmd|bat)\b)`)},
	}
	forbiddenSystemCommandRules = append(scriptRules, forbiddenSystemCommandRules...)
}
