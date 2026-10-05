# Photograph geometry

`--quads` accepts a JSON object keyed by image basename. Each value is one quadrilateral for a single page or two for an open spread, ordered left to right. Each quadrilateral contains `[x, y]` source-image pixel coordinates **after EXIF orientation**, in top-left, top-right, bottom-right, bottom-left order.

```json
{
  "photo.jpg": [
    [[100, 80], [900, 90], [890, 1200], [110, 1190]]
  ],
  "spread.jpg": [
    [[70, 95], [570, 90], [560, 860], [5, 850]],
    [[570, 90], [1100, 100], [1150, 880], [560, 860]]
  ]
}
```

For a single image page, the supplied four corners are used exactly. For a two-page spread, approximate corners are automatically moved toward nearby continuous paper edges, and the two pages share spine endpoints. The logged coordinates are the coordinates used for the perspective transforms. Inspect them against the source photo; use `--no-refine-corners` if precise spread corners should stay fixed or edge snapping follows printed artwork.

With no supplied corners, `--geometry auto` detects **one** photographed sheet. Uncertain detection stops with an error; it never converts the surrounding table as a page. Supply corners for a spread or a failed single-sheet detection. `--geometry none` is for an already-cropped image and cannot be combined with `--quads`. PDF pages use their rendered frame and do not accept image geometry overrides.

The default stitched-spread width/height ratio is 1.35. Supply a known proportion with `--aspect 1.4` or a per-image entry such as `{"quads": [[[x,y], ...]], "aspect": 1.4}`. `--no-auto-spread-aspect` keeps the ratio derived from the photographed corner lengths when no aspect is supplied. A single page keeps its rectified proportion unless given an explicit aspect.

After rectification, printed-line deskew may rigidly rotate a photographed page by at most `--deskew-max-degrees` (default 1.5°, allowed 0–2°). It skips weak or decorative diagonal lines; zero disables it. This is distinct from the final whole-page random slope (default ±0.25%, disabled by `--tilt-percent 0`). Neither operation can flatten curved book paper, uncover hidden text, or infer an occluded corner.
