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


## Publish to Sage ECR (Production)

The Sage Edge Code Repository (ECR) is **not** a Docker registry.
You do not `docker push`. ECR pulls from GitHub and builds for you.

1. Go to https://portal.sagecontinuum.org
2. Sign in → My Apps → Create App
3. Enter: `https://github.com/flint-pete/sage-yolo`
4. ECR builds the image and assigns a registry tag:
   `registry.sagecontinuum.org/flint-pete/yolo-object-counter:0.2.0`

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
