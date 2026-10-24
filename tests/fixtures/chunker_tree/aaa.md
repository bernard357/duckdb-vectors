# Alpha

A top-level file whose name sorts before the `sub/` directory. Together with
`zebra.md` it brackets the subdirectory in sorted order, so the walk visits the
root directory, descends into `sub`, then returns to the root — the exact
interleaving that must still fire each directory's hooks only once.
