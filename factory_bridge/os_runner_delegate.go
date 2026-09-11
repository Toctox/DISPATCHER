package main

import "context"

// managedOSRunner is the process-tree-safe runtime runner. Keeping this
// adapter separate makes the Windows containment mechanism independently
// testable while preserving the runner interface.
type managedOSRunner struct{}

func (managedOSRunner) Run(ctx context.Context, spec runSpec) (string, string, int, error) {
	return runManagedProcess(ctx, spec)
}
