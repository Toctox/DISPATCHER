//go:build !windows

package main

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
)

func runManagedProcess(ctx context.Context, spec runSpec) (string, string, int, error) {
	cmd := exec.CommandContext(ctx, spec.exe, spec.args...)
	cmd.Dir = spec.dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	code := 0
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			code = exitErr.ExitCode()
		} else {
			code = -1
		}
	}
	return truncate(stdout.String(), 128*1024), truncate(stderr.String(), 128*1024), code, err
}
