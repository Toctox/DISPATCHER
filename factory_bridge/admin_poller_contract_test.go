package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestAdminPollerSequentialContract(t *testing.T) {
	path := filepath.Join("..", "scripts", "factory-bridge-admin-poller.ps1")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	script := string(data)
	for _, required := range []string{
		"lastProcessedApprovalId",
		"Get-TerminalUpdateIds",
		"no later approval will run until delivery succeeds",
		"later approvals remain eligible on the next poll",
		"if ($null -ne $candidate) {",
		"break",
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("admin poller missing sequencing invariant %q", required)
		}
	}
	if runtime.GOOS != "windows" {
		return
	}
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		t.Fatal(err)
	}
	quoted := strings.ReplaceAll(path, "'", "''")
	command := "$ErrorActionPreference='Stop'; [scriptblock]::Create((Get-Content -LiteralPath '" + quoted + "' -Raw)) | Out-Null"
	cmd := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("admin poller PowerShell parse failed: %v %s", err, out)
	}
}
