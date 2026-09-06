package main

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// UnmarshalJSON keeps older local config files loadable while preserving a
// strict allowlist. dispatcherStart/dispatcherTest are compatibility-only and
// ignored: Drive can never select or override executable text through those
// legacy keys. pollSeconds is also compatibility-only and is converted to
// pollIntervalMs when the current key is absent.
func (c *Config) UnmarshalJSON(data []byte) error {
	allowed := map[string]bool{
		"bridgeRoot":        true,
		"dispatcherWorkDir": true,
		"dispatcherStart":   true,
		"dispatcherTest":    true,
		"allowGitPull":      true,
		"commandTimeoutSec": true,
		"pollIntervalMs":    true,
		"pollSeconds":       true,
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	for key := range raw {
		if !allowed[key] {
			return fmt.Errorf("json: unknown field %q", key)
		}
	}
	type wire struct {
		BridgeRoot        string `json:"bridgeRoot"`
		DispatcherWorkDir string `json:"dispatcherWorkDir"`
		DispatcherStart   string `json:"dispatcherStart"`
		DispatcherTest    string `json:"dispatcherTest"`
		AllowGitPull      bool   `json:"allowGitPull"`
		CommandTimeoutSec int    `json:"commandTimeoutSec"`
		PollIntervalMs    *int   `json:"pollIntervalMs"`
		PollSeconds       *int   `json:"pollSeconds"`
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var w wire
	if err := dec.Decode(&w); err != nil {
		return err
	}

	pollIntervalMs := 0
	if w.PollIntervalMs != nil {
		pollIntervalMs = *w.PollIntervalMs
	} else if w.PollSeconds != nil {
		maxInt := int(^uint(0) >> 1)
		if *w.PollSeconds > 0 && *w.PollSeconds > maxInt/1000 {
			return fmt.Errorf("pollSeconds is too large")
		}
		pollIntervalMs = *w.PollSeconds * 1000
	}

	*c = Config{
		BridgeRoot:        w.BridgeRoot,
		DispatcherWorkDir: w.DispatcherWorkDir,
		AllowGitPull:      w.AllowGitPull,
		CommandTimeoutSec: w.CommandTimeoutSec,
		PollIntervalMs:    pollIntervalMs,
	}
	return nil
}
