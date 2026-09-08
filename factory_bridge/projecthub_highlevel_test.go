package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type highLevelRunner struct {
	specs []runSpec
}

func (r *highLevelRunner) Run(_ context.Context, spec runSpec) (string, string, int, error) {
	r.specs = append(r.specs, spec)
	switch spec.logical {
	case "git branch --show-current":
		return "main\n", "", 0, nil
	case "git rev-parse HEAD", "git rev-parse origin/main":
		return "abc123\n", "", 0, nil
	case "git status --porcelain":
		return "", "", 0, nil
	case "git rev-list --left-right --count HEAD...origin/main":
		return "0\t0\n", "", 0, nil
	case "dotnet restore ProjectHub.slnx --locked-mode":
		return "restore ok\n", "", 0, nil
	case "dotnet build ProjectHub.slnx --configuration Release --no-restore":
		return "build ok\n", "", 0, nil
	case "dotnet test ProjectHub.slnx --configuration Release --no-build --no-restore":
		return "test ok\n", "", 0, nil
	default:
		return "", "", 1, fmt.Errorf("unexpected command: %s", spec.logical)
	}
}

func TestProjectHubVerifyBundlesCanonicalBuildAndTest(t *testing.T) {
	workDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(workDir, "ProjectHub.slnx"), []byte(""), 0o600); err != nil {
		t.Fatal(err)
	}
	r := &highLevelRunner{}
	res := executeAction(Config{ProjectHubWorkDir: workDir, CommandTimeoutSec: 10}, Command{ID: "VERIFY-1", Action: "projecthub.verify"}, r)
	if res.Status != "ok" {
		t.Fatalf("verify failed: %+v", res)
	}
	if got := res.Meta["verifiedCommit"]; got != "abc123" {
		t.Fatalf("verifiedCommit=%v", got)
	}
	if !strings.Contains(res.Output, "verify=ok") || !strings.Contains(res.Output, "build=ok") || !strings.Contains(res.Output, "test=ok") {
		t.Fatalf("output=%q", res.Output)
	}
	if len(r.specs) != 8 {
		t.Fatalf("expected 8 fixed commands, got %d", len(r.specs))
	}
	for _, spec := range r.specs {
		if spec.exe != "git.exe" && spec.exe != "dotnet.exe" {
			t.Fatalf("unexpected executable: %q", spec.exe)
		}
	}
}
