package main

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"sync"
)

const (
	factoryBusProtocolVersion = "FACTORY_BUS_V2"
	factoryRiskPolicyVersion  = "2026-09-10.1"
)

// buildSourceCommit is injected by the canonical updater using -ldflags -X.
// Development builds intentionally report "development" rather than inventing
// a source identity.
var buildSourceCommit = "development"

var (
	binaryHashOnce sync.Once
	binaryHash     string
)

func runningBinarySHA256() string {
	binaryHashOnce.Do(func() {
		exe, err := os.Executable()
		if err != nil {
			binaryHash = "unavailable"
			return
		}
		data, err := os.ReadFile(exe)
		if err != nil {
			binaryHash = "unavailable"
			return
		}
		sum := sha256.Sum256(data)
		binaryHash = hex.EncodeToString(sum[:])
	})
	return binaryHash
}

func runtimeSourceCommit() string {
	v := strings.ToLower(strings.TrimSpace(buildSourceCommit))
	if v == "" {
		return "development"
	}
	return v
}
