# Testing

Run the Python checks from the project folder:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The macOS smoke test opens the native app and replaces its input, so only use a
disposable encrypted JPEG copy:

```sh
DJVU_RECOVER_SAMPLE="/path/to/disposable-photo.jpg.kook" ./macos/test-app.sh
```

The suite makes its own disposable copies under `ui-tests/`. It checks successful
recovery, nested folders, unsupported input, cancellation, filename conflicts,
and a mixed batch. The source sample itself is read only. Generated reports and
screenshots remain local and are excluded from Git.

## Safety and continuation coverage

The regression suite covers default original preservation, explicit deletion, rollback, interrupted-job continuation, retrying only failed files, and once-per-batch dependency messages. The CLI writes local checkpoints and preserves sources. Native smoke tests use disposable inputs and check that originals remain unchanged.

The six additional suffixes have synthetic coverage only. No genuine samples for those variants were available for this update; do not describe them as verified. User media and reports must never be included in commits or release packages.

Signing script credential checks can be exercised without credentials; Developer ID signing and Apple notarization cannot be validated until a membership and certificate are available.
