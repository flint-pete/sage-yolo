# Building and Testing the YOLO Plugin Docker Image

How to build the Docker image, test it locally, and deploy it to
a Sage node.


## Prerequisites

- A build machine with internet access, Docker, and an NVIDIA GPU
- SSH access to a Sage Thor node (for on-node testing)
- NVIDIA Container Toolkit configured for Docker (see below)

### NVIDIA Container Toolkit Setup

Docker needs the NVIDIA Container Toolkit to pass GPUs into
containers. The toolkit may already be **installed** but not
**configured** — both steps are required.

**Check if it's already working:**

```bash
docker run --rm --runtime=nvidia nvidia/cuda:12.9.0-base-ubuntu24.04 nvidia-smi
```

If that prints your GPU info, you're set. If it fails:

```bash
# Step 1: Install (if not already)
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

# Step 2: Configure Docker to use the nvidia runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Step 3: Verify
docker info | grep -i runtime
#  Runtimes: runc io.containerd.runc.v2 nvidia   ← nvidia must appear
```

This is a one-time setup per machine.


## Base Image

The Dockerfile uses `nvcr.io/nvidia/pytorch:25.08-py3`:

| Component | Version |
|-----------|---------|
| CUDA | 13.0 |
| PyTorch | 2.8 |
| Python | 3.12 |
| Ubuntu | 24.04 |
| Min driver | R575+ |

This image supports both Blackwell GPU variants natively:
- **DGX Spark** (GB10, sm_121)
- **Thor nodes** (NVIDIA Thor / Jetson Thor, sm_110)

Previous base image (25.04-py3, CUDA 12.9) lacked sm_110 cubins
and would fail on Thor with "CUDA capability sm_110 is not
compatible" warnings.


## Building the Image

### Option A: Build on a Machine with Internet (DGX Spark)

```bash
cd ~/sage-yolo
docker build --no-cache -t yolo-object-counter:0.2.0 .
```

Then transfer to Thor (see "Transfer to Thor" below).

### Option B: Build Directly on Thor

If the Thor node has outbound internet access, you can clone
and build directly — no transfer step needed:

```bash
# One-time setup
git clone https://github.com/flint-pete/sage-yolo.git ~/sage-yolo

# Build (sudo required for Docker on Thor)
cd ~/sage-yolo
sudo docker build --no-cache -t yolo-object-counter:0.2.0 .
```

To rebuild after code changes:

```bash
cd ~/sage-yolo
git pull
sudo docker build --no-cache -t yolo-object-counter:0.2.0 .
```

This is the fastest iteration loop — edit code, `git push` from
your dev machine, `git pull && sudo docker build` on Thor.

Build time: ~5 minutes (the YOLO model is downloaded during build).

### Dockerfile: OpenCV Fix

The Dockerfile includes this fix to use `opencv-python-headless`
(no GUI) instead of the base image's `opencv-python`:

```dockerfile
RUN pip uninstall -y opencv-python opencv-python-headless 2>/dev/null; \
    rm -rf /usr/local/lib/python3.*/dist-packages/cv2* && \
    pip install --no-cache-dir opencv-python-headless>=4.8.0
```

The `rm -rf cv2*` clears stale files that `pip uninstall`
sometimes leaves behind.


## Testing the Image Locally

Before deploying, verify the image works with GPU:

### Quick sanity check

```bash
# Verify the image exists and app.py runs
sudo docker run --rm --runtime=nvidia yolo-object-counter:0.2.0 --help
```

### Batch test with test images

Run against the committed test images — same as the QA test,
but inside Docker:

```bash
mkdir -p ~/yolo-test-output

sudo docker run --rm --runtime=nvidia \
    -e PYWAGGLE_LOG_DIR=/output \
    -v ~/yolo-test-output:/output \
    -v ~/sage-yolo/tests/test-images:/images:ro \
    yolo-object-counter:0.2.0 \
    --image-dir /images --continuous N

# Check results
cat ~/yolo-test-output/data.ndjson | python3 -m json.tool
ls -la ~/yolo-test-output/uploads/
```

### Test with an RTSP camera (e.g. Reolink)

