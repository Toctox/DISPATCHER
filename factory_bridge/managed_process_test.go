package main

import (
	"context"
	"testing"
)

func TestManagedOSRunnerImplementsRunner(t *testing.T) {
	var _ runner = managedOSRunner{}
	_ = context.Background()
}
