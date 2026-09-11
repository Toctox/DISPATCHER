package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestAutoUpdaterImmutablePromotionContract(t *testing.T) {
	path := filepath.Join("..", "scripts", "factory-bridge-autoupdate.ps1")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	script := string(data)
	for _, required := range []string{
		"merge-base --is-ancestor $target origin/main",
		"promotion-certificate.json",
		"Save-GoldenSnapshot",
		"Wait-ExactRuntimeHealth",
		"binarySha256",
		"protocolVersion",
		"policyVersion",
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("auto updater missing %q", required)
		}
	}
	if strings.Contains(script, "$originMain -ne $target") || strings.Contains(script, "no longer equals origin/main") {
		t.Fatal("mutable origin/main equality race still present")
	}
	if runtime.GOOS == "windows" {
		powershell, err := exec.LookPath("powershell.exe")
		if err != nil {
			t.Fatal(err)
		}
		cmd := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; [scriptblock]::Create([Console]::In.ReadToEnd()) | Out-Null")
		cmd.Stdin = strings.NewReader(script)
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("auto updater PowerShell parse failed: %v %s", err, out)
		}
	}
}

func TestGoldenRecoveryIsOfflineAndHashPinned(t *testing.T) {
	path := filepath.Join("RECOVER_FACTORY_BRIDGE_GOLDEN.cmd")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	script := strings.ToLower(string(data))
	for _, forbidden := range []string{"git.exe", "go.exe", "github.com", "git clone", "git fetch"} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("offline recovery still depends on %q", forbidden)
		}
	}
	for _, required := range []string{
		"factorybridge.golden.exe",
		"factorybridge.golden.sha256",
		"get-filehash",
		"/public/health",
		"offline-recovery-result.json",
		"binarysha256",
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("offline recovery missing %q", required)
		}
	}
}
