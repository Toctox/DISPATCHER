package main

import (
	"errors"
	"os"
	"path/filepath"
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

func TestProjectHubBuildUsesOnlyFixedDotnetCommands(t *testing.T) {
	workDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(workDir, "ProjectHub.slnx"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "ok\n", code: 0}
	res := executeAction(Config{
		ProjectHubWorkDir: workDir,
		CommandTimeoutSec: 10,
	}, Command{ID: "PH-BUILD-1", Action: "projecthub.build"}, f)

	if res.Status != "ok" || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Meta["restore"] != "ok" || res.Meta["build"] != "ok" {
		t.Fatalf("unexpected phases: %+v", res.Meta)
	}
	if len(f.specs) != 2 {
		t.Fatalf("runs=%d", len(f.specs))
	}
	for i, spec := range f.specs {
		if spec.exe != "dotnet.exe" {
			t.Fatalf("spec[%d].exe=%q", i, spec.exe)
		}
		if spec.dir != workDir {
			t.Fatalf("spec[%d].dir=%q", i, spec.dir)
		}
	}
	restoreArgs := strings.Join(f.specs[0].args, " ")
	if restoreArgs != "restore ProjectHub.slnx --locked-mode" {
		t.Fatalf("restore args=%q", restoreArgs)
	}
	buildArgs := strings.Join(f.specs[1].args, " ")
	if buildArgs != "build ProjectHub.slnx --configuration Release --no-restore" {
		t.Fatalf("build args=%q", buildArgs)
	}
}

func TestProjectHubBuildStopsWhenRestoreFails(t *testing.T) {
	workDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(workDir, "ProjectHub.slnx"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stderr: "restore failed", code: 1, err: errors.New("exit status 1")}
	res := executeAction(Config{ProjectHubWorkDir: workDir}, Command{ID: "PH-BUILD-2", Action: "projecthub.build"}, f)
	if res.Status != "failed" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Meta["restore"] != "failed" || res.Meta["build"] != "not_run" {
		t.Fatalf("unexpected phases: %+v", res.Meta)
	}
	if len(f.specs) != 1 {
		t.Fatalf("runner invoked %d times", len(f.specs))
	}
}

func TestProjectHubBuildFailsClosedWithoutSolution(t *testing.T) {
	f := &fakeRunner{code: 0}
	res := executeAction(Config{ProjectHubWorkDir: t.TempDir()}, Command{ID: "PH-BUILD-3", Action: "projecthub.build"}, f)
	if res.Status != "failed" || res.Error != "ProjectHub.slnx is unavailable" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 0 {
		t.Fatalf("runner invoked %d times", len(f.specs))
	}
}

func TestProjectHubTestUsesOnlyFixedDotnetCommand(t *testing.T) {
	workDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(workDir, "ProjectHub.slnx"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stdout: "Passed!\n", code: 0}
	res := executeAction(Config{
		ProjectHubWorkDir: workDir,
		CommandTimeoutSec: 10,
	}, Command{ID: "PH-TEST-1", Action: "projecthub.test"}, f)

	if res.Status != "ok" || res.ExitCode == nil || *res.ExitCode != 0 {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Meta["test"] != "ok" {
		t.Fatalf("test=%v", res.Meta["test"])
	}
	if len(f.specs) != 1 {
		t.Fatalf("runs=%d", len(f.specs))
	}
	spec := f.specs[0]
	if spec.exe != "dotnet.exe" {
		t.Fatalf("exe=%q", spec.exe)
	}
	if spec.dir != workDir {
		t.Fatalf("dir=%q", spec.dir)
	}
	args := strings.Join(spec.args, " ")
	if args != "test ProjectHub.slnx --configuration Release --no-build --no-restore" {
		t.Fatalf("args=%q", args)
	}
}

func TestProjectHubTestReportsFailure(t *testing.T) {
	workDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(workDir, "ProjectHub.slnx"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := &fakeRunner{stderr: "test failed", code: 1, err: errors.New("exit status 1")}
	res := executeAction(Config{ProjectHubWorkDir: workDir}, Command{ID: "PH-TEST-2", Action: "projecthub.test"}, f)
	if res.Status != "failed" || res.Meta["test"] != "failed" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(f.specs) != 1 {
		t.Fatalf("runner invoked %d times", len(f.specs))
	}
}

func TestProjectHubTestFailsClosedWithoutSolution(t *testing.T) {
	f := &fakeRunner{code: 0}
	res := executeAction(Config{ProjectHubWorkDir: t.TempDir()}, Command{ID: "PH-TEST-3", Action: "projecthub.test"}, f)
	if res.Status != "failed" || res.Error != "ProjectHub.slnx is unavailable" {
		t.Fatalf("unexpected result: %+v", res)
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
