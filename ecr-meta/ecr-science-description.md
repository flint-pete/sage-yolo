# YOLO Object Counter for Edge AI

## Science

Real-time object detection and counting from camera sensors is foundational
to agriculture analytics, wildlife monitoring, traffic engineering, and
infrastructure management.  Traditional approaches require streaming raw
video to the cloud for post-hoc analysis — incurring high bandwidth costs,
latency, and privacy concerns.  By deploying state-of-the-art YOLO
(You Only Look Once) models directly on edge nodes, we perform inference in
milliseconds at the point of data collection, publishing only compact
measurement records (per-class counts and optional annotated images) to the
Sage data store.

## Model

This plugin ships **YOLO11x** — the largest variant in the Ultralytics v11
family (56.9 M parameters, 54.7 % mAP on COCO val2017).  YOLO11x provides
an excellent balance between accuracy and throughput for edge deployments on
GPU-equipped nodes.  Smaller variants (yolo11n, yolo11s) can be selected at
runtime via the `--model` flag to trade accuracy for speed.  The model
recognises all 80 COCO object classes and can be filtered at runtime to
count only specific classes of interest (e.g. `--classes person,car,bird`).

## Architecture

The plugin captures frames from any Waggle camera abstraction (named camera,
RTSP stream, or static image file) at a configurable interval, runs YOLO
inference on the GPU, draws bounding-box annotations on the image, and
publishes per-class counts via `plugin.publish()` and annotated images via
`plugin.upload_file()`.  A single iteration completes in under 50 ms on
NVIDIA Blackwell (GB10), leaving the GPU available for concurrent workloads.

## Runtime Modes

By default (`--continuous Y`) the plugin loops indefinitely, capturing and
publishing every `--interval` seconds.  With `--continuous N` it performs a
single shot and exits.

| Argument         | Default | Description                                                                 |
|------------------|---------|-----------------------------------------------------------------------------|
| `--continuous`   | `Y`     | `Y` = loop every `--interval` seconds; `N` = single-shot then exit.         |
| `--interval`     | `30`    | Seconds between captures (camera mode only).                                |
| `--max-runtime`  | `0`     | When in continuous mode, self-exit after this many seconds (`0` = run forever). Lets a scheduled job behave like one long bounded single-shot. Ignored when `--continuous N`. |

## Windowed GPU Sharing

Some edge nodes carry a **single GPU** that must be shared between multiple
always-on continuous plugins — which cannot truly co-run without contending
for GPU memory and compute.  The `--max-runtime` flag turns YOLO into a
*bounded* continuous job that occupies the GPU for a fixed window and then
voluntarily releases it.

For example, on a node where YOLO shares one GPU with the BioCLIP plugin, a
scheduler (cron) starts each plugin at a fixed minute and each runs a bounded
10-minute window:

- **:00 — YOLO** runs `--continuous Y --max-runtime 600 --interval 15`,
  sampling roughly every 15 s (~40 frames) for 10 minutes, then self-exits.
- **:20 — BioCLIP** takes the next window after a 10-minute guard-band that
  guarantees YOLO has fully exited and freed the GPU.

The 10-minute guard-bands between windows (e.g. :10–:20) prevent overlap from
slow model teardown, leaving the GPU free at the boundaries.  Total GPU
occupancy is roughly 20 minutes per hour, allowing both workloads to coexist
on one device without a dedicated GPU each.

> **Subtle behavior — `--max-runtime` is WALL-CLOCK, not inference time.** The
> timer starts when the process starts, so model load, the first camera
> connection, and any startup overhead all count against the window. YOLO's
> model is small (~seconds), so the effective sampling window is close to the
> full `--max-runtime`. But the practical implication is general: the actual
> time spent sampling is `--max-runtime` *minus* startup, not the full value.
> If you need a guaranteed amount of *inference* time, size `--max-runtime`
> above your target to absorb the cold start, and keep the guard-band wide
> enough that a slow start can't push the self-exit into the next plugin's
> window.

## Measurements Published

| Topic                     | Type  | Description                        |
|---------------------------|-------|------------------------------------|
| `env.count.<class_name>`  | int   | Count of each detected class       |
| `env.count.total`         | int   | Total objects detected in frame    |

Annotated JPEG images with bounding boxes are uploaded each cycle when
`--upload-image Y` is set.

## Example Use Cases

- **Urban traffic monitoring** — count vehicles, pedestrians, and cyclists
  at intersections every 30 seconds.
- **Bird counting at feeders** — `--classes bird --interval 60` on
  camera-equipped nodes near wildlife stations.
- **Parking occupancy** — count `car` in a fixed-view parking lot camera.
- **Construction site safety** — detect `person` in restricted zones.
