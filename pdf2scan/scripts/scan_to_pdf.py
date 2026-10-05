#!/usr/bin/env python3
"""Convert photographed or PDF pages to a clean scan-style raster PDF."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def natural_key(path: Path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", path.name)]


def collect_inputs(paths: list[Path], output: Path) -> list[Path]:
    result = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted((p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES | {".pdf"}), key=natural_key))
        elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES | {".pdf"}:
            result.append(path)
        else:
            raise ValueError(f"Unsupported or missing input: {path}")
    result = [p for p in result if p.resolve() != output.resolve()]
    if not result:
        raise ValueError("No supported input files found")
    return result


def load_pages(path: Path, dpi: int):
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as document:
            page_count = len(document)
        poppler = shutil.which("pdftoppm")
        if poppler:
            # Capture the final page appearance as pixels before scan styling.
            # Poppler's default page rendering retains filled fields but omits
            # an empty signature widget's interactive "SIGN" affordance.
            with tempfile.TemporaryDirectory(prefix="pdf2scan-") as folder:
                for index in range(1, page_count + 1):
                    prefix = Path(folder) / f"page-{index}"
                    command = [poppler, "-f", str(index), "-l", str(index), "-singlefile", "-r", str(dpi), "-png", str(path), str(prefix)]
                    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                    screenshot = prefix.with_suffix(".png")
                    if result.returncode != 0 or not screenshot.is_file():
                        message = result.stderr.decode("utf-8", errors="replace").strip()
                        raise RuntimeError(f"Could not capture PDF page {index}: {message}")
                    with Image.open(screenshot) as image:
                        rgb = np.asarray(image.convert("RGB"))
                    yield f"{path.name}#{index}", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy(), True
            return
        print("pdftoppm unavailable; using PyMuPDF page capture", file=sys.stderr)
        with pymupdf.open(path) as document:
            for index, page in enumerate(document, 1):
                # Fallback renderer: remove only empty signature prompts from
                # the in-memory page, while keeping populated form appearances.
                for widget in list(page.widgets() or []):
                    if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and not widget.field_value:
                        value_type, _ = document.xref_get_key(widget.xref, "V")
                        if value_type == "null":
                            page.delete_widget(widget)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), colorspace=pymupdf.csRGB, alpha=False)
                rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
                yield f"{path.name}#{index}", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy(), True
    else:
        with Image.open(path) as source:
            frame = ImageOps.exif_transpose(source).convert("RGB")
            yield path.name, cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR), False


def order_quad(points: np.ndarray) -> np.ndarray:
    # Works for convex, roughly rectangular contours; manual quads are already ordered.
    points = np.asarray(points, np.float32).reshape(4, 2)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).ravel()
    indices = [int(np.argmin(sums)), int(np.argmin(diffs)), int(np.argmax(sums)), int(np.argmax(diffs))]
    if len(set(indices)) != 4:
        raise ValueError("Could not order automatic page corners; provide --quads")
    return points[indices]


def auto_quad(image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    scale = min(1.0, 1400 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    # Paper and pale print tend to be brighter/less saturated than the table.
    mask = (((gray > 155) & (hsv[:, :, 1] < 95)) | (gray > 205)).astype(np.uint8) * 255
    side = max(9, int(max(small.shape[:2]) * 0.025) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((side, side), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((max(5, side // 3),) * 2, np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < gray.size * 0.15:
        return None
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    quad = None
    for epsilon in (0.025, 0.035, 0.045, 0.055, 0.07, 0.085):
        candidate = cv2.approxPolyDP(hull, epsilon * perimeter, True).reshape(-1, 2)
        if len(candidate) == 4:
            quad = candidate
            break
    if quad is None:
        return None
    try:
        ordered = order_quad(quad / scale)
        # A contour merged with the table often reaches the photo boundary.
        # Require an explicit crop decision instead of silently using it.
        margin_x, margin_y = width * 0.005, height * 0.005
        if (np.any(ordered[:, 0] <= margin_x)
                or np.any(ordered[:, 0] >= width - margin_x)
                or np.any(ordered[:, 1] <= margin_y)
                or np.any(ordered[:, 1] >= height - margin_y)):
            return None
        return ordered
    except ValueError:
        return None


def validate_quad(raw, shape):
    quad = np.asarray(raw, dtype=np.float32)
    if quad.shape != (4, 2) or not np.isfinite(quad).all():
        raise ValueError("Each page quadrilateral needs four finite [x,y] points")
    height, width = shape[:2]
    if np.any(quad[:, 0] < 0) or np.any(quad[:, 0] > width) or np.any(quad[:, 1] < 0) or np.any(quad[:, 1] > height):
        raise ValueError(f"Page corners must fit within source image {width}x{height}")
    if (not cv2.isContourConvex(quad)
            or cv2.contourArea(quad) < width * height * 0.01):
        raise ValueError("Page quadrilateral is too small or has incorrect point order")
    return quad


def warp_page(image: np.ndarray, quad: np.ndarray, max_dimension: int) -> np.ndarray:
    tl, tr, br, bl = quad
    out_width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    out_height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if min(out_width, out_height) < 20:
        raise ValueError("Rectified page would be too small")
    scale = min(1.0, max_dimension / max(out_width, out_height))
    out_width, out_height = max(1, round(out_width * scale)), max(1, round(out_height * scale))
    destination = np.float32([[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]])
    matrix = cv2.getPerspectiveTransform(quad, destination)
    return cv2.warpPerspective(image, matrix, (out_width, out_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def refine_page_corners(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Snap approximate page sides to continuous photo edges before rectifying."""
    height, width = image.shape[:2]
    scale = min(1.0, 1400 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA) if scale < 1 else image
    points = quad.astype(np.float32) * scale
    lab = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
                           .astype(np.float32), (0, 0), 1.7)
    gradient_x = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3)
    fitted = []
    for index in range(4):
        start, end = points[index], points[(index + 1) % 4]
        direction = end - start
        length = float(np.linalg.norm(direction))
        normal = np.array([-direction[1], direction[0]], np.float32) / length
        positions = np.linspace(0.08, 0.92, 80, dtype=np.float32)
        centers = start + positions[:, None] * direction
        radius = max(6, round(length * 0.055))
        offsets = np.arange(-radius, radius + 1, dtype=np.float32)
        sample_x = np.float32(centers[:, None, 0] + offsets[None, :] * normal[0])
        sample_y = np.float32(centers[:, None, 1] + offsets[None, :] * normal[1])
        strength = np.zeros(sample_x.shape, np.float32)
        for channel in range(3):
            gx = cv2.remap(gradient_x[:, :, channel], sample_x, sample_y,
                           cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            gy = cv2.remap(gradient_y[:, :, channel], sample_x, sample_y,
                           cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            strength += np.abs(gx * normal[0] + gy * normal[1])
        strength = cv2.GaussianBlur(strength, (5, 1), 0)
        best_score, best_offsets = -np.inf, (0.0, 0.0)
        candidates = np.arange(-radius, radius + 1, 3)
        for first in candidates:
            for last in candidates:
                if abs(last - first) > max(12, length * 0.045):
                    continue
                selected = np.clip(np.rint((1 - positions) * first
                                           + positions * last + radius).astype(int),
                                   0, len(offsets) - 1)
                values = strength[np.arange(len(positions)), selected]
                score = (np.sort(values)[len(values) // 4:].mean()
                         - 0.08 * (abs(first) + abs(last))
                         - 0.15 * abs(first - last))
                if score > best_score:
                    best_score, best_offsets = score, (first, last)
        first, last = best_offsets
        fitted.append((start + first * normal, end + last * normal))

    def intersection(left, right):
        a, b = left
        c, d = right
        matrix = np.column_stack((b - a, c - d))
        if abs(np.linalg.det(matrix)) < 1e-5:
            return c
        return a + np.linalg.solve(matrix, c - a)[0] * (b - a)

    candidate = np.float32([intersection(fitted[(i - 1) % 4], fitted[i])
                            for i in range(4)]) / scale
    # Nearby print can be stronger than a faint paper border. Do not allow an
    # edge estimate to move a corner far from its initial page outline.
    limit = 0.022 * np.hypot(width, height)
    delta = candidate - quad
    distances = np.linalg.norm(delta, axis=1)
    candidate = np.float32(quad + delta * np.minimum(
        1, limit / np.maximum(distances, 1))[:, None])
    if (not np.isfinite(candidate).all()
            or np.any(candidate[:, 0] < 0) or np.any(candidate[:, 0] > width)
            or np.any(candidate[:, 1] < 0) or np.any(candidate[:, 1] > height)
            or cv2.contourArea(candidate) < cv2.contourArea(quad) * 0.85):
        return quad
    return candidate


def refine_corners(image: np.ndarray, quads: list[np.ndarray]) -> list[np.ndarray]:
    refined = [refine_page_corners(image, quad) for quad in quads]
    if len(refined) == 2:
        # The two halves of an open book share the same spine endpoints.
        # Keep the join continuous even when one side has a stronger edge.
        diagonal = np.hypot(image.shape[1], image.shape[0])
        for left_index, right_index in ((1, 0), (2, 3)):
            original = (quads[0][left_index] + quads[1][right_index]) / 2
            shared = (refined[0][left_index] + refined[1][right_index]) / 2
            delta = shared - original
            length = np.linalg.norm(delta)
            if length > diagonal * 0.007:
                shared = original + delta * (diagonal * 0.007 / length)
            refined[0][left_index] = shared
            refined[1][right_index] = shared
    try:
        for quad in refined:
            validate_quad(quad, image.shape)
    except ValueError:
        return quads
    return refined


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return float(values[order[np.searchsorted(cumulative, cumulative[-1] / 2)]])


def deskew_page(page: np.ndarray, max_degrees: float = 1.5) -> tuple[np.ndarray, float]:
    """Align convincing printed rules without following diagonal page artwork."""
    if max_degrees == 0:
        return page, 0.0
    height, width = page.shape[:2]
    scale = min(1.0, 1800 / max(height, width))
    small = (cv2.resize(page, None, fx=scale, fy=scale,
                        interpolation=cv2.INTER_AREA) if scale < 1 else page)
    small_height, small_width = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 60, 160)
    margin_x, margin_y = round(small_width * 0.05), round(small_height * 0.07)
    edges[:margin_y] = 0
    edges[small_height - margin_y:] = 0
    edges[:, :margin_x] = 0
    edges[:, small_width - margin_x:] = 0
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=60,
                            minLineLength=max(60, round(small_width * 0.07)),
                            maxLineGap=20)
    if lines is None:
        return page, 0.0
    angles, lengths = [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        length = float(np.hypot(x2 - x1, y2 - y1))
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if length >= small_width * 0.1 and abs(angle) <= 6:
            angles.append(angle)
            lengths.append(length)
    if len(angles) < 8:
        return page, 0.0
    angles = np.asarray(angles)
    lengths = np.asarray(lengths)
    angle = weighted_median(angles, lengths)
    deviation = weighted_median(np.abs(angles - angle), lengths)
    support = lengths[np.abs(angles - angle) < 0.75].sum()
    if (abs(angle) < 0.15 or abs(angle) > max_degrees
            or deviation > 1.2 or support < small_width * 3):
        return page, 0.0
    matrix = cv2.getRotationMatrix2D(((width - 1) / 2, (height - 1) / 2),
                                     angle, 1.0)
    aligned = cv2.warpAffine(page, matrix, (width, height),
                             flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(255, 255, 255))
    return aligned, angle


def stitch(pages: list[np.ndarray]) -> np.ndarray:
    if len(pages) == 1:
        return pages[0]
    target_height = max(page.shape[0] for page in pages)
    scaled = [cv2.resize(page, (round(page.shape[1] * target_height / page.shape[0]), target_height), interpolation=cv2.INTER_AREA) for page in pages]
    output = np.full((target_height, sum(p.shape[1] for p in scaled), 3), 255, np.uint8)
    x = 0
    for page in scaled:
        output[:, x:x + page.shape[1]] = page
        x += page.shape[1]
    return output


def tone_rendered_pdf(image: np.ndarray, white_point: int,
                      contrast: float, black_point: int) -> np.ndarray:
    """Set levels for a PDF page whose lighting is already uniform."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Correct light paper areas while preserving broad dark illustration/cover tones.
    sigma = max(18.0, min(gray.shape) / 20.0)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    baseline = float(np.percentile(background, 82))
    source = gray.astype(np.float32)
    lift = np.clip(baseline / np.maximum(background.astype(np.float32), 35) - 1, 0, 0.28)
    saturation = hsv[:, :, 1].astype(np.float32)
    paper_weight = np.clip((source - 105) / 95, 0, 1) * (1 - np.clip(saturation / 100, 0, 1))
    normalized = source * (1 + lift * paper_weight)
    # Pale neutral paper receives more whitening; blue printed fields stay midgray.
    effective_white = float(white_point) - 30 * (1 - np.clip(saturation / 60, 0, 1))
    blue_field = (hsv[:, :, 0] >= 80) & (hsv[:, :, 0] <= 135) & (saturation > 35)
    effective_white = np.where(blue_field, np.maximum(effective_white, white_point + 35), effective_white)
    normalized = (normalized - 70) * (255.0 / (effective_white - 70))
    normalized = (normalized - 128) * contrast + 128
    output = np.uint8(np.clip(normalized, 0, 255))
    blur = cv2.GaussianBlur(output, (0, 0), 0.85)
    sharpened = cv2.addWeighted(output, 1.64, blur, -0.64, 0)
    # Deepen ink without lowering the white paper level or binarizing edges.
    return np.uint8(np.clip((sharpened.astype(np.float32) - black_point)
                            * (255.0 / (255 - black_point)), 0, 255))


def tone_photograph(image: np.ndarray, white_point: int,
                    contrast: float, black_point: int) -> np.ndarray:
    """Lift broad lighting shadows before setting restrained page levels."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32)
    # Closing fills thin ink strokes while leaving broad shadows in the
    # illumination estimate. Its radius and the blur scale with page size.
    kernel_side = max(31, round(min(gray.shape) / 55) // 2 * 2 + 1)
    closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE,
                             np.ones((kernel_side, kernel_side), np.uint8))
    illumination = cv2.GaussianBlur(closed, (0, 0),
                                    sigmaX=min(gray.shape) / 24)
    gain = np.clip((white_point - 3) /
                   np.maximum(illumination.astype(np.float32), 45), 0.9, 3.8)
    source = gray.astype(np.float32)
    lifted = source * gain
    # Warm cast shadows can have moderate saturation. Protect stronger printed
    # color and very dark photographic/ink detail, not the tinted shadow field.
    color_weight = (np.clip((saturation - 60) / 25, 0, 1)
                    * np.clip((215 - source) / 80, 0, 1))
    cool_ink = ((hsv[:, :, 0] >= 80) & (hsv[:, :, 0] <= 175)).astype(np.float32)
    color_weight = np.maximum(color_weight,
                              0.70 * cool_ink * np.clip((saturation - 28) / 16, 0, 1))
    dark_weight = np.clip((105 - source) / 45, 0, 1)
    print_weight = np.maximum(color_weight, dark_weight)
    balanced = lifted * (1 - print_weight) + source * print_weight
    # Bring shadowed paper close to unshadowed paper without whitening text:
    # ink is much darker than its local illumination estimate, while paper
    # stays near that estimate. Printed color panels retain their own tone.
    relative_light = source / np.maximum(illumination.astype(np.float32), 1)
    paper_weight = (np.clip((relative_light - 0.70) / 0.22, 0, 1)
                    * (1 - print_weight))
    balanced += np.maximum(0, white_point - balanced) * paper_weight * 0.95
    levels = (balanced - black_point) * (255.0 / (white_point - black_point))
    return np.uint8(np.clip((levels - 128) * contrast + 128, 0, 255))


def restore_pdf_colors(source: np.ndarray, toned_gray: np.ndarray) -> np.ndarray:
    """Keep intentional source colors while cleaning neutral paper and ink."""
    saturation = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
    source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY).astype(np.float32)
    color_weight = np.clip((saturation - 4) / 8, 0, 1)
    # Dark antialiased ink on a pale tinted field is still ink; vivid blue
    # links stay colored even though their luminance is low.
    neutral_ink = np.clip((source_gray - 90) / 90, 0, 1)
    color_weight *= np.where(saturation >= 40, 1, neutral_ink)
    color_weight = color_weight[:, :, None]
    neutral = cv2.cvtColor(toned_gray, cv2.COLOR_GRAY2BGR).astype(np.float32)
    return np.uint8(np.clip(neutral * (1 - color_weight)
                            + source.astype(np.float32) * color_weight, 0, 255))


def restore_photo_colors(source: np.ndarray, toned_gray: np.ndarray) -> np.ndarray:
    """Put cleaned luminance under printed color while neutralizing paper casts."""
    saturation = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
    chroma_weight = np.clip((saturation - 25) / 35, 0, 1)
    ycrcb = cv2.cvtColor(source, cv2.COLOR_BGR2YCrCb).astype(np.float32)
    ycrcb[:, :, 0] = toned_gray
    ycrcb[:, :, 1:3] = (128 + (ycrcb[:, :, 1:3] - 128)
                         * chroma_weight[:, :, None])
    return cv2.cvtColor(np.uint8(np.clip(ycrcb, 0, 255)),
                        cv2.COLOR_YCrCb2BGR)


def reinforce_ink(image: np.ndarray) -> np.ndarray:
    """Darken existing stroke edges after resampling without tinting paper."""
    nearby_dark = cv2.erode(image, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)))
    if image.ndim == 3:
        neutral_weight = np.clip((15 - cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
                                  .astype(np.float32)) / 12, 0, 1)[:, :, None]
    else:
        neutral_weight = 1
    weight = (0.52 * np.clip((235 - image.astype(np.float32)) / 180, 0, 1)
              * neutral_weight)
    result = image.astype(np.float32) - (image.astype(np.float32) - nearby_dark) * weight
    return np.uint8(np.clip(result, 0, 255))


