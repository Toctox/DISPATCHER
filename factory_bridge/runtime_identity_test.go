package main

import (
	"strings"
	"testing"
)

func TestRuntimeIdentityHasStableProtocolAndPolicy(t *testing.T) {
	if factoryBusProtocolVersion != "FACTORY_BUS_V2" {
		t.Fatalf("unexpected protocol: %s", factoryBusProtocolVersion)
	}
	if strings.TrimSpace(factoryRiskPolicyVersion) == "" {
		t.Fatal("policy version must not be empty")
	}
}

func TestGenericSystemCommandBlocksPowerShellFile(t *testing.T) {
	risk := classifySystemCommand(`powershell.exe -NoProfile -File C:\temp\do-work.ps1`)
	if !risk.Forbidden || risk.Rule != "indirect-script-execution" {
		t.Fatalf("expected indirect script execution to be forbidden, got %+v", risk)
	}
}

func TestGenericSystemCommandBlocksDirectScriptFile(t *testing.T) {
	risk := classifySystemCommand(`C:\temp\do-work.cmd arg1`)
	if !risk.Forbidden || risk.Rule != "indirect-script-execution" {
		t.Fatalf("expected direct script execution to be forbidden, got %+v", risk)
	}
}