```bash
mkdir -p ~/yolo-camera-test

sudo docker run --rm --runtime=nvidia \
    -e PYWAGGLE_LOG_DIR=/output \
    -v ~/yolo-camera-test:/output \
    yolo-object-counter:0.2.0 \
    --stream "rtsp://admin:PASSWORD@CAMERA_IP:554/h264Preview_01_sub" \
    --interval 30 --continuous Y

# In another terminal, watch results:
tail -f ~/yolo-camera-test/data.ndjson
ls -la ~/yolo-camera-test/uploads/
```

Use the sub stream (`h264Preview_01_sub`, 640x360) rather than
the main stream — YOLO resizes to 640px anyway, so 4K frames
waste bandwidth.

Press Ctrl-C to stop.

### Test with an HTTP snapshot camera

For cameras behind a port-mapped router (e.g. Reolink with
only HTTP port forwarded, no RTSP):

```bash
mkdir -p ~/yolo-camera-test

# One-shot test (--continuous N)
sudo docker run --rm --runtime=nvidia \
    -e PYWAGGLE_LOG_DIR=/output \
    -v ~/yolo-camera-test:/output \
    yolo-object-counter:0.2.0 \
    --snapshot-url "http://IP:PORT/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=snap&user=USER&password=PASS&width=640&height=360" \
    --continuous N

# Check results
cat ~/yolo-camera-test/data.ndjson
ls -la ~/yolo-camera-test/uploads/
```

The `&width=640&height=360` parameters request a low-resolution
snapshot (~12KB vs ~445KB at full 4K). This saves significant
bandwidth on LTE-connected cameras while giving YOLO exactly
the resolution it needs (it resizes to 640px anyway).

To verify the camera is reachable before running YOLO:

```bash
curl -o /tmp/test.jpg "http://IP:PORT/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=test&user=USER&password=PASS"
file /tmp/test.jpg   # Should say "JPEG image data"
```


## Transfer to Thor (Option A only)

If you built on DGX Spark (not on Thor), transfer the image:

```bash
# On the build machine
docker save yolo-object-counter:0.2.0 | gzip > /tmp/yolo-object-counter.tar.gz
scp /tmp/yolo-object-counter.tar.gz beckman@thor-node:~/

# On Thor — load into Docker
sudo docker load < ~/yolo-object-counter.tar.gz
```


## Deploy via pluginctl (Sage Workflow)

For running on a Thor node with the Sage infrastructure:

### Step 1: Import the image into k3s

pluginctl uses k3s/containerd, not Docker. The image must be
imported even if it was built locally with Docker:

```bash
sudo docker save yolo-object-counter:0.2.0 | sudo k3s ctr images import -
```

This takes ~6 minutes for the full image. Verify:

```bash
sudo k3s ctr images ls | grep yolo
```

### Step 2: Deploy

**With an RTSP camera (named or URL):**

```bash
sudo pluginctl deploy -n yolo-hummingcam \
    --resource 'memory=8Gi,limit.memory=16Gi' \
    docker.io/library/yolo-object-counter:0.2.0 \
    -- --stream bottom_camera --interval 60 --continuous Y
```

**With an HTTP snapshot camera (e.g. Reolink via port-mapped router):**

```bash
sudo pluginctl deploy -n yolo-hummingcam \
    --resource 'memory=8Gi,limit.memory=16Gi' \
    docker.io/library/yolo-object-counter:0.2.0 \
    -- --snapshot-url "http://IP:PORT/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=snap&user=USER&password=PASS&width=640&height=360" \
       --interval 60 --continuous Y --upload-image Y
```

The `&width=640&height=360` fetches the sub-stream resolution
(~12KB vs ~445KB at 4K) — YOLO resizes to 640px anyway.
Critical for LTE-connected cameras.

**Important:** The `--resource 'memory=8Gi,limit.memory=16Gi'`
flag is required. Without it, the default k3s memory limit is
too low for YOLO11x and the pod gets OOMKilled (exit code 137).

### Step 3: Monitor

```bash
# Check the pod is running
sudo pluginctl ps

# Watch logs (live inference output)
sudo pluginctl logs yolo-hummingcam

# Follow logs continuously (Ctrl-C to stop watching)
sudo pluginctl logs -f yolo-hummingcam

# Check pod status (Running, Failed, etc.)
sudo kubectl get pod yolo-hummingcam
```

