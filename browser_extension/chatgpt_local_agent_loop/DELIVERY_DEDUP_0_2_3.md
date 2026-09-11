# Local Agent Loop 0.2.3 delivery deduplication

Adds a per-request delivery claim in the ChatGPT page DOM so concurrent content-script instances cannot post the same `LOCAL_AGENT_RESULT_V1` twice. The execution protocol remains 0.2.2; this patch only hardens result delivery.
