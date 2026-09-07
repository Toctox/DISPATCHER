package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type projectHubTestRunner struct {
	specs []runSpec
	run   func(runSpec) (string, string, int, error)
}

func (r *projectHubTestRunner) Run(_ context.Context, spec runSpec) (string, string, int, error) {
	r.specs = append(r.specs, spec)
	if r.run == nil {
		return "", "", 0, nil
	}
	return r.run(spec)
}

func canonicalRunner(head string) *projectHubTestRunner {
	return &projectHubTestRunner{run: func(spec runSpec) (string, string, int, error) {
		if spec.exe == "dotnet.exe" {
			return "ok\n", "", 0, nil
		}
		args := strings.Join(spec.args[2:], " ")
		switch args {
		case "branch --show-current":
			return "main\n", "", 0, nil
		case "rev-parse HEAD":
			return head + "\n", "", 0, nil
		case "rev-parse origin/main":
			return head + "\n", "", 0, nil
		case "status --porcelain":
			return "", "", 0, nil
		case "rev-list --left-right --count HEAD...origin/main":
			return "0\t0\n", "", 0, nil
		case "fetch origin main", "switch main", "merge --ff-only origin/main":
			return "ok\n", "", 0, nil
		default:
			return "", "unexpected git command", 1, errors.New("unexpected git command")
		}
	}}
}

func makeCanonicalProjectHub(t *testing.T) (string, string) {
	t.Helper()
	bridge := t.TempDir()
	workDir := makeProjectHubServerProject(t)
	if err := os.WriteFile(filepath.Join(workDir, "ProjectHub.slnx"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(bridge, statusDirName), 0o755); err != nil {
		t.Fatal(err)
	}
	return bridge, workDir
}

func TestProjectHubStatusCanonicalReportsCommitAndDivergence(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := &projectHubTestRunner{run: func(spec runSpec) (string, string, int, error) {
		args := strings.Join(spec.args[2:], " ")
		switch args {
		case "branch --show-current":
			return "feature\n", "", 0, nil
		case "rev-parse HEAD":
			return "abc123\n", "", 0, nil
		case "rev-parse origin/main":
			return "def456\n", "", 0, nil
		case "status --porcelain":
			return " M file.go\n", "", 0, nil
		case "rev-list --left-right --count HEAD...origin/main":
			return "2\t3\n", "", 0, nil
		default:
			return "", "bad", 1, errors.New("bad")
		}
	}}
	oldProbe := projectHubHealthProbe
	projectHubHealthProbe = func() bool { return true }
	defer func() { projectHubHealthProbe = oldProbe }()

	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "STATUS-V10", Action: "projecthub.status"}, r)
	if res.Status != "ok" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if res.Meta["branch"] != "feature" || res.Meta["head"] != "abc123" || res.Meta["originMain"] != "def456" {
		t.Fatalf("unexpected commit meta: %+v", res.Meta)
	}
	if res.Meta["dirty"] != true || res.Meta["ahead"] != 2 || res.Meta["behind"] != 3 {
		t.Fatalf("unexpected divergence meta: %+v", res.Meta)
	}
	if res.Meta["applicationRunning"] != true || res.Meta["provenanceKnown"] != false {
		t.Fatalf("unexpected runtime meta: %+v", res.Meta)
	}
}

func TestProjectHubSyncUsesOnlyFixedCommandsAndVerifiesCanonicalHead(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := canonicalRunner("abc123")
	oldProbe := projectHubHealthProbe
	projectHubHealthProbe = func() bool { return false }
	defer func() { projectHubHealthProbe = oldProbe }()

	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "SYNC-V10", Action: "projecthub.sync"}, r)
	if res.Status != "ok" || res.Meta["syncedCommit"] != "abc123" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(r.specs) != 9 {
		t.Fatalf("runs=%d", len(r.specs))
	}
	got := []string{}
	for _, spec := range r.specs[:4] {
		if spec.exe != "git.exe" || spec.dir != workDir {
			t.Fatalf("unexpected sync spec: %+v", spec)
		}
		got = append(got, strings.Join(spec.args[2:], " "))
	}
	expected := []string{"status --porcelain", "fetch origin main", "switch main", "merge --ff-only origin/main"}
	for i := range expected {
		if got[i] != expected[i] {
			t.Fatalf("sync command[%d]=%q want %q", i, got[i], expected[i])
		}
	}
}

func TestProjectHubSyncRefusesDirtyCheckoutBeforeFetch(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := &projectHubTestRunner{run: func(spec runSpec) (string, string, int, error) {
		if strings.Join(spec.args[2:], " ") == "status --porcelain" {
			return " M local.txt\n", "", 0, nil
		}
		return "", "should not run", 1, errors.New("should not run")
	}}
	oldProbe := projectHubHealthProbe
	projectHubHealthProbe = func() bool { return false }
	defer func() { projectHubHealthProbe = oldProbe }()

	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "SYNC-DIRTY", Action: "projecthub.sync"}, r)
	if res.Status != "failed" || !strings.Contains(res.Error, "dirty") {
		t.Fatalf("unexpected result: %+v", res)
	}
	if len(r.specs) != 1 {
		t.Fatalf("runner invoked %d times", len(r.specs))
	}
}

