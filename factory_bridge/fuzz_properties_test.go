package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

func FuzzFactoryBusEnvelopeRoundTrip(f *testing.F) {
	for _, seed := range []string{"plain", `quote\"slash\\`, "unicode-ç-漢字", "<tag>&value", "line1\nline2"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, objective string) {
		if len(objective) > 4096 {
			t.Skip()
		}
		e := githubBusEnvelope{
			Protocol:     githubBusProtocol,
			Type:         "MISSION",
			ID:           "M-FUZZ-ROUNDTRIP",
			Kind:         "projecthub.verify",
			Objective:    objective,
			TargetCommit: strings.Repeat("a", 40),
			IssuedAt:     "2099-01-01T00:00:00Z",
			ExpiresAt:    "2099-01-01T01:00:00Z",
			PayloadHash:  strings.Repeat("b", 64),
		}
		data, err := json.Marshal(e)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := parseGitHubBusEnvelope(githubBusMarker + "\n```json\n" + string(data) + "\n```")
		if err != nil {
			t.Fatalf("valid marshaled envelope rejected: %v", err)
		}
		if parsed.Objective != objective {
			t.Fatalf("objective changed across JSON/marker round trip: got=%q want=%q", parsed.Objective, objective)
		}
	})
}

func FuzzMissionPayloadHashCanonicalization(f *testing.F) {
	for _, seed := range []string{"plain", " spaces ", `quote\"backslash\\`, "unicode-ç-漢字", "<>&"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, objective string) {
		if len(objective) > 4096 {
			t.Skip()
		}
		upper := strings.Repeat("A", 40)
		left := Mission{ID: " M-FUZZ-HASH ", Kind: " projecthub.verify ", Objective: " " + objective + " ", TargetCommit: upper, IssuedAt: " 2099-01-01T00:00:00Z ", ExpiresAt: " 2099-01-01T01:00:00Z "}
		right := Mission{ID: "M-FUZZ-HASH", Kind: "projecthub.verify", Objective: strings.TrimSpace(objective), TargetCommit: strings.ToLower(upper), IssuedAt: "2099-01-01T00:00:00Z", ExpiresAt: "2099-01-01T01:00:00Z"}
		a, err := missionPayloadHash(left)
		if err != nil {
			t.Fatal(err)
		}
		b, err := missionPayloadHash(right)
		if err != nil {
			t.Fatal(err)
		}
		if a != b {
			t.Fatalf("canonical hash diverged for equivalent payloads: %s != %s", a, b)
		}
	})
}

func FuzzRemoteRedactionNeverLeaksRecognizedSecretValues(f *testing.F) {
	f.Add([]byte("alpha"))
	f.Add([]byte{0, 1, 2, 3, 255})
	f.Fuzz(func(t *testing.T, raw []byte) {
		sum := sha256.Sum256(raw)
		secret := hex.EncodeToString(sum[:])
		input := "Password=" + secret + "; TOKEN=" + secret + "; Authorization: Bearer " + secret
		out := sanitizeRemoteText(input)
		if strings.Contains(out, secret) {
			t.Fatalf("recognized secret survived redaction: %q", out)
		}
	})
}

func FuzzRiskClassificationDeterministicAndForbiddenIndirectionStaysBlocked(f *testing.F) {
	for _, seed := range []string{"", "Write-Output ok", "Get-ChildItem", "safe-prefix; "} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, prefix string) {
		if len(prefix) > 4096 {
			t.Skip()
		}
		first := classifySystemCommand(prefix)
		second := classifySystemCommand(prefix)
		if first != second {
			t.Fatalf("risk classification is nondeterministic: %+v != %+v", first, second)
		}
		forbidden := classifySystemCommand(prefix + "; Invoke-Expression 'Write-Output blocked'")
		if !forbidden.Forbidden || forbidden.Level != "forbidden" {
			t.Fatalf("indirect execution ceased to be forbidden for prefix %q: %+v", prefix, forbidden)
		}
	})
}
