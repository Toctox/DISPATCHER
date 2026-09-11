# Windows process-tree containment

The production `osRunner` delegates to the platform-managed runner. On Windows, each child process is created suspended, assigned to a fresh Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and resumed only after successful assignment. Context cancellation or timeout terminates the Job Object, so descendants are terminated with the mission process tree.

The Windows integration test spawns a descendant process, forces a timeout, and verifies that the descendant does not survive. The platform-neutral implementation preserves the prior `exec.CommandContext` behavior outside Windows.
