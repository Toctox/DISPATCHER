//go:build windows

package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"syscall"
	"time"
	"unsafe"
)

const (
	createSuspended                    = 0x00000004
	jobObjectExtendedLimitInformation = 9
	jobObjectLimitKillOnJobClose       = 0x00002000
	processTerminate                   = 0x0001
	processSetQuota                    = 0x0100
	processSuspendResume               = 0x0800
)

type jobObjectBasicLimitInformation struct {
	PerProcessUserTimeLimit int64
	PerJobUserTimeLimit     int64
	LimitFlags              uint32
	MinimumWorkingSetSize   uintptr
	MaximumWorkingSetSize   uintptr
	ActiveProcessLimit      uint32
	Affinity                uintptr
	PriorityClass           uint32
	SchedulingClass         uint32
}

type ioCounters struct {
	ReadOperationCount  uint64
	WriteOperationCount uint64
	OtherOperationCount uint64
	ReadTransferCount   uint64
	WriteTransferCount  uint64
	OtherTransferCount  uint64
}

type jobObjectExtendedLimitInformation struct {
	BasicLimitInformation jobObjectBasicLimitInformation
	IoInfo                ioCounters
	ProcessMemoryLimit    uintptr
	JobMemoryLimit        uintptr
	PeakProcessMemoryUsed uintptr
	PeakJobMemoryUsed     uintptr
}

var (
	kernel32                   = syscall.NewLazyDLL("kernel32.dll")
	ntdll                      = syscall.NewLazyDLL("ntdll.dll")
	procCreateJobObjectW       = kernel32.NewProc("CreateJobObjectW")
	procSetInformationJobObject = kernel32.NewProc("SetInformationJobObject")
	procAssignProcessToJobObject = kernel32.NewProc("AssignProcessToJobObject")
	procTerminateJobObject     = kernel32.NewProc("TerminateJobObject")
	procOpenProcess            = kernel32.NewProc("OpenProcess")
	procCloseHandle            = kernel32.NewProc("CloseHandle")
	procNtResumeProcess        = ntdll.NewProc("NtResumeProcess")
)

func winCallError(name string, errno error) error {
	if errno == nil || errors.Is(errno, syscall.Errno(0)) {
		return fmt.Errorf("%s failed", name)
	}
	return fmt.Errorf("%s failed: %w", name, errno)
}

func newKillOnCloseJob() (syscall.Handle, error) {
	h, _, errno := procCreateJobObjectW.Call(0, 0)
	if h == 0 {
		return 0, winCallError("CreateJobObjectW", errno)
	}
	job := syscall.Handle(h)
	info := jobObjectExtendedLimitInformation{}
	info.BasicLimitInformation.LimitFlags = jobObjectLimitKillOnJobClose
	ok, _, errno := procSetInformationJobObject.Call(
		uintptr(job),
		jobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)),
		unsafe.Sizeof(info),
	)
	if ok == 0 {
		procCloseHandle.Call(uintptr(job))
		return 0, winCallError("SetInformationJobObject", errno)
	}
	return job, nil
}

func closeWinHandle(h syscall.Handle) {
	if h != 0 {
		procCloseHandle.Call(uintptr(h))
	}
}

func terminateJob(job syscall.Handle, exitCode uint32) {
	if job != 0 {
		procTerminateJobObject.Call(uintptr(job), uintptr(exitCode))
	}
}

func openManagedProcess(pid int) (syscall.Handle, error) {
	access := uintptr(processTerminate | processSetQuota | processSuspendResume)
	h, _, errno := procOpenProcess.Call(access, 0, uintptr(uint32(pid)))
	if h == 0 {
		return 0, winCallError("OpenProcess", errno)
	}
	return syscall.Handle(h), nil
}

func assignToJob(job, process syscall.Handle) error {
	ok, _, errno := procAssignProcessToJobObject.Call(uintptr(job), uintptr(process))
	if ok == 0 {
		return winCallError("AssignProcessToJobObject", errno)
	}
	return nil
}

func resumeManagedProcess(process syscall.Handle) error {
	status, _, _ := procNtResumeProcess.Call(uintptr(process))
	if status != 0 {
		return fmt.Errorf("NtResumeProcess failed with NTSTATUS 0x%x", status)
	}
	return nil
}

// runManagedProcessWindows creates the child suspended, assigns it to a Job
// Object configured with KILL_ON_JOB_CLOSE, and only then resumes it. This
// removes the usual process-tree escape window between Start and job assignment.
func runManagedProcess(ctx context.Context, spec runSpec) (string, string, int, error) {
	job, err := newKillOnCloseJob()
	if err != nil {
		return "", "", -1, err
	}
	defer closeWinHandle(job)

	cmd := exec.Command(spec.exe, spec.args...)
	cmd.Dir = spec.dir
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createSuspended}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return "", "", -1, err
	}

	process, err := openManagedProcess(cmd.Process.Pid)
	if err != nil {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return truncate(stdout.String(), 128*1024), truncate(stderr.String(), 128*1024), -1, err
	}
	defer closeWinHandle(process)

	if err := assignToJob(job, process); err != nil {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		return truncate(stdout.String(), 128*1024), truncate(stderr.String(), 128*1024), -1, err
	}
	if err := resumeManagedProcess(process); err != nil {
		terminateJob(job, 1)
		_ = cmd.Wait()
		return truncate(stdout.String(), 128*1024), truncate(stderr.String(), 128*1024), -1, err
	}

	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	var runErr error
	select {
	case runErr = <-done:
	case <-ctx.Done():
		terminateJob(job, 1)
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
		runErr = ctx.Err()
	}

	code := 0
	if cmd.ProcessState != nil {
		code = cmd.ProcessState.ExitCode()
	} else if runErr != nil {
		code = -1
	}
	return truncate(stdout.String(), 128*1024), truncate(stderr.String(), 128*1024), code, runErr
}
