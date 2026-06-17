# YOLO Object Counter — Sage/Waggle edge plugin
# Default model: YOLO11x (54.7% mAP COCO, 56.9M params)
# Target: 128GB unified memory ARM64 (DGX Spark / Sage Thor)
#
# Base image: NVIDIA PyTorch 25.04 — CUDA 12.9, PyTorch 2.7, Python 3.12
# Supports Blackwell GPUs (sm_120/sm_121) natively. Requires driver R575+.
# Previous base (24.06-py3) only supported up to sm_90 (Hopper).
FROM nvcr.io/nvidia/pytorch:25.04-py3

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# The NVIDIA base image may ship an opencv compiled against a different numpy.
# Fix: fully remove old opencv (pip uninstall + rm stale files), then
# install a fresh opencv-python-headless matching the current numpy.
RUN pip uninstall -y opencv-python opencv-python-headless 2>/dev/null; \
    rm -rf /usr/local/lib/python3.*/dist-packages/cv2* && \
    pip install --no-cache-dir opencv-python-headless>=4.8.0

# Pre-download default model weights into the image
# Layer order matters: model weights change rarely, app.py changes often.
# Putting the model download BEFORE COPY app.py means code edits don't
# invalidate the expensive (~130 MB) model download layer.
RUN python3 -c "from ultralytics import YOLO; YOLO('yolo11x.pt')"

COPY app.py .

ENTRYPOINT ["python3", "/app/app.py"]
