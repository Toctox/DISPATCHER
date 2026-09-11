package main

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"strings"
	"sync"
)

const factoryBusProtocolVersion = "FACTORY_BUS_V2"
const factoryRiskPolicyVersion = "2026-09-10.4"

// Injected by the approved build. Never infer executing code from a mutable
// checkout or installed-runtime.json (which can already describe a replacement).
var buildSourceCommit = "development"

type RuntimeIdentity struct {
	SourceCommit    string `json:"sourceCommit"`
	BinarySHA256    string `json:"binarySha256"`
	ProtocolVersion string `json:"protocolVersion"`
	PolicyVersion   string `json:"policyVersion"`
}

var binaryHashOnce sync.Once
var binaryHash string

func runningBinarySHA256() string {
	binaryHashOnce.Do(func() {
		binaryHash = "unavailable"
		path, err := os.Executable()
		if err != nil {
			return
		}
		f, err := os.Open(path)
		if err != nil {
			return
		}
		defer f.Close()
		h := sha256.New()
		if _, err := io.Copy(h, f); err == nil {
			binaryHash = hex.EncodeToString(h.Sum(nil))
		}
	})
	return binaryHash
}

func runtimeSourceCommit() string {
	s := strings.ToLower(strings.TrimSpace(buildSourceCommit))
	if len(s) == 40 {
		if _, err := hex.DecodeString(s); err == nil {
			return s
		}
	}
	return "development"
}

func currentRuntimeIdentity() RuntimeIdentity {
	return RuntimeIdentity{runtimeSourceCommit(), runningBinarySHA256(), factoryBusProtocolVersion, factoryRiskPolicyVersion}
}

func init() { runningBinarySHA256() }
