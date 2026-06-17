"""
YOLO Object Counter Plugin for Sage/Waggle
Captures frames from node cameras, runs YOLO11x inference (54.7% mAP COCO),
publishes per-class object counts and uploads annotated images.

Default model: yolo11x.pt (56.9M params, best accuracy in YOLO family).
Requires ~4-5 GB GPU memory at 1080p. Fits easily in 128GB unified memory
on DGX Spark / Sage Thor nodes.

Measurement topics:
  env.count.<class_name>   — integer count per detected class
  env.count.total          — total detections across all classes
  upload                   — annotated JPEG with bounding boxes
"""
import argparse
import logging
import os
import time
import tempfile

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from waggle.plugin import Plugin
from waggle.data.vision import Camera

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("yolo-object-counter")


# ── detector ────────────────────────────────────────────────────────
class YOLODetector:
    """Thin wrapper around Ultralytics YOLO for Sage plugins."""

    def __init__(self, model_name: str, conf_thres: float = 0.25, iou_thres: float = 0.45,
                 imgsz: int = 640, half: bool = False, max_det: int = 300,
                 augment: bool = False, agnostic_nms: bool = False):
        self.model_name = model_name
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.imgsz = imgsz
        self.half = half
        self.max_det = max_det
        self.augment = augment
        self.agnostic_nms = agnostic_nms
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading %s on %s (conf=%.2f, iou=%.2f, imgsz=%d)",
                     model_name, self.device, conf_thres, iou_thres, imgsz)
        self.model = YOLO(model_name)
        self.model.to(self.device)
        logger.info("Model loaded — %d classes available", len(self.model.names))

    def detect(self, frame: np.ndarray, target_classes: list[str] | None = None):
        """
        Run inference on a BGR numpy frame.
        Returns list of dicts: [{class, confidence, bbox:[x1,y1,x2,y2]}, ...]
        """
        results = self.model(
            frame,
            conf=self.conf_thres,
            iou=self.iou_thres,
            imgsz=self.imgsz,
            half=self.half,
            max_det=self.max_det,
            augment=self.augment,
            agnostic_nms=self.agnostic_nms,
            verbose=False,
        )
        detections = []
        for r in results:
            for box in r.boxes:
                cls_name = r.names[int(box.cls[0])]
                if target_classes and cls_name.lower() not in target_classes:
                    continue
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                detections.append({
                    "class": cls_name,
                    "confidence": float(box.conf[0]),
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                })
        return detections