func TestProjectHubBuildRefusesNonMainCheckoutBeforeDotnet(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
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
			return "", "bad", 1, errors.New("bad")
		}
	}}
	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "BUILD-NONMAIN", Action: "projecthub.build"}, r)
	if res.Status != "failed" || !strings.Contains(res.Error, "branch=cr/old") {
		t.Fatalf("unexpected result: %+v", res)
	}
}

func TestProjectHubBuildRecordsBuiltCommit(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := canonicalRunner("abc123")
	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "BUILD-V10", Action: "projecthub.build"}, r)
	if res.Status != "ok" || res.Meta["builtCommit"] != "abc123" {
		t.Fatalf("unexpected result: %+v", res)
	}
	if !strings.Contains(res.Output, "builtCommit=abc123") {
		t.Fatalf("output=%q", res.Output)
	}
}

func TestProjectHubTestRecordsTestedCommit(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := canonicalRunner("abc123")
	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "TEST-V10", Action: "projecthub.test"}, r)
	if res.Status != "ok" || res.Meta["testedCommit"] != "abc123" {
		t.Fatalf("unexpected result: %+v", res)
	}
}

func TestProjectHubStartRefusesHealthyUnknownProcess(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := canonicalRunner("abc123")
	oldProbe := projectHubHealthProbe
	oldStart := projectHubStartProcess
	projectHubHealthProbe = func() bool { return true }
	projectHubStartProcess = func(string, runSpec) (int, error) {
		t.Fatal("unknown healthy process must not be replaced or accepted")
		return 0, nil
	}
	defer func() {
		projectHubHealthProbe = oldProbe
		projectHubStartProcess = oldStart
	}()

	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "START-UNKNOWN", Action: "projecthub.start"}, r)
	if res.Status != "failed" || !strings.Contains(res.Error, "provenance is unknown") {
		t.Fatalf("unexpected result: %+v", res)
	}
}

func TestProjectHubStartPersistsManagedCommit(t *testing.T) {
	bridge, workDir := makeCanonicalProjectHub(t)
	r := canonicalRunner("abc123")
	oldProbe := projectHubHealthProbe
	oldStart := projectHubStartProcess
	defer func() {
		projectHubHealthProbe = oldProbe
		projectHubStartProcess = oldStart
	}()
	calls := 0
	projectHubHealthProbe = func() bool {
		calls++
		return calls >= 3
	}
	projectHubStartProcess = func(string, runSpec) (int, error) { return 4321, nil }

	res := executeAction(Config{BridgeRoot: bridge, ProjectHubWorkDir: workDir}, Command{ID: "START-V10", Action: "projecthub.start"}, r)
	if res.Status != "ok" || res.Meta["startedCommit"] != "abc123" || res.Meta["provenanceKnown"] != true {
		t.Fatalf("unexpected result: %+v", res)
	}
	state, err := readJSONFile[projectHubProcessState](filepath.Join(bridge, statusDirName, projectHubProcessStateFileName))
	if err != nil {
		t.Fatal(err)
	}
	if state.PID != 4321 || state.StartedCommit != "abc123" {
		t.Fatalf("unexpected state: %+v", state)
	}
}

func TestProjectHubStopRefusesUnknownHealthyProcess(t *testing.T) {
	bridge := t.TempDir()
	oldProbe := projectHubHealthProbe
	projectHubHealthProbe = func() bool { return true }
	defer func() { projectHubHealthProbe = oldProbe }()
	res := executeAction(Config{BridgeRoot: bridge}, Command{ID: "STOP-UNKNOWN", Action: "projecthub.stop"}, &fakeRunner{})
	if res.Status != "failed" || !strings.Contains(res.Error, "refusing to kill an unknown process") {
		t.Fatalf("unexpected result: %+v", res)
	}
}

func TestProjectHubStopKillsOnlyRecordedPidAndClearsState(t *testing.T) {
	bridge := t.TempDir()
	if err := os.MkdirAll(filepath.Join(bridge, statusDirName), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := writeJSONAtomic(filepath.Join(bridge, statusDirName, projectHubProcessStateFileName), projectHubProcessState{
		PID: 7777, StartedCommit: "abc123", StartedAt: time.Now().Format(time.RFC3339), URL: projectHubDefaultURL,
	}); err != nil {
		t.Fatal(err)
	}
	oldProbe := projectHubHealthProbe
	oldKill := projectHubKillProcess
	oldSleep := projectHubSleep
	defer func() {
		projectHubHealthProbe = oldProbe
		projectHubKillProcess = oldKill
		projectHubSleep = oldSleep
	}()
	probeCalls := 0
	projectHubHealthProbe = func() bool {
		probeCalls++
		return probeCalls == 1
	}
	killed := 0
	projectHubKillProcess = func(pid int) error {
		killed = pid
		return nil
	}
	projectHubSleep = func(time.Duration) {}

	res := executeAction(Config{BridgeRoot: bridge}, Command{ID: "STOP-V10", Action: "projecthub.stop"}, &fakeRunner{})
	if res.Status != "ok" || killed != 7777 || res.Meta["stoppedCommit"] != "abc123" {
		t.Fatalf("unexpected result: %+v killed=%d", res, killed)
	}
	if _, err := os.Stat(filepath.Join(bridge, statusDirName, projectHubProcessStateFileName)); !os.IsNotExist(err) {
		t.Fatalf("process state should be removed, err=%v", err)
	}
}
