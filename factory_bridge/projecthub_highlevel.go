package main

import (
	"fmt"
	"time"
)

// executeProjectHubSnapshot returns a compact, read-only runtime snapshot for
// frequent agent loops. Detailed server logs stay behind projecthub.logs so a
// healthy observation does not flood the model context with repeated output.
func executeProjectHubSnapshot(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.snapshot"
	res.Meta["project"] = "ProjectHub"
	res.Meta["agentRuntimeVersion"] = agentRuntimeVersion
	res.Meta["pipeline"] = []string{"bridge.doctor", "projecthub.status"}

	doctor := executeBridgeDoctor(cfg, Command{ID: cmd.ID, Action: "bridge.doctor"}, time.Now())
	status := executeProjectHubStatusCanonical(cfg, Command{ID: cmd.ID, Action: "projecthub.status"}, time.Now(), r)

	res.Meta["steps"] = []map[string]any{
		stepSummary("bridge.doctor", doctor),
		stepSummary("projecthub.status", status),
	}
	res.Meta["runtime"] = doctor.Output
	res.Meta["projecthub"] = status.Meta
	res.Meta["logsAvailableVia"] = "projecthub.logs"

	if doctor.Status != "ok" {
		res.Error = "snapshot failed at bridge.doctor: " + doctor.Error
		finish(&res, start)
		return res
	}
	if status.Status != "ok" {
		res.Error = "snapshot failed at projecthub.status: " + status.Error
		finish(&res, start)
		return res
	}

	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Output = "snapshot=ok " + status.Output
	finish(&res, start)
	return res
}

// executeProjectHubVerify performs one canonical preflight followed by the
// build/test pipeline. Unlike projecthub.validate it never starts the server,
// making it suitable for repeated implementation cycles.
func executeProjectHubVerify(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.verify"
	res.Meta["project"] = "ProjectHub"
	res.Meta["agentRuntimeVersion"] = agentRuntimeVersion
	res.Meta["pipeline"] = []string{"canonical-preflight", "build", "test"}
	steps := []map[string]any{}

	state, err := requireCanonicalProjectHub(cfg, r)
	if err != nil {
		addProjectHubGitMeta(res.Meta, state)
		res.Error = err.Error()
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}
	addProjectHubGitMeta(res.Meta, state)
	res.Meta["verifiedCommit"] = state.Head

	build := executeProjectHubBuild(cfg, Command{ID: cmd.ID, Action: "projecthub.build"}, time.Now(), r)
	steps = append(steps, stepSummary("build", build))
	if build.Status != "ok" {
		res.Error = "verify failed at build: " + build.Error
		res.Stdout, res.Stderr = build.Stdout, build.Stderr
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	test := executeProjectHubTest(cfg, Command{ID: cmd.ID, Action: "projecthub.test"}, time.Now(), r)
	steps = append(steps, stepSummary("test", test))
	if test.Status != "ok" {
		res.Error = "verify failed at test: " + test.Error
		res.Stdout = truncate("[build]\n"+build.Stdout+"\n[test]\n"+test.Stdout, 128*1024)
		res.Stderr = truncate("[build]\n"+build.Stderr+"\n[test]\n"+test.Stderr, 128*1024)
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	res.Stdout = truncate("[build]\n"+build.Stdout+"\n[test]\n"+test.Stdout, 128*1024)
	res.Stderr = truncate("[build]\n"+build.Stderr+"\n[test]\n"+test.Stderr, 128*1024)
	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Meta["steps"] = steps
	res.Output = fmt.Sprintf("verify=ok commit=%s build=ok test=ok", state.Head)
	finish(&res, start)
	return res
}

// executeProjectHubRefreshValidate compresses the normal post-commit local
// cycle into one allowlisted action: fast-forward main, then run the complete
// stopped-to-stopped validation pipeline.
func executeProjectHubRefreshValidate(cfg Config, cmd Command, start time.Time, r runner) Result {
	res := baseResult(cmd, start)
	res.LogicalCommand = "projecthub.refresh_validate"
	res.Meta["project"] = "ProjectHub"
	res.Meta["agentRuntimeVersion"] = agentRuntimeVersion
	res.Meta["pipeline"] = []string{"sync", "validate"}
	steps := []map[string]any{}

	syncResult := executeProjectHubSync(cfg, Command{ID: cmd.ID, Action: "projecthub.sync"}, time.Now(), r)
	steps = append(steps, stepSummary("sync", syncResult))
	res.Meta["sync"] = syncResult.Meta
	if syncResult.Status != "ok" {
		res.Error = "refresh_validate failed at sync: " + syncResult.Error
		res.Stdout, res.Stderr = syncResult.Stdout, syncResult.Stderr
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	validation := executeProjectHubValidate(cfg, Command{ID: cmd.ID, Action: "projecthub.validate"}, time.Now(), r)
	steps = append(steps, stepSummary("validate", validation))
	res.Meta["validation"] = validation.Meta
	res.Stdout = truncate("[sync]\n"+syncResult.Stdout+"\n[validate]\n"+validation.Stdout, 128*1024)
	res.Stderr = truncate("[sync]\n"+syncResult.Stderr+"\n[validate]\n"+validation.Stderr, 128*1024)
	if validation.Status != "ok" {
		res.Error = "refresh_validate failed at validate: " + validation.Error
		res.Meta["steps"] = steps
		finish(&res, start)
		return res
	}

	code := 0
	res.ExitCode = &code
	res.Status = "ok"
	res.Meta["steps"] = steps
	commit, _ := validation.Meta["validatedCommit"].(string)
	res.Output = fmt.Sprintf("refresh_validate=ok commit=%s sync=ok build=ok test=ok health=ok provenance=ok stopped=true", commit)
	finish(&res, start)
	return res
}
