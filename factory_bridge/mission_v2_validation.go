package main

import (
	"errors"
	"strings"
)

func validateHardenedMission(m Mission) error {
	if strings.TrimSpace(m.TargetCommit) == "" {
		return errors.New("targetCommit is required for FACTORY_BUS_V2 missions")
	}
	if strings.TrimSpace(m.IssuedAt) == "" {
		return errors.New("issuedAt is required for FACTORY_BUS_V2 missions")
	}
	if strings.TrimSpace(m.ExpiresAt) == "" {
		return errors.New("expiresAt is required for FACTORY_BUS_V2 missions")
	}
	if strings.TrimSpace(m.PayloadHash) == "" {
		return errors.New("payloadHash is required for FACTORY_BUS_V2 missions")
	}
	return validateMissionIntegrity(m)
}
