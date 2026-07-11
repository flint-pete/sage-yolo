# Changelog

All notable changes to the `yolo-object-counter` Sage plugin.

## 0.3.1 — 2026-07-10

### Changed
- **Docs/version bookkeeping only — deploy path UNCHANGED (still side-load).**
  The CI team fixed the buildkit `/proc/acpi` runc bug (Infra #2), so `RUN` steps
  now start. BUT this NVIDIA-base plugin STILL cannot build in the ECR portal:
  the pipeline cross-builds `linux/arm64` under QEMU on x86, and the NVIDIA CUDA
  base crashes with `qemu: uncaught target signal 6 (Aborted)` / exit 134 during
  `pip` (Infra #3 — a native arm64 builder does NOT yet exist; verified by the
  failed ECR build of this exact tag, 2026-07-10). So yolo continues to deploy by
  building natively on Thor and side-loading into k3s. Version bumped + image refs
  normalized to `beckman/…:0.3.1`; no plugin code change.

## 0.3.0 — 2026-06-24

### Added
- **`--save-match`: class-aware image saving, decoupled from publishing.**
  The annotated frame is now uploaded only when a detection matches a
  user-supplied OR-list of `Class:confidence` rules (e.g. `"bird:0.5,cat:0.6"`).
  A frame is saved when ANY detection matches ANY rule. Class matching is
  case-insensitive and EXACT against the COCO class name. The wildcard `"*:0.5"`
  saves any frame with a detection ≥0.5. Implemented via the shared
  `save_match.py` helper (29 unit tests, identical copy to bioclip/birdnet).

### Changed
- **Image saving is now selective when `--save-match` is set**, replacing the
  upload-every-cycle behavior. Counts (`env.count.*`) and the `env.count.total`
  heartbeat still publish every cycle regardless. Upload meta now also carries
  `top_class` and `confidence`.
- **`--upload-image` is now a deprecated back-compat gate.** With `--save-match`
  omitted, behavior is unchanged: `--upload-image Y` uploads every cycle that has
  detections (legacy), `N` never uploads. When `--save-match` is provided it takes
  precedence and `--upload-image` is ignored.

### Migration
- To save selectively, add `--save-match` (e.g. `"bird:0.5"` or `"*:0.4"`).
  Omitting it keeps the previous upload-every-cycle behavior via `--upload-image`.

## 0.2.2 — 2026-06-23

### Added
- **Standard `plugin.duration.*` performance telemetry** (matching
  `avian-diversity-monitoring` / TAFT-node convention). Each cycle publishes
  nanosecond phase timings via pywaggle's `plugin.timeit`:
  `plugin.duration.loadmodel` (model load + device move, once),
  `plugin.duration.input` (snapshot/capture + decode, per cycle),
  `plugin.duration.inference` (YOLO detection, per cycle). Makes cold-start cost
  and per-cycle latency observable from the data plane and doubles as a liveness
  signal on empty scenes. Model load refactored into a `load()` method so it can
  be timed inside the Plugin context.

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

This NVIDIA-base plugin is built natively on Thor and **side-loaded** into the
node's k3s containerd (`docker save | sudo k3s ctr images import -`), because the
ECR portal build still fails: it cross-builds `linux/arm64` under QEMU on x86 and
the CUDA base crashes (`signal 6` / exit 134). The buildkit `/proc/acpi` bug
(Infra #2) is fixed, but the QEMU-on-NVIDIA crash (Infra #3) is NOT — no native
arm64 builder exists yet (verified 2026-07-10). The ECR **catalog** version is
registered separately via `scripts/register-ecr-version.py` (metadata SES
validates against); SES pods use `imagePullPolicy=IfNotPresent`, so the
side-loaded image serves the pull. See `DOCKER-BUILD.md` for the full
build → register → side-load → submit workflow.
