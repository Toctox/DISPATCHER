package main

// Job Object containment is implemented in managed_process_windows.go and is
// intentionally kept behind the runner abstraction. The remaining integration
// step is to route osRunner through runManagedProcess once the Windows tests
// prove the native lifecycle and descendant-kill behavior.
