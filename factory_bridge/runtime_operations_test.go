package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestRuntimeCapabilitiesDescribeActualPolicy(t *testing.T) {
	caps := currentRuntimeCapabilities()
	if caps.ProtocolVersion != factoryBusProtocolVersion || caps.PolicyVersion != factoryRiskPolicyVersion {
		t.Fatalf("identity mismatch: %#v", caps)
	}
	if len(caps.TypedScripts) != len(scriptPolicy) {
		t.Fatalf("typed script manifest drift: got %d want %d", len(caps.TypedScripts), len(scriptPolicy))
	}
	joined := strings.Join(caps.RiskRules["forbidden"], ",")
	if !strings.Contains(joined, "indirect-execution") || !strings.Contains(joined, "credential-dump") {
		t.Fatalf("forbidden capability manifest is incomplete: %s", joined)
	}
	if caps.ExecutionLimits["missionRetentionDays"] != 30 || caps.ExecutionLimits["missionRetentionMiB"] != 512 {
		t.Fatalf("retention limits missing: %#v", caps.ExecutionLimits)
	}
}

func TestRuntimeStatusOperationsNeverContainsGatewayTokenValue(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	if err := maintainGatewayTokenLifecycle(); err != nil {
		t.Fatal(err)
	}
	token, err := ensureGatewayToken()
	if err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(publicRuntimeStatus{BridgeVersion: bridgeVersion})
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	if strings.Contains(text, token) {
		t.Fatal("runtime status leaked gateway bearer token")
	}
	for _, field := range []string{`"operations"`, `"capabilities"`, `"busEpoch"`, `"gatewayToken"`, `"fingerprint"`} {
		if !strings.Contains(text, field) {
			t.Fatalf("missing runtime operations field %s in %s", field, text)
		}
	}
}

func TestTokenFingerprintIsStableAndNonSecret(t *testing.T) {
	const token = "test-secret-token-value"
	fingerprint := tokenFingerprint(token)
	if fingerprint == "" || fingerprint == token || len(fingerprint) != 16 {
		t.Fatalf("unexpected fingerprint %q", fingerprint)
	}
	if fingerprint != tokenFingerprint(token) {
		t.Fatal("fingerprint must be deterministic")
	}
}
