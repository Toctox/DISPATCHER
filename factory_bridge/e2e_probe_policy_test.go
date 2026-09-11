package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestE2EProbePolicyAllowsOnlyNamedModes(t *testing.T) {
	for _, mode := range []string{"success", "failure", "spawn-timeout", "inspect-tree"} {
		m := Mission{Objective: `{"repo":"dispatcher","script":"scripts/factorybridge-e2e-probe.ps1","args":{"Mode":"` + mode + `"}}`}
		if _, err := decodeScriptRun(m); err != nil {
			t.Fatalf("mode %s rejected: %v", mode, err)
		}
	}
	for _, mode := range []string{"", "whoami", "failure;whoami", "$(Get-Process)"} {
		m := Mission{Objective: `{"repo":"dispatcher","script":"scripts/factorybridge-e2e-probe.ps1","args":{"Mode":"` + mode + `"}}`}
		if _, err := decodeScriptRun(m); err == nil {
			t.Fatalf("unsafe mode %q accepted", mode)
		}
	}
}

func TestE2EProbePowerShellParses(t *testing.T) {
	if runtime.GOOS != "windows" {
		return
	}
	path := filepath.Join("..", "scripts", "factorybridge-e2e-probe.ps1")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; [scriptblock]::Create([Console]::In.ReadToEnd()) | Out-Null")
	cmd.Stdin = bytes.NewReader(data)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("probe parse failed: %v %s", err, out)
	}
}