def sharpen_surface(image: np.ndarray) -> np.ndarray:
    """Sharpen existing print and paper detail after page processing."""
    blurred = cv2.GaussianBlur(image, (0, 0), 0.85)
    return cv2.addWeighted(image, 1.64, blurred, -0.64, 0)


def enforce_pure_tones(image: np.ndarray, source: np.ndarray) -> np.ndarray:
    """Set neutral paper to RGB 255 and dark neutral ink to RGB 0."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    source_saturation = cv2.cvtColor(source, cv2.COLOR_BGR2HSV)[:, :, 1]
    image_saturation = (cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
                        if image.ndim == 3 else np.zeros_like(gray))
    output = image.copy()
    output[(gray >= 235) & (source_saturation <= 6)] = 255
    output[(gray <= 96) & (image_saturation <= 20)] = 0
    return output


def compose_tilted_page(page: np.ndarray, background_photo: np.ndarray,
                        max_tilt_percent: float, rng: np.random.Generator,
                        grayscale: bool) -> tuple[np.ndarray, float, float]:
    """Place a lightly tilted page over the supplied shadow photograph."""
    if page.ndim == 2:
        page = cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)
    height, width = page.shape[:2]
    slope = float(rng.uniform(-max_tilt_percent, max_tilt_percent) / 100)
    angle = float(np.degrees(np.arctan(slope)))
    matrix = cv2.getRotationMatrix2D(((width - 1) / 2, (height - 1) / 2),
                                     angle, 1.0)

    photo_height, photo_width = background_photo.shape[:2]
    fit = max(width / photo_width, height / photo_height)
    backdrop = cv2.resize(background_photo,
                          (max(width, round(photo_width * fit)),
                           max(height, round(photo_height * fit))),
                          interpolation=cv2.INTER_CUBIC)
    y0 = (backdrop.shape[0] - height) // 2
    x0 = (backdrop.shape[1] - width) // 2
    backdrop = backdrop[y0:y0 + height, x0:x0 + width]
    if grayscale:
        backdrop = cv2.cvtColor(cv2.cvtColor(backdrop, cv2.COLOR_BGR2GRAY),
                                cv2.COLOR_GRAY2BGR)

    mask = cv2.warpAffine(np.full((height, width), 255, np.uint8), matrix,
                          (width, height), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    rotated = cv2.warpAffine(page, matrix, (width, height),
                             flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(255, 255, 255))
    alpha = mask.astype(np.float32)[:, :, None] / 255
    output = np.uint8(np.clip(rotated.astype(np.float32) * alpha
                              + backdrop.astype(np.float32) * (1 - alpha), 0, 255))
    # Anchor the selection at (0, 0). Pull its right and bottom boundaries
    # inward to the first rotated paper corners, trimming exposed background
    # on those sides without moving the top or left edges.
    corners = np.float32([[0, 0, 1], [width - 1, 0, 1],
                          [width - 1, height - 1, 1], [0, height - 1, 1]])
    rotated_corners = corners @ matrix.T
    if angle == 0:
        crop_right, crop_bottom = width, height
    else:
        crop_right = min(width, max(1, int(np.floor(min(rotated_corners[1, 0],
                                                     rotated_corners[2, 0]))) - 1))
        crop_bottom = min(height, max(1, int(np.floor(min(rotated_corners[2, 1],
                                                       rotated_corners[3, 1]))) - 1))
    return output[:crop_bottom, :crop_right], slope, angle


def size_page(image: np.ndarray, max_dimension: int,
              aspect: float | None = None) -> np.ndarray:
    """Apply an explicit physical proportion, then cap raster dimensions."""
    if aspect is not None:
        target_height = max(1, round(image.shape[1] / aspect))
        interpolation = (cv2.INTER_CUBIC if target_height > image.shape[0]
                         else cv2.INTER_AREA)
        image = cv2.resize(image, (image.shape[1], target_height),
                           interpolation=interpolation)
    if max(image.shape[:2]) > max_dimension:
        scale = max_dimension / max(image.shape[:2])
        image = cv2.resize(image, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
    return image


def prepare_photograph(key: str, image: np.ndarray, override,
                       args: argparse.Namespace):
    """Return rectified BGR pixels, geometry, deskew, and chosen aspect."""
    aspect = args.aspect
    raw_quads = override
    if isinstance(override, dict):
        unexpected = set(override) - {"quads", "aspect"}
        if unexpected:
            raise ValueError(f"{key}: unknown geometry keys: {sorted(unexpected)}")
        if override.get("aspect") is not None:
            aspect = override["aspect"]
        raw_quads = override.get("quads")
    if aspect is not None and (not isinstance(aspect, (int, float))
                               or not 0.3 <= aspect <= 4):
        raise ValueError(f"{key}: aspect must be between 0.3 and 4")
    if raw_quads is not None and args.geometry == "none":
        raise ValueError(f"{key}: --geometry none conflicts with --quads")

    if raw_quads is not None:
        if not isinstance(raw_quads, list) or not 1 <= len(raw_quads) <= 2:
            raise ValueError(f"{key}: supply one page quad or two spread quads")
        quads = [validate_quad(quad, image.shape) for quad in raw_quads]
        method = "manual"
    elif args.geometry == "none":
        quads = []
        method = "pre-cropped image"
    else:
        found = auto_quad(image)
        if found is None:
            raise ValueError(f"{key}: paper corners are uncertain; supply --quads "
                             "or use --geometry none for an already-cropped image")
        quads = [validate_quad(found, image.shape)]
        method = "automatic"

    if quads and not args.no_refine_corners and (method == "automatic" or len(quads) == 2):
        quads = refine_corners(image, quads)
        method += " + edge-refined"
    pages = [warp_page(image, quad, args.max_dimension) for quad in quads] if quads else [image]
    deskew_angles = []
    if quads:
        aligned = []
        for page in pages:
            straightened, angle = deskew_page(page, args.deskew_max_degrees)
            aligned.append(straightened)
            deskew_angles.append(angle)
        pages = aligned
    if aspect is None and len(quads) == 2 and not args.no_auto_spread_aspect:
        aspect = 1.35
    corrected = size_page(stitch(pages), args.max_dimension, aspect)
    return corrected, quads, method, deskew_angles, aspect


def style_page(image: np.ndarray, is_pdf: bool,
               args: argparse.Namespace) -> np.ndarray:
    """Map a prepared BGR page to color BGR or monochrome scan pixels."""
    color_pdf = is_pdf and args.color_mode == "preserve"
    white_point = args.white_point if args.white_point is not None else (215 if color_pdf else 228)
    contrast = args.contrast if args.contrast is not None else (1.84 if color_pdf else 1.08)
    black_point = args.black_point if args.black_point is not None else (90 if color_pdf else 25)
    if is_pdf:
        toned = tone_rendered_pdf(image, white_point, contrast, black_point)
        scanned = restore_pdf_colors(image, toned) if color_pdf else toned
    else:
        toned = tone_photograph(image, white_point, contrast, black_point)
        scanned = (restore_photo_colors(image, toned)
                   if args.color_mode == "preserve" else toned)
    scanned = reinforce_ink(sharpen_surface(scanned))
    if args.color_mode == "preserve":
        return enforce_pure_tones(scanned, image)
    scanned[scanned >= 245] = 255
    scanned[scanned <= 38] = 0
    return scanned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="image/PDF files or directories")
    parser.add_argument("--output", required=True, type=Path, help="output PDF path")
    parser.add_argument("--quads", type=Path, help="JSON page corners for image inputs")
    parser.add_argument("--geometry", choices=("auto", "none"), default="auto",
                        help="detect photo paper automatically, or use a pre-cropped image unchanged")
    parser.add_argument("--no-refine-corners", action="store_true",
                        help="use detected or supplied corners exactly, without photo-edge refinement")
    parser.add_argument("--dpi", type=int, default=220, help="PDF capture and output resolution")
    parser.add_argument("--white-point", type=int, help="gray level mapped to white, 180–255 (default: color PDF 215, photos/grayscale 228)")
    parser.add_argument("--contrast", type=float, help="contrast multiplier, 0.8–2.2 (default: color PDF 1.84, photos/grayscale 1.08)")
    parser.add_argument("--black-point", type=int, help="gray level mapped to black, 0–100 (default: color PDF 90, photos/grayscale 25)")
    parser.add_argument("--color-mode", choices=("preserve", "grayscale"), default="preserve", help="preserve source colors or produce a grayscale scan")
    parser.add_argument("--max-dimension", type=int, default=4800, help="maximum rectified page side in pixels")
    parser.add_argument("--aspect", type=float,
                        help="photo output width/height ratio; two-page spreads otherwise use 1.35")
    parser.add_argument("--no-auto-spread-aspect", action="store_true",
                        help="keep the projected width/height ratio for two-page spreads instead of the usual 1.35")
    parser.add_argument("--deskew-max-degrees", type=float, default=1.5,
                        help="maximum automatic page deskew in degrees, 0–2; 0 disables it")
    parser.add_argument("--tilt-percent", type=float, default=0.25,
                        help="maximum final random page-edge slope in percent, 0–1 (default 0.25)")
    parser.add_argument("--background-image", type=Path, help="photo behind the tilted page; defaults to the bundled shadow photo")
    parser.add_argument("--seed", type=int, help="optional random seed for reproducible tilt")
    args = parser.parse_args()
    limits = (
        ("dpi", args.dpi, 72, 600),
        ("white-point", args.white_point, 180, 255),
        ("contrast", args.contrast, 0.8, 2.2),
        ("black-point", args.black_point, 0, 100),
        ("max-dimension", args.max_dimension, 500, float("inf")),
        ("aspect", args.aspect, 0.3, 4),
        ("deskew-max-degrees", args.deskew_max_degrees, 0, 2),
        ("tilt-percent", args.tilt_percent, 0, 1),
    )
    for name, value, minimum, maximum in limits:
        if value is not None and not minimum <= value <= maximum:
            parser.error(f"--{name} outside supported range {minimum}–{maximum}")
    overrides = json.loads(args.quads.read_text()) if args.quads else {}
    if not isinstance(overrides, dict):
        parser.error("--quads must contain a JSON object")
    inputs = collect_inputs(args.inputs, args.output)
    background_path = args.background_image or Path(__file__).resolve().parent.parent / "assets" / "wood-shadow-reference.jpg"
    background_photo = cv2.imread(str(background_path), cv2.IMREAD_COLOR)
    if background_photo is None:
        raise ValueError(f"Could not read background image: {background_path}")
    rng = np.random.default_rng(args.seed)
    pdf = pymupdf.open()
    try:
        for path in inputs:
            for key, image, is_pdf in load_pages(path, args.dpi):
                if is_pdf:
                    if key in overrides or path.name in overrides or args.aspect is not None:
                        raise ValueError(f"{key}: PDF pages are captured as displayed; "
                                         "--quads and --aspect apply only to images")
                    corrected = size_page(image, args.max_dimension)
                    quads = []
                    deskew_angles = []
                    aspect = None
                    method = "PDF page capture"
                else:
                    corrected, quads, method, deskew_angles, aspect = prepare_photograph(
                        key, image, overrides.get(key, overrides.get(path.name)), args)
                scanned = style_page(corrected, is_pdf, args)
                scanned, slope, angle = compose_tilted_page(
                    scanned, background_photo, args.tilt_percent, rng,
                    args.color_mode == "grayscale")
                success, encoded = cv2.imencode(".png", scanned)
                if not success:
                    raise RuntimeError(f"Could not encode {key}")
                height, width = scanned.shape[:2]
                pdf_page = pdf.new_page(width=width * 72 / args.dpi, height=height * 72 / args.dpi)
                pdf_page.insert_image(pdf_page.rect, stream=encoded.tobytes())
                print(f"{key}: {image.shape[1]}x{image.shape[0]} -> {width}x{height}; {method}; aspect={aspect if aspect is not None else 'source'}; deskew={[round(value, 3) for value in deskew_angles]}; tilt={slope:+.3%} ({angle:+.3f}°); corners={[q.round().astype(int).tolist() for q in quads] if quads else 'none'}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(args.output, garbage=3, deflate=True)
        print(f"Wrote {len(pdf)} page(s): {args.output}")
        return 0
    finally:
        pdf.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