def draw_boxes(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    """Draw bounding boxes + labels on a copy of the frame."""
    annotated = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        label = f"{det['class']} {det['confidence']:.2f}"
        color = (0, 255, 0)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw, y1), color, -1)
        cv2.putText(annotated, label, (x1, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    return annotated


# ── image sources ────────────────────────────────────────────────────

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def iter_image_dir(directory: str):
    """
    Yield (image_path, frame_bgr, timestamp_ns) for every image in a
    directory.  Used for local testing without a live camera.
    """
    from pathlib import Path

    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Image directory not found: {directory}")

    files = sorted(
        p for p in dir_path.iterdir()
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()
        and not p.name.startswith(".")
    )
    if not files:
        raise FileNotFoundError(
            f"No image files found in {directory}. "
            f"Supported extensions: {', '.join(sorted(IMAGE_EXTENSIONS))}"
        )

    logger.info("Found %d test images in %s", len(files), directory)
    for img_path in files:
        frame = cv2.imread(str(img_path))
        if frame is None:
            logger.warning("Skipping unreadable file: %s", img_path.name)
            continue
        yield str(img_path), frame, time.time_ns()


# ── main loop ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="YOLO Object Counter for Sage",
        epilog="""
Examples:
  # Normal mode — capture from camera on a Sage node
  python3 app.py --stream bottom_camera --classes bird --interval 60

  # Local testing — detect objects in all images in a directory
  export PYWAGGLE_LOG_DIR=./test-output
  python3 app.py --image-dir ./test-images --continuous N

  # Local testing — single image via --stream (legacy)
  python3 app.py --stream /path/to/photo.jpg --continuous N

  # Filter to specific COCO classes
  python3 app.py --image-dir ./test-images --classes "person,car,truck" --continuous N
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stream", default="bottom_camera",
                        help="Camera stream name or RTSP URL (ignored if --image-dir is set)")
    parser.add_argument("--image-dir", default=None,
                        help="Directory of test images (replaces camera input for local testing)")
    parser.add_argument("--model", default="yolo11x.pt",
                        help="YOLO model name/path (e.g. yolo11x.pt, yolov8x.pt, yolo11n.pt)")
    parser.add_argument("--interval", type=int, default=30,
                        help="Seconds between captures (camera mode only)")
    parser.add_argument("--conf-thres", type=float, default=0.25,
                        help="Confidence threshold (0.0-1.0, default: 0.25)")
    parser.add_argument("--iou-thres", type=float, default=0.45,
                        help="IoU threshold for NMS (0.0-1.0, default: 0.45)")
    parser.add_argument("--imgsz", type=int, default=640,
                        help="Input image size for inference — images are resized to this "
                             "before YOLO processes them (default: 640). Larger values "
                             "detect smaller objects but use more GPU memory and are slower. "
                             "See: https://docs.ultralytics.com/modes/predict/#inference-arguments")
    parser.add_argument("--half", action="store_true",
                        help="Use FP16 half-precision inference (faster, slightly less accurate). "
                             "See: https://docs.ultralytics.com/modes/predict/#inference-arguments")
    parser.add_argument("--max-det", type=int, default=300,
                        help="Maximum detections per image (default: 300). Lower this if you "
                             "only expect a few objects per frame.")
    parser.add_argument("--augment", action="store_true",
                        help="Enable test-time augmentation (TTA) — runs inference at multiple "
                             "scales/flips for better accuracy at the cost of ~3x slower speed. "
                             "See: https://docs.ultralytics.com/modes/predict/#inference-arguments")
    parser.add_argument("--agnostic-nms", action="store_true",
                        help="Class-agnostic NMS — treats all classes as one during NMS. "
                             "Useful when overlapping objects of different classes cause duplicates.")
    parser.add_argument("--classes", default="",
                        help="Comma-separated classes to count (empty = all)")
    parser.add_argument("--continuous", default="Y",
                        help="Y = loop, N = single-shot")
    parser.add_argument("--upload-image", default="Y",
                        help="Y = upload annotated image each cycle")
    args = parser.parse_args()

    target_classes = None
    if args.classes:
        target_classes = [c.strip().lower() for c in args.classes.split(",")]
        logger.info("Filtering to classes: %s", target_classes)

    detector = YOLODetector(args.model, args.conf_thres, args.iou_thres,
                            imgsz=args.imgsz, half=args.half,
                            max_det=args.max_det, augment=args.augment,
                            agnostic_nms=args.agnostic_nms)

    # ── Choose image source ──────────────────────────────────────────
    using_image_dir = args.image_dir is not None

    if using_image_dir:
        # Local testing mode: read images from a directory
        image_source = iter_image_dir(args.image_dir)
        source_label = f"image-dir:{args.image_dir}"
    else:
        # Production mode: capture from camera
        camera = Camera(args.stream)
        source_label = args.stream

    with Plugin() as plugin:
        logger.info("Plugin started — source=%s, interval=%ds, model=%s",
                     source_label, args.interval, args.model)

        if not using_image_dir:
            logger.info("Capture interval: %ds", args.interval)

        while True:
            try:
                if using_image_dir:
                    # Get next image from directory iterator
                    try:
                        img_path, frame, timestamp = next(image_source)
                    except StopIteration:
                        logger.info("All test images processed")
                        break
                    source_name = os.path.basename(img_path)
                    logger.info("Processing: %s (%dx%d)",
                                source_name, frame.shape[1], frame.shape[0])
                else:
                    sample = camera.snapshot()
                    frame = sample.data  # numpy BGR
                    timestamp = sample.timestamp
                    source_name = args.stream

                detections = detector.detect(frame, target_classes)

                # Aggregate counts per class
                counts: dict[str, int] = {}
                for det in detections:
                    counts[det["class"]] = counts.get(det["class"], 0) + 1

                # Publish counts
                for cls_name, count in counts.items():
                    # Sanitize class name for pywaggle topic (a-z0-9_ only)
                    safe_name = cls_name.replace(" ", "_").replace("-", "_")
                    topic = f"env.count.{safe_name}"
                    plugin.publish(
                        topic, count,
                        timestamp=timestamp,
                        meta={"camera": source_name, "model": args.model},
                    )
                    logger.info("Published %s = %d", topic, count)

                # Publish total
                plugin.publish(
                    "env.count.total",
                    sum(counts.values()),
                    timestamp=timestamp,
                    meta={"camera": source_name, "model": args.model},
                )

                # Upload annotated image
                if args.upload_image == "Y" and detections:
                    annotated = draw_boxes(frame, detections)
                    stem = os.path.splitext(source_name)[0]
                    tmp_path = os.path.join(tempfile.gettempdir(),
                                            f"{stem}-annotated.jpg")
                    cv2.imwrite(tmp_path, annotated)
                    plugin.upload_file(tmp_path, timestamp=timestamp,
                                       meta={"camera": source_name,
                                             "detections": str(len(detections))})
                    os.unlink(tmp_path)
                    logger.info("Uploaded annotated image (%d detections)", len(detections))

                if not detections:
                    logger.info("No detections this cycle")

            except Exception:
                logger.exception("Inference error")

            if args.continuous != "Y" and not using_image_dir:
                break
            if not using_image_dir:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
