package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Ordinary shell is deliberately a small literal grammar. General shell is a
// privileged capability requiring a local HMAC, distinct from GitHub identity
// and the public integrity digest. Regex risk classification is not a sandbox.
var ordinaryRead = regexp.MustCompile(`(?i)^(Get-Date|Get-Location|Get-ChildItem|Write-Output [A-Za-z0-9_.-]+|Write-Output '[^'\r\n]*')$`)
var ordinaryWrite = regexp.MustCompile(`(?i)^Set-Content -LiteralPath '([^'\r\n]+)' -Value '([^'\r\n]*)'$`)
var indirectExecution = regexp.MustCompile(`(?i)(\.(ps1|cmd|bat|psm1)\b|\b(powershell|pwsh|cmd)(\.exe)?\b|\b(start-process|invoke-command|invoke-expression|add-type|invoke-cimmethod|invoke-wmimethod)\b|\[(System\.)?(Diagnostics|Reflection)|[&` + "`" + `]|\$\(|::)`)

func withinRoot(path, root string) bool {
	rel, err := filepath.Rel(root, path)
	return err == nil && !filepath.IsAbs(rel) && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func resolveExecutionRoot(cfg Config, requested string) (string, error) {
	dir := strings.TrimSpace(requested)
	if dir == "" {
		dir = strings.TrimSpace(cfg.DispatcherWorkDir)
	}
	if dir == "" {
		return "", errors.New("no approved working root configured")
	}
	if !filepath.IsAbs(dir) {
		dir = filepath.Join(cfg.DispatcherWorkDir, dir)
	}
	abs, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	real, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return "", err
	}
	roots := []string{cfg.DispatcherWorkDir, cfg.ProjectHubWorkDir}
	if local := os.Getenv("LOCALAPPDATA"); local != "" {
		roots = append(roots, filepath.Join(local, "FactoryNode", "workspaces"), filepath.Join(local, "FactoryNode", "artifacts"))
	}
	for _, root := range roots {
		if strings.TrimSpace(root) == "" {
			continue
		}
		rootAbs, err := filepath.Abs(root)
		if err != nil {
			continue
		}
		rootReal, err := filepath.EvalSymlinks(rootAbs)
		if err != nil {
			continue
		}
		if withinRoot(abs, rootAbs) && withinRoot(real, rootReal) {
			info, err := os.Stat(real)
			if err != nil {
				return "", err
			}
			if !info.IsDir() {
				return "", errors.New("workingDir is not a directory")
			}
			return real, nil
		}
	}
	return "", errors.New("workingDir is outside approved execution roots (including link target)")
}

func ordinarySystemCommand(req systemCommandRequest, dir string) bool {
	if req.Shell != "powershell" {
		return false
	}
	if ordinaryRead.MatchString(req.Command) {
		return true
	}
	match := ordinaryWrite.FindStringSubmatch(req.Command)
	if match == nil {
		return false
	}
	name := match[1]
	if filepath.IsAbs(name) || strings.Contains(name, ":") {
		return false
	}
	target := filepath.Join(dir, name)
	if !withinRoot(target, dir) {
		return false
	}
	dirReal, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return false
	}
	parentReal, err := filepath.EvalSymlinks(filepath.Dir(target))
	if err != nil || !withinRoot(parentReal, dirReal) {
		return false
	}
	if info, err := os.Lstat(target); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return false
	}
	return true
}

func localApprovalKey(create bool) ([]byte, error) {
	dir, err := localRuntimeStateDir()
	if err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "privileged-approval.key")
	key, err := os.ReadFile(path)
	if err == nil {
		if len(key) != 32 {
			return nil, errors.New("invalid local approval key")
		}
		return key, nil
	}
	if !create || !os.IsNotExist(err) {
		return nil, err
	}
	key = make([]byte, 32)
	if _, err = rand.Read(key); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	if _, err = f.Write(key); err != nil {
		return nil, err
	}
	if err = f.Sync(); err != nil {
		return nil, err
	}
	return key, nil
}

func privilegedMissionMAC(m Mission, req systemCommandRequest, create bool) (string, error) {
	key, err := localApprovalKey(create)
	if err != nil {
		return "", err
	}
	req.LocalApproval = ""
	body, err := json.Marshal(req)
	if err != nil {
		return "", err
	}
	m.Objective = string(body)
	m.PayloadHash = ""
	hash, err := missionPayloadHash(m)
	if err != nil {
		return "", err
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte("FactoryBridge privileged system.command v1\n" + hash))
	return hex.EncodeToString(mac.Sum(nil)), nil
}

func verifyPrivilegedMission(m Mission, req systemCommandRequest) bool {
	expected, err := privilegedMissionMAC(m, req, false)
	if err != nil {
		return false
	}
	a, err := hex.DecodeString(req.LocalApproval)
	if err != nil {
		return false
	}
	b, _ := hex.DecodeString(expected)
	return hmac.Equal(a, b)
}
