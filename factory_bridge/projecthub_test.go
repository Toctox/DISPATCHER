package main

import (
	"strings"
	"testing"
)

func TestProjectHubStatusUsesOnlyLocalAllowlistedInputs(t *testing.T) {
	workDir := t.TempDir()
	f := &fakeRunner{stdout: "main\n", code: 0}
	oldProbe := projectHubHealthProbe
	projectHubHealthProbe = func() bool { return true }
	defer func() { projectHubHealthProbe = oldProbe }()

	res := executeAction(Config{
		ProjectHubWorkDir: workDir,
		CommandTimeoutSec: 10,
	}, Command{ID: "PH-STATUS-1", Action: "projecthub.status"}, f)

	if res.Status != "ok" {
		t.Fatalf("unexpected status: %+v", res)
	}
	if res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected exit code: %+v", res)
	}
	if res.Meta["project"] != "ProjectHub" {
		t.Fatalf("project=%v", res.Meta["project"])
	}
	if res.Meta["repositoryAvailable"] != true {
		t.Fatalf("repositoryAvailable=%v", res.Meta["repositoryAvailable"])
	}
	if res.Meta["branch"] != "main" {
		t.Fatalf("branch=%v", res.Meta["branch"])
	}
	if res.Meta["applicationRunning"] != true {
		t.Fatalf("applicationRunning=%v", res.Meta["applicationRunning"])
	}
	if res.Meta["applicationUrl"] != projectHubDefaultURL {
		t.Fatalf("applicationUrl=%v", res.Meta["applicationUrl"])
	}
	if len(f.specs) != 1 {
		t.Fatalf("runs=%d", len(f.specs))
	}
	spec := f.specs[0]
	if spec.exe != "git.exe" {
		t.Fatalf("exe=%q", spec.exe)
	}
	if spec.dir != workDir {
		t.Fatalf("dir=%q", spec.dir)
	}
	joined := strings.Join(spec.args, " ")
	if !strings.Contains(joined, "branch --show-current") {
		t.Fatalf("args=%q", joined)
	}
}

func TestProjectHubStatusFailsClosedWithoutLocalWorkDir(t *testing.T) {
	f := &fakeRunner{code: 0}
	res := executeAction(Config{}, Command{ID: "PH-STATUS-2", Action: "projecthub.status"}, f)
	if res.Status != "failed" {
		t.Fatalf("unexpected status: %+v", res)
	}
	if res.Error != "projectHubWorkDir is not configured" {
		t.Fatalf("error=%q", res.Error)
	}
	if len(f.specs) != 0 {
		t.Fatalf("runner invoked %d times", len(f.specs))
	}
}

func TestProjectHubWorkDirIsAcceptedInLocalConfig(t *testing.T) {
	cfg, err := loadConfig(writeConfigForTest(t, `{"bridgeRoot":"C:\\bridge","projectHubWorkDir":"C:\\src\\ProjectHub"}`))
	if err != nil {
		t.Fatalf("projectHubWorkDir should load: %v", err)
	}
	if cfg.ProjectHubWorkDir != `C:\src\ProjectHub` {
		t.Fatalf("ProjectHubWorkDir=%q", cfg.ProjectHubWorkDir)
	}
}
