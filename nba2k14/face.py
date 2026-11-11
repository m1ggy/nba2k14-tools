"""Make a cyberface texture from a photo (texture only; the head shape stays the template's).

Steps:
1. find 478 face landmarks in the photo and in the template's face_color texture
   (MediaPipe Face Landmarker, runs locally: models/face_landmarker.task)
2. warp the photo so its landmarks land on the template's (piecewise linear over
   the landmark triangulation)
3. shift the template's skin tone (forehead, ears, neck, chest) toward the photo's
4. blend the warped face in with a feathered mask
"""
import os

import numpy as np
from PIL import Image
from scipy.interpolate import LinearNDInterpolator
from scipy.ndimage import gaussian_filter
from scipy.spatial import ConvexHull

from . import texture as T

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(HERE, "models", "face_landmarker.task")

# MediaPipe face-oval landmark indices (outline of the face)
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400,
             377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
# cheek patches used to measure skin colour
CHEEKS = [50, 101, 118, 117, 123, 187, 205, 36, 280, 330, 347, 346, 352, 411, 425, 266]

_landmarker = None


class FaceError(ValueError):
    pass


def _get_landmarker():
    global _landmarker
    if _landmarker is None:
        from mediapipe.tasks.python import BaseOptions, vision
        if not os.path.exists(MODEL):
            raise FaceError(f"Face model missing: {MODEL}")
        _landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL), num_faces=1,
            min_face_detection_confidence=0.3, min_face_presence_confidence=0.3))
    return _landmarker


def landmarks(rgb):
    """(478, 2) pixel coordinates of the face in an RGB uint8 image."""
    import mediapipe as mp
    rgb = np.ascontiguousarray(rgb[..., :3], np.uint8)
    res = _get_landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not res.face_landmarks:
        raise FaceError("No face found in the image. Use a clear, front-facing photo.")
    h, w = rgb.shape[:2]
    return np.array([(p.x * w, p.y * h) for p in res.face_landmarks[0]], np.float32)


def _to_lab(rgb):
    from cv2 import COLOR_RGB2LAB, cvtColor
    return cvtColor(rgb.astype(np.uint8), COLOR_RGB2LAB).astype(np.float32)


def _from_lab(lab):
    from cv2 import COLOR_LAB2RGB, cvtColor
    return cvtColor(np.clip(lab, 0, 255).astype(np.uint8), COLOR_LAB2RGB)


def _polygon_mask(shape, pts):
    from cv2 import fillPoly
    mask = np.zeros(shape[:2], np.uint8)
    fillPoly(mask, [np.round(pts).astype(np.int32)], 255)
    return mask.astype(np.float32) / 255


def _skin_stats(lab, pts):
    """Mean / std of skin colour in small discs around the cheek landmarks."""
    h, w = lab.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    sel = np.zeros((h, w), bool)
    r = max(3, int(0.012 * w))
    for i in CHEEKS:
        x, y = pts[i]
        sel |= (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    px = lab[sel]
    return px.mean(0), px.std(0) + 1e-3


def make_face_texture(photo_rgb, template_rgba, strength=1.0, feather=0.035):
    """Return a new face_color RGBA image in the template's layout."""
    photo = np.asarray(photo_rgb)[..., :3].astype(np.uint8)
    tmpl = np.asarray(template_rgba).astype(np.uint8)
    th, tw = tmpl.shape[:2]
    p_pts = landmarks(photo)
    t_pts = landmarks(tmpl[..., :3])

    # 1. warp: for every template pixel, where to sample the photo
    interp = LinearNDInterpolator(t_pts, p_pts)
    yy, xx = np.mgrid[0:th, 0:tw].astype(np.float32)
    mapped = interp(np.stack([xx.ravel(), yy.ravel()], -1)).reshape(th, tw, 2).astype(np.float32)
    inside = ~np.isnan(mapped[..., 0])
    mapped[~inside] = 0
    from cv2 import BORDER_REFLECT, INTER_LINEAR, remap
    warped = remap(photo, mapped[..., 0], mapped[..., 1], INTER_LINEAR, borderMode=BORDER_REFLECT)

    # 2. skin tone: move the whole template toward the photo's skin colour
    t_lab, w_lab = _to_lab(tmpl[..., :3]), _to_lab(warped)
    t_mean, t_std = _skin_stats(t_lab, t_pts)
    p_mean, p_std = _skin_stats(w_lab, t_pts)
    recol = (t_lab - t_mean) * (p_std / t_std) + p_mean
    # recolour skin (face, ears, neck, chest) but not the hair above the forehead
    hairline = t_pts[10][1]                      # top of the forehead
    ramp = np.clip((yy - (hairline - 0.02 * th)) / (0.06 * th), 0, 1)
    oval_core = gaussian_filter(_polygon_mask(tmpl.shape, t_pts[FACE_OVAL]), sigma=0.02 * tw)
    t_skin = np.maximum(ramp, oval_core)[..., None]
    base = _from_lab(t_lab + (recol - t_lab) * strength * t_skin)

    # 3. blend the warped face over the recoloured template
    oval = t_pts[FACE_OVAL]
    centre = oval.mean(0)
    oval = centre + (oval - centre) * 0.9  # stay inside the outline
    mask = _polygon_mask(tmpl.shape, oval) * inside
    # fade out warped pixels that aren't skin-like (photo background, hair at the edges)
    dist = np.sqrt((((w_lab - p_mean) / (p_std * 2.5)) ** 2).sum(-1))
    skin = np.clip(1.5 - dist, 0, 1)
    skin = np.maximum(skin, _polygon_mask(tmpl.shape, centre + (oval - centre) * 0.6))  # always keep the core
    mask = gaussian_filter(mask * skin, sigma=feather * tw)
    mask = np.clip(mask * 1.2, 0, 1)[..., None]
    out = base.astype(np.float32) * (1 - mask) + warped.astype(np.float32) * mask
    rgba = tmpl.copy()
    rgba[..., :3] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    return rgba


def rank_templates(photo_rgb, iff_paths, top=5):
    """Rank face files as templates for a photo: similar skin tone, little beard."""
    from .iff import IffFile
    photo = np.asarray(photo_rgb)[..., :3].astype(np.uint8)
    p_pts = landmarks(photo)
    p_mean, _ = _skin_stats(_to_lab(photo), p_pts)
    jaw = [152, 148, 377, 176, 400, 149, 378, 136, 365]
    scored = []
    for path in iff_paths:
        try:
            tex = next(t for t in T.textures(IffFile.load(path)) if t.name == "face_color")
            img = tex.image()[..., :3]
            lab, pts = _to_lab(img), landmarks(img)
        except Exception:
            continue
        mean, _ = _skin_stats(lab, pts)
        jaw_l = np.mean([lab[int(pts[i][1]) - 6:int(pts[i][1]) + 6, int(pts[i][0]) - 6:int(pts[i][0]) + 6, 0].mean()
                         for i in jaw])
        beard = max(mean[0] - jaw_l, 0)
        scored.append((float(np.linalg.norm(mean - p_mean) + 0.8 * beard), path))
    scored.sort()
    return scored[:top]


def apply_to_iff(iff, photo_rgb, **kw):
    """Put a photo-made face_color into an IffFile (in memory). Returns the new texture image."""
    tex = next((t for t in T.textures(iff) if t.name == "face_color"), None)
    if tex is None or not tex.editable:
        raise FaceError("This file has no editable face_color texture.")
    img = make_face_texture(photo_rgb, tex.image(), **kw)
    tex.set_image(img)
    return img


def load_photo(path, max_side=1600):
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.array(im)
