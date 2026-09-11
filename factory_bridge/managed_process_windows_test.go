//go:build windows

package main

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestManagedProcessTimeoutKillsDescendantTree(t *testing.T) {
	dir := t.TempDir()
	pidPath := filepath.Join(dir, "child.pid")
	child := `Start-Sleep -Seconds 30`
	parent := `$p=Start-Process powershell.exe -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-Command',` + "'" + child + "'" + `) -PassThru; Set-Content -LiteralPath '` + strings.ReplaceAll(pidPath, "'", "''") + `' -Value $p.Id; Start-Sleep -Seconds 30`
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()
	_, _, _, err := runManagedProcess(ctx, runSpec{exe: "powershell.exe", args: []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-Command", parent}, dir: dir})
	if err == nil || ctx.Err() != context.DeadlineExceeded {
		t.Fatalf("expected deadline, got err=%v ctx=%v", err, ctx.Err())
	}
	data, err := os.ReadFile(pidPath)
	if err != nil {
		t.Fatalf("child pid was not recorded: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		t.Fatalf("invalid child pid: %v", err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		out, _ := exec.Command("powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$p=Get-Process -Id "+strconv.Itoa(pid)+" -ErrorAction SilentlyContinue; if($null -eq $p){'DEAD'}else{'ALIVE'}").Output()
		if strings.Contains(string(out), "DEAD") {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("descendant pid %d survived parent timeout", pid)
}
