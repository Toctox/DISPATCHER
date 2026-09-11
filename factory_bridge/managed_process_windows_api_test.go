//go:build windows

package main

import "testing"

func TestKillOnCloseJobLifecycle(t *testing.T) {
	job, err := newKillOnCloseJob()
	if err != nil {
		t.Fatal(err)
	}
	closeWinHandle(job)
}