Note: `pluginctl logs` requires `sudo` on Thor (k3s kubeconfig
is root-only). If the pod shows `Failed`, check for OOMKilled:

```bash
sudo kubectl get pod yolo-hummingcam -o jsonpath='{.status.containerStatuses[0].state}' && echo ''
```

### Step 4: Stop

```bash
sudo pluginctl rm yolo-hummingcam
```

### Step 5: Rebuild and redeploy (after code changes)

```bash
cd ~/sage-yolo
git pull
sudo docker build -t yolo-object-counter:0.2.0 .

# If only app.py changed, skip --no-cache (uses cached layers, ~5 seconds)
# If Dockerfile or requirements.txt changed, use --no-cache

# Re-import into k3s
sudo docker save yolo-object-counter:0.2.0 | sudo k3s ctr images import -

# Remove old deployment and redeploy
sudo pluginctl rm yolo-hummingcam
sudo pluginctl deploy -n yolo-hummingcam \
    --resource 'memory=8Gi,limit.memory=16Gi' \
    docker.io/library/yolo-object-counter:0.2.0 \
    -- --snapshot-url "..." --interval 60 --continuous Y --upload-image Y
```


## Production: Scheduled SES Cron Jobs on Thor (arm64)

This is the production deployment path — a scheduler-managed one-shot
cron job (every 10 min) instead of a hand-deployed continuous pod. It
replaces the `pluginctl deploy ... --continuous Y` approach above, which
pins the GPU/RAM 24/7, dies on reboot, and is invisible to the scheduler.

### Why the normal ECR portal build does NOT work for this plugin

The documented Sage workflow is "Create App → Register and Build App" and
the ECR portal builds the image from your GitHub repo. **That build fails
for any arm64 plugin on the NVIDIA base image**, and here is why:

- The ECR/Jenkins build pipeline runs on **x86_64** hardware.
- To produce a `linux/arm64` image it cross-builds under **QEMU emulation**.
- The NVIDIA base (`nvcr.io/nvidia/pytorch:25.08-py3`) contains aarch64
  binaries that QEMU cannot emulate; the `pip install` step crashes with
  `qemu: uncaught target signal 6 (Aborted) - core dumped`, build exit 134.

So the portal build is a dead end for Thor-targeted NVIDIA plugins until
the ECR pipeline gets a **native arm64 builder**.

### Why `docker push` to the registry also does NOT work (yet)

You might think: build natively on Thor (arm64, no QEMU), then push to
`registry.sagecontinuum.org`. The build succeeds, but the push is denied:

```
denied: requested access to the resource is denied
```

`docker login registry.sagecontinuum.org` with a Sage portal access token
**authenticates** (login succeeds) but the token is **read/pull-only** — it
lacks push/write scope to the `beckman` namespace. Registry writes are
reserved for the Jenkins build pipeline. Getting push access (or a native
arm64 builder) is an ECR-team request — see "Systemic fix" below.

### The working workaround: build locally + sideload into k3s

Because SES pods on Thor use **`imagePullPolicy: IfNotPresent`**, the
scheduler will use a locally-cached image if one is already present in k3s
containerd under the exact registry-qualified name — it never has to pull
from the registry. So we build natively on Thor, tag with the full
registry path, and import it straight into k3s. No registry push needed.

**Step 1 — build natively on Thor (arm64, no QEMU):**

```bash
cd ~/sage-yolo
git pull
sudo docker build -t registry.sagecontinuum.org/beckman/yolo-object-counter:0.2.0 .
```

Note the tag is the **full registry path**, not the bare
`yolo-object-counter:0.2.0`. This must exactly match the `image:` field in
the job YAML so k3s finds the cached copy.

**Step 2 — sideload into k3s containerd:**

```bash
sudo docker save registry.sagecontinuum.org/beckman/yolo-object-counter:0.2.0 \
  | sudo k3s ctr images import -
```

**Step 3 — verify it landed (and is CRI-managed):**

```bash
sudo k3s ctr images ls | grep yolo-object-counter
# Expect a line tagged registry.sagecontinuum.org/beckman/yolo-object-counter:0.2.0
# with io.cri-containerd.image=managed  (that label = k8s/SES can see it)
```

