package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

func runEnvelopeComposer(args []string, out, errOut io.Writer) int {
	if len(args) < 1 || len(args) > 2 {
		fmt.Fprintln(errOut, "usage: --compose mission.json [--approve-local-system]")
		return 2
	}
	f, err := os.Open(args[0])
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	defer f.Close()
	var m Mission
	dec := json.NewDecoder(io.LimitReader(f, 65536))
	dec.DisallowUnknownFields()
	if err = dec.Decode(&m); err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	if err = dec.Decode(new(any)); err != io.EOF {
		fmt.Fprintln(errOut, "trailing input")
		return 2
	}
	if len(args) == 2 {
		if args[1] != "--approve-local-system" || m.Kind != "system.command" {
			fmt.Fprintln(errOut, "invalid local authorization request")
			return 2
		}
		req, err := decodeSystemCommandObjective(m)
		if err != nil {
			fmt.Fprintln(errOut, err)
			return 2
		}
		if classifySystemCommand(req.Command).Forbidden {
			fmt.Fprintln(errOut, "forbidden commands cannot be locally approved")
			return 2
		}
		req.LocalApproval, err = privilegedMissionMAC(m, req, true)
		if err != nil {
			fmt.Fprintln(errOut, err)
			return 2
		}
		data, _ := json.Marshal(req)
		m.Objective = string(data)
	}
	m.PayloadHash, err = missionPayloadHash(m)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	if err = validateMissionIntegrity(m); err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	e := githubBusEnvelope{Protocol: githubBusProtocol, Type: "MISSION", ID: m.ID, Kind: m.Kind, Objective: m.Objective, TargetCommit: m.TargetCommit, IssuedAt: m.IssuedAt, ExpiresAt: m.ExpiresAt, PayloadHash: m.PayloadHash}
	data, err := json.Marshal(e)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	fmt.Fprintln(out, githubBusMarker+"\n```json\n"+string(data)+"\n```")
	return 0
}

func init() {
	if len(os.Args) >= 2 && os.Args[1] == "--compose" {
		os.Exit(runEnvelopeComposer(os.Args[2:], os.Stdout, os.Stderr))
	}
}
