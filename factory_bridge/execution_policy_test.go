package main

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestExecutionRootsAndShellIndirection(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	root := t.TempDir()
	outside := t.TempDir()
	cfg := Config{DispatcherWorkDir: root}
	if _, err := resolveExecutionRoot(cfg, outside); err == nil {
		t.Fatal("outside root accepted")
	}
	if _, err := resolveExecutionRoot(Config{}, ""); err == nil {
		t.Fatal("home fallback accepted")
	}
	if _, err := resolveExecutionRoot(cfg, root); err != nil {
		t.Fatal(err)
	}
	for _, command := range []string{`& $p`, `.\build.ps1`, `cmd /c whoami`, `Start-Process foo`, `[System.Diagnostics.Process]::Start('foo')`, `powershell -Command whoami`, `npm run test`} {
		req := systemCommandRequest{Shell: "powershell", Command: command, RiskApproval: "approved"}
		data, _ := json.Marshal(req)
		f := &fakeRunner{}
		res := executeSystemCommand(cfg, Mission{ID: "M-boundary", Objective: string(data)}, time.Now(), f)
		if res.Status != "blocked" || len(f.specs) != 0 {
			t.Fatalf("indirect execution escaped: %s", command)
		}
	}
	for _, cmd := range []string{`Write-Output 'hello'`, `Get-Date`, `Set-Content -LiteralPath 'output.txt' -Value 'ok'`} {
		if !ordinarySystemCommand(systemCommandRequest{Shell: "powershell", Command: cmd}, root) {
			t.Fatalf("ordinary command rejected %s", cmd)
		}
	}
	if ordinarySystemCommand(systemCommandRequest{Shell: "powershell", Command: `Set-Content -LiteralPath '../outside.txt' -Value 'ok'`}, root) {
		t.Fatal("write traversal accepted")
	}
}

func TestLocalApprovalBindsExactMissionIntent(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-hmac")
	req := systemCommandRequest{Shell: "powershell", Command: "Get-Process", RiskApproval: "approved"}
	var err error
	req.LocalApproval, err = privilegedMissionMAC(m, req, true)
	if err != nil {
		t.Fatal(err)
	}
	if !verifyPrivilegedMission(m, req) {
		t.Fatal("local signature rejected")
	}
	req.Command = "Get-Service"
	if verifyPrivilegedMission(m, req) {
		t.Fatal("changed command retained authority")
	}
	req.Command = "Get-Process"
	m.ID += "-replay"
	if verifyPrivilegedMission(m, req) {
		t.Fatal("signature could authorize a new mission")
	}
}

func TestComposerGoldenUnicodeAndEscaping(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	m := hardenedTestMission(t, "M-compose")
	m.Objective = `Café <>& "quoted" C:\work\á`
	path := filepath.Join(t.TempDir(), "mission.json")
	data, _ := json.Marshal(m)
	os.WriteFile(path, data, 0600)
	var out, stderr bytes.Buffer
	if runEnvelopeComposer([]string{path}, &out, &stderr) != 0 {
		t.Fatal(stderr.String())
	}
	e, err := parseGitHubBusEnvelope(out.String())
	if err != nil {
		t.Fatal(err)
	}
	if e.Objective != m.Objective {
		t.Fatal("unicode/escaping changed")
	}
	m.PayloadHash = e.PayloadHash
	if err := validateMissionIntegrity(m); err != nil {
		t.Fatal(err)
	}
}

func TestScriptArgumentsRejectInjectionAndUnlistedPaths(t *testing.T) {
	for _, obj := range []string{
		`{"repo":"dispatcher","script":"../evil.ps1"}`,
		`{"repo":"dispatcher","script":"scripts/factorybridge-smoke.ps1","args":{"Message":"$(Get-Process)"}}`,
		`{"repo":"dispatcher","script":"scripts/factorybridge-smoke.ps1","args":{"Command":"whoami"}}`,
		`{"repo":"dispatcher","script":"scripts/factorybridge-smoke.ps1"} {}`,
	} {
		if _, err := decodeScriptRun(Mission{Objective: obj}); err == nil {
			t.Fatalf("accepted %s", obj)
		}
	}
}

func TestScriptRunUsesExactIsolatedCommit(t *testing.T) {
	if _, err := exec.LookPath("powershell.exe"); err != nil {
		t.Skip("requires Windows PowerShell")
	}
	t.Setenv("LOCALAPPDATA", t.TempDir())
	repo := t.TempDir()
	git := func(args ...string) string {
		t.Helper()
		c := exec.Command("git.exe", args...)
		c.Dir = repo
		out, err := c.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	git("init", "-b", "main")
	git("config", "user.name", "FactoryBridge Test")
	git("config", "user.email", "factorybridge-test@example.invalid")
	os.Mkdir(filepath.Join(repo, "scripts"), 0700)
	os.WriteFile(filepath.Join(repo, "scripts", "factorybridge-smoke.ps1"), []byte("param([string]$Message)\nWrite-Output ('EXACT:'+ $Message)\n"), 0600)
	git("add", ".")
	git("commit", "-m", "fixture")
	target := git("rev-parse", "HEAD")
	git("remote", "add", "origin", repo)
	m := Mission{ID: "M-script-exact", Kind: "script.run", TargetCommit: target, Objective: `{"repo":"dispatcher","script":"scripts/factorybridge-smoke.ps1","args":{"Message":"SPACE VALUE"},"timeoutSec":30}`}
	res := executeScriptRun(Config{DispatcherWorkDir: repo}, m, time.Now(), osRunner{})
	if res.Status != "ok" || !strings.Contains(res.Output, "EXACT:SPACE VALUE") {
		t.Fatalf("script failed %+v", res)
	}
	if res.Meta["sourceCommit"] != target || res.Meta["workingDir"] == repo {
		t.Fatal("script did not use isolated exact commit")
	}
	if retry := executeScriptRun(Config{DispatcherWorkDir: repo}, m, time.Now(), osRunner{}); retry.Status != "blocked" {
		t.Fatal("workspace was reused")
	}
}
