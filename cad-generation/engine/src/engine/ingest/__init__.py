"""Reading facts back out of the deterministic subsystems the engine invokes.

The pure transforms live in the submodules and take already-parsed data; the
thin `from_*_dir` helpers are the only places a file is opened, so §12
non-negotiable #7 ("the engine has zero I/O") still holds where it matters —
`evaluate()` and everything it calls remain a function of the IR alone.
"""
