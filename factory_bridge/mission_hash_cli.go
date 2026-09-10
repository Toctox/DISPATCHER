package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
)

// mission-hash is a local trusted helper for FACTORY_BUS_V2 producers.
// It deliberately calls missionPayloadHash so producers cannot drift from
// the exact canonicalization enforced by validateMissionIntegrity.
func runMissionHashCLI(args []string, stdout, stderr io.Writer) int {
	if len(args) != 1 || strings.TrimSpace(args[0]) == "" {
		fmt.Fprintln(stderr, "usage: factory_bridge --mission-hash <mission.json>")
		return 2
	}

	f, err := os.Open(args[0])
	if err != nil {
		fmt.Fprintln(stderr, "FactoryBridge mission-hash:", err)
		return 2
	}
	defer f.Close()

	dec := json.NewDecoder(io.LimitReader(f, 256*1024))
	dec.DisallowUnknownFields()
	var mission Mission
	if err := dec.Decode(&mission); err != nil {
		fmt.Fprintln(stderr, "FactoryBridge mission-hash:", err)
		return 2
	}

	hash, err := missionPayloadHash(mission)
	if err != nil {
		fmt.Fprintln(stderr, "FactoryBridge mission-hash:", err)
		return 1
	}
	canonical, err := json.Marshal(canonicalMissionPayload(mission))
	if err != nil {
		fmt.Fprintln(stderr, "FactoryBridge mission-hash:", err)
		return 1
	}

	result := struct {
		PayloadHash string          `json:"payloadHash"`
		Canonical   json.RawMessage `json:"canonical"`
	}{
		PayloadHash: hash,
		Canonical:   canonical,
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		fmt.Fprintln(stderr, "FactoryBridge mission-hash:", err)
		return 1
	}
	fmt.Fprintln(stdout, string(encoded))
	return 0
}

func init() {
	if len(os.Args) >= 2 && os.Args[1] == "--mission-hash" {
		os.Exit(runMissionHashCLI(os.Args[2:], os.Stdout, os.Stderr))
	}
}
