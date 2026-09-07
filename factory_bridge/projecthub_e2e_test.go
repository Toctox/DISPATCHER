package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func makeProjectHubE2ETestProject(t *testing.T, workDir string) {
	t.Helper()
	testDir := filepath.Join(workDir, "tests", "ProjectHub.Server.Tests")
	if err := os.MkdirAll(testDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(testDir, "ProjectHub.Server.Tests.csproj"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestProjectHubE2EUsesFixedFilterAndRecordsCanonicalCommit(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	makeProjectHubE2ETestProject(t, workDir)
	r := canonicalRunner("abc123")

	res := executeAction(
		Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir},
		Command{ID: "E2E-V11", Action: "projecthub.e2e"},
		r,
	)
	if res.Status != "ok" || res.Meta["e2e"] != "ok" || res.Meta["e2eCommit"] != "abc123" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if !strings.Contains(res.Output, "e2eCommit=abc123") {
		t.Fatalf("output=%q", res.Output)
	}

	var dotnet *runSpec
	for i := range r.specs {
		if r.specs[i].exe == "dotnet.exe" {
			dotnet = &r.specs[i]
			break
		}
	}
	if dotnet == nil {
		t.Fatal("dotnet e2e command was not executed")
	}
	if dotnet.dir != workDir {
		t.Fatalf("dir=%q", dotnet.dir)
	}
	args := strings.Join(dotnet.args, " ")
	expected := "test tests/ProjectHub.Server.Tests/ProjectHub.Server.Tests.csproj --configuration Release --no-build --no-restore --filter Category=E2E"
	if args != expected {
		t.Fatalf("args=%q want=%q", args, expected)
	}
}

func TestProjectHubE2ERefusesNonCanonicalCheckoutBeforeDotnet(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	makeProjectHubE2ETestProject(t, workDir)
	r := &projectHubTestRunner{run: func(spec runSpec) (string, string, int, error) {
		if spec.exe == "dotnet.exe" {
			t.Fatal("dotnet must not run for non-canonical checkout")
		}
		args := strings.Join(spec.args[2:], " ")
		switch args {
		case "branch --show-current":
			return "cr/old\n", "", 0, nil
		case "rev-parse HEAD", "rev-parse origin/main":
			return "abc123\n", "", 0, nil
		case "status --porcelain":
			return "", "", 0, nil
		case "rev-list --left-right --count HEAD...origin/main":
			return "0\t0\n", "", 0, nil
		default:
			return "", "bad", 1, os.ErrInvalid
		}
	}}

	res := executeAction(
		Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir},
		Command{ID: "E2E-NONMAIN", Action: "projecthub.e2e"},
		r,
	)
	if res.Status != "failed" || !strings.Contains(res.Error, "branch=cr/old") {
		t.Fatalf("unexpected result: %+v", res)
	}
}
