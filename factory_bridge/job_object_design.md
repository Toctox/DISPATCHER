# Windows process-tree containment

The Windows managed runner creates each child process suspended, assigns it to a fresh Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and resumes it only after successful assignment. Context cancellation or timeout terminates the Job Object, so descendants are terminated with the mission process tree.

This branch must not merge until the runtime runner is wired to `runManagedProcess` and the Windows integration test proves a spawned descendant does not survive cancellation.
