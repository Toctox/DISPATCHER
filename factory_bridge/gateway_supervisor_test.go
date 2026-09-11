package main

import (
	"testing"
	"time"
)

func TestGatewayCrashTrackerQuarantinesRapidCrashLoop(t *testing.T) {
	var tracker gatewayCrashTracker
	base := time.Date(2026, 9, 11, 1, 0, 0, 0, time.UTC)
	for i := 0; i < gatewayCrashLimit-1; i++ {
		if tracker.recordExit(base.Add(time.Duration(i) * time.Second)) {
			t.Fatalf("quarantined too early at crash %d", i+1)
		}
	}
	at := base.Add(time.Duration(gatewayCrashLimit-1) * time.Second)
	if !tracker.recordExit(at) {
		t.Fatal("expected rapid crash loop to enter quarantine")
	}
	if !tracker.quarantined(at.Add(time.Second)) {
		t.Fatal("expected active quarantine")
	}
	if tracker.quarantined(at.Add(gatewayQuarantineDuration + time.Second)) {
		t.Fatal("expected quarantine to expire")
	}
}

func TestGatewayCrashTrackerDropsOldCrashes(t *testing.T) {
	var tracker gatewayCrashTracker
	base := time.Date(2026, 9, 11, 1, 0, 0, 0, time.UTC)
	for i := 0; i < gatewayCrashLimit-1; i++ {
		if tracker.recordExit(base.Add(time.Duration(i) * time.Second)) {
			t.Fatal("unexpected quarantine")
		}
	}
	if tracker.recordExit(base.Add(gatewayCrashWindow + time.Minute)) {
		t.Fatal("old crashes outside the rolling window must not trigger quarantine")
	}
}
