package main

import (
	"testing"
	"time"
)

func TestRestartDelayIsBounded(t *testing.T) {
	if got := restartDelay(1); got != time.Second {
		t.Fatalf("restartDelay(1)=%s", got)
	}
	if got := restartDelay(2); got != 2*time.Second {
		t.Fatalf("restartDelay(2)=%s", got)
	}
	if got := restartDelay(3); got != 5*time.Second {
		t.Fatalf("restartDelay(3)=%s", got)
	}
	if got := restartDelay(100); got != 10*time.Second {
		t.Fatalf("restartDelay(100)=%s", got)
	}
}