**Step 4 — register the app in the ECR portal (metadata only).** The app
must exist in the ECR *catalog* so the SES scheduler's validation passes
(SES checks the app catalog, not the raw Docker registry). The portal
*build* will fail (QEMU) — that's fine, we only need the app + version
record registered. Make the app **public** or SES returns
`registry does not exist in ECR`.

**Step 5 — create + submit the SES cron job** (needs a write-scoped SES
token in your interactive shell; see jobs/yolo-hummingcam-h00f.yaml):

```bash
sesctl --server https://es.sagecontinuum.org --token "$SES_USER_TOKEN" \
    create -f jobs/yolo-hummingcam-h00f.yaml      # returns a numeric job ID
sesctl --server https://es.sagecontinuum.org --token "$SES_USER_TOKEN" \
    submit -j <job-id>
```

**Step 6 — verify it fires and publishes.** The pod appears in the `ses`
namespace each tick, runs ~30-40s, exits (one-shot), and is GC'd — so it's
invisible between ticks. Confirm via the data API instead:

```bash
curl -s -X POST https://data.sagecontinuum.org/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"start":"-15m","filter":{"vsn":"H00F","name":"env.count.total"}}'
```

The proof it's the SES job (not a leftover hand-deployed pod) is in the
record metadata: `"job": "yolo-object-counter-<id>"` and
`"plugin": "registry.sagecontinuum.org/beckman/yolo-object-counter:0.2.0"`
("already present on machine" in the pod events confirms the sideload hit).

### Re-deploying after a code change (new version)

Bump the version everywhere (sage.yaml, Makefile, job YAML), then repeat
build → sideload with the new tag. Because the tag changes, k3s pulls the
new local image on the next tick automatically; no job re-submit needed if
the job YAML already points at the new tag (otherwise update + re-submit).

### Systemic fix (escalate to the ECR/cyberinfra team)

The sideload workaround is manual and per-node. The durable fix is one of:

- **(a)** Grant push/write access to `registry.sagecontinuum.org/beckman/`
  for a Sage portal token, so `docker push` works after a native Thor build; or
- **(b)** Add a **native arm64 build node** to the Jenkins ECR pipeline so
  the portal "Register and Build" path works without QEMU.

Either unblocks every Thor-targeted NVIDIA plugin (yolo, bioclip, birdnet)
and removes the manual sideload step entirely.

See: https://sagecontinuum.org/docs/tutorials/edge-apps/publishing-to-ecr


## Troubleshooting

**"unknown or invalid runtime name: nvidia"**
  → NVIDIA Container Toolkit not configured. Run:
  ```
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```

**"No CUDA GPUs are available" inside container**
  → Missing `--runtime=nvidia` flag on docker run.

**numpy.core.multiarray failed to import**
  → OpenCV fix didn't run. Rebuild with `--no-cache`.

**"permission denied" on docker commands (Thor)**
  → Use `sudo docker ...` — Thor's Docker socket is root-only.

**Image too large to transfer**
  → The image is ~8-10 GB compressed. Use a fast network, or
  build directly on Thor (Option B) to skip the transfer entirely.

**RTSP stream timeout or black frames**
  → Verify the camera is reachable: `ffprobe rtsp://admin:PASS@IP:554/...`
  → Check firewall rules between the container and camera network.
  → Try the sub stream instead of main stream.

**OOMKilled (exit code 137) via pluginctl**
  → Default k3s memory limits are too low for YOLO11x (~4-5GB GPU
  + several GB system memory). Add `--resource 'memory=8Gi,limit.memory=16Gi'`
  to the `pluginctl deploy` command.

**HTTP snapshot returns HTML instead of JPEG**
  → The URL path is wrong. For Reolink cameras, the snapshot
  endpoint is `/cgi-bin/api.cgi?cmd=Snap&channel=0&...`, not `/`.
  Check credentials and verify with curl first:
  `curl -o test.jpg "http://IP:PORT/cgi-bin/api.cgi?cmd=Snap&channel=0&rs=test&user=USER&password=PASS"`

**pluginctl logs says "image can't be pulled"**
  → The image exists in Docker but not in k3s containerd.
  Import it: `sudo docker save IMAGE | sudo k3s ctr images import -`
