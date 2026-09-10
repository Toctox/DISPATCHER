package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestSystemCommandRiskClassification(t *testing.T) {
	tests := []struct {
		name      string
		command   string
		level     string
		approval  bool
		forbidden bool
	}{
		{name: "read", command: "Get-ChildItem C:\\Temp", level: "standard"},
		{name: "task-start", command: "Start-ScheduledTask -TaskName 'FactoryBridge Admin Poller'", level: "guarded"},
		{name: "copy", command: "Copy-Item a.txt b.txt -Force", level: "guarded"},
		{name: "delete", command: "Remove-Item C:\\Temp\\old.txt -Force", level: "approval", approval: true},
		{name: "git-hard-reset", command: "git reset --hard HEAD~1", level: "approval", approval: true},
		{name: "format", command: "format C: /Q", level: "forbidden", forbidden: true},
		{name: "encoded", command: "powershell -EncodedCommand ZQBjAGgAbwA=", level: "forbidden", forbidden: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			risk := classifySystemCommand(tc.command)
			if risk.Level != tc.level || risk.RequiresApproval != tc.approval || risk.Forbidden != tc.forbidden {
				t.Fatalf("risk=%+v", risk)
			}
		})
	}
}

func TestSystemCommandDeletionRequiresExplicitApproval(t *testing.T) {
	mission := Mission{ID: "CMD-DELETE-1", Kind: "system.command", Objective: `{"shell":"powershell","command":"Remove-Item C:\\Temp\\old.txt -Force"}`}
	f := &fakeRunner{code: 0}
	res := executeSystemCommand(Config{DispatcherWorkDir: t.TempDir()}, mission, time.Now(), f)
	if res.Status != "blocked" || !strings.Contains(res.Error, "riskApproval=approved") {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 0 {
		t.Fatalf("blocked deletion reached runner: %d", len(f.specs))
	}
}

func TestSystemCommandApprovedDeletionCanRun(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	mission := Mission{ID: "CMD-DELETE-2", Kind: "system.command", Objective: `{"shell":"powershell","command":"Remove-Item .\\old.txt -Force","riskApproval":"approved"}`}
	req, err := decodeSystemCommandObjective(mission)
	if err != nil {
		t.Fatal(err)
	}
	req.LocalApproval, err = privilegedMissionMAC(mission, req, true)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(req)
	mission.Objective = string(body)
	f := &fakeRunner{stdout: "removed\n", code: 0}
	res := executeSystemCommand(Config{DispatcherWorkDir: t.TempDir(), CommandTimeoutSec: 10}, mission, time.Now(), f)
	if res.Status != "ok" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 1 || f.specs[0].exe != "powershell.exe" {
		t.Fatalf("unexpected runner specs: %+v", f.specs)
	}
	joined := strings.Join(f.specs[0].args, " ")
	if !strings.Contains(joined, "-Command") || !strings.Contains(joined, "Remove-Item") {
		t.Fatalf("unexpected args: %q", joined)
	}
}

func TestSystemCommandForbiddenCannotBeOverridden(t *testing.T) {
	mission := Mission{ID: "CMD-FORMAT-1", Kind: "system.command", Objective: `{"shell":"cmd","command":"format C: /Q","riskApproval":"approved"}`}
	f := &fakeRunner{code: 0}
	res := executeSystemCommand(Config{DispatcherWorkDir: t.TempDir()}, mission, time.Now(), f)
	if res.Status != "blocked" || res.Meta["forbidden"] != true {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 0 {
		t.Fatalf("forbidden command reached runner: %d", len(f.specs))
	}
}

func TestDecodeSystemCommandObjectiveRejectsUnknownFields(t *testing.T) {
	mission := Mission{Objective: `{"shell":"powershell","command":"whoami","surprise":true}`}
	if _, err := decodeSystemCommandObjective(mission); err == nil {
		t.Fatal("expected unknown field rejection")
	}
}
