# YOLO Object Counter for Edge AI

## Science

Real-time object detection and counting from camera sensors is foundational
to urban analytics, wildlife monitoring, traffic engineering, and
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
