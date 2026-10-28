# Third-party notices

## PhotoPhoenix

Required Notice: Copyright 2026 Ahmed Raza Bhatti (https://github.com/ahmed-raza-bhatti/photophoenix)

The files under `vendor/photophoenix/` (engine.py, fastjpeg.py, colour.py, video.py,
video_index.py, __init__.py) are taken from PhotoPhoenix and are licensed under the
**PolyForm Noncommercial License 1.0.0**:
<https://polyformproject.org/licenses/noncommercial/1.0.0>

The full licence text is in `vendor/photophoenix/LICENSE.txt`. In short: you may use, change
and share this software for any **non-commercial** purpose; commercial use (including
offering data-recovery services with it, or bundling it into a paid product) needs a separate
written agreement with the author. See the upstream `COMMERCIAL.md`.

### Changes made to the vendored copy

`engine.py`: the file marker was generalised - `MARKER_RE` and `has_marker()` accept any
STOP/Djvu filemarker of the form `{GUID}` instead of one hard-coded GUID; `split_file()`
uses them. `video.py` and `video_index.py` use the same helper for their trailer check.

`recover.py`, `README.md`, `run.bat`, `requirements.txt` in this folder are the launcher
written for this tool and are not part of PhotoPhoenix.
