package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func init() {
	if len(os.Args) != 2 {
		return
	}
	switch os.Args[1] {
	case "--self-test":
		os.Exit(runSelfTest())
	case "--smoke":
		os.Exit(runSmoke())
	}
}

func runSelfTest() int {
	exe, version, err := findCodex()
	if err != nil {
		printCLI(map[string]any{
			"ok":      false,
			"host":    hostName,
			"version": hostVersion,
			"error":   err.Error(),
		})
		return 1
	}
	printCLI(map[string]any{
		"ok":           true,
		"host":         hostName,
		"version":      hostVersion,
		"codexVersion": version,
		"codexPath":    exe,
	})
	return 0
}

func runSmoke() int {
	exe, version, err := findCodex()
	if err != nil {
		printCLI(map[string]any{"ok": false, "phase": "discover", "error": err.Error()})
		return 1
	}

	root, err := os.MkdirTemp("", "chatops-codex-smoke-*")
	if err != nil {
		printCLI(map[string]any{"ok": false, "phase": "tempdir", "error": err.Error()})
		return 1
	}
	defer os.RemoveAll(root)

	finalPath := filepath.Join(root, "final-message.txt")
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()

	args := []string{
		"exec",
		"--skip-git-repo-check",
		"--ephemeral",
		"--sandbox", "read-only",
		"--color", "never",
		"--output-last-message", finalPath,
		"-",
	}
	cmd := exec.CommandContext(ctx, exe, args...)
	cmd.Stdin = strings.NewReader("Não modifique arquivos. Não execute comandos. Não use ferramentas. Responda somente: HOST_CHATGPT_TO_CODEX_OK")

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	runErr := cmd.Run()

	final := ""
	if data, readErr := os.ReadFile(finalPath); readErr == nil {
		final = strings.TrimSpace(string(data))
	}
	if final == "" && runErr == nil {
		final = strings.TrimSpace(stdout.String())
	}

	exitCode := -1
	if cmd.ProcessState != nil {
		exitCode = cmd.ProcessState.ExitCode()
	}

	ok := runErr == nil && final == "HOST_CHATGPT_TO_CODEX_OK"
	result := map[string]any{
		"ok":           ok,
		"phase":        "codex-smoke",
		"codexVersion": version,
		"exitCode":     exitCode,
		"finalMessage": truncate(final, 2048),
	}
	if ctx.Err() == context.DeadlineExceeded {
		result["error"] = "timeout"
	} else if runErr != nil {
		result["error"] = runErr.Error()
		result["stderrTail"] = tail(stderr.String(), 4096)
	} else if !ok {
		result["error"] = "unexpected final message"
	}
	printCLI(result)
	if ok {
		return 0
	}
	return 1
}

func printCLI(value any) {
	data, err := json.Marshal(value)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return
	}
	fmt.Println(string(data))
}
