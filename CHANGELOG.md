# Changelog

All notable changes to the `yolo-object-counter` Sage plugin.

## 0.2.1 — 2026-06-22

### Added
- **`--max-runtime N` flag for windowed GPU sharing.** When combined with
  `--continuous Y`, the plugin loops every `--interval` seconds and then
  self-exits after N seconds — behaving like one long bounded single-shot.
  Default `0` = run forever (previous behavior, unchanged). This lets a single
  GPU be time-shared: on Thor (one GPU) YOLO runs a bounded 10-minute window at
  the top of each hour (`cronjob('0 * * * *')`, `--max-runtime 600 --interval 15`,
  ~40 frames) then frees the GPU for the BioCLIP plugin's :20 window, with
  10-minute guard-bands so the two never contend. ~20 min/hour total GPU use.

### Changed
- H00F hummingcam job converted to windowed mode and class-filtered to
  **`person,bird,fork`**. The `fork` class is a deliberate **sentinel**: a fork
  cannot occur naturally in the scene, so a fork detection unambiguously means a
  human placed one in-frame to demonstrate the trigger end-to-end.
- `DOCKER-BUILD.md` gained a 3-way Continuous / One-shot / Windowed decision
  table with the window-layout diagram.

## 0.2.0 and earlier

- See git history. Core: YOLO11x object counting, per-class
  `env.count.<class>` + `env.count.total` records, annotated-image upload,
  HTTP-snapshot and RTSP/camera sources.

---

### Deployment note (arm64 / Thor)

This plugin is built locally and **sideloaded** into the node's k3s containerd
(`docker save | sudo k3s ctr images import -`) because the ECR portal's arm64
NVIDIA-base build crashes under QEMU. The ECR **catalog** version is registered
separately via `scripts/register-ecr-version.py` (the metadata record SES
validates against). SES pods use `imagePullPolicy=IfNotPresent`, so the
sideloaded image serves the actual pull. See `DOCKER-BUILD.md` for the full
build → register → sideload → submit workflow.
