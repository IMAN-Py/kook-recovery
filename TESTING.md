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
